"""Hardened wrapper around the Gemini q10 production candidate.

Adds production-critical protections without changing entry economics:
1. durable one-use state so a restart cannot create a second campaign under the
   same approval id;
2. transient missing depth never cancels a live complement and post-cancel prices
   are always revalidated before repost;
3. WebSocket-first fill/cancel reconciliation so the hot complement loop does not
   burn Gemini's epoch-second private REST nonce budget.

Private REST is now only a slow fallback for a missed WebSocket fill. Normal fill,
reprice, and cancel handling stays on the authenticated trading WebSocket. Live
mutation still requires the exact dual-key arm implemented by v1. No taker rescue
and no automatic restart are introduced here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from src import gemini_q10_production_candidate as v1

ZERO = D("0")
Q = D("10")
STATE_DIR_ENV = "GEMINI_Q10_PRODUCTION_STATE_DIR"
STATUS_RECONCILE_SECONDS = 8.0
CANCEL_RACE_LISTEN_SECONDS = 0.25


def _state_path(run_id: str) -> Path:
    root = Path(os.getenv(STATE_DIR_ENV, "/state"))
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in run_id)[:80]
    return root / f"q10-production-{safe}.json"


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(dict(payload), sort_keys=True, default=str) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o640)
    try:
        os.write(fd, raw.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _replace_state(path: Path, **updates: Any) -> None:
    current = json.loads(path.read_text(encoding="utf-8"))
    current.update(updates)
    tmp = path.with_suffix(".tmp")
    raw = json.dumps(current, sort_keys=True, default=str) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(fd, raw.encode("utf-8")); os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _fresh_top_or_none(symbol: str):
    return v1.base.fresh_top(symbol)


async def _top_or_none(symbol: str):
    try:
        return await _fresh_top_or_none(symbol)
    except RuntimeError as exc:
        text = str(exc).lower()
        if "fresh" in text and "depth" in text:
            return None
        raise


def _event_client_id(item: Mapping[str, Any]) -> str:
    return str(item.get("c") or item.get("clientOrderId") or item.get("client_order_id") or "")


def _event_filled(item: Mapping[str, Any]) -> D:
    for key in ("Z", "filledQuantity", "executedQuantity", "filled_quantity"):
        value = item.get(key)
        if value not in (None, ""):
            try:
                return D(str(value))
            except Exception:
                return ZERO
    return ZERO


async def _wait_fill_event(maker: v1.base.GeminiMakerSession, cid: str, timeout: float) -> D:
    """Return cumulative fill for cid from WS events without losing other order events."""
    events = getattr(maker, "events", None)
    if not cid or timeout <= 0 or events is None:
        return ZERO
    deadline = time.monotonic() + timeout
    deferred: list[Any] = []
    best = ZERO
    try:
        while time.monotonic() < deadline:
            try:
                item = await asyncio.wait_for(
                    events.get(), timeout=max(0.001, deadline - time.monotonic())
                )
            except asyncio.TimeoutError:
                break
            if not isinstance(item, Mapping):
                deferred.append(item)
                continue
            if _event_client_id(item) != cid:
                deferred.append(item)
                continue
            best = max(best, _event_filled(item))
            if best >= Q:
                break
    finally:
        for item in deferred:
            events.put_nowait(item)
    return best


def _known_order_id(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    value = row.get("orderId") or row.get("order_id") or row.get("id")
    return None if value in (None, "") else str(value)


async def _cancel_known(maker: v1.base.GeminiMakerSession, *, cid: str, symbol: str,
                        row: Mapping[str, Any] | None) -> tuple[dict[str, Any], D, float]:
    """Cancel a known order over WS and detect race fills from WS; never REST-poll."""
    if not _known_order_id(row):
        return {"method":"NO_KNOWN_ORDER_ID","ok":False}, ZERO, 0.0
    started = time.monotonic()
    result = await v1.base._cancel_maker(maker, row=row)
    ms = (time.monotonic() - started) * 1000.0
    race_fill = await _wait_fill_event(maker, cid, CANCEL_RACE_LISTEN_SECONDS)
    return result, race_fill, ms


def _current_price_safe(*, filled_outcome: str, filled_price: D, current_price: D,
                        yes_bid: D, yes_ask: D, seconds_to_expiry: float,
                        increment: D) -> bool:
    """True only if current complement remains maker-passive and meets trade floor."""
    try:
        floor = v1.risk.completion.time_dependent_total_profit_floor(seconds_to_expiry)
        missing = "NO" if filled_outcome.upper() == "YES" else "YES"
        passive = v1.risk.completion.maker_passive_ceiling(
            missing_outcome=missing, yes_bid=yes_bid, yes_ask=yes_ask, increment=increment,
        )
        profit = v1.risk.completion.completed_pair_profit_q10(
            filled_price=filled_price, complement_price=current_price,
        )
        return D(str(current_price)) <= D(str(passive)) and profit >= floor
    except ValueError:
        return False


async def _dynamic_complete_v2(maker: v1.base.GeminiMakerSession, *, event: v1.base.Event,
                               run_id: str, filled_outcome: str, filled_price: D,
                               complement_id: str, complement_price: D,
                               complement_row: Mapping[str, Any] | None) -> dict[str, Any]:
    missing = "NO" if filled_outcome.upper() == "YES" else "YES"
    current_id = complement_id
    current_price = D(str(complement_price))
    current_row = complement_row
    reprices = 0
    deadline = time.monotonic() + max(0.0, event.seconds_to_expiry - v1.STOP_BEFORE_EXPIRY_SECONDS)
    next_check = 0.0
    next_rest_reconcile = time.monotonic() + STATUS_RECONCILE_SECONDS

    while time.monotonic() < deadline:
        wait_for = min(0.10, max(0.001, deadline - time.monotonic()))
        fill = await _wait_fill_event(maker, current_id, wait_for) if current_id else ZERO
        if fill == Q:
            return {"status":"PAIRED_BY_DYNAMIC_COMPLEMENT","paired":True,"reprices":reprices,
                    "missing_outcome":missing,"final_price":str(current_price),"fill_source":"WS"}
        if fill > ZERO:
            return {"status":"FRACTIONAL_COMPLEMENT_FILL","paired":False,"hard_halt":True,
                    "reprices":reprices,"fill":str(fill),"fill_source":"WS"}

        now = time.monotonic()
        if current_id and now >= next_rest_reconcile:
            next_rest_reconcile = now + STATUS_RECONCILE_SECONDS
            row = await v1.base._order_by_client(current_id, event.symbol)
            rest_fill = v1.base._filled(row)
            if row is not None:
                current_row = row
            if rest_fill == Q:
                return {"status":"PAIRED_BY_DYNAMIC_COMPLEMENT","paired":True,"reprices":reprices,
                        "missing_outcome":missing,"final_price":str(current_price),"fill_source":"REST_FALLBACK"}
            if rest_fill > ZERO:
                return {"status":"FRACTIONAL_COMPLEMENT_FILL","paired":False,"hard_halt":True,
                        "reprices":reprices,"fill":str(rest_fill),"fill_source":"REST_FALLBACK"}

        if now < next_check:
            continue
        next_check = now + v1.REPRICE_INTERVAL_SECONDS
        if event.seconds_to_expiry < v1.STOP_BEFORE_EXPIRY_SECONDS:
            break

        top = await _top_or_none(event.symbol)
        if top is None:
            v1.emit("COMPLEMENT_DEPTH_UNAVAILABLE", action="KEEP_EXISTING_COMPLEMENT_AND_RETRY",
                    complement_resting=bool(current_id), order_entry=False)
            continue
        bid, _, ask, _ = top
        try:
            plan = v1.risk.completion_decision(
                filled_outcome=filled_outcome, filled_price=filled_price,
                current_complement_price=current_price, yes_bid=bid, yes_ask=ask,
                seconds_to_expiry=event.seconds_to_expiry, increment=event.price_increment,
            )
        except ValueError:
            break
        target = plan.get("target_price")
        target_d = D(str(target)) if target is not None else None
        if plan.get("action") != "REPRICE_MAKER_COMPLEMENT" or target_d is None or target_d <= current_price:
            continue
        if reprices >= v1.MAX_REPRICES:
            continue

        cancel, race_fill, cancel_ms = await v1._cancel(
            maker, cid=current_id, symbol=event.symbol, row=current_row,
        )
        v1.emit("COMPLEMENT_REPRICE_CANCEL", cid=current_id, cancel=cancel,
                cancel_ms=round(cancel_ms,3), race_fill=str(race_fill), target=str(target_d), order_entry=True)
        if race_fill == Q:
            return {"status":"PAIRED_DURING_REPRICE_CANCEL","paired":True,"reprices":reprices}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"status":"AMBIGUOUS_REPRICE_CANCEL","paired":False,"hard_halt":True,
                    "reprices":reprices,"race_fill":str(race_fill)}
        current_id = ""
        current_row = None

        post_cancel_top = None
        for _ in range(10):
            post_cancel_top = await _top_or_none(event.symbol)
            if post_cancel_top is not None:
                break
            await asyncio.sleep(0.05)
        if post_cancel_top is None:
            v1.emit("COMPLEMENT_POST_CANCEL_DEPTH_UNAVAILABLE", action="WAIT_DARK_NO_BLIND_REPOST",
                    order_entry=False)
            continue
        bid2, _, ask2, _ = post_cancel_top
        repost_price: D | None = None
        try:
            plan2 = v1.risk.completion_decision(
                filled_outcome=filled_outcome, filled_price=filled_price,
                current_complement_price=current_price, yes_bid=bid2, yes_ask=ask2,
                seconds_to_expiry=event.seconds_to_expiry, increment=event.price_increment,
            )
            t2 = plan2.get("target_price")
            if plan2.get("action") == "REPRICE_MAKER_COMPLEMENT" and t2 is not None:
                repost_price = D(str(t2))
        except ValueError:
            repost_price = None
        if repost_price is None and _current_price_safe(
            filled_outcome=filled_outcome, filled_price=filled_price, current_price=current_price,
            yes_bid=bid2, yes_ask=ask2, seconds_to_expiry=event.seconds_to_expiry,
            increment=event.price_increment,
        ):
            repost_price = current_price
        if repost_price is None:
            v1.emit("COMPLEMENT_REPOST_WITHHELD", reason="NO_FRESH_SAFE_MAKER_PRICE", order_entry=False)
            continue

        reprices += 1
        new_id = v1.client_id(run_id, missing, reprices)
        ack, row2, ack_ms = await v1._post(
            maker, symbol=event.symbol, outcome=missing, price=repost_price, cid=new_id,
        )
        v1.emit("COMPLEMENT_REPOST", cid=new_id, outcome=missing, price=str(repost_price),
                ack_status=ack.get("status"), ack_ms=round(ack_ms,3), order_entry=True)
        if int(ack.get("status",0)) != 200:
            return {"status":"COMPLEMENT_REPOST_REJECTED","paired":False,"hard_halt":True,
                    "reprices":reprices}
        current_id, current_row, current_price = new_id, row2, repost_price
        next_rest_reconcile = time.monotonic() + STATUS_RECONCILE_SECONDS

    if current_id:
        cancel, race_fill, cancel_ms = await v1._cancel(
            maker, cid=current_id, symbol=event.symbol, row=current_row
        )
        v1.emit("COMPLEMENT_TIMEOUT_CANCEL", cid=current_id, cancel=cancel,
                cancel_ms=round(cancel_ms,3), race_fill=str(race_fill), order_entry=True)
        if race_fill == Q:
            return {"status":"PAIRED_AT_TIMEOUT","paired":True,"reprices":reprices}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"status":"AMBIGUOUS_TIMEOUT_CANCEL","paired":False,"hard_halt":True,
                    "reprices":reprices,"race_fill":str(race_fill)}
    return {"status":"DYNAMIC_COMPLETION_EXPIRED_ONE_SIDED","paired":False,
            "hard_halt":False,"reprices":reprices}


async def run() -> None:
    if not v1.live_armed():
        await v1.run()
        return
    run_id = os.environ[v1.RUN_ID_ENV]
    path = _state_path(run_id)
    if path.exists():
        raise RuntimeError(f"one-use q10 production approval already consumed: {run_id}")
    _write_once(path, {
        "run_id": run_id,
        "classification": "LIVE_Q10_PRODUCTION_CANDIDATE_NOT_REALIZED",
        "status": "APPROVAL_CONSUMED_PRE_MUTATION",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "order_entry": False,
        "realized_pnl": None,
        "rewards_realized": None,
    })
    original_dynamic = v1._dynamic_complete
    original_cancel = v1._cancel
    v1._dynamic_complete = _dynamic_complete_v2
    v1._cancel = _cancel_known
    try:
        await v1.run()
    except Exception as exc:
        _replace_state(path, status="FAILED_OR_HALTED", ended_at_utc=datetime.now(timezone.utc).isoformat(),
                       error_type=type(exc).__name__)
        raise
    else:
        _replace_state(path, status="PROCESS_COMPLETED", ended_at_utc=datetime.now(timezone.utc).isoformat())
    finally:
        v1._dynamic_complete = original_dynamic
        v1._cancel = original_cancel


if __name__ == "__main__":
    asyncio.run(run())