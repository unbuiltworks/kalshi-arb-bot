"""Operator entrypoint for the one-shot maker LIVE_PROBE."""
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
from src import gemini_kalshi_maker_live_probe_q1_venue_once as venue_once

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_final")
PREFLIGHT_ENV = "MAKER_LIVE_PROBE_PREFLIGHT"
TARGET_ENV = "MAKER_LIVE_PROBE_TARGET_PROFIT"
POLICY_ENV = "MAKER_LIVE_PROBE_POLICY"


def preflight_requested() -> bool:
    return os.getenv(PREFLIGHT_ENV, "YES").strip().upper() == "YES"


def configure_target_profit() -> Decimal:
    target = D(os.getenv(TARGET_ENV, "0.03").strip())
    if target < D("0.01") or target > D("0.03"):
        raise ValueError(f"{TARGET_ENV} must be between 0.01 and 0.03")
    base.TARGET_PROFIT = target
    safe.TARGET_PROFIT = target
    hardened.TARGET_PROFIT = target
    return target


def configure_policy() -> str:
    policy = os.getenv(POLICY_ENV, "MAX_SAFE").strip().upper()
    if policy not in {"JOIN", "IMPROVE_1T", "MAX_SAFE"}:
        raise ValueError(f"{POLICY_ENV} must be JOIN, IMPROVE_1T, or MAX_SAFE")
    base.POLICY = policy
    return policy


async def readonly_preflight() -> dict:
    target = configure_target_profit()
    policy = configure_policy()
    gemini = base.GeminiTradingSession()
    kalshi = base.KalshiV2Executor()
    try:
        await gemini.connect()
        report = await base._preflight(kalshi, live_requested=False, token_path=Path("/tmp/maker-live-probe-preflight-no-arm"))
        gemini_usd = D(str(report.get("gemini_usd_available") or "0"))
        kalshi_usd = D(str(report.get("kalshi_usd_available") or "0"))
        if gemini_usd < D("1") or kalshi_usd < D("1"):
            raise RuntimeError("maker live probe preflight requires at least $1 available on both venues")
        async with httpx.AsyncClient(timeout=20.0) as http:
            fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[base.ASSET])
            safe._fee_mapping_gate(fee_meta)
        return {**report,"mode":"LIVE_PROBE_PREFLIGHT_ONLY","asset":base.ASSET,"quantity_max":"1","target_profit":str(target),"policy":policy,"fee_type":fee_meta.fee_type,"fee_multiplier":fee_meta.fee_multiplier,"settlement_status":base.SETTLEMENT_STATUS,"order_entry":False,"preflight_passed":True}
    finally:
        await gemini.close()
        await kalshi.close()


async def run() -> dict:
    target = configure_target_profit()
    policy = configure_policy()
    if not base._live_software_authorized():
        if preflight_requested():
            report = await readonly_preflight()
            logger.warning("MAKER_LIVE_PROBE_READONLY_PREFLIGHT %s", json.dumps(report, separators=(",", ":"), default=str))
            return report
        return {"mode":"LIVE_PROBE","outcome":"DISARMED_NO_PREFLIGHT","settlement_status":base.SETTLEMENT_STATUS,"order_entry":False}
    safe.candidate_from_books = hardened.candidate_from_books
    venue_once.install()
    logger.warning("MAKER_LIVE_PROBE_ONE_SHOT_MODE %s", json.dumps({"mode":"LIVE_PROBE","idempotency":"KALSHI_CLIENT_ORDER_ID","process_claim":"EPHEMERAL","restart_policy":"NEVER","maker_order_ttl_s":venue_once.ORDER_TTL_S,"quantity_max":"1","target_profit":str(target),"policy":policy,"settlement_status":base.SETTLEMENT_STATUS}, separators=(",", ":")))
    return await safe.run_once()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run())
    logger.warning("MAKER_LIVE_PROBE_OPERATOR_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
