"""Safety refinement for q1 dynamic complement execution.

V1 proved the bounded order-chain/reconciliation shape. V2 changes one important
behavior: after the first fill, the existing complement remains resting until a
strictly better edge-safe maker target is available. We cancel-before-repost only
when we already have a better target. If the book moves during cancellation, we
revalidate and either repost the last safe price or temporarily wait without
crossing. Taker execution remains disabled.

Only venue-accepted replacement client IDs are appended to the durable order chain.
A rejected client ID must never be treated as an authoritative terminal order.

Transient loss of fresh public depth is expected infrastructure noise, not a reason
to abandon a live post-fill rescue. While the existing complement is still resting,
V2 leaves it untouched and retries on the next bounded reprice check. If depth is
lost after a deliberate cancel, V2 waits without posting blindly and resumes only
after a fresh edge-safe book is available. The normal rescue deadline remains the
hard bound in both cases.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import time
from typing import Any, Mapping

from src import gemini_short_crypto_dynamic_complement_q1 as v1
from src import gemini_incentive_mm_dynamic_complement as policy

ENTRY_PAIR_EDGE = v1.ENTRY_PAIR_EDGE
FINAL_PAIR_EDGE = v1.FINAL_PAIR_EDGE
INITIAL_WAIT_SECONDS = v1.INITIAL_WAIT_SECONDS
RESCUE_SECONDS = v1.RESCUE_SECONDS
REPRICE_INTERVAL_SECONDS = v1.REPRICE_INTERVAL_SECONDS
MAX_REPRICES = v1.MAX_REPRICES
MIN_PRICE = v1.MIN_PRICE
MAX_PRICE = v1.MAX_PRICE
STOP_BEFORE_EXPIRY_SECONDS = v1.STOP_BEFORE_EXPIRY_SECONDS
LIVE_ENV = v1.LIVE_ENV
CANARY_ID_ENV = v1.CANARY_ID_ENV
CANARY_ARM_ENV = v1.CANARY_ARM_ENV

_TRANSIENT_DEPTH_ERROR = "no fresh Gemini BTC5m depth"


def _safe_to_rest_current(*, missing: str, filled_price: D, current_price: D,
                          yes_bid: D, yes_ask: D, increment: D) -> bool:
    try:
        passive = policy.maker_passive_ceiling(
            missing_outcome=missing, yes_bid=yes_bid, yes_ask=yes_ask, increment=increment,
        )
        edge = policy.completed_pair_edge(
            filled_price=D(str(filled_price)), complement_price=D(str(current_price)),
        )
    except Exception:
        return False
    return (
        MIN_PRICE <= D(str(current_price)) <= MAX_PRICE
        and D(str(current_price)) <= passive
        and edge >= FINAL_PAIR_EDGE
    )


async def _fresh_top_or_none(
    symbol: str,
    *,
    missing: str,
    current_id: str | None,
    current_price: D,
    phase: str,
) -> tuple[D, D, D, D] | None:
    """Return fresh top-of-book or None for the one known transient depth outage.

    Do not swallow unrelated RuntimeErrors: once real money has mutated, masking an
    unexpected programming/authentication failure would be less safe than halting.
    """
    try:
        return await v1.base.fresh_top(symbol)
    except RuntimeError as exc:
        if _TRANSIENT_DEPTH_ERROR not in str(exc):
            raise
        v1.emit(
            "DYNAMIC_RESCUE_DEPTH_UNAVAILABLE",
            missing_outcome=missing,
            client_order_id=current_id,
            current_price=str(current_price),
            complement_resting=current_id is not None,
            phase=phase,
            action=(
                "KEEP_EXISTING_COMPLEMENT_AND_RETRY"
                if current_id is not None
                else "WAIT_FOR_FRESH_DEPTH_WITHOUT_BLIND_REPOST"
            ),
            maker_only_tif="MOC",
            order_entry=True,
        )
        return None


async def _dynamic_rescue(
    maker: v1.base.GeminiMakerSession,
    *, event: v1.base.Event, trade_id: str, filled_outcome: str, filled_price: D,
    complement_initial_id: str, complement_initial_price: D,
    complement_initial_row: Mapping[str, Any] | None,
    yes_ids: list[str], no_ids: list[str],
) -> dict[str, Any]:
    missing = "NO" if filled_outcome == "YES" else "YES"
    current_id: str | None = complement_initial_id
    current_price = D(str(complement_initial_price))
    current_row: Mapping[str, Any] | None = complement_initial_row
    sequence = 0
    rescue_deadline = min(
        time.monotonic() + RESCUE_SECONDS,
        time.monotonic() + max(1.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS),
    )
    next_reprice_check = time.monotonic()

    async def post_replacement(price: D) -> tuple[str | None, Mapping[str, Any] | None, dict[str, Any] | None]:
        nonlocal sequence, current_price
        if sequence >= MAX_REPRICES:
            return None, None, {"status": "MAX_REPRICES_REACHED", "paired": False, "hard_halt": False}
        sequence += 1
        rid = v1.replacement_client_id(trade_id, missing, sequence)
        ack, row, ack_ms = await v1._post_maker(
            maker, symbol=event.symbol, outcome=missing, price=price, client_id=rid,
        )
        accepted = int(ack.get("status", 0)) == 200
        v1.emit(
            "DYNAMIC_COMPLEMENT_REPOST",
            missing_outcome=missing,
            sequence=sequence,
            client_order_id=rid,
            prior_price=str(current_price),
            target_price=str(price),
            modeled_final_edge=str(policy.completed_pair_edge(
                filled_price=D(str(filled_price)), complement_price=D(str(price)),
            )),
            ack_status=ack.get("status"),
            ack_latency_ms=round(ack_ms, 3),
            accepted=accepted,
            maker_only_tif="MOC",
            order_entry=True,
        )
        if not accepted:
            return None, None, {
                "status": "DYNAMIC_COMPLEMENT_REPOST_REJECTED",
                "paired": False,
                "hard_halt": True,
                "rejected_client_order_id": rid,
            }
        # Persist only accepted orders. Rejected IDs have no authoritative terminal
        # row and therefore must not enter the settlement identity chain.
        if missing == "YES":
            yes_ids.append(rid)
        else:
            no_ids.append(rid)
        current_price = D(str(price))
        return rid, row, None

    while time.monotonic() < rescue_deadline:
        if current_id is not None:
            fill = await v1._wait_for_specific_fill(
                maker, client_id=current_id,
                deadline=min(rescue_deadline, time.monotonic() + 0.30),
            )
            if fill == D("1"):
                return {"status": "DYNAMIC_COMPLEMENT_FILLED", "paired": True, "hard_halt": False, "reprices": sequence}
            if fill > D("0"):
                return {"status": "DYNAMIC_COMPLEMENT_FRACTIONAL_FILL", "paired": False, "hard_halt": True, "reprices": sequence}

        now = time.monotonic()
        if now < next_reprice_check:
            if current_id is None:
                await asyncio.sleep(min(0.20, max(0.05, rescue_deadline - now)))
            continue
        next_reprice_check = now + REPRICE_INTERVAL_SECONDS

        top = await _fresh_top_or_none(
            event.symbol,
            missing=missing,
            current_id=current_id,
            current_price=current_price,
            phase="PRE_CANCEL_REPRICE_CHECK" if current_id is not None else "NO_RESTING_COMPLEMENT_RETRY",
        )
        if top is None:
            continue
        bid, _, ask, _ = top
        plan = policy.dynamic_complement_plan(
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            current_complement_price=current_price,
            yes_bid=bid,
            yes_ask=ask,
            min_final_edge=FINAL_PAIR_EDGE,
            increment=event.price_increment,
            min_price=MIN_PRICE,
            max_price=MAX_PRICE,
        )
        target = plan.get("target_price")
        target_d = None if target is None else D(str(target))

        if current_id is None:
            if plan.get("action") == "REPRICE_MAKER_COMPLEMENT" and target_d is not None and target_d > current_price:
                rid, row, terminal = await post_replacement(target_d)
            elif _safe_to_rest_current(
                missing=missing, filled_price=filled_price, current_price=current_price,
                yes_bid=bid, yes_ask=ask, increment=event.price_increment,
            ):
                rid, row, terminal = await post_replacement(current_price)
            else:
                continue
            if terminal is not None:
                return terminal
            current_id, current_row = rid, row
            continue

        if plan.get("action") != "REPRICE_MAKER_COMPLEMENT" or target_d is None or target_d <= current_price:
            continue
        if sequence >= MAX_REPRICES:
            continue

        cancel, cancel_ms, _, race_fill = await v1._cancel(
            maker, client_id=current_id, symbol=event.symbol, row=current_row,
        )
        v1.emit(
            "DYNAMIC_REPRICE_CANCEL",
            missing_outcome=missing,
            client_order_id=current_id,
            cancel=cancel,
            cancel_roundtrip_ms=round(cancel_ms, 3),
            cancel_race_fill=str(race_fill),
            proposed_target_price=str(target_d),
            order_entry=True,
        )
        if race_fill == D("1"):
            return {"status": "COMPLEMENT_FILLED_DURING_REPRICE_CANCEL", "paired": True, "hard_halt": False, "reprices": sequence}
        if race_fill > D("0") or not cancel.get("ok"):
            return {"status": "REPRICE_CANCEL_AMBIGUOUS", "paired": False, "hard_halt": True, "reprices": sequence}
        current_id = None
        current_row = None

        top2 = await _fresh_top_or_none(
            event.symbol,
            missing=missing,
            current_id=None,
            current_price=current_price,
            phase="POST_CANCEL_REVALIDATION",
        )
        if top2 is None:
            # We deliberately do not repost from stale prices after a cancel. The
            # next loop iteration will wait for fresh depth, bounded by the same
            # rescue deadline, then re-evaluate both maker passivity and pair edge.
            continue
        bid2, _, ask2, _ = top2
        plan2 = policy.dynamic_complement_plan(
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            current_complement_price=current_price,
            yes_bid=bid2,
            yes_ask=ask2,
            min_final_edge=FINAL_PAIR_EDGE,
            increment=event.price_increment,
            min_price=MIN_PRICE,
            max_price=MAX_PRICE,
        )
        target2 = plan2.get("target_price")
        target2_d = None if target2 is None else D(str(target2))
        if plan2.get("action") == "REPRICE_MAKER_COMPLEMENT" and target2_d is not None and target2_d > current_price:
            repost_price = target2_d
        elif _safe_to_rest_current(
            missing=missing, filled_price=filled_price, current_price=current_price,
            yes_bid=bid2, yes_ask=ask2, increment=event.price_increment,
        ):
            repost_price = current_price
        else:
            continue
        rid, row, terminal = await post_replacement(repost_price)
        if terminal is not None:
            return terminal
        current_id, current_row = rid, row

    if current_id is not None:
        cancel, cancel_ms, _, race_fill = await v1._cancel(
            maker, client_id=current_id, symbol=event.symbol, row=current_row,
        )
        v1.emit(
            "DYNAMIC_RESCUE_TIMEOUT_CANCEL",
            missing_outcome=missing,
            client_order_id=current_id,
            cancel=cancel,
            cancel_roundtrip_ms=round(cancel_ms, 3),
            cancel_race_fill=str(race_fill),
            order_entry=True,
        )
        if race_fill == D("1"):
            return {"status": "COMPLEMENT_FILLED_AT_RESCUE_TIMEOUT", "paired": True, "hard_halt": False, "reprices": sequence}
        if race_fill > D("0") or not cancel.get("ok"):
            return {"status": "RESCUE_TIMEOUT_CANCEL_AMBIGUOUS", "paired": False, "hard_halt": True, "reprices": sequence}
    return {"status": "DYNAMIC_RESCUE_EXPIRED_ONE_SIDED", "paired": False, "hard_halt": False, "reprices": sequence}


async def run() -> None:
    original = v1._dynamic_rescue
    try:
        v1._dynamic_rescue = _dynamic_rescue
        await v1.run()
    finally:
        v1._dynamic_rescue = original