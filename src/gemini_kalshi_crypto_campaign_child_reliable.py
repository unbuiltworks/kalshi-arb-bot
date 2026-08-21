"""One-attempt crypto canary child using Gemini-first staged execution.

Market discovery/readiness/risk gates are identical to the proven campaign child.
Only the final two-leg submission policy changes: Gemini must terminally fill before
Kalshi receives the hedge order. The authenticated Gemini trading socket is deferred
until an actionable trade has passed all market-data and safety gates.

Kalshi strike metadata and order-book market data use authoritative REST fallbacks in
this campaign because authenticated market-data WebSocket handshakes are rate-limited.
REST books carry exact price/depth and are timestamped conservatively at request start;
all existing freshness, skew, survival and execution gates remain unchanged.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import sys
from typing import Any

from src import gemini_kalshi_sol_live_canary_v2 as v2
from src import gemini_kalshi_rollover_optimizer as rollover_optimizer
from src import gemini_kalshi_target_lifecycle_cache as target_lifecycle_cache
from src import gemini_kalshi_target_freshness_guard as target_freshness_guard
from src import gemini_kalshi_gemini_ws_recovery as gemini_ws_recovery
from src import gemini_kalshi_gemini_flatten_fix as gemini_flatten_fix
from src import gemini_kalshi_gate_attribution as gate_attribution
from src import gemini_kalshi_micro_live_executor_20bp as micro_executor
from src import gemini_kalshi_ws_rate_limit_guard as ws_rate_limit_guard
from src import gemini_kalshi_deferred_gemini_session as deferred_gemini_session
from src import gemini_kalshi_kalshi_rest_book_fallback as kalshi_rest_book_fallback
from src.gemini_kalshi_staged_probe_transport import submit_gemini_first_probe

DEADLINE_ENV = "SOL_CANARY_V2_ARM_NOT_AFTER_UTC"
HEDGE_CUSHION_ENV = "CRYPTO_KALSHI_HEDGE_CUSHION"
_ALLOWED_ASSETS = ("BTC", "ETH", "SOL", "XRP")


def _selected_assets() -> tuple[str, ...]:
    requested = os.getenv("CRYPTO_CANARY_ASSET", "").strip().upper()
    if not requested:
        return _ALLOWED_ASSETS
    if requested not in _ALLOWED_ASSETS:
        raise ValueError(f"unsupported CRYPTO_CANARY_ASSET={requested}")
    return (requested,)


def _hedge_cushion_enabled() -> bool:
    return os.getenv(HEDGE_CUSHION_ENV, "NO").strip().upper() == "YES"


async def _disabled_lifecycle_stream() -> None:
    """Keep authoritative REST strike fallback active without a second Kalshi WS."""
    await asyncio.Event().wait()


async def _submit_and_persist_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
    run_id = v2._clean_run_id(os.environ[v2.RUN_ID_ENV])
    _, claim, result_path = v2._paths(run_id)
    if not claim.is_file():
        raise PermissionError("persistent crypto canary attempt was not claimed")

    cushion_enabled = _hedge_cushion_enabled()
    kwargs["kalshi_hedge_cushion_enabled"] = cushion_enabled
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
            "kalshi_payload_original": kalshi_payload,
            "modeled_locked_profit": str(kwargs.get("modeled_locked_profit", "")),
            "kalshi_fee_multiplier": str(kwargs.get("kalshi_fee_multiplier", "")),
            "quote_skew_ms": kwargs.get("quote_skew_ms"),
            "kalshi_hedge_cushion_enabled": cushion_enabled,
            "execution_policy": result.get(
                "execution_policy", "GEMINI_FIRST_TERMINAL_THEN_KALSHI"
            ),
            "result": result,
        },
    )
    return result


def main() -> None:
    deadline = os.getenv(DEADLINE_ENV, "").strip()
    if not deadline:
        raise RuntimeError(f"{DEADLINE_ENV} is required for live canary child")

    assets = _selected_assets()
    gate_attribution.install(micro_executor.logger)
    gemini_ws_recovery.install()
    gemini_flatten_fix.install()
    ws_rate_limit_guard.install_child_preflight()
    deferred_gemini_session.install()
    v2._submit_and_persist_result = _submit_and_persist_context

    rollover_optimizer.install()
    kalshi_rest_book_fallback.install()
    target_lifecycle_cache._lifecycle_stream = _disabled_lifecycle_stream
    micro_executor.logger.warning(
        "CRYPTO_KALSHI_TARGET_REST_ONLY enabled=true lifecycle_ws=false "
        "orderbook_transport=REST_EXACT_ORDERBOOK_FAIL_CLOSED risk_gates_changed=false"
    )
    target_lifecycle_cache.install()
    target_freshness_guard.install()

    micro_executor.logger.warning(
        "CRYPTO_RELIABLE_CHILD_EXECUTION_POLICY staged=true kalshi_hedge_cushion_enabled=%s "
        "min_profit=0.03 quantity=1",
        _hedge_cushion_enabled(),
    )

    sys.argv = [
        sys.argv[0],
        "--arm-deadline-utc", deadline,
        "--assets", *assets,
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
