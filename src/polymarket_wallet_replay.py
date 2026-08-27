"""Read-only replay tooling for a public Polymarket wallet.

This module NEVER authenticates and NEVER submits, modifies, or cancels orders.
It uses only Polymarket's public Data API to reconstruct wallet activity.

Primary use: test whether a short-duration crypto trader scales into a favored
outcome, switches sides on reversals, and accumulates opposing inventory whose
weighted combined cost sometimes drops below $1.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal as D, InvalidOperation
import json
import re
from typing import Any, Iterable

import httpx

DATA_API = "https://data-api.polymarket.com"
DEFAULT_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
SHORT_CRYPTO_SLUG = re.compile(
    r"(?P<asset>btc|eth|bitcoin|ethereum)-updown-(?P<horizon>5m|15m)-(?P<start>\d{10})",
    re.IGNORECASE,
)
WALLET_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


@dataclass(frozen=True)
class WalletTrade:
    timestamp: int
    condition_id: str
    asset_id: str
    side: str
    outcome: str
    size: D
    price: D
    title: str
    slug: str
    event_slug: str
    transaction_hash: str
    crypto_asset: str | None
    horizon_minutes: int | None
    window_start: int | None
    window_end: int | None

    @property
    def is_short_crypto(self) -> bool:
        return (
            self.crypto_asset in {"BTC", "ETH"}
            and self.horizon_minutes in {5, 15}
            and self.outcome.lower() in {"up", "down"}
        )


def _decimal(value: Any) -> D:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return D("0")


def parse_short_crypto_slug(slug: str) -> tuple[str | None, int | None, int | None, int | None]:
    match = SHORT_CRYPTO_SLUG.search(str(slug or ""))
    if not match:
        return None, None, None, None
    raw_asset = match.group("asset").lower()
    asset = "BTC" if raw_asset in {"btc", "bitcoin"} else "ETH"
    horizon = int(match.group("horizon")[:-1])
    start = int(match.group("start"))
    return asset, horizon, start, start + horizon * 60


def normalize_trade(raw: dict[str, Any]) -> WalletTrade:
    slug = str(raw.get("slug") or raw.get("eventSlug") or "")
    asset, horizon, start, end = parse_short_crypto_slug(slug)
    return WalletTrade(
        timestamp=int(raw.get("timestamp") or 0),
        condition_id=str(raw.get("conditionId") or ""),
        asset_id=str(raw.get("asset") or ""),
        side=str(raw.get("side") or "").upper(),
        outcome=str(raw.get("outcome") or ""),
        size=_decimal(raw.get("size")),
        price=_decimal(raw.get("price")),
        title=str(raw.get("title") or ""),
        slug=slug,
        event_slug=str(raw.get("eventSlug") or ""),
        transaction_hash=str(raw.get("transactionHash") or ""),
        crypto_asset=asset,
        horizon_minutes=horizon,
        window_start=start,
        window_end=end,
    )


async def fetch_activity(
    wallet: str,
    *,
    start: int | None = None,
    end: int | None = None,
    page_size: int = 500,
    max_rows: int = 10000,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch public wallet TRADE activity oldest-first.

    The Data API supports 500 rows/page and offsets up to 10,000. We explicitly
    request TRADE activity because it supports start/end timestamps and avoids
    the /trades endpoint's default takerOnly behavior.
    """
    if not WALLET_RE.fullmatch(wallet):
        raise ValueError("wallet must be a 0x-prefixed 40-hex address")
    page_size = max(1, min(int(page_size), 500))
    max_rows = max(1, min(int(max_rows), 10000))

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    out: list[dict[str, Any]] = []
    offset = 0
    try:
        while len(out) < max_rows:
            limit = min(page_size, max_rows - len(out))
            params: dict[str, Any] = {
                "user": wallet,
                "type": "TRADE",
                "sortBy": "TIMESTAMP",
                "sortDirection": "ASC",
                "limit": limit,
                "offset": offset,
            }
            if start is not None:
                params["start"] = int(start)
            if end is not None:
                params["end"] = int(end)

            response = await http.get(f"{DATA_API}/activity", params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("unexpected Polymarket activity payload")
            rows = [row for row in payload if isinstance(row, dict)]
            out.extend(rows)
            if len(rows) < limit:
                break
            offset += len(rows)
            if offset >= 10000:
                break
        return out[:max_rows]
    finally:
        if own_client:
            await http.aclose()


def _trade_key(t: WalletTrade) -> tuple[Any, ...]:
    return (
        t.timestamp,
        t.condition_id,
        t.asset_id,
        t.side,
        t.outcome,
        str(t.size),
        str(t.price),
        t.transaction_hash,
    )


def dedupe_trades(trades: Iterable[WalletTrade]) -> list[WalletTrade]:
    seen: set[tuple[Any, ...]] = set()
    out: list[WalletTrade] = []
    for trade in sorted(trades, key=lambda t: (t.timestamp, t.condition_id, t.asset_id)):
        key = _trade_key(trade)
        if key in seen:
            continue
        seen.add(key)
        out.append(trade)
    return out


def replay_market(trades: Iterable[WalletTrade]) -> dict[str, Any]:
    rows = dedupe_trades(trades)
    if not rows:
        raise ValueError("no trades")
    condition_ids = {t.condition_id for t in rows}
    if len(condition_ids) != 1:
        raise ValueError("replay_market requires exactly one condition_id")

    bought_qty = {"up": D("0"), "down": D("0")}
    bought_cost = {"up": D("0"), "down": D("0")}
    sold_qty = {"up": D("0"), "down": D("0")}
    sold_proceeds = {"up": D("0"), "down": D("0")}
    sequence: list[dict[str, Any]] = []
    last_buy_outcome: str | None = None
    buy_side_switches = 0

    for i, trade in enumerate(rows, start=1):
        outcome = trade.outcome.lower()
        if outcome not in {"up", "down"}:
            continue

        if trade.side == "BUY":
            bought_qty[outcome] += trade.size
            bought_cost[outcome] += trade.size * trade.price
            if last_buy_outcome is not None and outcome != last_buy_outcome:
                buy_side_switches += 1
            last_buy_outcome = outcome
        elif trade.side == "SELL":
            sold_qty[outcome] += trade.size
            sold_proceeds[outcome] += trade.size * trade.price

        avg_up = bought_cost["up"] / bought_qty["up"] if bought_qty["up"] else None
        avg_down = bought_cost["down"] / bought_qty["down"] if bought_qty["down"] else None
        paired_qty = min(bought_qty["up"], bought_qty["down"])
        pair_avg_sum = (avg_up + avg_down) if avg_up is not None and avg_down is not None else None
        gross_edge_per_pair = (D("1") - pair_avg_sum) if pair_avg_sum is not None else None
        gross_locked_edge = (
            paired_qty * gross_edge_per_pair
            if gross_edge_per_pair is not None and gross_edge_per_pair > 0
            else D("0")
        )
        directional_outcome: str | None = None
        directional_qty = D("0")
        if bought_qty["up"] > bought_qty["down"]:
            directional_outcome = "Up"
            directional_qty = bought_qty["up"] - bought_qty["down"]
        elif bought_qty["down"] > bought_qty["up"]:
            directional_outcome = "Down"
            directional_qty = bought_qty["down"] - bought_qty["up"]

        sequence.append({
            "n": i,
            "timestamp": trade.timestamp,
            "seconds_to_expiry": (
                trade.window_end - trade.timestamp if trade.window_end is not None else None
            ),
            "side": trade.side,
            "outcome": trade.outcome,
            "price": trade.price,
            "size": trade.size,
            "buy_qty_up": bought_qty["up"],
            "buy_qty_down": bought_qty["down"],
            "avg_up": avg_up,
            "avg_down": avg_down,
            "paired_qty": paired_qty,
            "pair_avg_sum": pair_avg_sum,
            "gross_edge_per_pair": gross_edge_per_pair,
            "gross_locked_edge_if_pair_sum_below_1": gross_locked_edge,
            "directional_outcome": directional_outcome,
            "directional_qty": directional_qty,
        })

    buy_trades = [t for t in rows if t.side == "BUY" and t.outcome.lower() in {"up", "down"}]
    buy_volume = sum((t.size * t.price for t in buy_trades), D("0"))
    favored_buy_count = sum(1 for t in buy_trades if t.price >= D("0.55"))
    favored_buy_volume = sum((t.size * t.price for t in buy_trades if t.price >= D("0.55")), D("0"))
    high_conviction_buy_count = sum(1 for t in buy_trades if t.price >= D("0.70"))
    high_conviction_buy_volume = sum((t.size * t.price for t in buy_trades if t.price >= D("0.70")), D("0"))

    avg_up = bought_cost["up"] / bought_qty["up"] if bought_qty["up"] else None
    avg_down = bought_cost["down"] / bought_qty["down"] if bought_qty["down"] else None
    pair_avg_sum = (avg_up + avg_down) if avg_up is not None and avg_down is not None else None
    paired_qty = min(bought_qty["up"], bought_qty["down"])
    gross_edge_per_pair = (D("1") - pair_avg_sum) if pair_avg_sum is not None else None

    first = rows[0]
    return {
        "condition_id": first.condition_id,
        "title": first.title,
        "slug": first.slug,
        "crypto_asset": first.crypto_asset,
        "horizon_minutes": first.horizon_minutes,
        "window_start": first.window_start,
        "window_end": first.window_end,
        "trade_count": len(rows),
        "buy_trade_count": len(buy_trades),
        "buy_side_switches": buy_side_switches,
        "bought_qty_up": bought_qty["up"],
        "bought_qty_down": bought_qty["down"],
        "avg_up": avg_up,
        "avg_down": avg_down,
        "paired_qty": paired_qty,
        "pair_avg_sum": pair_avg_sum,
        "gross_edge_per_pair": gross_edge_per_pair,
        "gross_complete_set_edge": (
            paired_qty * gross_edge_per_pair
            if gross_edge_per_pair is not None and gross_edge_per_pair > 0
            else D("0")
        ),
        "favored_buy_count_ge_55c": favored_buy_count,
        "favored_buy_share": D(favored_buy_count) / D(len(buy_trades)) if buy_trades else D("0"),
        "favored_buy_volume_share": favored_buy_volume / buy_volume if buy_volume else D("0"),
        "high_conviction_buy_count_ge_70c": high_conviction_buy_count,
        "high_conviction_buy_volume_share": high_conviction_buy_volume / buy_volume if buy_volume else D("0"),
        "sold_qty_up": sold_qty["up"],
        "sold_qty_down": sold_qty["down"],
        "sold_proceeds_up": sold_proceeds["up"],
        "sold_proceeds_down": sold_proceeds["down"],
        "sequence": sequence,
    }


def analyze_wallet_trades(trades: Iterable[WalletTrade]) -> dict[str, Any]:
    short = [t for t in dedupe_trades(trades) if t.is_short_crypto]
    grouped: dict[str, list[WalletTrade]] = defaultdict(list)
    for trade in short:
        grouped[trade.condition_id].append(trade)

    markets = [replay_market(rows) for _, rows in sorted(grouped.items(), key=lambda kv: min(t.timestamp for t in kv[1]))]
    buy_trades = [t for t in short if t.side == "BUY"]
    total_buy_notional = sum((t.size * t.price for t in buy_trades), D("0"))
    threshold_counts = {}
    threshold_volume = {}
    for threshold in (D("0.50"), D("0.55"), D("0.60"), D("0.70"), D("0.80"), D("0.90")):
        selected = [t for t in buy_trades if t.price >= threshold]
        threshold_counts[str(threshold)] = len(selected)
        notional = sum((t.size * t.price for t in selected), D("0"))
        threshold_volume[str(threshold)] = notional / total_buy_notional if total_buy_notional else D("0")

    markets_with_both = [m for m in markets if m["bought_qty_up"] > 0 and m["bought_qty_down"] > 0]
    sub_one = [m for m in markets_with_both if m["pair_avg_sum"] is not None and m["pair_avg_sum"] < D("1")]
    switched = [m for m in markets if m["buy_side_switches"] > 0]

    return {
        "short_crypto_trade_count": len(short),
        "market_count": len(markets),
        "buy_trade_count": len(buy_trades),
        "buy_notional": total_buy_notional,
        "markets_with_both_sides": len(markets_with_both),
        "markets_with_side_switches": len(switched),
        "markets_with_sub_1_weighted_pair_cost": len(sub_one),
        "buy_count_at_or_above_price": threshold_counts,
        "buy_notional_share_at_or_above_price": threshold_volume,
        "markets": markets,
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, D):
        return str(value)
    if isinstance(value, WalletTrade):
        d = asdict(value)
        return {k: _json_default(v) if isinstance(v, D) else v for k, v in d.items()}
    raise TypeError(type(value).__name__)


async def _amain() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only replay of a public Polymarket wallet's 5m/15m BTC/ETH trades"
    )
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--start", type=int, default=None, help="Unix timestamp inclusive")
    parser.add_argument("--end", type=int, default=None, help="Unix timestamp inclusive")
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    raw = await fetch_activity(
        args.wallet,
        start=args.start,
        end=args.end,
        max_rows=args.max_rows,
    )
    normalized = [normalize_trade(row) for row in raw]
    analysis = analyze_wallet_trades(normalized)
    if args.summary_only:
        analysis = {k: v for k, v in analysis.items() if k != "markets"}
    print(json.dumps(analysis, default=_json_default, sort_keys=True))


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
