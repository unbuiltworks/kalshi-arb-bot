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
from src import gemini_kalshi_maker_rest_guard as rest_guard
from src import gemini_kalshi_maker_dominance_guard as dominance_guard

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_final")
PREFLIGHT_ENV = "MAKER_LIVE_PROBE_PREFLIGHT"
TARGET_ENV = "MAKER_LIVE_PROBE_TARGET_PROFIT"
POLICY_ENV = "MAKER_LIVE_PROBE_POLICY"
ASSET_ENV = "MAKER_LIVE_PROBE_ASSET"
ELIGIBLE_ASSETS = ("ETH", "SOL", "XRP")
MIN_TARGET_PROFIT = D("0.03")
MAX_TARGET_PROFIT = D("0.25")


def preflight_requested() -> bool:
    return os.getenv(PREFLIGHT_ENV, "YES").strip().upper() == "YES"


def configure_asset() -> str:
    asset = os.getenv(ASSET_ENV, "XRP").strip().upper()
    if asset == "BTC":
        raise ValueError("BTC remains quarantined for settlement-basis maker research")
    if asset not in ELIGIBLE_ASSETS:
        raise ValueError(f"{ASSET_ENV} must be one of {','.join(ELIGIBLE_ASSETS)}")
    if asset not in base.ASSET_SERIES:
        raise ValueError(f"maker series mapping missing for asset={asset}")
    base.ASSET = asset
    return asset


def configure_target_profit() -> Decimal:
    target = D(os.getenv(TARGET_ENV, str(MIN_TARGET_PROFIT)).strip())
    if target < MIN_TARGET_PROFIT or target > MAX_TARGET_PROFIT:
        raise ValueError(
            f"{TARGET_ENV} must be between {MIN_TARGET_PROFIT} and {MAX_TARGET_PROFIT}; "
            "the live maker lane may not weaken the 3-cent after-fee floor"
        )
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
    asset = configure_asset()
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
            fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[asset])
            safe._fee_mapping_gate(fee_meta)
        return {
            **report,
            "mode":"LIVE_PROBE_PREFLIGHT_ONLY",
            "asset":asset,
            "eligible_assets":list(ELIGIBLE_ASSETS),
            "btc_quarantined":True,
            "quantity_max":"1",
            "target_profit":str(target),
            "min_target_profit":str(MIN_TARGET_PROFIT),
            "policy":policy,
            "fee_type":fee_meta.fee_type,
            "fee_multiplier":fee_meta.fee_multiplier,
            "settlement_status":base.SETTLEMENT_STATUS,
            "threshold_dominance_min_gap_bps":str(dominance_guard.MIN_GAP_BPS),
            "threshold_dominance_required_before_post":True,
            "kalshi_book_transport":"EXACT_REST_DEPTH1",
            "kalshi_account_transport":"REST_ORDER_POLL_PLUS_FINAL_FILL_RECONCILIATION",
            "order_entry":False,
            "preflight_passed":True,
        }
    finally:
        await gemini.close()
        await kalshi.close()


async def run() -> dict:
    asset = configure_asset()
    target = configure_target_profit()
    policy = configure_policy()
    if not base._live_software_authorized():
        if preflight_requested():
            report = await readonly_preflight()
            logger.warning("MAKER_LIVE_PROBE_READONLY_PREFLIGHT %s", json.dumps(report, separators=(",", ":"), default=str))
            return report
        return {"mode":"LIVE_PROBE","outcome":"DISARMED_NO_PREFLIGHT","settlement_status":base.SETTLEMENT_STATUS,"order_entry":False}
    safe.candidate_from_books = hardened.candidate_from_books
    # Process-local only: preserve Gemini streaming and all existing execution/risk
    # gates, replace both Kalshi websocket readers with REST, and require the same
    # threshold-dominance-safe orientation as the bounded basis-risk lane before
    # candidate discovery can ever reach the post-only order mutation.
    rest_guard.install()
    dominance_guard.install_wait_guard()
    venue_once.install()
    logger.warning("MAKER_LIVE_PROBE_ONE_SHOT_MODE %s", json.dumps({
        "mode":"LIVE_PROBE",
        "asset":asset,
        "eligible_assets":list(ELIGIBLE_ASSETS),
        "btc_quarantined":True,
        "idempotency":"KALSHI_CLIENT_ORDER_ID",
        "process_claim":"EPHEMERAL",
        "restart_policy":"NEVER",
        "maker_order_ttl_s":venue_once.ORDER_TTL_S,
        "quantity_max":"1",
        "target_profit":str(target),
        "min_target_profit":str(MIN_TARGET_PROFIT),
        "policy":policy,
        "settlement_status":base.SETTLEMENT_STATUS,
        "threshold_dominance_min_gap_bps":str(dominance_guard.MIN_GAP_BPS),
        "kalshi_book_transport":"EXACT_REST_DEPTH1",
        "kalshi_account_transport":"REST_ORDER_POLL_PLUS_FINAL_FILL_RECONCILIATION",
    }, separators=(",", ":")))
    return await safe.run_once()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run())
    logger.warning("MAKER_LIVE_PROBE_OPERATOR_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
