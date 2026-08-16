"""Disarmed one-shot nested Kalshi probe service.

Default/only operational mode today is an end-to-end DRY RUN: discover the best
current same-event nested candidate, pass the exact one-lot guard, and print the V2
maker and hedge payloads.  A --live flag is parsed only so an operator cannot mistake
its absence for permission; sending remains blocked until the private fill->hedge
orchestrator is installed and tested.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from decimal import Decimal
import json
import logging

from src.kalshi_nested_live_candidate import discover_best_spec
from src.kalshi_nested_live_probe_guard import D, build_v2_payloads, validate_spec

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
    maker_payload, hedge_payload = build_v2_payloads(spec)
    report = {
        "status": "PAYLOAD_READY_LIVE_BLOCKED" if live else "DRY_RUN_READY",
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
        "hedge_ticker": spec.hedge_ticker,
        "hedge_side": spec.hedge_side,
        "hedge_cost": str(spec.hedge_cost),
        "modeled_hedge_fee": str(hedge_fee),
        "modeled_locked_profit": str(locked),
        "maker_rest_seconds": spec.maker_rest_seconds,
        "source_current_best_bid": source_row.get("current_best_bid"),
        "source_current_best_ask": source_row.get("current_best_ask"),
        "source_quote_improvement": source_row.get("quote_improvement"),
        "maker_payload": maker_payload,
        "hedge_payload": hedge_payload,
        "actual_order_placed": False,
    }
    logger.info("NESTED_LIVE_DRY_RUN %s", json.dumps(report, separators=(",", ":"), default=str))
    if live:
        raise RuntimeError(
            "LIVE_SEND_BLOCKED: payload is valid, but private maker-fill -> IOC-hedge orchestration is not installed yet"
        )
    return report


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
