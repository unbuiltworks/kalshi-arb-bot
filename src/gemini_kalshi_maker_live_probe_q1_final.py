"""Operator entrypoint for the one-shot maker LIVE_PROBE.

Default Railway deployment is read-only preflight. Real order entry remains behind
all independent arm gates in the hardened q1 state machine.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
import os
from pathlib import Path

import httpx

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe
from src import gemini_kalshi_maker_live_probe_q1_safe_v2 as hardened

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_final")
PREFLIGHT_ENV = "MAKER_LIVE_PROBE_PREFLIGHT"


def preflight_requested() -> bool:
    return os.getenv(PREFLIGHT_ENV, "YES").strip().upper() == "YES"


async def readonly_preflight() -> dict:
    gemini = base.GeminiTradingSession()
    kalshi = base.KalshiV2Executor()
    try:
        await gemini.connect()
        report = await base._preflight(
            kalshi,
            live_requested=False,
            token_path=Path("/tmp/maker-live-probe-preflight-no-arm"),
        )
        gemini_usd = D(str(report.get("gemini_usd_available") or "0"))
        kalshi_usd = D(str(report.get("kalshi_usd_available") or "0"))
        if gemini_usd < D("1") or kalshi_usd < D("1"):
            raise RuntimeError("maker live probe preflight requires at least $1 available on both venues")
        async with httpx.AsyncClient(timeout=20.0) as http:
            fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[base.ASSET])
            safe._fee_mapping_gate(fee_meta)
        return {
            **report,
            "mode": "LIVE_PROBE_PREFLIGHT_ONLY",
            "asset": base.ASSET,
            "quantity_max": "1",
            "target_profit": str(base.TARGET_PROFIT),
            "fee_type": fee_meta.fee_type,
            "fee_multiplier": fee_meta.fee_multiplier,
            "settlement_status": base.SETTLEMENT_STATUS,
            "order_entry": False,
            "preflight_passed": True,
        }
    finally:
        await gemini.close()
        await kalshi.close()


async def run() -> dict:
    if not base._live_software_authorized():
        if preflight_requested():
            report = await readonly_preflight()
            logger.warning("MAKER_LIVE_PROBE_READONLY_PREFLIGHT %s", json.dumps(report, separators=(",", ":"), default=str))
            return report
        return {
            "mode": "LIVE_PROBE",
            "outcome": "DISARMED_NO_PREFLIGHT",
            "settlement_status": base.SETTLEMENT_STATUS,
            "order_entry": False,
        }
    # safe.run_once uses safe.candidate_from_books at runtime. Pin it to the final
    # non-recursive selector before crossing into any mutating code.
    safe.candidate_from_books = hardened.candidate_from_books
    return await safe.run_once()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run())
    logger.warning("MAKER_LIVE_PROBE_OPERATOR_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
