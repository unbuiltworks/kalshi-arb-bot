"""Independent stale-heartbeat watchdog for Gemini selective maker V5.6.

Never starts new exposure. On a stale exact V5.6 quote it may cancel only the
durable quote id. If that exact row proves a maker fill, and no grace/FOK mutation
is pending, it may use V5.5's bounded FOK once. Ambiguity blocks layering.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src import gemini_adaptive_maker_executor_v5_5 as v55
from src import gemini_q10_v5_2_deadman as legacy_deadman
from src import gemini_q10_v5_recovery as legacy_recovery
from src.gemini_prediction_depth_cache_v5_3 import GeminiDepth5Cache
from src import gemini_selective_maker_v5_6 as v56

ZERO = D("0")
STALE_DEFAULT = 15.0
POLL_DEFAULT = 0.25


def heartbeat_age_seconds(state: dict | None, *, now: datetime | None = None) -> float | None:
    if not state:
        return None
    raw = state.get("heartbeat_at_utc")
    if not raw:
        return float("inf")
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - ts).total_seconds())


def stale_seconds() -> float:
    value = float(os.getenv(legacy_deadman.STALE_SECONDS_ENV, str(STALE_DEFAULT)))
    if not 0.5 <= value <= 60.0:
        raise RuntimeError("invalid V5.6 watchdog stale seconds")
    return value


def poll_seconds() -> float:
    value = float(os.getenv(legacy_deadman.POLL_SECONDS_ENV, str(POLL_DEFAULT)))
    if not 0.05 <= value <= 5.0:
        raise RuntimeError("invalid V5.6 watchdog poll seconds")
    return value


def armed() -> bool:
    aid = os.getenv(v53.v5.v1.ARM_ID_ENV, "").strip()
    return bool(v53.v5.live_armed() and aid
                and os.getenv(legacy_deadman.WATCHDOG_ARM_ENV, "").strip() == aid
                and os.getenv(legacy_recovery.RECOVERY_ARM_ENV, "").strip() == aid)


def _close_state(state: dict, status: str, **fields) -> None:
    v53.v5._heartbeat(state, stage=status, open=False,
                      deadman_closed_at_utc=datetime.now(timezone.utc).isoformat(), **fields)


async def check_once() -> dict:
    v55.install()
    state = v53.v5.v1._read_state()
    if not state or not bool(state.get("open")):
        return {"status": "IDLE_NO_OPEN_CAMPAIGN", "intervened": False}
    if str(state.get("classification") or "") != v56.CLASSIFICATION:
        return {"status": "FOREIGN_OPEN_STATE_BLOCKED", "intervened": False}
    age = heartbeat_age_seconds(state)
    if age is not None and age <= stale_seconds():
        return {"status": "HEALTHY_HEARTBEAT", "intervened": False, "heartbeat_age_s": round(age, 3)}
    if not armed():
        return {"status": "STALE_NOT_FULLY_ARMED", "intervened": False, "heartbeat_age_s": age}

    flat = await v53.v5.v1._flat_snapshot()
    if not flat["orders"] and not flat["positions"]:
        _close_state(state, "DEADMAN_ACCOUNT_ALREADY_FLAT", flat=flat)
        return {"status": "DEADMAN_ACCOUNT_ALREADY_FLAT", "intervened": False, "heartbeat_age_s": age}
    if state.get("pending_grace") or state.get("pending_rescue"):
        return {"status": "STALE_PENDING_MUTATION_AMBIGUOUS_BLOCKED", "intervened": False,
                "heartbeat_age_s": age, "has_pending_grace": bool(state.get("pending_grace")),
                "has_pending_rescue": bool(state.get("pending_rescue"))}
    if str(state.get("status") or "") == "SETTLEMENT_WAIT":
        return {"status": "STALE_MATCHED_WAITING_SETTLEMENT", "intervened": False,
                "heartbeat_age_s": age, "flat": flat}

    symbol = str(state.get("symbol") or "")
    quote_id = str(state.get("quote_id") or "")
    trade_id = str(state.get("trade_id") or "")
    outcome = str(state.get("selected_outcome") or "").upper()
    if not symbol or not quote_id or not trade_id or outcome not in {"YES", "NO"}:
        return {"status": "STALE_STATE_CONTRACT_INVALID", "intervened": False}

    maker = v53.v5.v1.base.GeminiMakerSession()
    cache_stop = None
    cache_task = None
    try:
        await maker.connect()
        row = await v53._lookup_exact(quote_id, symbol)
        if row is None:
            flat2 = await v53.v5.v1._flat_snapshot()
            if not flat2["orders"] and not flat2["positions"]:
                _close_state(state, "DEADMAN_ROW_MISSING_ACCOUNT_FLAT", flat=flat2)
                return {"status": "DEADMAN_ROW_MISSING_ACCOUNT_FLAT", "intervened": False}
            return {"status": "DEADMAN_ROW_MISSING_ACCOUNT_NOT_FLAT_BLOCKED", "intervened": False, "flat": flat2}

        resolved, filled, cancel = await v53._cancel_one_exact(maker, row=row, client_id=quote_id, symbol=symbol)
        if resolved is None:
            return {"status": "DEADMAN_CANCEL_RECONCILE_AMBIGUOUS", "intervened": True, "cancel": cancel}
        selected_q = D(str(state.get("quantity") or "0"))
        if filled < ZERO or filled > selected_q or selected_q <= ZERO:
            return {"status": "DEADMAN_FILL_BOUND_INVALID", "intervened": True,
                    "filled": str(filled), "selected_q": str(selected_q)}
        if filled == ZERO:
            flat3 = await v53.v5.v1._flat_snapshot()
            if not flat3["orders"] and not flat3["positions"]:
                _close_state(state, "DEADMAN_CANCELLED_ZERO_FILL_FLAT", flat=flat3)
                return {"status": "DEADMAN_CANCELLED_ZERO_FILL_FLAT", "intervened": True, "cancel": cancel}
            return {"status": "DEADMAN_ZERO_FILL_BUT_ACCOUNT_NOT_FLAT_BLOCKED", "intervened": True, "flat": flat3}

        filled_price = v53.v5._fill_price(resolved, D(str(state.get("quote_price") or "0")))
        increment = D(str(state.get("price_increment") or "0.01"))
        cache = GeminiDepth5Cache(symbol, stale_ms=750)
        cache_stop = asyncio.Event()
        cache_task = asyncio.create_task(cache.run(cache_stop))
        await cache.wait_fresh(timeout=3.0, max_age_ms=v53.v5.BOOK_MAX_AGE_MS)
        result = await v55.bounded_fok_v55(
            maker, cache=cache, symbol=symbol, filled_outcome=outcome, filled_price=filled_price,
            quantity=filled, increment=increment, trade_id=trade_id, state=state)
        if result.get("hard_halt"):
            return {"status": "DEADMAN_RESCUE_HARD_HALT", "intervened": True, "rescue": result}
        action = str((result.get("route") or {}).get("action") or "")
        if result.get("flattened") and action == "UNWIND":
            for _ in range(15):
                flat4 = await v53.v5.v1._flat_snapshot()
                if not flat4["orders"] and not flat4["positions"]:
                    _close_state(state, "DEADMAN_UNWIND_FLAT", flat=flat4, deadman_rescue=result)
                    return {"status": "DEADMAN_UNWIND_FLAT", "intervened": True, "rescue": result}
                await asyncio.sleep(0.2)
            return {"status": "DEADMAN_UNWIND_ACCOUNT_NOT_FLAT_BLOCKED", "intervened": True, "rescue": result}
        if result.get("flattened") and action == "COMPLETE":
            v53.v5._heartbeat(state, stage="SETTLEMENT_WAIT", open=True, deadman_rescue=result)
            return {"status": "DEADMAN_COMPLETED_PAIR_WAIT_SETTLEMENT", "intervened": True, "rescue": result}
        return {"status": "DEADMAN_RESCUE_NONTERMINAL_BLOCKED", "intervened": True, "rescue": result}
    finally:
        try:
            if maker.ws is not None:
                await maker.request("order.cancel_session", {}, timeout=1.0)
        except Exception:
            pass
        try:
            await maker.close()
        except Exception:
            pass
        if cache_stop is not None:
            cache_stop.set()
        if cache_task is not None:
            cache_task.cancel()
            await asyncio.gather(cache_task, return_exceptions=True)


async def run() -> None:
    last_status = None
    last_print = 0.0
    while True:
        try:
            result = await check_once()
        except Exception as exc:
            result = {"status": "WATCHDOG_CHECK_ERROR", "intervened": False,
                      "error_type": type(exc).__name__, "error_message": str(exc)[:240]}
        now = asyncio.get_running_loop().time()
        status = result.get("status")
        if status != last_status or now - last_print >= 30.0:
            print("GEMINI_SELECTIVE_MAKER_V5_6_DEADMAN=" + json.dumps(result, separators=(",", ":"), default=str), flush=True)
            last_status, last_print = status, now
        if status in {"IDLE_NO_OPEN_CAMPAIGN", "DEADMAN_ACCOUNT_ALREADY_FLAT",
                      "DEADMAN_ROW_MISSING_ACCOUNT_FLAT", "DEADMAN_CANCELLED_ZERO_FILL_FLAT",
                      "DEADMAN_UNWIND_FLAT", "DEADMAN_COMPLETED_PAIR_WAIT_SETTLEMENT"}:
            return
        await asyncio.sleep(poll_seconds())


if __name__ == "__main__":
    asyncio.run(run())
