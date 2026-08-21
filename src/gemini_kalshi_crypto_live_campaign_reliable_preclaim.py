"""Sequential reliable crypto campaign with bounded pre-claim startup retry.

A child that exits before a persistent attempt claim and before a result cannot have
submitted a venue order. Treat only that state as a benign zero-fill execution miss,
back off to relieve authenticated WebSocket rate limits, and continue inside the
existing campaign attempt cap. Claim-present or result ambiguity remains fail-closed.
"""
from __future__ import annotations

import asyncio
import json
import logging

from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_live_campaign_reliable_preclaim")
_ORIGINAL_OBTAIN = reliable._obtain_execution
BACKOFF_S = 30.0


async def _obtain_with_preclaim_retry(run_id, arm_deadline):
    try:
        return await _ORIGINAL_OBTAIN(run_id, arm_deadline)
    except RuntimeError as exc:
        if not str(exc).startswith("reliable canary child exited "):
            raise
        _arm, claim, result_path = v2._paths(run_id)
        if result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8"))
        if claim.is_file():
            logger.critical(
                "CRYPTO_SEQUENTIAL_PRECLAIM_RETRY_REFUSED run_id=%s claim_present=true",
                run_id,
            )
            raise
        logger.warning(
            "CRYPTO_SEQUENTIAL_PRECLAIM_STARTUP_RETRY run_id=%s no_claim=true "
            "no_result=true orders_possible=false backoff_s=%.1f risk_gates_changed=false error=%s",
            run_id,
            BACKOFF_S,
            str(exc)[:220],
        )
        await asyncio.sleep(BACKOFF_S)
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


def main() -> None:
    reliable._obtain_execution = _obtain_with_preclaim_retry
    logger.warning(
        "CRYPTO_SEQUENTIAL_PRECLAIM_RETRY_INSTALLED backoff_s=30 "
        "claim_present_fail_closed=true risk_gates_changed=false"
    )
    reliable.main()


if __name__ == "__main__":
    main()
