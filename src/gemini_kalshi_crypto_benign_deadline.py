"""Fail-closed classification for bounded-campaign deadline expiry.

A campaign deadline is benign only when the order-capable child reached the arm
expiry with no persistent attempt claim and no result. That proves no venue order
was submitted by the child. Any claim-without-result remains ambiguous and must
fail closed. This module only wraps the parallel supervisor's wait primitive; it
does not alter pricing, quantity, settlement, reservation, or live-order gates.
"""
from __future__ import annotations

import json
from typing import Any

from src import gemini_kalshi_crypto_parallel_campaign_safe as safe
from src import gemini_kalshi_sol_live_canary_v2 as v2

_INSTALLED = False
_ORIGINAL = None


def _deadline_record_if_clean(run_id: str) -> dict[str, Any]:
    _arm, claim, result_path = v2._paths(run_id)
    if result_path.is_file():
        return json.loads(result_path.read_text(encoding="utf-8"))
    if claim.is_file():
        raise RuntimeError(
            f"campaign deadline reached with attempt claim but no result run_id={run_id}"
        )
    return {
        "completed_at_utc": None,
        "result": {
            "outcome": "PRIMARY_NOT_FILLED",
            "hard_halt_required": False,
            "gemini": {
                "filled_quantity": "0",
                "status": "EXPIRED_NO_CLAIM",
                "reason": "CAMPAIGN_ARM_DEADLINE_NO_CLAIM",
            },
            "kalshi": None,
        },
        "deadline_expiry_no_claim": True,
        "order_entry": False,
    }


def install() -> None:
    """Install the deadline classifier once for the parallel live supervisor."""
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    _ORIGINAL = safe._obtain_asset_execution

    async def wrapped(*args, **kwargs):
        run_id = str(args[0] if args else kwargs["run_id"])
        try:
            return await _ORIGINAL(*args, **kwargs)
        except TimeoutError:
            return _deadline_record_if_clean(run_id)

    safe._obtain_asset_execution = wrapped
    _INSTALLED = True
