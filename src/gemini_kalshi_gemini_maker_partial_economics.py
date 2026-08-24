"""Read-only fractional-fill economics audit for Gemini-maker -> Kalshi-hedge 15m crypto.

A q=1 resting Gemini maker order may fill in 0.01-contract increments.  The q=1
maker census is therefore insufficient for live admission because Gemini rounds
prediction-market fees up to the next cent and the Kalshi hedge also has fee/tick
cost.  This module observes current dominance-authorized ETH/SOL/XRP pairs, finds
Gemini-maker candidates, and evaluates every possible cumulative fill size from
0.01 through 1.00 using a one-Kalshi-tick adverse hedge cushion.

READ ONLY: no private clients, no order placement, no realized-P&L claims.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import httpx

from src import gemini_kalshi_dual_maker_census as census
from src import gemini_kalshi_maker_dominance_guard as dominance
from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src.gemini_kalshi_aligned_watch import _discover_pairs
from src.gemini_kalshi_current_refdata_probe import _kalshi_market
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

D = Decimal
ONE = D("1")
TARGET_PER_CONTRACT = D("0.03")
ASSETS = ("ETH", "SOL", "XRP")
MAX_SKEW_MS = 50
MAX_AGE_MS = 250
logger = logging.getLogger("kalshi_arb.gemini_kalshi_gemini_maker_partial_economics")


def _tick_for_yes_price(market: Mapping[str, Any], yes_price: D) -> D:
    """Return a conservative valid Kalshi tick from current market price_ranges."""
    matches: list[D] = []
    for row in market.get("price_ranges") or []:
        if not isinstance(row, Mapping):
            continue
        try:
            start, end, step = D(str(row["start"])), D(str(row["end"])), D(str(row["step"]))
        except Exception:
            continue
        if step <= 0:
            continue
        if start <= yes_price <= end:
            matches.append(step)
    if not matches:
        raise ValueError(f"no Kalshi price-range tick for yes_price={yes_price}")
    # At a shared range boundary use the larger step. This is intentionally
    # conservative for hedge-cost admission.
    return max(matches)


def _hedge_tick(market: Mapping[str, Any], hedge_outcome: str, hedge_price: D) -> D:
    yes_reference = hedge_price if hedge_outcome == "YES" else ONE - hedge_price
    return _tick_for_yes_price(market, yes_reference)


def partial_economics(candidate: Mapping[str, Any], fee_meta: Any, market: Mapping[str, Any]) -> dict[str, Any]:
    maker_price = D(str(candidate["maker_quote"]))
    hedge_price = D(str(candidate["hedge_price"]))
    hedge_outcome = str(candidate["hedge_outcome"])
    tick = _hedge_tick(market, hedge_outcome, hedge_price)
    hedge_cushioned = min(D("0.999"), hedge_price + tick)
    kalshi_fee = fee_meta.taker_fee_fn()

    rows: list[dict[str, Any]] = []
    first_nonnegative: D | None = None
    first_target: D | None = None
    safe_nonnegative = 0
    safe_target = 0
    for i in range(1, 101):
        q = D(i) / D("100")
        maker_fee = gemini_predictions_maker_fee(maker_price, q)
        hedge_fee = kalshi_fee(hedge_cushioned, q)
        raw_cost = q * (maker_price + hedge_cushioned)
        total_cost = raw_cost + maker_fee + hedge_fee
        locked = q - total_cost
        per_contract = locked / q
        nonnegative = locked >= 0
        target = per_contract >= TARGET_PER_CONTRACT
        if nonnegative:
            safe_nonnegative += 1
            if first_nonnegative is None:
                first_nonnegative = q
        if target:
            safe_target += 1
            if first_target is None:
                first_target = q
        rows.append({
            "quantity": str(q),
            "maker_fee": str(maker_fee),
            "hedge_fee": str(hedge_fee),
            "raw_cost": str(raw_cost),
            "locked_profit": str(locked),
            "locked_per_contract": str(per_contract),
            "nonnegative": nonnegative,
            "target_3c_per_contract": target,
        })

    by_q = {r["quantity"]: r for r in rows}
    return {
        "maker_price": str(maker_price),
        "maker_outcome": candidate.get("maker_outcome"),
        "hedge_outcome": hedge_outcome,
        "hedge_observed_price": str(hedge_price),
        "kalshi_adverse_tick": str(tick),
        "hedge_cushioned_price": str(hedge_cushioned),
        "smallest_nonnegative_quantity": None if first_nonnegative is None else str(first_nonnegative),
        "smallest_3c_per_contract_quantity": None if first_target is None else str(first_target),
        "nonnegative_quantity_count": safe_nonnegative,
        "target_quantity_count": safe_target,
        "q_0_01": by_q["0.01"],
        "q_0_25": by_q["0.25"],
        "q_0_50": by_q["0.5"],
        "q_0_75": by_q["0.75"],
        "q_1_00": by_q["1"],
        "all_quantity_rows": rows,
    }


async def _audit_pair(pair: Any, fee_meta: Any, market: Mapping[str, Any], seconds: float) -> dict[str, Any]:
    geometry = await dominance.fetch_pair_geometry(pair, pair.asset)
    if not geometry.get("authorized"):
        return {"asset": pair.asset, "geometry": geometry, "candidate_observed": False, "order_entry": False}
    allowed = str(geometry["dominance_safe_direction"])
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    stats = base.MakerStats(asset=pair.asset)
    tasks = [
        asyncio.create_task(base._gemini_stream(pair.gemini_symbol, queue, stats)),
        asyncio.create_task(rest_guard.kalshi_rest_book_stream(pair.kalshi_ticker, queue, stats)),
    ]
    g = k = None
    best: dict[str, Any] | None = None
    fresh = 0
    deadline = min(time.monotonic() + seconds, time.monotonic() + max(0.0, pair.window_end.timestamp() - time.time()))
    try:
        while time.monotonic() < deadline:
            try:
                kind, _, top = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if kind == "gemini":
                g = top
            elif kind == "kalshi":
                k = top
            if g is None or k is None:
                continue
            now_ms = int(time.time() * 1000)
            skew = abs(int(g.ts_ms) - int(k.ts_ms))
            age = max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms))
            if skew > MAX_SKEW_MS or age > MAX_AGE_MS or age < 0:
                continue
            fresh += 1
            candidate = census._gemini_maker_candidate(direction=allowed, gemini=g, kalshi=k, fee_meta=fee_meta)
            if candidate is None:
                continue
            audit = partial_economics(candidate, fee_meta, market)
            score = D(str(audit["q_1_00"]["locked_profit"]))
            if best is None or score > D(str(best["partial_economics"]["q_1_00"]["locked_profit"])):
                best = {
                    "candidate": {k: str(v) if isinstance(v, D) else v for k, v in candidate.items()},
                    "partial_economics": audit,
                    "quote_skew_ms": skew,
                    "max_book_age_ms": age,
                }
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "asset": pair.asset,
        "window_end_utc": pair.window_end.isoformat(),
        "gemini_symbol": pair.gemini_symbol,
        "kalshi_ticker": pair.kalshi_ticker,
        "dominance_safe_direction": allowed,
        "threshold_gap_bps": geometry.get("threshold_gap_bps"),
        "fresh_evaluations": fresh,
        "candidate_observed": best is not None,
        "best": best,
        "classification": dominance.CLASSIFICATION,
        "guaranteed_arbitrage": False,
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }


async def run(seconds: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        pairs = await _discover_pairs(http, 30.0)
        now = time.time()
        selected: dict[str, Any] = {}
        for asset in ASSETS:
            rows = sorted((p for p in pairs if p.asset == asset and p.window_end.timestamp() > now), key=lambda p: p.window_end)
            if rows:
                selected[asset] = rows[0]
        fees = {a: await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[a]) for a in selected}
        markets = {a: await _kalshi_market(http, p.kalshi_ticker) for a, p in selected.items()}

    rows = await asyncio.gather(*(
        _audit_pair(pair=p, fee_meta=fees[a], market=markets[a], seconds=seconds)
        for a, p in selected.items()
    ))
    out = {
        "seconds": seconds,
        "assets": rows,
        "target_per_contract": str(TARGET_PER_CONTRACT),
        "partial_grid": "0.01..1.00 by 0.01",
        "hedge_cushion": "one current Kalshi yes-space price-range tick",
        "fee_policy": "Gemini maker ceil-cent + live Kalshi mapped taker fee; rebates excluded",
        "read_only": True,
        "order_entry": False,
        "realized": False,
    }
    logger.warning("GEMINI_MAKER_PARTIAL_ECONOMICS %s", json.dumps(out, separators=(",", ":"), default=str))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds > 300:
        raise SystemExit("seconds must be in (0,300]")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds))


if __name__ == "__main__":
    main()
