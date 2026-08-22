"""Post-incident q=1 boot with pre-claim infrastructure retry safety.

The persistent attempt claim is created atomically before any venue submission.  If a
child exits before that claim exists, no order can have been authorized by the live
transport.  Treat that narrow condition as a benign infrastructure miss instead of a
trading incident, cool down briefly, and let the existing bounded supervisor retry.

Any child failure after the claim exists remains fail-closed.  Existing q=1 economics,
settlement, capital reservations, hedge cushion and global trading-incident halts are
unchanged.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_crypto_parallel_campaign_safe as safe
from src import gemini_kalshi_crypto_postincident_boot as postincident
from src import gemini_kalshi_sol_live_canary_v2 as v2

logger = logging.getLogger("kalshi_arb.gemini_kalshi_crypto_preclaim_retry_boot")
RETRY_BACKOFF_S = 2.0
_ORIGINAL_OBTAIN_ASSET_EXECUTION = safe._obtain_asset_execution


def _preclaim_exit(exc: Exception, run_id: str) -> bool:
    """True only when a child died before the persistent submission claim exists."""
    message = str(exc)
    if "asset child exited" not in message or f"run_id={run_id}" not in message:
        return False
    _, claim, result_path = v2._paths(run_id)
    return not claim.is_file() and not result_path.is_file()


def _synthetic_preclaim_miss(run_id: str, asset: str, exc: Exception) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "infrastructure_retry": True,
        "preclaim_no_order_proof": True,
        "result": {
            "outcome": "PRIMARY_NOT_FILLED",
            "hard_halt_required": False,
            "gemini": {
                "venue": "gemini",
                "filled_quantity": "0",
                "average_fill_price": None,
                "fee_paid": None,
                "status": "CANCELED",
                "reason": "PRECLAIM_CHILD_EXIT_NO_ORDER_PROOF",
                "reconciled": False,
            },
            "kalshi": None,
            "flatten": None,
            "preclaim_no_order_proof": True,
            "asset": asset,
            "child_error": str(exc)[:300],
        },
    }


async def _obtain_asset_execution_with_preclaim_retry(
    run_id: str,
    asset: str,
    arm_deadline,
    stop_event,
    registry,
    registry_lock,
) -> dict[str, Any]:
    try:
        record = await _ORIGINAL_OBTAIN_ASSET_EXECUTION(
            run_id, asset, arm_deadline, stop_event, registry, registry_lock,
        )
    except RuntimeError as exc:
        if not _preclaim_exit(exc, run_id):
            raise
        logger.warning(
            "CRYPTO_PARALLEL_PRECLAIM_CHILD_EXIT_BENIGN asset=%s run_id=%s "
            "no_persistent_claim=true no_order_authorized=true retry_backoff_s=%s error=%s",
            asset, run_id, RETRY_BACKOFF_S, str(exc)[:200],
        )
        await asyncio.sleep(RETRY_BACKOFF_S)
        return _synthetic_preclaim_miss(run_id, asset, exc)

    result = record.get("result") or {}
    if reliable._benign_primary_miss(result):
        logger.warning(
            "CRYPTO_PARALLEL_BENIGN_PRIMARY_COOLDOWN asset=%s run_id=%s retry_backoff_s=%s reason=%s",
            asset, run_id, RETRY_BACKOFF_S,
            str((result.get("gemini") or {}).get("reason") or "")[:160],
        )
        await asyncio.sleep(RETRY_BACKOFF_S)
    return record


def install() -> None:
    safe._obtain_asset_execution = _obtain_asset_execution_with_preclaim_retry
    logger.warning(
        "CRYPTO_PRECLAIM_INFRA_RETRY_INSTALLED retry_backoff_s=%s "
        "no_claim_means_no_order=true claimed_failures_still_halt=true risk_gates_changed=false",
        RETRY_BACKOFF_S,
    )


def main() -> None:
    install()
    postincident.main()


if __name__ == "__main__":
    main()
