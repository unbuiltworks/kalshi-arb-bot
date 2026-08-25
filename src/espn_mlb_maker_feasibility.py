"""Read-only MLB maker feasibility study: ESPN archived DK close vs Kalshi touch.

This is deliberately *not* an execution backtest. ESPN's archived close has no
point-in-time update timestamp, so using it 60 seconds pregame is an optimistic
valuation upper bound rather than a causal signal test.

Precommitted methodology (before maker results):
- same strict MLB physical matching used by the taker study;
- valuation: ESPN archived DraftKings closing moneyline, multiplicatively devigged;
- quote time: 60 seconds before scheduled start;
- maker quote: existing best Kalshi bid for the target outcome at that time;
- fees: conservatively charge the general Kalshi maker-fee formula on every fill
  (ceil cent of 0.0175 * C * P * (1-P)), even where a market might have no maker fee;
- modeled edge buckets: >=1c, >=2c, >=3c, >=5c, >=8c;
- possible fill: a subsequent pregame trade touches our price;
- strict fill proxy: a subsequent pregame trade moves THROUGH our price, which
  avoids assuming priority for equal-price prints;
- settlement P&L is shown only for strict-fill proxies, never as executed/realized P&L;
- mirrored Kalshi contracts are deduped to the best maker edge per physical team.

No order transport, account endpoints, or production service imports are used.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

import src.espn_mlb_closing_ev_backtest as ev
import src.kalshi_mlb_sharp_ev_backtest as bt
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern

D = Decimal
PREGAME_BUFFER_S = 60
LOOKBACK_S = 4 * 60
MAKER_RATE = D("0.0175")
EDGE_BUCKETS = (D("0.01"), D("0.02"), D("0.03"), D("0.05"), D("0.08"))


def _dollars(block: Mapping[str, Any], field: str) -> Decimal | None:
    raw = block.get(f"{field}_dollars") if f"{field}_dollars" in block else block.get(field)
    try:
        return D(str(raw))
    except Exception:
        return None


def _block(point: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = point.get(key)
    return value if isinstance(value, Mapping) else {}


def _volume(point: Mapping[str, Any]) -> Decimal:
    raw = point.get("volume_fp") if "volume_fp" in point else point.get("volume", 0)
    try:
        return D(str(raw))
    except Exception:
        return D("0")


def _maker_touch(point: Mapping[str, Any], side: str) -> Decimal | None:
    if side == "YES":
        return _dollars(_block(point, "yes_bid"), "close")
    yes_ask = _dollars(_block(point, "yes_ask"), "close")
    if yes_ask is None:
        return None
    return D("1") - yes_ask


def _taker_ask(point: Mapping[str, Any], side: str) -> Decimal | None:
    if side == "YES":
        return _dollars(_block(point, "yes_ask"), "close")
    yes_bid = _dollars(_block(point, "yes_bid"), "close")
    if yes_bid is None:
        return None
    return D("1") - yes_bid


def maker_fee(price: Decimal, contracts: int) -> Decimal:
    raw = MAKER_RATE * D(contracts) * price * (D("1") - price)
    return (raw * D("100")).to_integral_value(rounding=ROUND_CEILING) / D("100")


def _trade_range(point: Mapping[str, Any]) -> tuple[Decimal | None, Decimal | None]:
    price = _block(point, "price")
    low = _dollars(price, "low")
    high = _dollars(price, "high")
    if low is None:
        low = _dollars(price, "min")
    if high is None:
        high = _dollars(price, "max")
    return low, high


def _fill_proxy(points: Sequence[Mapping[str, Any]], *, cutoff_ts: int, start_ts: int,
                side: str, maker_price: Decimal) -> tuple[bool, bool, Decimal]:
    touch = False
    through = False
    traded_volume = D("0")
    yes_threshold = D("1") - maker_price
    for point in points:
        end_ts = int(point.get("end_period_ts") or 0)
        if not (cutoff_ts < end_ts <= start_ts):
            continue
        volume = _volume(point)
        if volume <= 0:
            continue
        low, high = _trade_range(point)
        traded_volume += volume
        if side == "YES" and low is not None:
            touch = touch or low <= maker_price
            through = through or low < maker_price
        elif side == "NO" and high is not None:
            touch = touch or high >= yes_threshold
            through = through or high > yes_threshold
    return touch, through, traded_volume


@dataclass(frozen=True)
class MakerRow:
    event_key: tuple[str, str]
    start_ts: int
    ticker: str
    team: str
    side: str
    sharp_p: Decimal
    maker_price: Decimal
    taker_ask: Decimal
    spread_capture: Decimal
    contracts: int
    maker_fee_total: Decimal
    maker_fee_pc: Decimal
    maker_edge: Decimal
    won: bool
    pnl_pc_if_filled: Decimal
    order_pnl_if_filled: Decimal
    order_stake: Decimal
    touch_fill_possible: bool
    strict_trade_through: bool
    subsequent_trade_volume: Decimal

    def json(self) -> dict[str, Any]:
        out = asdict(self)
        out["event_key"] = list(self.event_key)
        for key, value in list(out.items()):
            if isinstance(value, Decimal):
                out[key] = str(value)
        out["start_time"] = datetime.fromtimestamp(self.start_ts, tz=timezone.utc).isoformat()
        return out


async def _row(http: httpx.AsyncClient, side: bt.KalshiSide, sharp: bt.SharpEvent,
               target_stake: Decimal) -> MakerRow | None:
    if abs(side.start_ts - sharp.start_ts) > bt.MAX_START_SKEW_S:
        return None
    cutoff_ts = sharp.start_ts - PREGAME_BUFFER_S
    points = await bt._ticker_candles(http, side, sharp.start_ts, lookback_s=LOOKBACK_S)
    prior = [p for p in points if int(p.get("end_period_ts") or 0) <= cutoff_ts]
    if not prior:
        return None
    point = max(prior, key=lambda p: int(p.get("end_period_ts") or 0))
    if cutoff_ts - int(point.get("end_period_ts") or 0) > 120:
        return None
    maker_price = _maker_touch(point, side.side)
    taker_ask = _taker_ask(point, side.side)
    sharp_p = sharp.fair_by_team.get(side.team)
    if maker_price is None or taker_ask is None or sharp_p is None:
        return None
    if not (D("0") < maker_price < D("1")) or taker_ask < maker_price:
        return None
    contracts = bt.representative_contracts(maker_price, target_stake)
    fee = maker_fee(maker_price, contracts)
    fee_pc = fee / D(contracts)
    edge = sharp_p - maker_price - fee_pc
    payout = D("1") if side.won else D("0")
    pnl_pc = payout - maker_price - fee_pc
    touch, through, volume = _fill_proxy(points, cutoff_ts=cutoff_ts, start_ts=sharp.start_ts,
                                         side=side.side, maker_price=maker_price)
    return MakerRow(
        event_key=side.event_key, start_ts=side.start_ts, ticker=side.ticker,
        team=side.team, side=side.side, sharp_p=sharp_p,
        maker_price=maker_price, taker_ask=taker_ask,
        spread_capture=taker_ask - maker_price,
        contracts=contracts, maker_fee_total=fee, maker_fee_pc=fee_pc,
        maker_edge=edge, won=side.won, pnl_pc_if_filled=pnl_pc,
        order_pnl_if_filled=pnl_pc * D(contracts),
        order_stake=maker_price * D(contracts) + fee,
        touch_fill_possible=touch, strict_trade_through=through,
        subsequent_trade_volume=volume,
    )


def _summary(rows: Sequence[MakerRow], threshold: Decimal) -> dict[str, Any]:
    candidates = [r for r in rows if r.maker_edge >= threshold]
    touches = [r for r in candidates if r.touch_fill_possible]
    strict = [r for r in candidates if r.strict_trade_through]
    strict_stake = sum((r.order_stake for r in strict), D("0"))
    strict_pnl = sum((r.order_pnl_if_filled for r in strict), D("0"))
    return {
        "edge": f">={int(threshold * 100)}c",
        "candidates": len(candidates),
        "unique_games": len({(r.event_key, r.start_ts) for r in candidates}),
        "touch_fill_possible": len(touches),
        "touch_rate": str(D(len(touches)) / D(len(candidates))) if candidates else None,
        "strict_trade_through": len(strict),
        "strict_trade_through_rate": str(D(len(strict)) / D(len(candidates))) if candidates else None,
        "mean_maker_edge": str(sum((r.maker_edge for r in candidates), D("0")) / D(len(candidates))) if candidates else None,
        "mean_spread_capture": str(sum((r.spread_capture for r in candidates), D("0")) / D(len(candidates))) if candidates else None,
        "strict_proxy_total_stake": str(strict_stake),
        "strict_proxy_total_pnl": str(strict_pnl),
        "strict_proxy_roi": str(strict_pnl / strict_stake) if strict_stake > 0 else None,
        "warning": "strict proxy is trade-through inference, not an actual historical fill",
    }


async def run(*, start_date: date, end_date: date, target_stake: Decimal,
              max_days: int | None = None) -> dict[str, Any]:
    timeout = httpx.Timeout(30.0, connect=10.0)
    limits = httpx.Limits(max_connections=24, max_keepalive_connections=12)
    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as http:
        archived, recent = await asyncio.gather(
            ev._page_markets_compat(http, historical=True),
            ev._page_markets_compat(http, historical=False),
        )
        by_ticker: dict[str, dict[str, Any]] = {}
        for market in archived + recent:
            ticker = str(market.get("ticker") or "")
            if ticker and (ticker not in by_ticker or market.get("_source_tier") == "live"):
                by_ticker[ticker] = market

        original = bt.normalize_kalshi_mlb
        bt.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern
        try:
            sides = bt._kalshi_sides(by_ticker.values(), start_date, end_date)
        finally:
            bt.normalize_kalshi_mlb = original

        days = sorted({datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() for s in sides})
        if max_days is not None:
            days = days[:max(0, max_days)]
        allowed = set(days)
        sides = [s for s in sides if datetime.fromtimestamp(s.start_ts, tz=timezone.utc).date() in allowed]
        by_event: dict[tuple[str, str], list[bt.KalshiSide]] = defaultdict(list)
        for side in sides:
            by_event[side.event_key].append(side)

        sharp_events, espn_stats = await ev._load_espn_closes(http, days)
        sem = asyncio.Semaphore(4)

        async def bounded(coro):
            async with sem:
                return await coro

        tasks = []
        matched = 0
        for sharp in sharp_events:
            candidates = [s for s in by_event.get(sharp.event_key, [])
                          if abs(s.start_ts - sharp.start_ts) <= bt.MAX_START_SKEW_S]
            if not candidates:
                continue
            matched += 1
            tasks.extend(bounded(_row(http, side, sharp, target_stake)) for side in candidates)
        raw = await asyncio.gather(*tasks) if tasks else []
        rows = [r for r in raw if r is not None]

    best: dict[tuple[tuple[str, str], int, str], MakerRow] = {}
    for row in rows:
        key = (row.event_key, row.start_ts, row.team)
        old = best.get(key)
        if old is None or row.maker_edge > old.maker_edge:
            best[key] = row
    rows = sorted(best.values(), key=lambda r: (r.start_ts, r.event_key, r.team))
    buckets = [_summary(rows, x) for x in EDGE_BUCKETS]
    top = sorted(rows, key=lambda r: r.maker_edge, reverse=True)[:50]
    return {
        "status": "KALSHI_MLB_ESPN_DK_CLOSE_MAKER_FEASIBILITY_READ_ONLY",
        "classification": "HISTORICAL_FEASIBILITY_UPPER_BOUND_NOT_CAUSAL_BACKTEST",
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat(), "game_days": len(days)},
        "kalshi": {"markets_discovered": len(by_ticker), "normalized_sides": len(sides)},
        "espn": espn_stats,
        "matched_physical_games": matched,
        "maker_rows": len(rows),
        "method": {
            "valuation": "ESPN archived DraftKings close (timestamp unavailable)",
            "quote_time_seconds_before_start": PREGAME_BUFFER_S,
            "maker_quote": "existing best outcome bid at quote time",
            "maker_fee": "conservative ceil_cent(0.0175*C*P*(1-P)) charged on every modeled fill",
            "fill_possible": "subsequent pregame trade touches quote; queue ambiguous",
            "strict_fill_proxy": "subsequent pregame trade moves through quote",
            "historical_depth_queue": "unavailable; no queue priority assumed",
        },
        "buckets": buckets,
        "top_rows": [r.json() for r in top],
        "warning": "Feasibility only. Do not count proxy fills/P&L as executed or realized. Live shadow is required before any live maker probe.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2026, 3, 25))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 8, 24))
    parser.add_argument("--target-stake", type=D, default=D("10"))
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/tmp/espn_mlb_maker_feasibility.json"))
    args = parser.parse_args()
    result = asyncio.run(run(start_date=args.start_date, end_date=args.end_date,
                             target_stake=args.target_stake, max_days=args.max_days))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
