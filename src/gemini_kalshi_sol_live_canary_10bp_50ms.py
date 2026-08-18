"""SOL-only 15-minute live canary using the hardened Gemini/Kalshi micro executor.

This wrapper reuses the existing one-contract dual-FOK transport and all fail-closed
authorization/ambiguity handling. It changes only the two research thresholds selected
from shadow evidence: 10 bp minimum double-boundary clearance and a 50 ms exact
original-price/depth survival checkpoint.

The first real-money canary is deliberately tiny: exactly one contract pair, >=$0.03
modeled after-fee locked profit, <=$1 planned capital, one submission attempt per
process, and the existing emergency flatten/hard-halt behavior. A short absolute arm
window can create the existing single-use token once; after that wall-clock deadline,
future redeploys cannot recreate authorization from this build.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys

from src import gemini_kalshi_micro_live_executor_20bp_safe as safe
from src.gemini_kalshi_live_probe_guard import DEFAULT_MIN_LOCKED_PROFIT, MAX_QUOTE_SKEW_MS
from src.gemini_prediction_auth import verify_authenticated_ws

D = Decimal
executor = safe.executor
readiness = safe.readiness
logger = logging.getLogger("kalshi_arb.gemini_kalshi_sol_live_canary_10bp_50ms")

CANARY_ASSET = "SOL"
CANARY_MIN_DOUBLE_BOUNDARY_BPS = D("10")
CANARY_EXECUTION_DELAY_MS = 50
CANARY_HARD_BUDGET_USD = D("5")
CANARY_PLANNED_CAPITAL_LIMIT_USD = D("1")
# Absolute one-time authorization window for this exact first canary build.
CANARY_ARM_NOT_AFTER_UTC = datetime(2026, 8, 18, 23, 20, 0, tzinfo=timezone.utc)


def strict_execution_gate_10bp_50ms(
    *,
    delay_ms: int,
    readiness_20bp: bool,
    quantity: Decimal,
    locked_profit: Decimal,
    entry_quote_skew_ms: int | None,
    current_quote_skew_ms: int | None,
) -> bool:
    return bool(
        int(delay_ms) == CANARY_EXECUTION_DELAY_MS
        and readiness_20bp
        and D(quantity) == D("1")
        and D(locked_profit) >= DEFAULT_MIN_LOCKED_PROFIT
        and entry_quote_skew_ms is not None
        and int(entry_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
        and current_quote_skew_ms is not None
        and int(current_quote_skew_ms) <= MAX_QUOTE_SKEW_MS
    )


async def _readonly_authenticated_preflight() -> None:
    """Verify private access/funding without authorizing or placing an order."""
    kalshi = executor.KalshiV2Executor()
    try:
        report = await executor._preflight(
            kalshi,
            live_requested=False,
            token_path=Path("/tmp/sol-canary-preflight-no-arm-token"),
        )
        gemini_ws_ok = await verify_authenticated_ws(timeout=5.0)
        if not gemini_ws_ok:
            raise RuntimeError("Gemini authenticated WebSocket preflight failed")
        gemini_funded = D(str(report.get("gemini_usd_available") or "0")) >= D("1")
        kalshi_funded = D(str(report.get("kalshi_usd_available") or "0")) >= D("1")
        logger.info(
            "SOL_LIVE_CANARY_AUTH_PREFLIGHT %s",
            json.dumps({
                "gemini_private_rest_authenticated": True,
                "gemini_authenticated_ws": True,
                "gemini_terms_accepted": bool(report.get("gemini_terms_accepted")),
                "kalshi_production_authenticated": bool(report.get("kalshi_production_authenticated")),
                "gemini_funded_ge_1usd": gemini_funded,
                "kalshi_funded_ge_1usd": kalshi_funded,
                "order_entry": False,
            }, separators=(",", ":")),
        )
        if not gemini_funded or not kalshi_funded:
            raise RuntimeError("SOL canary preflight requires at least $1 available on each venue")
    finally:
        await kalshi.close()


def install_canary() -> None:
    readiness.MIN_DOUBLE_BOUNDARY_BPS = CANARY_MIN_DOUBLE_BOUNDARY_BPS
    executor.strict_execution_gate = strict_execution_gate_10bp_50ms
    safe.install_safe_executor()


def _live_ready_requested(cli_live: bool) -> bool:
    return bool(cli_live) or os.getenv("SOL_CANARY_LIVE_READY", "NO").strip().upper() == "YES"


def _maybe_create_expiring_one_time_arm() -> bool:
    """Create the existing single-use token only inside this build's wall-clock window."""
    now = datetime.now(timezone.utc)
    token_path = executor._arm_token_path()
    software_authorized = executor.live_probe_authorized(executor._LIVE_REQUESTED)
    if not software_authorized:
        logger.warning(
            "SOL_CANARY_ARM_REFUSED reason=software_gates_not_authorized now=%s deadline=%s",
            now.isoformat(), CANARY_ARM_NOT_AFTER_UTC.isoformat(),
        )
        return False
    if now > CANARY_ARM_NOT_AFTER_UTC:
        logger.warning(
            "SOL_CANARY_ARM_REFUSED reason=absolute_arm_window_expired now=%s deadline=%s",
            now.isoformat(), CANARY_ARM_NOT_AFTER_UTC.isoformat(),
        )
        return False
    if token_path.is_file():
        logger.warning("SOL_CANARY_ARM_TOKEN_ALREADY_PRESENT path=%s", token_path)
        return True
    token_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with token_path.open("x", encoding="utf-8") as fh:
            fh.write("SOL_CANARY_V1_ONE_USE\n")
        token_path.chmod(0o600)
    except FileExistsError:
        pass
    armed = token_path.is_file()
    logger.warning(
        "SOL_CANARY_ONE_TIME_ARM_CREATED armed=%s deadline=%s quantity=1 planned_capital_limit_usd=%s hard_budget_usd=%s",
        armed,
        CANARY_ARM_NOT_AFTER_UTC.isoformat(),
        CANARY_PLANNED_CAPITAL_LIMIT_USD,
        CANARY_HARD_BUDGET_USD,
    )
    return armed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    live_args, remaining = parser.parse_known_args()
    executor._LIVE_REQUESTED = _live_ready_requested(bool(live_args.live))
    sys.argv = [sys.argv[0], *remaining]

    # Credential/funding verification is read-only and must pass before any arm token exists.
    asyncio.run(_readonly_authenticated_preflight())
    _maybe_create_expiring_one_time_arm()

    install_canary()
    logger.warning(
        "SOL_LIVE_CANARY_10BP_50MS_ENABLED asset=%s min_double_boundary_bps=%s "
        "execution_delay_ms=%s quantity=1 min_profit=%s hard_budget_usd=%s "
        "planned_capital_limit_usd=%s live_requested=%s live_ready_env=%s env_gate=%s "
        "token_present=%s arm_not_after_utc=%s single_attempt_per_process=true",
        CANARY_ASSET,
        CANARY_MIN_DOUBLE_BOUNDARY_BPS,
        CANARY_EXECUTION_DELAY_MS,
        DEFAULT_MIN_LOCKED_PROFIT,
        CANARY_HARD_BUDGET_USD,
        CANARY_PLANNED_CAPITAL_LIMIT_USD,
        executor._LIVE_REQUESTED,
        os.getenv("SOL_CANARY_LIVE_READY", "NO"),
        executor.live_probe_authorized(executor._LIVE_REQUESTED),
        executor._arm_token_path().is_file(),
        CANARY_ARM_NOT_AFTER_UTC.isoformat(),
    )
    readiness.guarded.main()


if __name__ == "__main__":
    main()
