"""Read-only replay/chronology analyzer for a public Polymarket wallet.

This module never authenticates and never submits, modifies, or cancels orders.
It uses Polymarket's public Data API to fetch trades and reconstructs short-
duration BTC/ETH Up/Down inventory through each market window.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import httpx

DATA_API = "https://data-api.polymarket.com"
DEFAULT_WALLET = "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
WINDOW_RE = re.compile(r"^(btc|eth)-updown-(5m|15m)-(\d+)$", re.IGNORECASE)
D = Decimal


@dataclass(frozen=True)
class Trade:
    timestamp: int
    condition_id: str
    asset_id: str
    side: str
    outcome: str
    price: Decimal
    size: Decimal
    title: str
    slug: str
    event_slug: str
    tx_hash: str
    coin: str
    horizon: str
    window_start_ts: int
    expiry_ts: int


@dataclass(frozen=True)
class Step:
    timestamp: int
    iso_utc: str
    seconds_to_expiry: int
    side: str
    outcome: str
    price: str
    size: str
    up_qty: str
    down_qty: str
    up_vwap: str | None
    down_vwap: str | None
    matched_qty: str
    matched_pair_cost: str | None
    locked_gross_per_pair: str | None
    residual_side: str | None
    residual_qty: str


def _dec(value: Any) -> Decimal:
    try:
        return D(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def parse_window_slug(slug: str, event_slug: str = "") -> tuple[str, str, int, int] | None:
    for candidate in (slug, event_slug):
        match = WINDOW_RE.match((candidate or "").strip())
        if not match:
            continue
        coin, horizon, start_text = match.groups()
        start = int(start_text)
        seconds = 300 if horizon.lower() == "5m" else 900
        return coin.lower(), horizon.lower(), start, start + seconds
    return None


def normalize_trade(row: dict[str, Any]) -> Trade | None:
    parsed = parse_window_slug(str(row.get("slug") or ""), str(row.get("eventSlug") or ""))
    if parsed is None:
        return None
    coin, horizon, start, expiry = parsed
    outcome = str(row.get("outcome") or "").strip().lower()
    side = str(row.get("side") or "").strip().upper()
    if outcome not in {"up", "down"} or side not in {"BUY", "SELL"}:
        return None
    return Trade(
        timestamp=int(row.get("timestamp") or 0),
        condition_id=str(row.get("conditionId") or ""),
        asset_id=str(row.get("asset") or ""),
        side=side,
        outcome=outcome,
        price=_dec(row.get("price")),
        size=_dec(row.get("size")),
        title=str(row.get("title") or ""),
        slug=str(row.get("slug") or ""),
        event_slug=str(row.get("eventSlug") or ""),
        tx_hash=str(row.get("transactionHash") or ""),
        coin=coin,
        horizon=horizon,
        window_start_ts=start,
        expiry_ts=expiry,
    )


def fetch_wallet_trades(
    wallet: str,
    *,
    start: int = 0,
    end: int | None = None,
    limit: int = 10000,
    client: httpx.Client | None = None,
) -> list[Trade]:
    # Polymarket's public /trades endpoint defaults takerOnly=true. Explicitly
    # disable that default so a wallet replay includes maker and taker fills.
    params: dict[str, Any] = {
        "user": wallet,
        "limit": min(max(limit, 1), 10000),
        "start": max(start, 0),
        "takerOnly": False,
    }
    if end is not None:
        params["end"] = max(end, 0)
    owns_client = client is None
    http = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        response = http.get(f"{DATA_API}/trades", params=params)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            http.close()
    if not isinstance(payload, list):
        raise RuntimeError("unexpected Polymarket trades payload")
    out: list[Trade] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        trade = normalize_trade(row)
        if trade is not None and trade.coin in {"btc", "eth"} and trade.horizon in {"5m", "15m"}:
            out.append(trade)
    return sorted(out, key=lambda t: (t.timestamp, t.condition_id, t.tx_hash, t.outcome))


def _fmt(value: Decimal | None) -> str | None:
    if value is None:
        return None
    q = value.quantize(D("0.0001"))
    return format(q.normalize(), "f")


def reconstruct(trades: Iterable[Trade]) -> dict[str, Any]:
    rows = sorted(trades, key=lambda t: (t.timestamp, t.tx_hash, t.outcome))
    if not rows:
        raise ValueError("no trades")

    first = rows[0]
    up_qty = D("0")
    down_qty = D("0")
    up_cost = D("0")
    down_cost = D("0")
    steps: list[Step] = []
    side_switches = 0
    last_outcome: str | None = None
    buy_count = 0
    sell_count = 0

    for trade in rows:
        sign = D("1") if trade.side == "BUY" else D("-1")
        if trade.side == "BUY":
            buy_count += 1
            if last_outcome is not None and last_outcome != trade.outcome:
                side_switches += 1
            last_outcome = trade.outcome
        else:
            sell_count += 1

        qty_delta = sign * trade.size
        cost_delta = sign * trade.size * trade.price
        if trade.outcome == "up":
            up_qty += qty_delta
            up_cost += cost_delta
        else:
            down_qty += qty_delta
            down_cost += cost_delta

        # Public trade replay is most useful for the observed BUY-heavy strategy.
        # Clamp negative inventory-derived metrics to zero rather than pretending
        # a short/sell sequence is a complete-set position.
        uq = max(up_qty, D("0"))
        dq = max(down_qty, D("0"))
        up_vwap = (up_cost / up_qty) if up_qty > 0 else None
        down_vwap = (down_cost / down_qty) if down_qty > 0 else None
        matched = min(uq, dq)
        pair_cost = (up_vwap + down_vwap) if matched > 0 and up_vwap is not None and down_vwap is not None else None
        locked = D("1") - pair_cost if pair_cost is not None else None
        residual_side = None
        residual_qty = D("0")
        if uq > dq:
            residual_side, residual_qty = "up", uq - dq
        elif dq > uq:
            residual_side, residual_qty = "down", dq - uq

        steps.append(
            Step(
                timestamp=trade.timestamp,
                iso_utc=datetime.fromtimestamp(trade.timestamp, timezone.utc).isoformat(),
                seconds_to_expiry=max(first.expiry_ts - trade.timestamp, 0),
                side=trade.side,
                outcome=trade.outcome,
                price=_fmt(trade.price) or "0",
                size=_fmt(trade.size) or "0",
                up_qty=_fmt(uq) or "0",
                down_qty=_fmt(dq) or "0",
                up_vwap=_fmt(up_vwap),
                down_vwap=_fmt(down_vwap),
                matched_qty=_fmt(matched) or "0",
                matched_pair_cost=_fmt(pair_cost),
                locked_gross_per_pair=_fmt(locked),
                residual_side=residual_side,
                residual_qty=_fmt(residual_qty) or "0",
            )
        )

    final = steps[-1]
    sub_dollar_steps = sum(
        1 for step in steps if step.matched_pair_cost is not None and D(step.matched_pair_cost) < D("1")
    )
    buy_prices = [t.price for t in rows if t.side == "BUY"]
    high_prob_buys = sum(1 for p in buy_prices if p >= D("0.60"))

    return {
        "condition_id": first.condition_id,
        "coin": first.coin,
        "horizon": first.horizon,
        "title": first.title,
        "slug": first.slug or first.event_slug,
        "window_start_ts": first.window_start_ts,
        "expiry_ts": first.expiry_ts,
        "trade_count": len(rows),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "side_switches_between_buys": side_switches,
        "high_probability_buy_count_ge_60c": high_prob_buys,
        "sub_dollar_matched_steps": sub_dollar_steps,
        "final": asdict(final),
        "steps": [asdict(step) for step in steps],
    }


def group_replays(trades: Iterable[Trade]) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[trade.condition_id or (trade.slug or trade.event_slug)].append(trade)
    replays = [reconstruct(group) for group in groups.values() if group]
    return sorted(replays, key=lambda item: (item["window_start_ts"], item["coin"], item["horizon"]))


def hypothesis_summary(replays: list[dict[str, Any]]) -> dict[str, Any]:
    if not replays:
        return {
            "markets": 0,
            "classification": "NO_DATA",
        }
    repeated = sum(1 for r in replays if r["buy_count"] >= 3)
    switched = sum(1 for r in replays if r["side_switches_between_buys"] >= 1)
    locked = sum(1 for r in replays if r["sub_dollar_matched_steps"] >= 1)
    high_prob = sum(1 for r in replays if r["high_probability_buy_count_ge_60c"] >= 1)
    return {
        "markets": len(replays),
        "markets_with_3plus_buys": repeated,
        "markets_with_buy_side_switch": switched,
        "markets_with_sub_dollar_matched_inventory": locked,
        "markets_with_buy_at_or_above_60c": high_prob,
        "classification": "OBSERVATIONAL_ONLY",
        "note": (
            "Side switches and sub-$1 matched inventory support the scaling/reversal hypothesis, "
            "but public fills alone do not reveal the trader's private fair-value signal or maker/taker intent."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only l5 Polymarket short-crypto replay")
    parser.add_argument("--wallet", default=DEFAULT_WALLET)
    parser.add_argument("--start", type=int, default=0, help="epoch seconds")
    parser.add_argument("--end", type=int, default=None, help="epoch seconds")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--latest-markets", type=int, default=20)
    args = parser.parse_args()

    trades = fetch_wallet_trades(args.wallet, start=args.start, end=args.end, limit=args.limit)
    replays = group_replays(trades)
    if args.latest_markets > 0:
        replays = replays[-args.latest_markets :]
    print(json.dumps({"wallet": args.wallet, "summary": hypothesis_summary(replays), "markets": replays}, indent=2))


if __name__ == "__main__":
    main()
