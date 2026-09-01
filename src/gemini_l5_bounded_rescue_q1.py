"""Bounded-loss staged completion overlay for Gemini crypto-5m q1.

This module intentionally leaves the proven q1 pair primitive unchanged on disk.
When used as the live entry point it installs three narrow overrides on
``gemini_l5_pair_q1`` before importing the continuous owner:

1. entry selection must prove that either possible first fill could be flattened
   against the *current* opposite top-of-book within a hard loss budget;
2. after a first fill the objective changes from profit seeking to inventory
   elimination: maker +edge -> maker breakeven -> bounded IOC; and
3. accounting distinguishes maker and taker fees for the emergency leg.

The hard invariant remains q=1.  IOC is used only for the missing outcome after
an authoritative first fill, only after the resting complement is cancelled and
its exact client ID is proven inactive, and only when fresh displayed top depth
can fill q=1 within ``MAX_EMERGENCY_LOSS`` after published Gemini taker fees.
Rewards/rebates are never counted in the safety math.
"""
from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal as D
from typing import Any, Mapping

from src import gemini_l5_pair_q1 as pair
from src.gemini_kalshi_execution_shadow import (
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")
MAX_EMERGENCY_LOSS = D(os.getenv("GEMINI_L5_MAX_EMERGENCY_LOSS", "0.05"))
PROFIT_STAGE_SECONDS = float(os.getenv("GEMINI_L5_PROFIT_STAGE_SECONDS", "3.0"))
BREAKEVEN_STAGE_SECONDS = float(os.getenv("GEMINI_L5_BREAKEVEN_STAGE_SECONDS", "3.0"))
MAX_IOC_ATTEMPTS = int(os.getenv("GEMINI_L5_MAX_IOC_ATTEMPTS", "3"))
IOC_RETRY_SECONDS = float(os.getenv("GEMINI_L5_IOC_RETRY_SECONDS", "0.35"))
MIN_EMERGENCY_TTE_SECONDS = float(os.getenv("GEMINI_L5_MIN_EMERGENCY_TTE_SECONDS", "8.0"))

_ORIGINAL_CHOOSE_PAIR = pair.choose_pair
_ORIGINAL_SUMMARIZE = pair.summarize


def mixed_completed_edge(
    *,
    filled_price: D,
    complement_price: D,
    complement_is_taker: bool,
) -> D:
    """Completed q1 edge with the first leg maker and complement maker/taker."""
    fp, cp = D(str(filled_price)), D(str(complement_price))
    if not (ZERO < fp < ONE and ZERO < cp < ONE):
        raise ValueError("prices must be inside (0,1)")
    first_fee = gemini_predictions_maker_fee(fp, ONE)
    second_fee = (
        gemini_predictions_taker_fee(cp, ONE)
        if complement_is_taker
        else gemini_predictions_maker_fee(cp, ONE)
    )
    return ONE - fp - cp - first_fee - second_fee


def emergency_ioc_plan(
    *,
    filled_outcome: str,
    filled_price: D,
    yes_bid: D,
    yes_bid_size: D,
    yes_ask: D,
    yes_ask_size: D,
    max_loss: D = MAX_EMERGENCY_LOSS,
) -> dict[str, Any]:
    """Plan an immediate q1 complement using fresh displayed top liquidity."""
    filled = str(filled_outcome).strip().upper()
    if filled not in {"YES", "NO"}:
        raise ValueError("filled_outcome must be YES or NO")
    bid, ask = D(str(yes_bid)), D(str(yes_ask))
    bs, az = D(str(yes_bid_size)), D(str(yes_ask_size))
    max_loss = D(str(max_loss))
    if max_loss < ZERO:
        raise ValueError("max_loss must be non-negative")
    if not (ZERO < bid < ask < ONE):
        return {"action": "BLOCK", "reason": "INVALID_BOOK"}

    if filled == "YES":
        missing = "NO"
        price = (ONE - bid).quantize(TICK)
        depth = bs
    else:
        missing = "YES"
        price = ask.quantize(TICK)
        depth = az

    edge = mixed_completed_edge(
        filled_price=D(str(filled_price)),
        complement_price=price,
        complement_is_taker=True,
    )
    if depth < ONE:
        return {
            "action": "BLOCK",
            "reason": "INSUFFICIENT_TOP_DEPTH",
            "missing_outcome": missing,
            "price": price,
            "depth": depth,
            "final_edge": edge,
        }
    if edge < -max_loss:
        return {
            "action": "BLOCK",
            "reason": "LOSS_CAP_EXCEEDED",
            "missing_outcome": missing,
            "price": price,
            "depth": depth,
            "final_edge": edge,
            "max_loss": max_loss,
        }
    return {
        "action": "IOC",
        "reason": "BOUNDED_DIRECTIONAL_FLATTEN",
        "missing_outcome": missing,
        "price": price,
        "depth": depth,
        "final_edge": edge,
        "max_loss": max_loss,
    }


def entry_emergency_flatten_ok(
    *,
    yes_price: D,
    no_price: D,
    yes_bid: D,
    yes_bid_size: D,
    yes_ask: D,
    yes_ask_size: D,
    max_loss: D = MAX_EMERGENCY_LOSS,
) -> bool:
    """Require current q1 emergency flattenability for either possible first fill."""
    y = emergency_ioc_plan(
        filled_outcome="YES",
        filled_price=yes_price,
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_size,
        max_loss=max_loss,
    )
    n = emergency_ioc_plan(
        filled_outcome="NO",
        filled_price=no_price,
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_size,
        max_loss=max_loss,
    )
    return y.get("action") == "IOC" and n.get("action") == "IOC"


def choose_pair(
    bid: D,
    ask: D,
    *,
    min_edge: D = pair.DEFAULT_MIN_PAIR_EDGE,
    yes_queue: D = ZERO,
    no_queue: D = ZERO,
    tie_seed: str = "",
) -> dict[str, D | str | int] | None:
    """Original maker selection plus a current-book emergency-flatten gate."""
    model = _ORIGINAL_CHOOSE_PAIR(
        bid,
        ask,
        min_edge=min_edge,
        yes_queue=yes_queue,
        no_queue=no_queue,
        tie_seed=tie_seed,
    )
    if model is None:
        return None
    if not entry_emergency_flatten_ok(
        yes_price=D(str(model["yes"])),
        no_price=D(str(model["no"])),
        yes_bid=D(str(bid)),
        yes_bid_size=D(str(yes_queue)),
        yes_ask=D(str(ask)),
        yes_ask_size=D(str(no_queue)),
    ):
        return None
    out = dict(model)
    out["emergency_loss_cap"] = MAX_EMERGENCY_LOSS
    out["emergency_flatten_preflight"] = "PASS"
    return out


def summarize(specs: list[dict[str, Any]], fills: Mapping[str, D]) -> dict[str, Any]:
    """Authoritative fill summary with maker/taker-aware fee accounting."""
    yes_qty = no_qty = yes_cost = no_cost = fees = ZERO
    detail: list[dict[str, str]] = []
    for spec in specs:
        qty = D(str(fills.get(spec["id"], ZERO)))
        if qty <= ZERO:
            continue
        price = D(str(spec["price"]))
        liquidity = str(spec.get("liquidity") or "MAKER").upper()
        fee = (
            gemini_predictions_taker_fee(price, qty)
            if liquidity == "TAKER"
            else gemini_predictions_maker_fee(price, qty)
        )
        cost = price * qty
        fees += fee
        if spec["side"] == "YES":
            yes_qty += qty
            yes_cost += cost
        else:
            no_qty += qty
            no_cost += cost
        detail.append(
            {
                "side": str(spec["side"]),
                "price": str(price),
                "qty": str(qty),
                "fee": str(fee),
                "liquidity": liquidity,
                "tif": str(spec.get("tif") or ("IOC" if liquidity == "TAKER" else "MOC")),
            }
        )
    matched = min(yes_qty, no_qty)
    net_cost = yes_cost + no_cost + fees
    locked_edge = matched - net_cost if matched > ZERO and yes_qty == no_qty else None
    return {
        "fills": detail,
        "yes_qty": str(yes_qty),
        "no_qty": str(no_qty),
        "matched_qty": str(matched),
        "residual_qty": str(abs(yes_qty - no_qty)),
        "maker_turnover": str(yes_cost + no_cost),
        "maker_fees": str(fees),
        "locked_edge_after_fees": None if locked_edge is None else str(locked_edge),
    }


async def _wait_missing_ws(
    maker: Any,
    *,
    ids: set[str],
    seconds: float,
) -> bool:
    """Low-request wait: order events first; REST reconciliation happens at transitions."""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        try:
            update = await asyncio.wait_for(
                maker.events.get(), timeout=min(0.35, max(0.01, deadline - time.monotonic()))
            )
        except asyncio.TimeoutError:
            continue
        if not isinstance(update, Mapping):
            continue
        client = str(update.get("c") or "")
        qty = D(str(update.get("Z") or "0"))
        if client in ids and qty > ZERO:
            return True
    return False


async def _cancel_prove_inactive_and_reconcile(
    maker: Any,
    *,
    current: dict[str, Any],
    event: Any,
    specs: list[dict[str, Any]],
    missing: str,
) -> tuple[str, dict[str, D]]:
    """No replacement/IOC until the old exact complement can no longer race fill."""
    cancel = await pair.ladder.cancel(maker, current, event.symbol)
    await asyncio.sleep(0.12)
    active = await pair._exact_active(current["id"], event.symbol)
    pair.emit(
        "COMPLEMENT_STAGE_CANCEL",
        side=missing,
        prior_id=current["id"],
        prior_price=str(current["price"]),
        cancel=cancel,
        exact_old_id_active=active,
        order_entry=True,
    )
    if active or not cancel.get("ok"):
        return "COMPLEMENT_CANCEL_AMBIGUOUS", {}
    fills = await pair.ladder.fills(specs, event.symbol)
    sm = summarize(specs, fills)
    if D(sm["yes_qty"]) == ONE and D(sm["no_qty"]) == ONE:
        return "MATCHED", fills
    return "INACTIVE_ZERO_OR_PARTIAL", fills


async def _maker_stage(
    maker: Any,
    *,
    event: Any,
    sid: str,
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    filled_side: str,
    filled_price: D,
    missing: str,
    current: dict[str, Any],
    sequence: int,
    final_edge_floor: D,
    wait_seconds: float,
) -> tuple[str, dict[str, Any], int]:
    """Move the complement to the most aggressive maker price for this stage."""
    current_price = D(str(current["price"]))
    try:
        bid, _, ask, _ = await pair.ladder.fresh_top_retry(event.symbol, attempts=2, delay_s=0.10)
    except RuntimeError:
        if await _wait_missing_ws(maker, ids={current["id"]}, seconds=wait_seconds):
            return "WS_FILL_SEEN", current, sequence
        return "CONTINUE", current, sequence

    plan = pair.completion.complement_plan(
        filled_outcome=filled_side,
        filled_price=filled_price,
        current_complement_price=current_price,
        yes_bid=bid,
        yes_ask=ask,
        min_final_edge=D(str(final_edge_floor)),
        tick=TICK,
        min_price=pair.MIN_PRICE,
        max_price=pair.MAX_PRICE,
    )
    target = plan.get("target_price")
    target_d = None if target is None else D(str(target))
    if plan.get("action") == "REPRICE" and target_d is not None and target_d > current_price:
        if sequence >= pair.MAX_COMPLEMENT_REPRICES:
            return "REPRICE_CAP", current, sequence
        status, _ = await _cancel_prove_inactive_and_reconcile(
            maker, current=current, event=event, specs=specs, missing=missing
        )
        if status == "MATCHED":
            return "MATCHED", current, sequence
        if status != "INACTIVE_ZERO_OR_PARTIAL":
            return status, current, sequence

        # Re-read after cancellation so passivity/economics are current at mutation.
        try:
            bid2, _, ask2, _ = await pair.ladder.fresh_top_retry(event.symbol, attempts=2, delay_s=0.10)
        except RuntimeError:
            return "NO_FRESH_DEPTH_AFTER_COMPLEMENT_CANCEL", current, sequence
        plan2 = pair.completion.complement_plan(
            filled_outcome=filled_side,
            filled_price=filled_price,
            current_complement_price=current_price,
            yes_bid=bid2,
            yes_ask=ask2,
            min_final_edge=D(str(final_edge_floor)),
            tick=TICK,
            min_price=pair.MIN_PRICE,
            max_price=pair.MAX_PRICE,
        )
        target2 = plan2.get("target_price")
        target2_d = None if target2 is None else D(str(target2))
        if plan2.get("action") != "REPRICE" or target2_d is None or target2_d <= current_price:
            return "NO_SAFE_COMPLEMENT_REPOST_AFTER_CANCEL", current, sequence
        sequence += 1
        replacement = await pair._post_replacement(
            maker,
            event=event,
            sid=sid,
            missing=missing,
            price=target2_d,
            sequence=sequence,
        )
        if replacement is None:
            return "COMPLEMENT_REPOST_REJECTED", current, sequence
        replacement["liquidity"] = "MAKER"
        replacement["tif"] = "MOC"
        specs.append(replacement)
        current = replacement
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
        state["completion_reprices"] = sequence
        pair._write_state(state)
        pair.emit(
            "COMPLEMENT_STAGE_ACTIVE",
            stage="PROFIT" if final_edge_floor > ZERO else "BREAKEVEN",
            side=missing,
            sequence=sequence,
            price=str(target2_d),
            modeled_final_edge=str(
                mixed_completed_edge(
                    filled_price=filled_price,
                    complement_price=target2_d,
                    complement_is_taker=False,
                )
            ),
            order_entry=True,
        )

    if await _wait_missing_ws(maker, ids={current["id"]}, seconds=wait_seconds):
        return "WS_FILL_SEEN", current, sequence
    return "CONTINUE", current, sequence


async def _post_emergency_ioc(
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
        "id": pair.ladder.cid(sid, missing, 50 + sequence),
        "row": None,
        "liquidity": "TAKER",
        "tif": "IOC",
    }
    ack = await maker.request(
        "order.place",
        {
            "symbol": event.symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "IOC",
            "price": str(spec["price"]),
            "quantity": "1",
            "eventOutcome": missing,
            "clientOrderId": spec["id"],
        },
        timeout=4.0,
    )
    accepted = int(ack.get("status", 0)) == 200
    pair.emit(
        "EMERGENCY_IOC_POST",
        side=missing,
        sequence=sequence,
        client_id=spec["id"],
        price=str(spec["price"]),
        accepted=accepted,
        ack_status=ack.get("status"),
        tif="IOC",
        order_entry=True,
    )
    return spec if accepted else None


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
    """Three-stage q1 completion: maker profit -> maker flat -> bounded IOC."""
    missing = "NO" if filled_side == "YES" else "YES"
    initial = await pair.ladder.fills(specs, event.symbol)
    sm = summarize(specs, initial)
    yq, nq = D(sm["yes_qty"]), D(sm["no_qty"])
    if yq == ONE and nq == ONE:
        return "MATCHED"
    filled_qty = yq if filled_side == "YES" else nq
    missing_qty = nq if missing == "NO" else yq
    if filled_qty != ONE or missing_qty != ZERO:
        return "UNEXPECTED_FIRST_FILL_SHAPE"

    filled_specs = [
        s for s in specs
        if s["side"] == filled_side and D(str(initial.get(s["id"], ZERO))) > ZERO
    ]
    current_candidates = [s for s in specs if s["side"] == missing]
    if len(filled_specs) != 1 or len(current_candidates) != 1:
        return "AMBIGUOUS_FIRST_FILL_IDENTITY"
    filled_price = D(str(filled_specs[0]["price"]))
    current = current_candidates[0]
    current.setdefault("liquidity", "MAKER")
    current.setdefault("tif", "MOC")
    sequence = 0

    pair.emit(
        "COMPLEMENT_RESCUE_START",
        filled_side=filled_side,
        filled_price=str(filled_price),
        missing_side=missing,
        current_price=str(current["price"]),
        policy="MAKER_PROFIT_THEN_BREAKEVEN_THEN_BOUNDED_IOC",
        min_profit_edge=str(min_edge),
        max_emergency_loss=str(MAX_EMERGENCY_LOSS),
        profit_stage_s=PROFIT_STAGE_SECONDS,
        breakeven_stage_s=BREAKEVEN_STAGE_SECONDS,
        max_ioc_attempts=MAX_IOC_ATTEMPTS,
        order_entry=True,
    )

    # Stage 1: preserve the original profit floor, but only briefly.
    status, current, sequence = await _maker_stage(
        maker,
        event=event,
        sid=sid,
        specs=specs,
        state=state,
        filled_side=filled_side,
        filled_price=filled_price,
        missing=missing,
        current=current,
        sequence=sequence,
        final_edge_floor=D(str(min_edge)),
        wait_seconds=PROFIT_STAGE_SECONDS,
    )
    if status == "MATCHED":
        return "MATCHED"
    if status == "WS_FILL_SEEN":
        fs = await pair.ladder.fills(specs, event.symbol)
        sm = summarize(specs, fs)
        if D(sm["yes_qty"]) == ONE and D(sm["no_qty"]) == ONE:
            return "MATCHED"
    elif status not in {"CONTINUE", "REPRICE_CAP"}:
        return status

    # Stage 2: spend the remaining edge down to fee-adjusted breakeven.
    status, current, sequence = await _maker_stage(
        maker,
        event=event,
        sid=sid,
        specs=specs,
        state=state,
        filled_side=filled_side,
        filled_price=filled_price,
        missing=missing,
        current=current,
        sequence=sequence,
        final_edge_floor=ZERO,
        wait_seconds=BREAKEVEN_STAGE_SECONDS,
    )
    if status == "MATCHED":
        return "MATCHED"
    if status == "WS_FILL_SEEN":
        fs = await pair.ladder.fills(specs, event.symbol)
        sm = summarize(specs, fs)
        if D(sm["yes_qty"]) == ONE and D(sm["no_qty"]) == ONE:
            return "MATCHED"
    elif status not in {"CONTINUE", "REPRICE_CAP"}:
        return status

    # Stage 3: cancel the resting maker and use q1 IOC only inside the hard loss cap.
    status, _ = await _cancel_prove_inactive_and_reconcile(
        maker, current=current, event=event, specs=specs, missing=missing
    )
    if status == "MATCHED":
        return "MATCHED"
    if status != "INACTIVE_ZERO_OR_PARTIAL":
        return status

    for attempt in range(1, MAX_IOC_ATTEMPTS + 1):
        if event.seconds_to_expiry < MIN_EMERGENCY_TTE_SECONDS:
            return "EMERGENCY_TTE_TOO_LOW"
        try:
            bid, bid_size, ask, ask_size = await pair.ladder.fresh_top_retry(
                event.symbol, attempts=2, delay_s=0.10
            )
        except RuntimeError:
            await asyncio.sleep(IOC_RETRY_SECONDS)
            continue
        plan = emergency_ioc_plan(
            filled_outcome=filled_side,
            filled_price=filled_price,
            yes_bid=bid,
            yes_bid_size=bid_size,
            yes_ask=ask,
            yes_ask_size=ask_size,
        )
        pair.emit(
            "EMERGENCY_IOC_PLAN",
            attempt=attempt,
            filled_side=filled_side,
            missing_side=missing,
            plan=plan,
            order_entry=True,
        )
        if plan.get("action") != "IOC":
            return f"EMERGENCY_{plan.get('reason') or 'BLOCKED'}"
        emergency = await _post_emergency_ioc(
            maker,
            event=event,
            sid=sid,
            missing=missing,
            price=D(str(plan["price"])),
            sequence=attempt,
        )
        if emergency is None:
            await asyncio.sleep(IOC_RETRY_SECONDS)
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
        state["completion_reprices"] = sequence
        state["emergency_ioc_attempts"] = attempt
        state["emergency_loss_cap"] = str(MAX_EMERGENCY_LOSS)
        pair._write_state(state)
        await asyncio.sleep(IOC_RETRY_SECONDS)
        fs = await pair.ladder.fills(specs, event.symbol)
        sm = summarize(specs, fs)
        if D(sm["yes_qty"]) == ONE and D(sm["no_qty"]) == ONE:
            pair.emit(
                "EMERGENCY_IOC_MATCHED",
                attempt=attempt,
                final_summary=sm,
                order_entry=False,
            )
            return "MATCHED_BOUNDED_IOC"

    return "EMERGENCY_IOC_EXHAUSTED"


def install() -> None:
    """Install the overlay before importing the continuous dynamic owner."""
    pair.choose_pair = choose_pair
    pair.summarize = summarize
    pair._dynamic_complement_rescue = _dynamic_complement_rescue


install()

# Import only after the pair module has been overlaid; selector imports the same
# module object and therefore inherits the emergency entry gate as well.
from src import gemini_l5_dynamic_q1 as owner  # noqa: E402

# Teach the outer owner that a successful IOC completion is a matched, locked pair.
owner.MATCHED_STATUSES = frozenset(set(owner.MATCHED_STATUSES) | {"MATCHED_BOUNDED_IOC"})


def main() -> None:
    owner.main()


if __name__ == "__main__":
    main()
