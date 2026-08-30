"""V5.2 crash/startup recovery hardening.

Installs the V5.2 executor patches and makes an in-flight or positively filled rescue
order part of the durable ambiguity boundary. Recovery must never place a second
automatic flatten while a prior rescue may already exist or have filled.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from src import gemini_q10_production_executor_v5_2 as v52
from src import gemini_q10_v5_recovery as recovery

ZERO = D("0")
_ORIGINAL_AUDIT = recovery.audit
_ORIGINAL_RECOVER = recovery.recover


async def audit():
    v52.install()
    report, state, yrow, nrow = await _ORIGINAL_AUDIT()
    if not report.open_state or not state:
        return report, state, yrow, nrow

    pending = state.get("pending_rescue")
    last_client_id = str(state.get("rescue_last_client_id") or "").strip()
    last_fill_raw = state.get("rescue_last_fill")

    # A durable pending rescue means the executor crossed the mutation boundary but
    # did not prove the result. Automatic recovery cannot safely infer whether that
    # FOK is absent, resting in venue history, or already filled.
    if pending:
        return replace(
            report,
            status="OPEN_STATE_PENDING_RESCUE_AMBIGUOUS",
            exact_residual=False,
            reason="durable pending_rescue is unresolved; automatic second FOK blocked",
        ), state, yrow, nrow

    # If a rescue row was reconciled with a positive fill but the process died before
    # terminal campaign state was persisted, the original-order residual alone is no
    # longer sufficient to reconstruct exposure. Leave it blocked for account audit.
    if last_client_id and last_fill_raw not in (None, ""):
        try:
            last_fill = D(str(last_fill_raw))
        except Exception:
            last_fill = None
        if last_fill is None or last_fill > ZERO:
            return replace(
                report,
                status="OPEN_STATE_PRIOR_RESCUE_RESULT_AMBIGUOUS",
                exact_residual=False,
                reason="prior rescue result changes residual reconstruction; automatic FOK blocked",
            ), state, yrow, nrow

    return report, state, yrow, nrow


def install() -> None:
    v52.install()
    recovery.audit = audit


async def recover() -> dict[str, Any]:
    install()
    return await _ORIGINAL_RECOVER()


if __name__ == "__main__":
    import json
    result = asyncio.run(recover())
    print("GEMINI_Q10_V5_2_RECOVERY=" + json.dumps(result, separators=(",", ":"), default=str))
