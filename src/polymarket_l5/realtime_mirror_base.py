from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

L5_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
GEMINI_API = "https://api.gemini.com"
CLASSIFICATION = "READ_ONLY_L5_REALTIME_SHADOW_NOT_REALIZED"


def utc_now() -> float:
    return time.time()


def iso_ts(ts: float | int | None = None) -> str:
    value = utc_now() if ts is None else float(ts)
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def fnum(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def best_prices(book: dict[str, Any]) -> tuple[float | None, float | None]:
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid = max((fnum(x.get("price"), -1.0) for x in bids), default=-1.0)
    ask = min((fnum(x.get("price"), 2.0) for x in asks), default=2.0)
    return (bid if bid >= 0 else None, ask if ask <= 1 else None)


def normalize_outcome(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in {"up", "yes"}:
        return "UP"
    if text in {"down", "no"}:
        return "DOWN"
    return text.upper() or "UNKNOWN"


def is_target_trade(trade: dict[str, Any]) -> bool:
    title = f"{trade.get('title','')} {trade.get('slug','')} {trade.get('eventSlug','')}".lower()
    return ("bitcoin" in title or "btc" in title or "ethereum" in title or "eth" in title) and (
        "up or down" in title or "updown" in title or "15m" in title or "15-min" in title
    )


def trade_key(trade: dict[str, Any]) -> str:
    tx = str(trade.get("transactionHash") or "")
    return "|".join(
        [
            tx,
            str(trade.get("timestamp") or ""),
            str(trade.get("asset") or ""),
            str(trade.get("price") or ""),
            str(trade.get("size") or ""),
            str(trade.get("side") or ""),
        ]
    )


def shadow_decision(snapshot: dict[str, Any] | None, trade: dict[str, Any]) -> dict[str, Any]:
    """Simple transparent baseline; this is a paper comparator, not a live trading rule."""
    if not snapshot:
        return {"action": "NO_TRADE", "reason": "NO_PRETRADE_SNAPSHOT"}
    up_ask = fnum(snapshot.get("up_ask"))
    down_ask = fnum(snapshot.get("down_ask"))
    spot = fnum(snapshot.get("spot"))
    ref_spot = fnum(snapshot.get("window_ref_spot"))
    if None in {up_ask, down_ask, spot, ref_spot} or ref_spot == 0:
        return {"action": "NO_TRADE", "reason": "INSUFFICIENT_FEATURES"}
    move_bps = 10000.0 * (spot - ref_spot) / ref_spot
    favored = "UP" if move_bps > 0 else "DOWN"
    favored_ask = up_ask if favored == "UP" else down_ask
    strength = min(abs(move_bps) / 20.0, 1.0)
    rough_fair = 0.50 + 0.40 * strength
    edge = rough_fair - favored_ask
    if abs(move_bps) < 2.0:
        return {"action": "NO_TRADE", "reason": "MOVE_TOO_SMALL", "move_bps": round(move_bps, 4)}
    if edge < 0.03:
        return {
            "action": "NO_TRADE",
            "reason": "MODELED_EDGE_LT_3C",
            "favored": favored,
            "move_bps": round(move_bps, 4),
            "rough_fair": round(rough_fair, 4),
            "ask": round(favored_ask, 4),
            "edge": round(edge, 4),
        }
    size = min(25.0, max(1.0, 5.0 + 100.0 * edge))
    return {
        "action": "PAPER_BUY",
        "side": favored,
        "price": round(favored_ask, 4),
        "size": round(size, 4),
        "move_bps": round(move_bps, 4),
        "rough_fair": round(rough_fair, 4),
        "edge": round(edge, 4),
        "reason": "BASELINE_SPOT_DISTANCE_EDGE",
    }


def inventory_summary(fills: list[dict[str, Any]]) -> dict[str, Any]:
    qty = defaultdict(float)
    cost = defaultdict(float)
    for fill in fills:
        side = normalize_outcome(fill.get("outcome"))
        size = fnum(fill.get("size"), 0.0) or 0.0
        price = fnum(fill.get("price"), 0.0) or 0.0
        if str(fill.get("side", "BUY")).upper() == "SELL":
            qty[side] -= size
            cost[side] -= size * price
        else:
            qty[side] += size
            cost[side] += size * price
    up = max(qty["UP"], 0.0)
    down = max(qty["DOWN"], 0.0)
    matched = min(up, down)
    up_avg = cost["UP"] / qty["UP"] if qty["UP"] > 0 else None
    down_avg = cost["DOWN"] / qty["DOWN"] if qty["DOWN"] > 0 else None
    pair_cost = up_avg + down_avg if up_avg is not None and down_avg is not None else None
    return {
        "up_qty": round(qty["UP"], 6),
        "down_qty": round(qty["DOWN"], 6),
        "up_avg": None if up_avg is None else round(up_avg, 6),
        "down_avg": None if down_avg is None else round(down_avg, 6),
        "matched_qty": round(matched, 6),
        "matched_avg_pair_cost": None if pair_cost is None else round(pair_cost, 6),
        "unmatched_up": round(max(up - matched, 0.0), 6),
        "unmatched_down": round(max(down - matched, 0.0), 6),
    }


@dataclass
class MarketWatch:
    condition_id: str
    up_token: str
    down_token: str
    slug: str
    title: str
    end_ts: float | None = None
    ref_spot: float | None = None


class MirrorCollector:
    def __init__(self, output: Path, duration_s: int, book_poll_s: float, trade_poll_s: float) -> None:
        self.output = output
        self.duration_s = duration_s
        self.book_poll_s = book_poll_s
        self.trade_poll_s = trade_poll_s
        self.output.mkdir(parents=True, exist_ok=True)
        self.tape_path = self.output / "mirror.jsonl"
        self.summary_path = self.output / "summary.json"
        self.seen_trades: set[str] = set()
        self.market_by_condition: dict[str, MarketWatch] = {}
        self.snapshots: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=240))
        self.fills_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.paper_ledger: list[dict[str, Any]] = []
        self.latest_spot: dict[str, float] = {}
        self.start_ts = utc_now()
        timeout = httpx.Timeout(5.0, connect=5.0)
        self.client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "arb-l5-shadow/1.0"})

    def emit(self, row: dict[str, Any]) -> None:
        row.setdefault("ts", utc_now())
        row.setdefault("iso", iso_ts(row["ts"]))
        row.setdefault("classification", CLASSIFICATION)
        row.setdefault("order_entry", False)
        row.setdefault("credentials_loaded", False)
        with self.tape_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    async def discover_from_trade(self, trade: dict[str, Any]) -> MarketWatch | None:
        condition = str(trade.get("conditionId") or "")
        asset = str(trade.get("asset") or "")
        if not condition or not asset:
            return None
        existing = self.market_by_condition.get(condition)
        if existing:
            return existing
        try:
            r = await self.client.get(f"{CLOB_API}/markets-by-token/{asset}")
            r.raise_for_status()
            parent = r.json()
        except Exception as exc:
            self.emit({"type": "DISCOVERY_ERROR", "condition_id": condition, "error": repr(exc)})
            return None
        primary = str(parent.get("primary_token_id") or "")
        secondary = str(parent.get("secondary_token_id") or "")
        outcome = normalize_outcome(trade.get("outcome"))
        if outcome == "UP":
            up_token, down_token = asset, secondary if asset == primary else primary
        elif outcome == "DOWN":
            down_token, up_token = asset, secondary if asset == primary else primary
        else:
            up_token, down_token = primary, secondary
        watch = MarketWatch(
            condition_id=condition,
            up_token=up_token,
            down_token=down_token,
            slug=str(trade.get("eventSlug") or trade.get("slug") or ""),
            title=str(trade.get("title") or ""),
        )
        self.market_by_condition[condition] = watch
        self.emit({"type": "MARKET_DISCOVERED_FROM_L5", "market": watch.__dict__})
        return watch

    async def refresh_spot(self) -> None:
        for symbol, pair in (("BTC", "btcusd"), ("ETH", "ethusd")):
            try:
                r = await self.client.get(f"{GEMINI_API}/v1/pubticker/{pair}")
                r.raise_for_status()
                self.latest_spot[symbol] = float(r.json()["last"])
            except Exception as exc:
                self.emit({"type": "SPOT_ERROR", "symbol": symbol, "error": repr(exc)})

    def symbol_for(self, watch: MarketWatch) -> str:
        text = f"{watch.title} {watch.slug}".lower()
        return "ETH" if "eth" in text or "ethereum" in text else "BTC"

    async def poll_books_once(self) -> None:
        await self.refresh_spot()
        for condition, watch in list(self.market_by_condition.items()):
            if not watch.up_token or not watch.down_token:
                continue
            try:
                r = await self.client.post(
                    f"{CLOB_API}/books",
                    json=[{"token_id": watch.up_token}, {"token_id": watch.down_token}],
                )
                r.raise_for_status()
                books = r.json()
                by_asset = {str(x.get("asset_id")): x for x in books}
                up_book = by_asset.get(watch.up_token, {})
                down_book = by_asset.get(watch.down_token, {})
                up_bid, up_ask = best_prices(up_book)
                down_bid, down_ask = best_prices(down_book)
                symbol = self.symbol_for(watch)
                spot = self.latest_spot.get(symbol)
                if watch.ref_spot is None and spot is not None:
                    watch.ref_spot = spot
                snap = {
                    "type": "MARKET_SNAPSHOT",
                    "condition_id": condition,
                    "slug": watch.slug,
                    "symbol": symbol,
                    "up_bid": up_bid,
                    "up_ask": up_ask,
                    "down_bid": down_bid,
                    "down_ask": down_ask,
                    "spot": spot,
                    "window_ref_spot": watch.ref_spot,
                    "up_last_trade": fnum(up_book.get("last_trade_price")),
                    "down_last_trade": fnum(down_book.get("last_trade_price")),
                }
                self.snapshots[condition].append({**snap, "ts": utc_now()})
                self.emit(snap)
            except Exception as exc:
                self.emit({"type": "BOOK_ERROR", "condition_id": condition, "error": repr(exc)})

    def nearest_pretrade_snapshot(self, condition: str, trade_ts: float) -> dict[str, Any] | None:
        rows = self.snapshots.get(condition) or []
        eligible = [row for row in rows if float(row.get("ts", 0)) <= trade_ts + 2.0]
        if not eligible:
            return None
        return min(eligible, key=lambda row: abs(float(row.get("ts", 0)) - trade_ts))

    async def poll_trades_once(self) -> int:
        params = {"user": L5_WALLET, "limit": 200, "takerOnly": "false"}
        r = await self.client.get(f"{DATA_API}/trades", params=params)
        r.raise_for_status()
        rows = r.json()
        new_count = 0
        for trade in sorted(rows, key=lambda x: int(x.get("timestamp") or 0)):
            if not is_target_trade(trade):
                continue
            key = trade_key(trade)
            if key in self.seen_trades:
                continue
            self.seen_trades.add(key)
            watch = await self.discover_from_trade(trade)
            condition = str(trade.get("conditionId") or "")
            trade_ts = float(trade.get("timestamp") or utc_now())
            if trade_ts > 10_000_000_000:
                trade_ts /= 1000.0
            snap = self.nearest_pretrade_snapshot(condition, trade_ts)
            if snap is None and watch is not None:
                await self.poll_books_once()
                snap = self.nearest_pretrade_snapshot(condition, utc_now())
            self.fills_by_condition[condition].append(trade)
            inv = inventory_summary(self.fills_by_condition[condition])
            copy_shadow = {
                "action": "PAPER_COPY_L5",
                "side": normalize_outcome(trade.get("outcome")),
                "price": fnum(trade.get("price")),
                "size": fnum(trade.get("size")),
            }
            ours = shadow_decision(snap, trade)
            comparison = {
                "type": "L5_TRADE_COMPARISON",
                "trade": trade,
                "pretrade_snapshot": snap,
                "l5_inventory": inv,
                "copy_shadow": copy_shadow,
                "our_shadow": ours,
                "same_side": ours.get("side") == copy_shadow.get("side") if ours.get("action") == "PAPER_BUY" else False,
            }
            self.paper_ledger.append(comparison)
            self.emit(comparison)
            new_count += 1
        return new_count

    async def write_summary(self) -> None:
        comparisons = self.paper_ledger
        model_trades = [x for x in comparisons if x.get("our_shadow", {}).get("action") == "PAPER_BUY"]
        same = [x for x in model_trades if x.get("same_side")]
        payload = {
            "type": "L5_REALTIME_SHADOW_SUMMARY_V1",
            "classification": CLASSIFICATION,
            "order_entry": False,
            "credentials_loaded": False,
            "realized_pnl": None,
            "started_at": iso_ts(self.start_ts),
            "finished_at": iso_ts(),
            "l5_trades_captured": len(comparisons),
            "markets_seen": len(self.market_by_condition),
            "our_model_paper_trades": len(model_trades),
            "our_model_same_side_as_l5": len(same),
            "same_side_rate_when_model_trades": (len(same) / len(model_trades)) if model_trades else None,
            "per_market_inventory": {
                condition: inventory_summary(fills) for condition, fills in self.fills_by_condition.items()
            },
        }
        self.summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.emit({"type": "SUMMARY_WRITTEN", "summary": payload})

    async def run(self) -> None:
        self.emit({"type": "MIRROR_STARTED", "duration_s": self.duration_s})
        end_at = self.start_ts + self.duration_s
        next_trade = 0.0
        next_book = 0.0
        while utc_now() < end_at:
            now = utc_now()
            if now >= next_trade:
                try:
                    await self.poll_trades_once()
                except Exception as exc:
                    self.emit({"type": "TRADE_POLL_ERROR", "error": repr(exc)})
                next_trade = now + self.trade_poll_s
            if now >= next_book:
                await self.poll_books_once()
                next_book = now + self.book_poll_s
            await asyncio.sleep(0.2)
        await self.write_summary()
        await self.client.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/output")
    parser.add_argument("--duration-seconds", type=int, default=36000)
    parser.add_argument("--book-poll-seconds", type=float, default=1.0)
    parser.add_argument("--trade-poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    collector = MirrorCollector(
        Path(args.output_dir),
        duration_s=args.duration_seconds,
        book_poll_s=args.book_poll_seconds,
        trade_poll_s=args.trade_poll_seconds,
    )
    asyncio.run(collector.run())


if __name__ == "__main__":
    main()
