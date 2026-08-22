"""Reliability boot for the one-trade q=5 scale campaign.

A q=5 child can fail during authenticated venue preflight before it creates the
persistent attempt claim.  The claim is created before any venue submission, so a
child exit with neither a result nor a claim proves that no order was sent.  This
wrapper converts only that state into a known-zero PRIMARY_NOT_FILLED miss so the
existing bounded q=5 campaign may retry.

A claim without a result remains fail-closed.  Price, quantity, profit, 10bp boundary,
50ms survival/skew, capital, settlement and execution gates are unchanged.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from src import gemini_kalshi_crypto_q5_scale_campaign as campaign
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_q5_scale_campaign_retry")


def _is_q5_pre_result_child_exit(exc: BaseException, run_id: str) -> bool:
    text = str(exc)
    return bool(
        isinstance(exc, RuntimeError)
        and text.startswith("q5 child exited ")
        and " before persistent result run_id=" in text
        and text.endswith(str(run_id))
    )


def _benign_q5_preclaim_record(run_id: str, exc: BaseException) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "startup_retry": True,
        "result": {
            "outcome": "PRIMARY_NOT_FILLED",
            "execution_policy": "NO_ORDER_PRECLAIM_STARTUP_FAILURE_Q5",
            "gemini": {
                "venue": "gemini",
                "filled_quantity": "0",
                "average_fill_price": None,
                "fee_paid": None,
                "status": "CANCELED",
                "reason": "Q5_CHILD_EXITED_BEFORE_PERSISTENT_CLAIM",
                "raw_error": str(exc)[:240],
            },
            "kalshi": None,
            "flatten": None,
            "hard_halt_required": False,
        },
    }


def _wrap_obtain(original: Callable[..., Awaitable[dict[str, Any]]]):
    async def wrapped(run_id: str, arm_deadline) -> dict[str, Any]:
        try:
            return await original(run_id, arm_deadline)
        except Exception as exc:
            if not _is_q5_pre_result_child_exit(exc, run_id):
                raise

            _arm, claim, result_path = v2._paths(run_id)
            # If the child persisted a terminal result as it exited, that result wins.
            if result_path.is_file():
                logger.warning("CRYPTO_Q5_PRECLAIM_EXIT_RACE_RESULT_FOUND run_id=%s", run_id)
                return json.loads(result_path.read_text(encoding="utf-8"))

            # A claim is the fail-closed boundary: a venue submission may have begun.
            if claim.is_file():
                logger.critical("CRYPTO_Q5_PRECLAIM_EXIT_CLAIM_PRESENT_FAIL_CLOSED run_id=%s", run_id)
                raise

            logger.warning(
                "CRYPTO_Q5_PRECLAIM_CHILD_EXIT_BENIGN run_id=%s no_claim=true no_result=true "
                "orders_possible=false continuing_with_bounded_retry=true error=%s",
                run_id, str(exc)[:220],
            )
            return _benign_q5_preclaim_record(run_id, exc)

    return wrapped


def install() -> None:
    campaign._obtain_q5_execution = _wrap_obtain(campaign._obtain_q5_execution)
    logger.warning(
        "CRYPTO_Q5_PRECLAIM_RETRY_INSTALLED safety_boundary=persistent_attempt_claim "
        "claim_present_fail_closed=true no_claim_no_result_retry=true risk_gates_changed=false"
    )


def main() -> None:
    install()
    campaign.main()


if __name__ == "__main__":
    main()
