"""Execution-first same-Gemini crypto ladder orchestrator.

Order of operations on every live start:
1) Gemini account-authoritative orphan/fill audit and orphan cancellation.
2) Command Center reservation reconciliation for the exact prior q1 maker.
3) Immediate same-event q1 FOK/FOK structural arb if fresh asks lock >=3c.
4) Otherwise one sticky q1 maker selected by recent contract flow + live tape.

The older Gemini<->Kalshi basis lane is intentionally not touched here.
"""
from __future__ import annotations

import asyncio
from typing import Any

from src import gemini_same_venue_crypto_account_gate as account
from src import gemini_same_venue_crypto_flow_v2 as flow
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src import gemini_same_venue_crypto_taker_q1 as taker


async def run() -> dict[str, Any]:
    live.discover_flow_ranked = flow.discover_flow_ranked_v2
    if not live.base.live_armed():
        return await live.run()

    try:
        clear = await account.account_gate()
    except Exception as exc:
        live.emit("EXECUTION_V2_ACCOUNT_GATE_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                  order_entry=False, realized=False)
        return {"status": "ACCOUNT_GATE_EXCEPTION_HARD_HALT", "order_entry": False, "realized": False}
    if not clear:
        return {"status": "ACCOUNT_GATE_HARD_HALT", "order_entry": False, "realized": False}

    try:
        reconciled = await live.reconcile_previous_unresolved()
    except Exception as exc:
        live.emit("EXECUTION_V2_LEDGER_RECOVERY_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                  order_entry=False, realized=False)
        return {"status": "LEDGER_RECOVERY_EXCEPTION_HARD_HALT", "order_entry": False, "realized": False}
    if not reconciled:
        return {"status": "UNRESOLVED_CRYPTO_HARD_HALT", "order_entry": False, "realized": False}

    terms = await live.base._gemini_terms_ok()
    available = await live.base._gemini_available_usd()
    if not terms or available < live.base.MIN_AVAILABLE_USD:
        live.emit("EXECUTION_V2_PREFLIGHT_BLOCKED", terms=bool(terms), funded=available >= live.base.MIN_AVAILABLE_USD,
                  order_entry=False, realized=False)
        return {"status": "PREFLIGHT_BLOCKED", "terms": bool(terms),
                "funded_ge_1_05": available >= live.base.MIN_AVAILABLE_USD, "order_entry": False, "realized": False}

    taker_result = await taker.maybe_execute()
    live.emit("TAKER_TAKER_RESULT", **taker_result)
    taker_status = str(taker_result.get("status") or "")
    if taker_status == "BOTH_FILLED_RECONCILED_NOT_SETTLED":
        return taker_result
    if taker_status not in {"NO_TAKER_TAKER_3C", "FRESH_TAKER_PAIR_LOST", "FIRST_FOK_ZERO_FILL"}:
        # Any first-leg fill, ambiguity, flatten, or exception freezes the campaign.
        return taker_result

    # No immediate deterministic taker pair filled. Fall back to exactly one sticky
    # maker exposure. live.run repeats the ledger recovery defensively before mutation.
    return await live.run()


if __name__ == "__main__":
    asyncio.run(run())
