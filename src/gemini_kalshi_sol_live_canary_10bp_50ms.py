"""SOL-only 15-minute live canary using the hardened Gemini/Kalshi micro executor.

This wrapper deliberately reuses the existing one-contract dual-FOK transport and
all of its fail-closed authorization/ambiguity handling. It changes only the two
research thresholds selected from the shadow evidence:

* minimum double-boundary clearance: 10 bp instead of 20 bp; and
* exact original-price/depth survival checkpoint: 50 ms instead of 100 ms.

Everything else stays conservative:
* exactly one contract;
* >= $0.03 fee-inclusive modeled locked profit;
* entry and current quote skew <= 50 ms;
* books post-activation and <= 250 ms old;
* same original positive episode throughout;
* exact outbound payload economics are recomputed immediately before send;
* live-ready intent requires either --live or SOL_CANARY_LIVE_READY=YES;
* ENABLE_GEMINI_KALSHI_LIVE_PROBES must independently equal YES;
* a single-use arm-token file must exist and is consumed before submission;
* at most one submission attempt per process;
* one-leg fill or unresolved ambiguity triggers the existing hard-halt/flatten path.

The Railway service for this module is SOL-only with --capital-limit 1, so the planned
fee-inclusive pair outlay is below $1 whenever the >=3c locked-profit gate passes.
That keeps the first authorized experiment well below a $5 total budget.
"""
from __future__ import annotations

import argparse
import asyncio
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


def strict_execution_gate_10bp_50ms(
    *,
    delay_ms: int,
    readiness_20bp: bool,
    quantity: Decimal,
    locked_profit: Decimal,
    entry_quote_skew_ms: int | None,
    current_quote_skew_ms: int | None,
) -> bool:
    """Fail closed unless the SOL canary's exact 50 ms gate passes.

    ``readiness_20bp`` is the legacy parameter name in the hardened executor. In this
    wrapper the readiness module's threshold constant is lowered to 10 bp before the
    hooks are installed, so the boolean represents the 10 bp whole-episode gate.
    """
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
    """Verify both private venue credentials without enabling or placing orders."""
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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    live_args, remaining = parser.parse_known_args()
    executor._LIVE_REQUESTED = _live_ready_requested(bool(live_args.live))
    sys.argv = [sys.argv[0], *remaining]

    # This preflight never authorizes orders, even when the later runtime is live-ready.
    asyncio.run(_readonly_authenticated_preflight())

    install_canary()
    logger.info(
        "SOL_LIVE_CANARY_10BP_50MS_ENABLED asset=%s min_double_boundary_bps=%s "
        "execution_delay_ms=%s quantity=1 min_profit=%s hard_budget_usd=%s "
        "live_requested=%s live_ready_env=%s env_gate=%s token_present=%s "
        "single_attempt_per_process=true",
        CANARY_ASSET,
        CANARY_MIN_DOUBLE_BOUNDARY_BPS,
        CANARY_EXECUTION_DELAY_MS,
        DEFAULT_MIN_LOCKED_PROFIT,
        CANARY_HARD_BUDGET_USD,
        executor._LIVE_REQUESTED,
        os.getenv("SOL_CANARY_LIVE_READY", "NO"),
        executor.live_probe_authorized(executor._LIVE_REQUESTED),
        executor._arm_token_path().is_file(),
    )
    readiness.guarded.main()


if __name__ == "__main__":
    main()
