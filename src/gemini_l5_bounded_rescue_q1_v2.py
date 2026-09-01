"""Hardened live entry point for staged Gemini crypto-5m q1 completion.

V1 supplies the pure emergency economics, entry gate and maker/taker accounting.
V2 replaces only the rescue state machine with a simpler sequence designed around
what the live failures taught us:

  existing profitable maker complement (brief wait)
    -> cancel + exact-inactive + authoritative race-fill check
    -> most aggressive fee-adjusted breakeven MOC (brief wait)
    -> cancel + exact-inactive + authoritative race-fill check
    -> q1 FOK against fresh top, bounded to <=5c completed-pair loss.

FOK is deliberately used instead of IOC because q=1 is indivisible for the risk
model: FOK either completes the entire missing contract immediately or fills none,
so retries cannot create fractional overfill.  No broad cancel is permitted.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
from typing import Any

from src import gemini_l5_pair_q1 as pair
from src import gemini_l5_bounded_rescue_q1 as v1

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")

# V1 installs its overlay at import time.  Keep its entry gate and fee-aware
# summary, but replace rescue with the hardened state machine below.


def _safe_emit(*args: Any, **kwargs: Any) -> None:
    """Permit a secondary rescue-stage label without colliding with emit(stage)."""
    if args:
        event_stage = str(args[0])
        rescue_stage = kwargs.pop("stage", None)
    else:
        event_stage = str(kwargs.pop("stage"))
        rescue_stage = None
    if rescue_stage is not None:
        kwargs["rescue_stage"] = rescue_stage
    v1.pair.emit(event_stage, **kwargs)


# Capture the real emitter before installing compatibility wrapper.
_REAL_EMIT = pair.emit


def _compat_emit(*args: Any, **kwargs: Any) -> None:
    if args:
        event_stage = str(args[0])
        rescue_stage = kwargs.pop("stage", None)
    else:
        event_stage = str(kwargs.pop("stage"))
        rescue_stage = None
    if rescue_stage is not None:
        kwargs["rescue_stage"] = rescue_stage
    _REAL_EMIT(event_stage, **kwargs)


async def _post_emergency_fok(
    maker: Any,
    *,
    event: Any,
    sid: str,
    missing: str,
    price: D,
    sequence: int,
) -> dict[str, Any] | None:
    spec = {
        "side": missing,
        "price": D(str(price)),
        "id": pair.ladder.cid(sid, missing, 70 + sequence),
        "row": None,
        "liquidity": "TAKER",
        "tif": "FOK",
    }
    ack = await maker.request(
        "order.place",
        {
            "symbol": event.symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "FOK",
            "price": str(spec["price"]),
            "quantity": "1",
            "eventOutcome": missing,
            "clientOrderId": spec["id"],
        },
        timeout=4.0,
    )
    accepted = int(ack.get("status", 0)) == 200
    pair.emit(
        "EMERGENCY_FOK_POST",
        side=missing,
        sequence=sequence,
        client_id=spec["id"],
        price=str(spec["price"]),
        accepted=accepted,
        ack_status=ack.get("status"),
        tif="FOK",
        order_entry=True,
    )
    return spec if accepted else None


async def _authoritative_matched(specs: list[dict[str, Any]], symbol: str) -> tuple[bool, dict[str, Any]]:
    fills = await pair.ladder.fills(specs, symbol)
    summary = v1.summarize(specs, fills)
    yq, nq = D(summary["yes_qty"]), D(summary["no_qty"])
    if yq > ONE or nq > ONE:
        raise RuntimeError(f"q1 completion bound violated yes={yq} no={nq}")
    return yq == ONE and nq == ONE, summary


async def _cancel_current(
    maker: Any,
    *,
    current: dict[str, Any],
    event: Any,
    specs: list[dict[str, Any]],
    missing: str,
) -> str:
    status, _ = await v1._cancel_prove_inactive_and_reconcile(
        maker,
        current=current,
        event=event,
        specs=specs,
        missing=missing,
    )
    return status


async def _post_breakeven_maker(
    maker: Any,
    *,
    event: Any,
    sid: str,
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    filled_side: str,
    filled_price: D,
    missing: str,
    prior_price: D,
) -> dict[str, Any] | None:
    """After old complement is inactive, post the best non-losing MOC if available."""
    try:
        bid, _, ask, _ = await pair.ladder.fresh_top_retry(event.symbol, attempts=2, delay_s=0.10)
    except RuntimeError:
        return None
    plan = pair.completion.complement_plan(
        filled_outcome=filled_side,
        filled_price=filled_price,
        current_complement_price=D(str(prior_price)),
        yes_bid=bid,
        yes_ask=ask,
        min_final_edge=ZERO,
        tick=TICK,
        min_price=pair.MIN_PRICE,
        max_price=pair.MAX_PRICE,
    )
    target = plan.get("target_price")
    target_d = None if target is None else D(str(target))
    if plan.get("action") == "REPRICE" and target_d is not None and target_d > prior_price:
        price = target_d
    elif pair._current_rest_is_safe(
        missing=missing,
        filled_price=filled_price,
        current_price=D(str(prior_price)),
        bid=bid,
        ask=ask,
        min_edge=ZERO,
    ):
        price = D(str(prior_price))
    else:
        return None

    replacement = await pair._post_replacement(
        maker,
        event=event,
        sid=sid,
        missing=missing,
        price=price,
        sequence=1,
    )
    if replacement is None:
        return None
    replacement["liquidity"] = "MAKER"
    replacement["tif"] = "MOC"
    specs.append(replacement)
    state["orders"] = [
        {
            "side": s["side"],
            "price": str(s["price"]),
            "id": s["id"],
            "liquidity": str(s.get("liquidity") or "MAKER"),
            "tif": str(s.get("tif") or "MOC"),
        }
        for s in specs
    ]
    state["completion_reprices"] = 1
    state["rescue_phase"] = "BREAKEVEN_MAKER"
    pair._write_state(state)
    pair.emit(
        "COMPLEMENT_BREAKEVEN_ACTIVE",
        side=missing,
        price=str(price),
        modeled_final_edge=str(
            v1.mixed_completed_edge(
                filled_price=filled_price,
                complement_price=price,
                complement_is_taker=False,
            )
        ),
        order_entry=True,
    )
    return replacement


async def _dynamic_complement_rescue(
    maker: Any,
    *,
    event: Any,
    sid: str,
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    filled_side: str,
    min_edge: D,
    complement_wait: float,
) -> str:
    missing = "NO" if filled_side == "YES" else "YES"
    fills = await pair.ladder.fills(specs, event.symbol)
    sm = v1.summarize(specs, fills)
    yq, nq = D(sm["yes_qty"]), D(sm["no_qty"])
    if yq == ONE and nq == ONE:
        return "MATCHED"
    if (yq if filled_side == "YES" else nq) != ONE or (nq if missing == "NO" else yq) != ZERO:
        return "UNEXPECTED_FIRST_FILL_SHAPE"

    filled_specs = [
        s for s in specs
        if s["side"] == filled_side and D(str(fills.get(s["id"], ZERO))) > ZERO
    ]
    current_candidates = [s for s in specs if s["side"] == missing]
    if len(filled_specs) != 1 or len(current_candidates) != 1:
        return "AMBIGUOUS_FIRST_FILL_IDENTITY"
    filled_price = D(str(filled_specs[0]["price"]))
    current = current_candidates[0]
    current.setdefault("liquidity", "MAKER")
    current.setdefault("tif", "MOC")

    state["rescue_policy"] = "PROFIT_MAKER_THEN_BREAKEVEN_MAKER_THEN_BOUNDED_FOK"
    state["emergency_loss_cap"] = str(v1.MAX_EMERGENCY_LOSS)
    pair._write_state(state)
    pair.emit(
        "COMPLEMENT_RESCUE_START",
        filled_side=filled_side,
        filled_price=str(filled_price),
        missing_side=missing,
        current_price=str(current["price"]),
        policy=state["rescue_policy"],
        profit_stage_s=v1.PROFIT_STAGE_SECONDS,
        breakeven_stage_s=v1.BREAKEVEN_STAGE_SECONDS,
        max_emergency_loss=str(v1.MAX_EMERGENCY_LOSS),
        max_fok_attempts=v1.MAX_IOC_ATTEMPTS,
        order_entry=True,
    )

    # Stage 1: let the already-resting profitable complement work briefly.  This
    # burns no private REST nonce budget while waiting for the order-event stream.
    if await v1._wait_missing_ws(
        maker,
        ids={current["id"]},
        seconds=v1.PROFIT_STAGE_SECONDS,
    ):
        matched, _ = await _authoritative_matched(specs, event.symbol)
        if matched:
            return "MATCHED"

    # Stage 2 transition: cancel old exact complement, prove it inactive, then
    # check its terminal fill before any new client ID can exist.
    status = await _cancel_current(
        maker,
        current=current,
        event=event,
        specs=specs,
        missing=missing,
    )
    if status == "MATCHED":
        return "MATCHED"
    if status != "INACTIVE_ZERO_OR_PARTIAL":
        return status

    breakeven = await _post_breakeven_maker(
        maker,
        event=event,
        sid=sid,
        specs=specs,
        state=state,
        filled_side=filled_side,
        filled_price=filled_price,
        missing=missing,
        prior_price=D(str(current["price"])),
    )
    if breakeven is not None:
        current = breakeven
        if await v1._wait_missing_ws(
            maker,
            ids={current["id"]},
            seconds=v1.BREAKEVEN_STAGE_SECONDS,
        ):
            matched, _ = await _authoritative_matched(specs, event.symbol)
            if matched:
                return "MATCHED"
        status = await _cancel_current(
            maker,
            current=current,
            event=event,
            specs=specs,
            missing=missing,
        )
        if status == "MATCHED":
            return "MATCHED"
        if status != "INACTIVE_ZERO_OR_PARTIAL":
            return status

    # Stage 3: no resting complement remains. FOK may cross only at the current
    # displayed q1 top and only if completed-pair loss including taker fee <= cap.
    state["rescue_phase"] = "BOUNDED_FOK"
    pair._write_state(state)
    for attempt in range(1, v1.MAX_IOC_ATTEMPTS + 1):
        if event.seconds_to_expiry < v1.MIN_EMERGENCY_TTE_SECONDS:
            return "EMERGENCY_TTE_TOO_LOW"
        try:
            bid, bid_size, ask, ask_size = await pair.ladder.fresh_top_retry(
                event.symbol, attempts=2, delay_s=0.10
            )
        except RuntimeError:
            await asyncio.sleep(v1.IOC_RETRY_SECONDS)
            continue
        plan = v1.emergency_ioc_plan(
            filled_outcome=filled_side,
            filled_price=filled_price,
            yes_bid=bid,
            yes_bid_size=bid_size,
            yes_ask=ask,
            yes_ask_size=ask_size,
        )
        pair.emit(
            "EMERGENCY_FOK_PLAN",
            attempt=attempt,
            filled_side=filled_side,
            missing_side=missing,
            price=None if plan.get("price") is None else str(plan.get("price")),
            depth=None if plan.get("depth") is None else str(plan.get("depth")),
            modeled_final_edge=None if plan.get("final_edge") is None else str(plan.get("final_edge")),
            loss_cap=str(v1.MAX_EMERGENCY_LOSS),
            plan_action=plan.get("action"),
            plan_reason=plan.get("reason"),
            order_entry=True,
        )
        if plan.get("action") != "IOC":
            return f"EMERGENCY_{plan.get('reason') or 'BLOCKED'}"
        emergency = await _post_emergency_fok(
            maker,
            event=event,
            sid=sid,
            missing=missing,
            price=D(str(plan["price"])),
            sequence=attempt,
        )
        if emergency is None:
            await asyncio.sleep(v1.IOC_RETRY_SECONDS)
            continue
        specs.append(emergency)
        state["orders"] = [
            {
                "side": s["side"],
                "price": str(s["price"]),
                "id": s["id"],
                "liquidity": str(s.get("liquidity") or "MAKER"),
                "tif": str(s.get("tif") or "MOC"),
            }
            for s in specs
        ]
        state["emergency_fok_attempts"] = attempt
        pair._write_state(state)
        await asyncio.sleep(v1.IOC_RETRY_SECONDS)
        matched, summary = await _authoritative_matched(specs, event.symbol)
        if matched:
            pair.emit(
                "EMERGENCY_FOK_MATCHED",
                attempt=attempt,
                locked_edge_after_fees=summary.get("locked_edge_after_fees"),
                fills=summary.get("fills"),
                order_entry=False,
            )
            return "MATCHED_BOUNDED_FOK"
        # FOK must never partially fill.  A nonzero unmatched quantity is a venue
        # or reconciliation invariant break and must not be retried.
        yq, nq = D(summary["yes_qty"]), D(summary["no_qty"])
        missing_qty = nq if missing == "NO" else yq
        if missing_qty > ZERO:
            return "FOK_PARTIAL_FILL_INVARIANT_VIOLATION"

    return "EMERGENCY_FOK_EXHAUSTED"


def install() -> None:
    v1.install()
    pair.emit = _compat_emit
    pair._dynamic_complement_rescue = _dynamic_complement_rescue


def main() -> None:
    install()
    from src import gemini_l5_dynamic_q1 as owner

    owner.MATCHED_STATUSES = frozenset(
        set(owner.MATCHED_STATUSES) | {"MATCHED_BOUNDED_FOK"}
    )
    owner.main()


if __name__ == "__main__":
    main()
