"""One-attempt crypto canary child using Gemini-first staged execution.

Market discovery/readiness/risk gates are identical to the proven campaign child.
Only the final two-leg submission policy changes: Gemini must terminally fill before
Kalshi receives the hedge order. The authenticated Gemini trading socket is health-
checked immediately before submission; an unhealthy pre-send socket becomes a safe
zero-fill retry, while any post-send ambiguity remains fail-closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
from typing import Any

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_gemini_ws_recovery as gemini_ws_recovery
from src.gemini_kalshi_staged_probe_transport import submit_gemini_first_probe

DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"


async def _submit_and_persist_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    _, claim, result_path = v2._paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent crypto canary attempt was not claimed")

    result = await submit_gemini_first_probe(*args, **kwargs)
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
            "execution_policy": "GEMINI_FIRST_TERMINAL_THEN_KALSHI",
            "result": result,
        },
    )
    return result


def main() -> None:
    deadline = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline:
        raise RuntimeError(f"{DEADLINE_ENV} is required for live canary child")

    # Install before v2 creates the persistent authenticated trading session.
    gemini_ws_recovery.install()
    v2._submit_and_persist_result = _submit_and_persist_context

    rollover_optimizer.install()
    target_lifecycle_cache.install()
    target_freshness_guard.install()

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
