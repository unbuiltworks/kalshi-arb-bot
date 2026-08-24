"""Execution-first same-Gemini q=1 supervisor v3.

Every idle cycle checks immediate same-event FOK/FOK structural arbitrage first,
then evaluates one flow-qualified sticky maker. Maker client IDs are unique across
process restarts so exchange recovery cannot collide with an earlier q=1 canary.

Safety remains unchanged: same-event/source/expiry certification, exact Gemini
fees, >=3c locked floor, q=1 only, one unresolved LIVE crypto reservation, and
fail-closed recovery on any ambiguous order/fill state.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from src import gemini_same_venue_crypto_account_gate as account
from src import gemini_same_venue_crypto_flow_v2 as flow
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src import gemini_same_venue_crypto_taker_q1_hardened as taker

_RUN_NONCE = uuid.uuid4().hex[:4]

# These statuses are authoritative no-fill / no-mutation outcomes. Everything else
# freezes the campaign for review/reconciliation.
MAKER_CONTINUE = {
    "MAKER_CANCELED_ZERO_FILL",
    "PRE_POST_FLOOR_LOST",
    "NO_FRESH_ROBUST_FRONTIER",
    "FRESH_TOP_NOT_MAKER_ACTIONABLE",
    "POST_ACK_STATE_RECOVERED_ZERO_FILL",
    "EXCEPTION_RECOVERED_ZERO_FILL",
    "MAKER_REJECTED_ZERO_FILL",
}
TAKER_CONTINUE = {
    "NO_TAKER_TAKER_3C",
    "FRESH_TAKER_PAIR_LOST",
    "FIRST_FOK_ZERO_FILL",
}


def unique_maker_canary_id() -> str:
    """Short per-process canary seed that preserves the '-mk' suffix under 36 chars."""
    raw = live.base._clean_id(os.getenv(live.base.CANARY_ID_ENV, ""))
    return f"{raw[:4]}{_RUN_NONCE}"[:8]


async def _attempt_unique(candidate: live.base.Candidate, attempt_no: int) -> dict[str, Any]:
    """Reuse the hardened maker lifecycle with a restart-unique deterministic ID."""
    env_name = live.base.CANARY_ID_ENV
    original = os.environ.get(env_name)
    os.environ[env_name] = unique_maker_canary_id()
    try:
        return await live.attempt_wsfirst(candidate, attempt_no)
    finally:
        if original is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = original


async def _startup_recovery() -> tuple[bool, str | None]:
    # Exchange account state wins over the ledger. Remove zero-fill orphan makers and
    # hard-halt on any unhedged fill before touching the current campaign.
    try:
        clear = await account.account_gate()
    except Exception as exc:
        live.emit("EXECUTION_V3_ACCOUNT_GATE_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                  order_entry=False, realized=False)
        return False, "ACCOUNT_GATE_EXCEPTION_HARD_HALT"
    if not clear:
        return False, "ACCOUNT_GATE_HARD_HALT"

    # A prior taker reservation needs its own semantic leg recovery before the maker
    # recovery code sees it as an unrelated crypto strategy.
    try:
        taker_recovery = await taker.reconcile_unresolved_taker()
    except Exception as exc:
        live.emit("EXECUTION_V3_TAKER_RECOVERY_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                  order_entry=False, realized=False)
        return False, "TAKER_RECOVERY_EXCEPTION_HARD_HALT"
    if taker_recovery is False:
        return False, "UNRESOLVED_TAKER_HARD_HALT"

    try:
        maker_recovery = await live.reconcile_previous_unresolved()
    except Exception as exc:
        live.emit("EXECUTION_V3_MAKER_RECOVERY_EXCEPTION", error=f"{type(exc).__name__}:{str(exc)[:180]}",
                  order_entry=False, realized=False)
        return False, "MAKER_RECOVERY_EXCEPTION_HARD_HALT"
    if not maker_recovery:
        return False, "UNRESOLVED_CRYPTO_HARD_HALT"
    return True, None


async def run() -> dict[str, Any]:
    # Keep the 100ms public depth and flow-qualified candidate selection from v2.
    live.base._book_stream = live.depth._depth_book_stream
    live.discover_flow_ranked = flow.discover_flow_ranked_v2

    if not live.base.live_armed():
        live.base.discover = flow.discover_flow_ranked_v2
        return await live.base.run()

    clear, reason = await _startup_recovery()
    if not clear:
        return {"status": reason, "order_entry": False, "realized": False}

    terms = await live.base._gemini_terms_ok()
    available = await live.base._gemini_available_usd()
    if not terms or available < live.base.MIN_AVAILABLE_USD:
        live.emit("EXECUTION_V3_PREFLIGHT_BLOCKED", terms=bool(terms),
                  funded=available >= live.base.MIN_AVAILABLE_USD,
                  order_entry=False, realized=False)
        return {"status": "PREFLIGHT_BLOCKED", "terms": bool(terms),
                "funded_ge_1_05": available >= live.base.MIN_AVAILABLE_USD,
                "order_entry": False, "realized": False}

    attempt = 0
    while True:
        # Immediate deterministic pair always gets first look. This repeats on every
        # idle cycle instead of only once at process startup.
        taker_result = await taker.maybe_execute()
        live.emit("TAKER_TAKER_RESULT", **taker_result)
        taker_status = str(taker_result.get("status") or "")
        if taker_status not in TAKER_CONTINUE:
            return taker_result

        candidate, diag = await flow.discover_flow_ranked_v2()
        if candidate is None:
            live.emit("WAITING_V3", diagnostic=diag, next_action="RECHECK_TAKER_THEN_MAKER",
                      order_entry=False, realized=False)
            await asyncio.sleep(live.base.DISCOVERY_PAUSE_SECONDS)
            continue

        attempt += 1
        live.emit("CANDIDATE_V3", attempt=attempt, maker_run_nonce=_RUN_NONCE,
                  candidate=candidate.as_dict(), order_entry=False, realized=False)
        result = await _attempt_unique(candidate, attempt)
        live.emit("ATTEMPT_RESULT_V3", attempt=attempt, candidate=candidate.as_dict(), **result)
        status = str(result.get("status") or "")
        if status in MAKER_CONTINUE:
            # Re-run the immediate taker scan before considering another maker.
            await asyncio.sleep(0.5)
            continue
        return {
            "armed": True,
            "target_per_contract": str(live.TARGET),
            "attempt": attempt,
            "candidate": candidate.as_dict(),
            "result": result,
            "order_entry": bool(result.get("order_entry")),
            "realized": False,
        }


if __name__ == "__main__":
    asyncio.run(run())
