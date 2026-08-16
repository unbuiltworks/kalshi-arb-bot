"""One-shot nested Kalshi probe service, dry-run by default.

The live path uses bounded-risk SEED-FIRST execution rather than maker-first:
1. IOC the cheap complementary nested leg (currently constrained to <=2c modeled
   cash risk including its taker fee).
2. Only after an exact 1.00 seed fill, rest the expensive nested maker leg with a
   short expiration.
3. No reactive hedge race exists: the complementary leg is already owned before
   the maker can fill.

Live execution remains triple-gated by --live, ENABLE_KALSHI_NESTED_LIVE_PROBES=YES,
and a one-use arm-token file. Railway's configured start command does NOT pass
--live and therefore remains read-only.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging

from src.kalshi_nested_live_candidate import discover_best_spec
from src.kalshi_nested_live_probe_guard import D, build_v2_payloads, validate_spec
from src.kalshi_nested_seed_first_probe import (
    execute_seed_first,
    modeled_seed_cash_risk,
    validate_seed_first,
)

logger = logging.getLogger("kalshi_arb.kalshi_nested_live_service")


async def run_once(*, series: list[str], target_profit: Decimal, maker_rest_seconds: int, live: bool) -> dict:
    found = await discover_best_spec(
        series=series,
        target_profit=D(target_profit),
        maker_rest_seconds=maker_rest_seconds,
    )
    if found is None:
        report = {
            "status": "NO_ELIGIBLE_CANDIDATE",
            "series": series,
            "target_profit": str(target_profit),
            "actual_order_placed": False,
        }
        logger.info("NESTED_LIVE_DRY_RUN %s", json.dumps(report, separators=(",", ":")))
        return report

    spec, source_row = found
    locked, hedge_fee = validate_spec(spec)
    seed_risk = validate_seed_first(spec)
    maker_payload, seed_payload = build_v2_payloads(spec)
    report = {
        "status": "ARM_REQUIRED" if live else "DRY_RUN_READY",
        "execution_order": "CHEAP_SEED_IOC_THEN_POST_ONLY_MAKER",
        "event_ticker": spec.event_ticker,
        "event_close": spec.event_close.isoformat(),
        "strike_type": spec.strike_type,
        "lower_ticker": spec.lower_ticker,
        "lower_strike": str(spec.lower_strike),
        "higher_ticker": spec.higher_ticker,
        "higher_strike": str(spec.higher_strike),
        "maker_ticker": spec.maker_ticker,
        "maker_side": spec.maker_side,
        "maker_cost": str(spec.maker_cost),
        "seed_ticker": spec.hedge_ticker,
        "seed_side": spec.hedge_side,
        "seed_cost": str(spec.hedge_cost),
        "modeled_seed_taker_fee": str(hedge_fee),
        "modeled_seed_cash_risk_if_maker_never_fills": str(seed_risk),
        "modeled_locked_profit_if_pair_fills": str(locked),
        "maker_rest_seconds": spec.maker_rest_seconds,
        "source_current_best_bid": source_row.get("current_best_bid"),
        "source_current_best_ask": source_row.get("current_best_ask"),
        "source_quote_improvement": source_row.get("quote_improvement"),
        "seed_payload_first": seed_payload,
        "maker_payload_second": maker_payload,
        "actual_order_placed": False,
    }
    logger.info("NESTED_LIVE_DRY_RUN %s", json.dumps(report, separators=(",", ":"), default=str))
    if not live:
        return report

    result = await execute_seed_first(spec, cli_live=True)
    live_report = {
        **report,
        "status": result.status,
        "execution_result": result.as_dict(),
        "actual_order_placed": result.seed_state is not None,
    }
    logger.info("NESTED_LIVE_RESULT %s", json.dumps(live_report, separators=(",", ":"), default=str))
    return live_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--series", nargs="+", default=["KXETHD", "KXSOLD"])
    parser.add_argument("--target-profit", type=Decimal, default=D("0.03"))
    parser.add_argument("--maker-rest-seconds", type=int, default=10)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run_once(
        series=[s.upper() for s in args.series],
        target_profit=args.target_profit,
        maker_rest_seconds=args.maker_rest_seconds,
        live=args.live,
    ))


if __name__ == "__main__":
    main()
