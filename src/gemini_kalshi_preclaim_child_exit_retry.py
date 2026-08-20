"""Retry safe child startup failures that provably occurred before order submission.

The persistent canary claim is the safety boundary: the claim is atomically created
before any venue submission.  Therefore a child process that exits with neither a
result nor a persistent claim cannot have submitted an order.  Such a startup failure
(e.g. an authenticated WebSocket HTTP 429 during preflight) is safe to classify as a
zero-fill execution miss and retry within the campaign's existing bounded attempt
budget.

If a claim exists without a result, behavior remains fail-closed.  This module does
not alter price, boundary, freshness, skew, quantity, capital, or settlement gates.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from src import gemini_kalshi_crypto_parallel_campaign_safe as safe
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_preclaim_child_exit_retry")
_INSTALLED = False


def _is_pre_result_child_exit(exc: BaseException, run_id: str) -> bool:
    text = str(exc)
    return bool(
        isinstance(exc, RuntimeError)
        and text.startswith("asset child exited ")
        and " before result run_id=" in text
        and text.endswith(str(run_id))
    )


def _benign_preclaim_record(run_id: str, exc: BaseException) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "startup_retry": True,
        "result": {
            "outcome": "PRIMARY_NOT_FILLED",
            "execution_policy": "NO_ORDER_PRECLAIM_STARTUP_FAILURE",
            "gemini": {
                "venue": "gemini",
                "filled_quantity": "0",
                "average_fill_price": None,
                "fee_paid": None,
                "status": "CANCELED",
                "reason": "CHILD_EXITED_BEFORE_PERSISTENT_CLAIM",
                "raw_error": str(exc)[:240],
            },
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": False,
        },
    }


def _wrap_obtain(original: Callable[..., Awaitable[dict[str, Any]]]):
    async def wrapped(
        run_id: str,
        asset: str,
        arm_deadline,
        stop_event,
        registry,
        registry_lock,
    ) -> dict[str, Any]:
        try:
            return await original(
                run_id, asset, arm_deadline, stop_event, registry, registry_lock,
            )
        except Exception as exc:
            if not _is_pre_result_child_exit(exc, run_id):
                raise

            _arm, claim, result_path = v2._paths(run_id)
            # Close the tiny race where a result appeared as the child exited.
            if result_path.is_file():
                logger.warning(
                    "CRYPTO_PRECLAIM_CHILD_EXIT_RACE_RESULT_FOUND asset=%s run_id=%s",
                    asset, run_id,
                )
                return json.loads(result_path.read_text(encoding="utf-8"))

            # A persistent claim means a venue submission may have happened.  Never
            # downgrade that state to a benign retry.
            if claim.is_file():
                logger.critical(
                    "CRYPTO_PRECLAIM_CHILD_EXIT_CLAIM_PRESENT_FAIL_CLOSED asset=%s run_id=%s",
                    asset, run_id,
                )
                raise

            logger.warning(
                "CRYPTO_PRECLAIM_CHILD_EXIT_BENIGN asset=%s run_id=%s no_claim=true no_result=true "
                "orders_possible=false continuing_with_bounded_retry=true error=%s",
                asset, run_id, str(exc)[:220],
            )
            return _benign_preclaim_record(run_id, exc)

    return wrapped


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    safe._obtain_asset_execution = _wrap_obtain(safe._obtain_asset_execution)
    _INSTALLED = True
    logger.warning(
        "CRYPTO_PRECLAIM_CHILD_EXIT_RETRY_INSTALLED safety_boundary=persistent_attempt_claim "
        "claim_present_fail_closed=true no_claim_no_result_retry=true risk_gates_changed=false"
    )
