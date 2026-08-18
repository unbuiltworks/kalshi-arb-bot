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
* process must include --live;
* ENABLE_GEMINI_KALSHI_LIVE_PROBES must equal YES;
* a single-use arm-token file must exist and is consumed before submission;
* at most one submission attempt per process;
* one-leg fill or unresolved ambiguity triggers the existing hard-halt/flatten path.

The Railway service for this module is intended to be SOL-only with --capital-limit 1,
so the planned fee-inclusive pair outlay is below $1 whenever the >=3c locked-profit
gate passes. That keeps the first authorized experiment well below a $5 total budget.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import logging
import sys

from src import gemini_kalshi_micro_live_executor_20bp_safe as safe
from src.gemini_kalshi_live_probe_guard import DEFAULT_MIN_LOCKED_PROFIT, MAX_QUOTE_SKEW_MS

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


def install_canary() -> None:
    # The readiness module computes its boundary gate from this constant at runtime.
    # Field names retain the historical "20bp" suffix, but their semantics in this
    # isolated process are explicitly 10 bp and are logged below.
    readiness.MIN_DOUBLE_BOUNDARY_BPS = CANARY_MIN_DOUBLE_BOUNDARY_BPS

    # Keep the hardened executor unchanged except for the exact delay gate.
    executor.strict_execution_gate = strict_execution_gate_10bp_50ms
    safe.install_safe_executor()


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--live", action="store_true")
    live_args, remaining = parser.parse_known_args()
    executor._LIVE_REQUESTED = bool(live_args.live)
    sys.argv = [sys.argv[0], *remaining]

    install_canary()
    logger.info(
        "SOL_LIVE_CANARY_10BP_50MS_ENABLED asset=%s min_double_boundary_bps=%s "
        "execution_delay_ms=%s quantity=1 min_profit=%s hard_budget_usd=%s "
        "live_requested=%s env_gate=%s single_attempt_per_process=true",
        CANARY_ASSET,
        CANARY_MIN_DOUBLE_BOUNDARY_BPS,
        CANARY_EXECUTION_DELAY_MS,
        DEFAULT_MIN_LOCKED_PROFIT,
        CANARY_HARD_BUDGET_USD,
        executor._LIVE_REQUESTED,
        executor.live_probe_authorized(executor._LIVE_REQUESTED),
    )
    readiness.guarded.main()


if __name__ == "__main__":
    main()
