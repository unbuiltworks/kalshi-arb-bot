"""Full-history public activity reconstruction for the l5 Polymarket wallet.

READ ONLY. No credentials, signing, order placement, cancellation, or mutation.

Purpose
-------
Reconstruct the wallet's BTC/ETH/SOL/XRP 5m/15m behavior from public Activity
rows across an arbitrary historical interval. This is intended to answer:

* how often it buys both outcomes;
* how frequently it switches sides within a market;
* how much quantity can be paired chronologically below $1;
* how much residual directional inventory remains;
* how often it buys high-priced favorites late in the window;
* resolved cash economics from BUY/SELL + REDEEM;
* explicit MAKER_REBATE and REWARD cashflows.

Important limits
----------------
The Activity feed does not expose the exact historical queue/book state or a
maker/taker flag for every historical fill. Recent maker/taker mix is audited
separately with the public `/trades?takerOnly=` comparison. Pairing statistics
here are accounting decompositions of observed fills, not proof of executable
arbitrage or intent.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
import statistics
import time
from typing import Any, Iterable

import httpx

from src.polymarket_wallet_shadow import activity_id

DATA_API = "https://data-api.polymarket.com"
WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"

ASSET_PATTERNS = {
    "BTC": re.compile(r"\b(bitcoin|btc)\b", re.I),
    "ETH": re.compile(r"\b(ethereum|eth)\b", re.I),
    "SOL": re.compile(r"\b(solana|sol)\b", re.I),
    "XRP": re.compile(r"\b(xrp|ripple)\b", re.I),
}
SLUG_WINDOW_RE = re.compile(r"(?:^|[-_])updown[-_](5m|15m)[-_](\d{10})(?:$|[-_])", re.I)
TITLE_WINDOW_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*(?P<a1>AM|PM)?\s*[-–]\s*"
    r"(?P<h2>\d{1,2}):(?P<m2>\d{2})\s*(?P<a2>AM|PM)\s*ET",
    re.I,
)


def _f(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _i(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _asset(row: dict[str, Any]) -> str | None:
    text = f"{row.get('title') or ''} {row.get('slug') or ''}"
    for symbol, pattern in ASSET_PATTERNS.items():
        if pattern.search(text):
            return symbol
    return None


def _clock_minutes(hour: int, minute: int, ampm: str) -> int:
    h = hour % 12
    if ampm.upper() == "PM":
        h += 12
    return h * 60 + minute


def _horizon_from_title(title: str) -> int | None:
    m = TITLE_WINDOW_RE.search(title or "")
    if not m:
        return None
    end_ampm = m.group("a2").upper()
    start_ampm = (m.group("a1") or end_ampm).upper()
    start = _clock_minutes(int(m.group("h1")), int(m.group("m1")), start_ampm)
    end = _clock_minutes(int(m.group("h2")), int(m.group("m2")), end_ampm)
    duration = (end - start) % (24 * 60)
    return duration if duration in {5, 15} else None


def market_meta(row: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    """Return (asset, horizon_minutes, market_start_ts)."""
    asset = _asset(row)
    slug = str(row.get("slug") or "")
    m = SLUG_WINDOW_RE.search(slug)
    if m:
        horizon = int(m.group(1).lower().replace("m", ""))
        return asset, horizon, int(m.group(2))
    horizon = _horizon_from_title(str(row.get("title") or ""))
    return asset, horizon, None


def _fetch_page(
    http: httpx.Client,
    *,
    kind: str,
    start: int,
    end: int,
    offset: int,
    limit: int = 500,
) -> list[dict[str, Any]]:
    r = http.get(
        f"{DATA_API}/activity",
        params={
            "user": WALLET,
            "type": kind,
            "start": int(start),
            "end": int(end),
            "limit": min(max(int(limit), 1), 500),
            "offset": max(0, int(offset)),
            "sortBy": "TIMESTAMP",
            "sortDirection": "ASC",
        },
    )
    r.raise_for_status()
    payload = r.json()
    return [x for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []


def _fetch_slice(
    http: httpx.Client,
    *,
    kind: str,
    start: int,
    end: int,
    depth: int = 0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    saturated = True
    for offset in range(0, 5000, 500):
        page = _fetch_page(http, kind=kind, start=start, end=end, offset=offset)
        rows.extend(page)
        if len(page) < 500:
            saturated = False
            break
    if saturated:
        if depth >= 18 or end - start <= 1:
            raise RuntimeError(f"activity slice saturated for {kind}: {start}-{end}")
        mid = (start + end) // 2
        rows = _fetch_slice(http, kind=kind, start=start, end=mid, depth=depth + 1)
        rows += _fetch_slice(http, kind=kind, start=mid + 1, end=end, depth=depth + 1)
    return rows


def fetch_history(
    http: httpx.Client,
    *,
    kind: str,
    start: int,
    end: int,
    chunk_seconds: int,
) -> list[dict[str, Any]]:
    """Fetch a long interval in bounded chunks and deduplicate rows."""
    dedup: dict[str, dict[str, Any]] = {}
    cursor = int(start)
    while cursor <= end:
        hi = min(end, cursor + max(60, int(chunk_seconds)) - 1)
        for row in _fetch_slice(http, kind=kind, start=cursor, end=hi):
            dedup[activity_id(row)] = row
        cursor = hi + 1
    return sorted(dedup.values(), key=lambda r: (_i(r.get("timestamp")), activity_id(r)))


@dataclass
class PairChunk:
    quantity: float
    pair_cost: float
    gross_edge: float
    lag_seconds: float


def fifo_pairing(buys: Iterable[dict[str, Any]]) -> dict[str, Any]:
    queues: dict[str, deque[list[float]]] = {"UP": deque(), "DOWN": deque()}
    chunks: list[PairChunk] = []
    for row in sorted(buys, key=lambda r: (_i(r.get("timestamp")), activity_id(r))):
        outcome = str(row.get("outcome") or "").strip().upper()
        if outcome not in queues:
            continue
        opposite = "DOWN" if outcome == "UP" else "UP"
        qty = _f(row.get("size"))
        price = _f(row.get("price"))
        ts = _i(row.get("timestamp"))
        if qty <= 0 or not (0 < price < 1):
            continue
        while qty > 1e-12 and queues[opposite]:
            old_price, old_qty, old_ts = queues[opposite][0]
            take = min(qty, old_qty)
            cost = price + old_price
            chunks.append(PairChunk(
                quantity=take,
                pair_cost=cost,
                gross_edge=1.0 - cost,
                lag_seconds=abs(float(ts - old_ts)),
            ))
            qty -= take
            old_qty -= take
            if old_qty <= 1e-12:
                queues[opposite].popleft()
            else:
                queues[opposite][0][1] = old_qty
        if qty > 1e-12:
            queues[outcome].append([price, qty, ts])

    matched = sum(c.quantity for c in chunks)
    gross_edge = sum(c.quantity * c.gross_edge for c in chunks)
    positive_qty = sum(c.quantity for c in chunks if c.gross_edge > 0)
    edge1_qty = sum(c.quantity for c in chunks if c.gross_edge >= 0.01)
    edge2_qty = sum(c.quantity for c in chunks if c.gross_edge >= 0.02)
    edge3_qty = sum(c.quantity for c in chunks if c.gross_edge >= 0.03)
    lag_weighted = sum(c.quantity * c.lag_seconds for c in chunks)
    cost_weighted = sum(c.quantity * c.pair_cost for c in chunks)

    residual: dict[str, dict[str, float | None]] = {}
    for outcome, queue in queues.items():
        qty = sum(x[1] for x in queue)
        cost = sum(x[0] * x[1] for x in queue)
        residual[outcome] = {
            "quantity": qty,
            "cost_usd": cost,
            "avg_price": (cost / qty) if qty > 0 else None,
        }

    return {
        "matched_quantity": matched,
        "gross_pair_edge_usd": gross_edge,
        "avg_pair_cost": (cost_weighted / matched) if matched else None,
        "avg_gross_edge_per_pair": (gross_edge / matched) if matched else None,
        "positive_pair_quantity": positive_qty,
        "edge_ge_1c_quantity": edge1_qty,
        "edge_ge_2c_quantity": edge2_qty,
        "edge_ge_3c_quantity": edge3_qty,
        "avg_pair_lag_seconds": (lag_weighted / matched) if matched else None,
        "pair_chunks": len(chunks),
        "residual": residual,
    }


def analyze_market(
    trades: list[dict[str, Any]],
    redeem_usd: float,
    redeem_ts: int | None,
) -> dict[str, Any] | None:
    if not trades:
        return None
    sample = trades[0]
    asset, horizon, start_ts = market_meta(sample)
    if asset not in {"BTC", "ETH", "SOL", "XRP"} or horizon not in {5, 15}:
        return None

    trades = sorted(trades, key=lambda r: (_i(r.get("timestamp")), activity_id(r)))
    buys = [r for r in trades if str(r.get("side") or "").upper() == "BUY"]
    sells = [r for r in trades if str(r.get("side") or "").upper() == "SELL"]
    if not buys and not sells:
        return None

    buy_cost = sum(_f(r.get("price")) * _f(r.get("size")) for r in buys)
    sell_proceeds = sum(_f(r.get("price")) * _f(r.get("size")) for r in sells)
    buy_qty: dict[str, float] = defaultdict(float)
    buy_cost_by_outcome: dict[str, float] = defaultdict(float)
    for r in buys:
        outcome = str(r.get("outcome") or "UNKNOWN").upper()
        q = _f(r.get("size"))
        p = _f(r.get("price"))
        buy_qty[outcome] += q
        buy_cost_by_outcome[outcome] += p * q

    ordered_outcomes = [str(r.get("outcome") or "").upper() for r in buys]
    ordered_outcomes = [o for o in ordered_outcomes if o in {"UP", "DOWN"}]
    switches = sum(1 for a, b in zip(ordered_outcomes, ordered_outcomes[1:]) if a != b)
    first_side = ordered_outcomes[0] if ordered_outcomes else None

    market_end_ts = (start_ts + horizon * 60) if start_ts is not None else None
    late_favorite_qty = 0.0
    late_favorite_cost = 0.0
    opening_mid_qty = 0.0
    fractional_fill_count = 0
    for r in buys:
        q = _f(r.get("size"))
        p = _f(r.get("price"))
        ts = _i(r.get("timestamp"))
        if abs(q - round(q)) > 1e-9:
            fractional_fill_count += 1
        if start_ts is not None and 0 <= ts - start_ts <= 60 and 0.45 <= p <= 0.55:
            opening_mid_qty += q
        if market_end_ts is not None and 0 <= market_end_ts - ts <= 60 and p >= 0.80:
            late_favorite_qty += q
            late_favorite_cost += p * q

    pair = fifo_pairing(buys)
    resolved = redeem_usd > 0 or redeem_ts is not None
    trading_pnl = (sell_proceeds + redeem_usd - buy_cost) if resolved else None
    return_pct = (trading_pnl / buy_cost) if resolved and buy_cost > 0 else None

    first_trade_ts = _i(trades[0].get("timestamp"))
    last_trade_ts = _i(trades[-1].get("timestamp"))
    return {
        "condition_id": str(sample.get("conditionId") or ""),
        "title": sample.get("title"),
        "slug": sample.get("slug"),
        "asset": asset,
        "horizon_minutes": horizon,
        "market_start_ts": start_ts,
        "market_end_ts": market_end_ts,
        "first_trade_ts": first_trade_ts,
        "last_trade_ts": last_trade_ts,
        "first_trade_offset_seconds": (first_trade_ts - start_ts) if start_ts is not None else None,
        "first_side": first_side,
        "buy_count": len(buys),
        "sell_count": len(sells),
        "fractional_buy_fill_count": fractional_fill_count,
        "fractional_buy_fill_share": (fractional_fill_count / len(buys)) if buys else None,
        "side_switches": switches,
        "two_sided_buys": buy_qty.get("UP", 0.0) > 0 and buy_qty.get("DOWN", 0.0) > 0,
        "buy_quantity": dict(buy_qty),
        "buy_vwap": {
            outcome: (buy_cost_by_outcome[outcome] / qty if qty > 0 else None)
            for outcome, qty in buy_qty.items()
        },
        "buy_cost_usd": buy_cost,
        "sell_proceeds_usd": sell_proceeds,
        "redeem_usd": redeem_usd,
        "redeem_ts": redeem_ts,
        "resolved": resolved,
        "trading_pnl_usd": trading_pnl,
        "trading_return_on_buy_cost": return_pct,
        "opening_45_55_quantity_first_60s": opening_mid_qty,
        "late_favorite_ge_80c_quantity_last_60s": late_favorite_qty,
        "late_favorite_ge_80c_cost_last_60s": late_favorite_cost,
        "fifo_pairing": pair,
    }


def _sum(rows: Iterable[dict[str, Any]], path: tuple[str, ...]) -> float:
    total = 0.0
    for row in rows:
        value: Any = row
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        total += _f(value)
    return total


def summarize(markets: list[dict[str, Any]], rewards: list[dict[str, Any]], rebates: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [m for m in markets if m["resolved"] and m["trading_pnl_usd"] is not None]
    trading_pnl = sum(float(m["trading_pnl_usd"]) for m in resolved)
    buy_cost = sum(float(m["buy_cost_usd"]) for m in resolved)
    reward_usd = sum(_f(r.get("usdcSize")) for r in rewards)
    rebate_usd = sum(_f(r.get("usdcSize")) for r in rebates)

    def group(rows: list[dict[str, Any]]) -> dict[str, Any]:
        matched = _sum(rows, ("fifo_pairing", "matched_quantity"))
        return {
            "markets": len(rows),
            "resolved_markets": sum(1 for m in rows if m["resolved"]),
            "buys": sum(int(m["buy_count"]) for m in rows),
            "sells": sum(int(m["sell_count"]) for m in rows),
            "two_sided_market_share": (sum(1 for m in rows if m["two_sided_buys"]) / len(rows)) if rows else None,
            "avg_side_switches": (sum(int(m["side_switches"]) for m in rows) / len(rows)) if rows else None,
            "resolved_buy_cost_usd": sum(float(m["buy_cost_usd"]) for m in rows if m["resolved"]),
            "resolved_trading_pnl_usd": sum(float(m["trading_pnl_usd"] or 0.0) for m in rows if m["resolved"]),
            "resolved_return_on_buy_cost": (
                sum(float(m["trading_pnl_usd"] or 0.0) for m in rows if m["resolved"])
                / sum(float(m["buy_cost_usd"]) for m in rows if m["resolved"])
            ) if sum(float(m["buy_cost_usd"]) for m in rows if m["resolved"]) > 0 else None,
            "fifo_matched_quantity": matched,
            "fifo_gross_pair_edge_usd": _sum(rows, ("fifo_pairing", "gross_pair_edge_usd")),
            "fifo_positive_pair_quantity": _sum(rows, ("fifo_pairing", "positive_pair_quantity")),
            "fifo_edge_ge_3c_quantity": _sum(rows, ("fifo_pairing", "edge_ge_3c_quantity")),
            "fifo_positive_pair_share": (_sum(rows, ("fifo_pairing", "positive_pair_quantity")) / matched) if matched else None,
            "late_favorite_ge_80c_quantity_last_60s": sum(float(m["late_favorite_ge_80c_quantity_last_60s"]) for m in rows),
            "opening_45_55_quantity_first_60s": sum(float(m["opening_45_55_quantity_first_60s"]) for m in rows),
        }

    by_stream: dict[str, Any] = {}
    for asset in ("BTC", "ETH", "SOL", "XRP"):
        for horizon in (5, 15):
            rows = [m for m in markets if m["asset"] == asset and m["horizon_minutes"] == horizon]
            by_stream[f"{asset}_{horizon}m"] = group(rows)

    by_horizon = {
        "5m": group([m for m in markets if m["horizon_minutes"] == 5]),
        "15m": group([m for m in markets if m["horizon_minutes"] == 15]),
    }

    winners = sorted(resolved, key=lambda m: float(m["trading_pnl_usd"] or 0.0), reverse=True)[:10]
    losers = sorted(resolved, key=lambda m: float(m["trading_pnl_usd"] or 0.0))[:10]

    return {
        "type": "POLY_L5_FULL_HISTORY_BACKTEST",
        "wallet": WALLET,
        "short_crypto_markets": len(markets),
        "resolved_short_crypto_markets": len(resolved),
        "resolved_buy_cost_usd": buy_cost,
        "resolved_trading_pnl_usd": trading_pnl,
        "resolved_trading_return_on_buy_cost": (trading_pnl / buy_cost) if buy_cost > 0 else None,
        "maker_rebate_usd": rebate_usd,
        "liquidity_reward_usd": reward_usd,
        "resolved_plus_explicit_incentives_usd": trading_pnl + rebate_usd + reward_usd,
        "by_horizon": by_horizon,
        "by_stream": by_stream,
        "best_markets": [
            {k: m.get(k) for k in ("title", "asset", "horizon_minutes", "buy_cost_usd", "redeem_usd", "trading_pnl_usd")}
            for m in winners
        ],
        "worst_markets": [
            {k: m.get(k) for k in ("title", "asset", "horizon_minutes", "buy_cost_usd", "redeem_usd", "trading_pnl_usd")}
            for m in losers
        ],
        "interpretation": {
            "resolved_trading_pnl": "Observed public cash accounting: SELL proceeds + REDEEM payout - BUY cost for markets with an observed redeem.",
            "fifo_pairing": "Chronological accounting decomposition of observed buys; not proof that the trader intended or could pre-lock those pairs.",
            "maker_taker": "Full-history Activity rows do not expose a reliable maker/taker flag. Use the separate recent takerOnly comparison for execution-role mix.",
            "unresolved": "Markets without an observed redeem are excluded from resolved trading P&L rather than forced to zero.",
        },
        "shadow_only": True,
        "order_entry": False,
        "realized_pnl": 0.0,
    }


def _parse_date(value: str) -> int:
    text = value.strip()
    if text.isdigit():
        return int(text)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def main() -> None:
    p = argparse.ArgumentParser(description="Full-history public l5 short-crypto activity reconstruction")
    p.add_argument("--start", default="2026-01-01T00:00:00Z")
    p.add_argument("--end", default="now")
    p.add_argument("--output", default="/tmp/l5_full_history_backtest.json")
    args = p.parse_args()

    start = _parse_date(args.start)
    end = int(time.time()) if args.end.lower() == "now" else _parse_date(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start")

    with httpx.Client(timeout=45.0, follow_redirects=True, headers={"User-Agent": "arb-l5-full-history/1.0"}) as http:
        # High-volume trades use 6-hour chunks. Lower-volume cashflows use larger chunks.
        trades = fetch_history(http, kind="TRADE", start=start, end=end, chunk_seconds=6 * 3600)
        redeems = fetch_history(http, kind="REDEEM", start=start, end=end, chunk_seconds=24 * 3600)
        rewards = fetch_history(http, kind="REWARD", start=start, end=end, chunk_seconds=7 * 86400)
        rebates = fetch_history(http, kind="MAKER_REBATE", start=start, end=end, chunk_seconds=7 * 86400)

    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        cid = str(row.get("conditionId") or "").strip()
        if cid:
            by_market[cid].append(row)

    redeem_usd: dict[str, float] = defaultdict(float)
    redeem_ts: dict[str, int] = {}
    for row in redeems:
        cid = str(row.get("conditionId") or "").strip()
        if not cid:
            continue
        redeem_usd[cid] += _f(row.get("usdcSize"))
        redeem_ts[cid] = max(redeem_ts.get(cid, 0), _i(row.get("timestamp")))

    markets: list[dict[str, Any]] = []
    for cid, rows in by_market.items():
        analyzed = analyze_market(rows, redeem_usd.get(cid, 0.0), redeem_ts.get(cid))
        if analyzed is not None:
            markets.append(analyzed)
    markets.sort(key=lambda m: (m.get("market_start_ts") or m.get("first_trade_ts") or 0, m["condition_id"]))

    summary = summarize(markets, rewards, rebates)
    result = {
        "summary": summary,
        "fetch": {
            "start": start,
            "end": end,
            "trade_rows": len(trades),
            "redeem_rows": len(redeems),
            "reward_rows": len(rewards),
            "maker_rebate_rows": len(rebates),
        },
        "markets": markets,
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, sort_keys=True, separators=(",", ":"))

    # Print only the compact summary to stdout so SSM output remains usable.
    print(json.dumps({"fetch": result["fetch"], "summary": summary}, sort_keys=True), flush=True)
    print(f"L5_FULL_HISTORY_WRITTEN path={args.output} markets={len(markets)}", flush=True)


if __name__ == "__main__":
    main()
