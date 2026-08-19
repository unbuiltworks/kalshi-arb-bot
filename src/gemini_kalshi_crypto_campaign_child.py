"""One-attempt crypto canary child with settlement-reconciliation context.

This launcher preserves the proven v2 execution gates and transport.  Its only changes
are to persist the exact order payloads alongside the execution result and to install
the latency-only rollover optimizer before scanning.  The optimizer reuses one Kalshi
WebSocket for current/next markets and prefetches next-window target metadata; it does
not change quantity, profit, boundary, freshness, skew, depth, or execution gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from typing import Any

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer

DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"


async def _submit_and_persist_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    _, claim, result_path = v2._paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent crypto canary attempt was not claimed")

    result = await v2._ORIGINAL_SUBMIT_DUAL_PROBE(*args, **kwargs)
    gemini_payload = dict(args[2]) if len(args) > 2 and isinstance(args[2], dict) else {}
    kalshi_payload = dict(args[3]) if len(args) > 3 and isinstance(args[3], dict) else {}
    v2._write_json_atomic(
        result_path,
        {
            "run_id": run_id,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "gemini_payload": gemini_payload,
            "kalshi_payload": kalshi_payload,
            "modeled_locked_profit": str(kwargs.get("modeled_locked_profit", "")),
            "kalshi_fee_multiplier": str(kwargs.get("kalshi_fee_multiplier", "")),
            "quote_skew_ms": kwargs.get("quote_skew_ms"),
            "result": result,
        },
    )
    return result


def main() -> None:
    deadline = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline:
        raise RuntimeError(f"{DEADLINE_ENV} is required for live canary child")

    # v2.install_canary_v2() resolves this module-global hook when it installs the
    # executor transport, so replace it before entering v2.main().
    v2._submit_and_persist_result = _submit_and_persist_context

    # Market-data latency optimization only.  Existing fail-closed live gates remain
    # owned by v2/safe executor modules and are not modified here.
    rollover_optimizer.install()

    sys.argv = [
        sys.argv[0],
        "--arm-deadline-utc", deadline,
        "--assets", "BTC", "ETH", "SOL", "XRP",
        "--seconds", "0",
        "--max-quote-skew-ms", "50",
        "--capital-limit", "1",
        "--min-profit", "0.03",
        "--prewarm-lead-s", "1.0",
        "--output-dir", "/tmp/crypto-live-canary-campaign",
    ]
    v2.main()


if __name__ == "__main__":
    main()
