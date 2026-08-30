"""Crash/startup recovery boundary for Gemini adaptive maker V5.3.

V5.3 refuses automatic recovery when the durable state cannot prove the exact
remaining directional quantity. In addition to V5.2's pending-FOK ambiguity gate,
it blocks unresolved/positively-filled maker-grace orders, foreign release state,
and any residual larger than the campaign's selected adaptive quantity.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_q10_v5_2_recovery as recovery52

ZERO = D("0")
MAX_Q = D("10")
V53_CLASSIFICATION = "LIVE_ADAPTIVE_MAKER_V5_3_OPEN_NOT_REALIZED"
_ORIGINAL_AUDIT = recovery52.audit
_ORIGINAL_RECOVER = recovery52.recover


def _quantity_from_state(state: dict[str, Any]) -> D | None:
    raw = state.get("quantity")
    try:
        q = D(str(raw))
    except Exception:
        return None
    if not (ZERO < q <= MAX_Q):
        return None
    return q


async def audit():
    v53.install()
    report, state, yrow, nrow = await _ORIGINAL_AUDIT()
    if not report.open_state or not state:
        return report, state, yrow, nrow

    classification = str(state.get("classification") or "").strip()
    if classification != V53_CLASSIFICATION:
        return replace(
            report,
            status="OPEN_STATE_FOREIGN_RELEASE_BLOCKED",
            exact_residual=False,
            reason=f"open state classification {classification or 'MISSING'} is not V5.3 adaptive maker",
        ), state, yrow, nrow

    selected_q = _quantity_from_state(state)
    if selected_q is None:
        return replace(
            report,
            status="OPEN_STATE_SELECTED_QUANTITY_INVALID",
            exact_residual=False,
            reason="durable V5.3 selected quantity is missing or outside (0,10]",
        ), state, yrow, nrow
    if report.residual_quantity > selected_q:
        return replace(
            report,
            status="OPEN_STATE_RESIDUAL_EXCEEDS_SELECTED_QUANTITY",
            exact_residual=False,
            reason=f"reconciled residual {report.residual_quantity} exceeds selected quantity {selected_q}",
        ), state, yrow, nrow

    pending_grace = state.get("pending_grace")
    if pending_grace:
        return replace(
            report,
            status="OPEN_STATE_PENDING_GRACE_AMBIGUOUS",
            exact_residual=False,
            reason="durable pending_grace is unresolved; automatic rescue layering blocked",
        ), state, yrow, nrow

    grace_client_id = str(state.get("grace_last_client_id") or "").strip()
    grace_fill_raw = state.get("grace_last_fill")
    if grace_client_id and grace_fill_raw not in (None, ""):
        try:
            grace_fill = D(str(grace_fill_raw))
        except Exception:
            grace_fill = None
        # A positive maker-grace fill changes the original YES/NO residual cost
        # basis. If the process died before terminal rescue state, do not reconstruct
        # exposure from original order rows alone.
        if grace_fill is None or grace_fill > ZERO:
            return replace(
                report,
                status="OPEN_STATE_PRIOR_GRACE_RESULT_AMBIGUOUS",
                exact_residual=False,
                reason="prior maker-grace fill changes residual reconstruction; automatic FOK blocked",
            ), state, yrow, nrow

    return report, state, yrow, nrow


def install() -> None:
    v53.install()
    # recovery52.install() refers to recovery52.audit dynamically. Replacing that
    # module attribute makes legacy recover() call this stricter audit boundary.
    recovery52.audit = audit
    recovery52.install()


async def recover() -> dict[str, Any]:
    install()
    return await _ORIGINAL_RECOVER()


if __name__ == "__main__":
    import json
    result = asyncio.run(recover())
    print("GEMINI_ADAPTIVE_MAKER_V5_3_RECOVERY=" + json.dumps(result, separators=(",", ":"), default=str))
