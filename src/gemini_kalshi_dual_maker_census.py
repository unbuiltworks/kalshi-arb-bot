"""Read-only dual-venue maker census for Gemini <-> Kalshi 15m crypto.

Extends the prior Kalshi-maker-only census by scoring both ways to express the
same threshold-dominance-safe complementary cover:

* Kalshi maker -> Gemini taker
* Gemini maker -> Kalshi taker

The census is intentionally read-only. It uses current q=1 venue fee schedules,
<=50ms cross-book skew, <=250ms book age, and a >=3c modeled floor. Gemini maker
prices use a 1c shadow grid only; live promotion must verify market-reported tick
and lot metadata before order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_dominance_guard as dominance
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_dual_maker_census")
ASSETS = ("ETH", "SOL", "XRP")
TARGET = D("0.03")
QTY = D("1")
TICK = D("0.01")
MAX_SKEW_MS = 50
MAX_AGE_MS = 250


@dataclass
class DirectionStats:
    evaluations: int = 0
    episodes: int = 0
    active_since_ms: int | None = None
    active_ms: int = 0
    max_profit: Decimal = D("0")
    best_gap_to_ask: Decimal | None = None
    best_quote: Decimal | None = None

    def close(self, now_ms: int) -> None:
        if self.active_since_ms is not None:
            self.active_ms += max(0, now_ms - self.active_since_ms)
            self.active_since_ms = None

    def hit(self, *, now_ms: int, profit: Decimal, gap: Decimal, quote: Decimal) -> None:
        self.evaluations += 1
        self.max_profit = max(self.max_profit, profit)
        if self.best_gap_to_ask is None or gap < self.best_gap_to_ask:
            self.best_gap_to_ask = gap
            self.best_quote = quote
        if self.active_since_ms is None:
            self.active_since_ms = now_ms
            self.episodes += 1


def _maker_book(top: Any, outcome: str) -> tuple[D, D, D]:
    if outcome == "YES":
        return D(top.yes_bid), D(top.yes_bid_size), D(top.yes_ask)
    return D(top.no_bid), D(top.yes_ask_size), D(top.no_ask)


def _taker_book(top: Any, outcome: str) -> tuple[D, D]:
    if outcome == "YES":
        return D(top.yes_ask), D(top.yes_ask_size)
    return D(top.no_ask), D(top.no_ask_size)


def _gemini_maker_candidate(*, direction: str, gemini: Any, kalshi: Any, fee_meta: Any) -> dict[str, Any] | None:
    if direction == "GEMINI_YES_KALSHI_NO":
        maker_outcome, hedge_outcome = "YES", "NO"
    elif direction == "KALSHI_YES_GEMINI_NO":
        maker_outcome, hedge_outcome = "NO", "YES"
    else:
        return None

    best_bid, queue_ahead, best_ask = _maker_book(gemini, maker_outcome)
    hedge_price, hedge_depth = _taker_book(kalshi, hedge_outcome)
    if hedge_depth < QTY or not (D("0") < hedge_price < D("1")):
        return None
    try:
        kalshi_taker = fee_meta.taker_fee_fn()
    except Exception:
        return None

    safe: D | None = None
    safe_profit = D("0")
    for cents in range(1, 100):
        p = D(cents) / D("100")
        maker_fee = gemini_predictions_maker_fee(p, QTY)
        hedge_fee = kalshi_taker(hedge_price, QTY)
        profit = QTY - p - maker_fee - hedge_price - hedge_fee
        if profit >= TARGET:
            safe, safe_profit = p, profit
    if safe is None:
        return None
    quote = max(best_bid, min(best_ask - TICK, safe))
    if quote <= 0 or quote >= best_ask or quote > safe:
        return None
    maker_fee = gemini_predictions_maker_fee(quote, QTY)
    hedge_fee = kalshi_taker(hedge_price, QTY)
    profit = QTY - quote - maker_fee - hedge_price - hedge_fee
    if profit < TARGET:
        return None
    return {
        "maker_venue": "gemini",
        "maker_outcome": maker_outcome,
        "hedge_venue": "kalshi",
        "hedge_outcome": hedge_outcome,
        "maker_quote": quote,
        "maker_best_bid": best_bid,
        "maker_best_ask": best_ask,
        "queue_ahead": queue_ahead if quote == best_bid else D("0"),
        "hedge_price": hedge_price,
        "hedge_depth": hedge_depth,
        "maker_fee": maker_fee,
        "hedge_fee": hedge_fee,
        "locked_profit": profit,
        "gap_to_ask": best_ask - quote,
    }


async def capture(*, pair: Any, fee_meta: Any, seconds: float) -> dict[str, Any]:
    geometry = await dominance.fetch_pair_geometry(pair, pair.asset)
    if not geometry.get("authorized"):
        return {"asset": pair.asset, "geometry": geometry, "order_entry": False}
    allowed = str(geometry["dominance_safe_direction"])
    q: asyncio.Queue = asyncio.Queue(maxsize=50000)
    stats_obj = base.MakerStats(asset=pair.asset)
    tasks = [
        asyncio.create_task(base._gemini_stream(pair.gemini_symbol, q, stats_obj)),
        asyncio.create_task(rest_guard.kalshi_rest_book_stream(pair.kalshi_ticker, q, stats_obj)),
    ]
    g = k = None
    kstats = DirectionStats()
    gstats = DirectionStats()
    paired = fresh = 0
    deadline = min(time.monotonic() + seconds, time.monotonic() + max(0.0, pair.window_end.timestamp() - time.time()))
    try:
        while time.monotonic() < deadline:
            try:
                kind, _, top = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if kind == "gemini":
                g = top
            elif kind == "kalshi":
                k = top
            if g is None or k is None:
                continue
            paired += 1
            now_ms = int(time.time() * 1000)
            skew = abs(int(g.ts_ms) - int(k.ts_ms))
            age = max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms))
            if skew > MAX_SKEW_MS or age > MAX_AGE_MS or age < 0:
                kstats.close(now_ms); gstats.close(now_ms)
                continue
            fresh += 1

            kc = dominance.candidate_for_authorized_direction(
                gemini=g, kalshi=k, fee_meta=fee_meta, now_ms=now_ms, direction=allowed,
            )
            if kc is not None and D(kc.locked_profit) >= TARGET:
                kstats.hit(now_ms=now_ms, profit=D(kc.locked_profit), gap=D(kc.best_ask)-D(kc.maker_price), quote=D(kc.maker_price))
            else:
                kstats.close(now_ms)

            gc = _gemini_maker_candidate(direction=allowed, gemini=g, kalshi=k, fee_meta=fee_meta)
            if gc is not None:
                gstats.hit(now_ms=now_ms, profit=D(gc["locked_profit"]), gap=D(gc["gap_to_ask"]), quote=D(gc["maker_quote"]))
            else:
                gstats.close(now_ms)
        now_ms = int(time.time() * 1000)
        kstats.close(now_ms); gstats.close(now_ms)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def pack(s: DirectionStats) -> dict[str, Any]:
        return {
            "candidate_evals": s.evaluations,
            "episodes": s.episodes,
            "active_ms": s.active_ms,
            "max_locked_profit": str(s.max_profit),
            "best_gap_to_ask": None if s.best_gap_to_ask is None else str(s.best_gap_to_ask),
            "best_quote": None if s.best_quote is None else str(s.best_quote),
        }
    return {
        "asset": pair.asset,
        "window_end_utc": pair.window_end.isoformat(),
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "dominance_safe_direction": allowed,
        "threshold_gap_bps": geometry.get("threshold_gap_bps"),
        "paired_evals": paired,
        "strict_fresh_evals": fresh,
        "kalshi_maker": pack(kstats),
        "gemini_maker": pack(gstats),
        "target_profit": str(TARGET),
        "gemini_grid_assumption": "0.01 shadow-only; live must verify market tick/lot",
        "classification": dominance.CLASSIFICATION,
        "guaranteed_arb": False,
        "realized": False,
        "order_entry": False,
    }


async def run(seconds: float) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        pairs = await _discover_pairs(http, 30.0)
        now = time.time()
        selected: dict[str, Any] = {}
        for asset in ASSETS:
            rows = sorted((p for p in pairs if p.asset == asset and p.window_end.timestamp() > now), key=lambda p: p.window_end)
            if rows:
                selected[asset] = rows[0]
        fees = {a: await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[a]) for a in selected}
    logger.warning("DUAL_MAKER_CENSUS_START %s", json.dumps({"seconds": seconds, "assets": sorted(selected), "target": str(TARGET), "btc_quarantined": True, "order_entry": False}, separators=(",", ":")))
    rows = await asyncio.gather(*(capture(pair=p, fee_meta=fees[a], seconds=seconds) for a,p in selected.items()))
    logger.warning("DUAL_MAKER_CENSUS_SUMMARY %s", json.dumps({
        "seconds": seconds,
        "assets": rows,
        "kalshi_maker_episodes": sum(int((r.get("kalshi_maker") or {}).get("episodes") or 0) for r in rows),
        "gemini_maker_episodes": sum(int((r.get("gemini_maker") or {}).get("episodes") or 0) for r in rows),
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }, separators=(",", ":"), default=str))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("--seconds", type=float, default=120.0)
    args = p.parse_args()
    if args.seconds <= 0 or args.seconds > 600:
        raise SystemExit("seconds must be in (0,600]")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds))


if __name__ == "__main__":
    main()
