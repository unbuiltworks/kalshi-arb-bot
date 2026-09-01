"""Adaptive single-pair Gemini ETH5 maker primitive with bounded completion rescue.

Exactly one q1 YES and one q1 NO order may be active initially.  Entry selection
requires a fee-adjusted complete-pair edge plus enough reserve to improve either
possible complement by at least one cent after the first fill while preserving
the configured final edge floor.

After a first fill, only the missing outcome may be repriced.  Repricing is
maker-only, fee/edge capped, cancel-before-repost, freshly revalidated, and
bounded to six replacements.  The exact old complement client ID must be absent
from Gemini's active-order endpoint before a replacement can be posted.  At no
point may this cycle create more than q=1 directional residual.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Mapping

from src import gemini_l5_completion_policy as completion
from src import gemini_l5_ladder_q1 as ladder
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok
from src.gemini_prediction_auth import private_rest_request

ONE = D("1")
ZERO = D("0")
TICK = D("0.01")
BACKOFFS = (D("0"), D("0.01"), D("0.02"))
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
DEFAULT_MIN_PAIR_EDGE = D("0.01")
DEFAULT_FIRST_WAIT = 45.0
DEFAULT_COMPLEMENT_WAIT = 60.0
DEFAULT_MIN_TTE = 105.0
COMPLETION_RESERVE_TICKS = 1
REPRICE_INTERVAL_SECONDS = 1.0
MAX_COMPLEMENT_REPRICES = 6
STOP_BEFORE_EXPIRY_SECONDS = 25.0

LIVE_ENV = "GEMINI_L5_PAIR_LIVE_Q1"
SID_ENV = "GEMINI_L5_PAIR_SESSION_ID"
ARM_ENV = "GEMINI_L5_PAIR_ARM"
STATE_ENV = "GEMINI_L5_PAIR_STATE_FILE"


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_L5_PAIR_Q1 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (
        os.getenv(LIVE_ENV, "").strip().upper() == "YES"
        and bool(sid)
        and os.getenv(ARM_ENV, "").strip() == sid
    )


def _write_state(payload: Mapping[str, Any]) -> None:
    path = Path(os.getenv(STATE_ENV, "/state/gemini-l5-pair-q1.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as handle:
        json.dump(dict(payload), handle, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def pair_edge(yes: D, no: D) -> D:
    return (
        ONE
        - yes
        - no
        - gemini_predictions_maker_fee(yes, ONE)
        - gemini_predictions_maker_fee(no, ONE)
    )


def _preferred_backoff_side(yes_queue: D, no_queue: D, tie_seed: str = "") -> str:
    """Back off the visibly faster/smaller queue; tie-break deterministically."""
    yq, nq = D(str(yes_queue)), D(str(no_queue))
    if yq < nq:
        return "YES"
    if nq < yq:
        return "NO"
    return "YES" if sum(tie_seed.encode("utf-8")) % 2 == 0 else "NO"


def choose_pair(
    bid: D,
    ask: D,
    *,
    min_edge: D = DEFAULT_MIN_PAIR_EDGE,
    yes_queue: D = ZERO,
    no_queue: D = ZERO,
    tie_seed: str = "",
) -> dict[str, D | str | int] | None:
    """Choose the least-conceded pair that also funds one-tick completion rescue."""
    bid, ask = D(str(bid)), D(str(ask))
    min_edge = D(str(min_edge))
    preferred = _preferred_backoff_side(yes_queue, no_queue, tie_seed)
    candidates: list[dict[str, D | str | int]] = []
    for yes_backoff in BACKOFFS:
        for no_backoff in BACKOFFS:
            yes = (bid - yes_backoff).quantize(TICK)
            no = (ONE - ask - no_backoff).quantize(TICK)
            if not (MIN_PRICE <= yes <= MAX_PRICE and MIN_PRICE <= no <= MAX_PRICE):
                continue
            edge = pair_edge(yes, no)
            if edge < min_edge:
                continue
            if not completion.reserve_ok_for_either_first_fill(
                yes_price=yes,
                no_price=no,
                min_final_edge=min_edge,
                reserve_ticks=COMPLETION_RESERVE_TICKS,
                tick=TICK,
                max_price=MAX_PRICE,
            ):
                continue
            total = yes_backoff + no_backoff
            preferred_backoff = yes_backoff if preferred == "YES" else no_backoff
            candidates.append(
                {
                    "yes": yes,
                    "no": no,
                    "edge": edge,
                    "yes_backoff": yes_backoff,
                    "no_backoff": no_backoff,
                    "total_backoff": total,
                    "preferred_backoff_side": preferred,
                    "preferred_backoff": preferred_backoff,
                    "completion_reserve_ticks": COMPLETION_RESERVE_TICKS,
                }
            )
    if not candidates:
        return None
    # 1) minimize total concession; 2) keep each side close to top so whichever
    # side remains after the first fill can reach the market with the reserved
    # tick; 3) use queue imbalance only as the final tie-break.
    candidates.sort(
        key=lambda x: (
            D(str(x["total_backoff"])),
            max(D(str(x["yes_backoff"])), D(str(x["no_backoff"]))),
            -D(str(x["preferred_backoff"])),
            D(str(x["yes_backoff"])),
        )
    )
    return candidates[0]


def summarize(specs: list[dict[str, Any]], fills: Mapping[str, D]) -> dict[str, Any]:
    yes_qty = no_qty = yes_cost = no_cost = fees = ZERO
    detail: list[dict[str, str]] = []
    for spec in specs:
        qty = D(str(fills.get(spec["id"], ZERO)))
        if qty <= ZERO:
            continue
        fee = gemini_predictions_maker_fee(spec["price"], qty)
        cost = spec["price"] * qty
        fees += fee
        if spec["side"] == "YES":
            yes_qty += qty
            yes_cost += cost
        else:
            no_qty += qty
            no_cost += cost
        detail.append(
            {"side": spec["side"], "price": str(spec["price"]), "qty": str(qty), "fee": str(fee)}
        )
    return {
        "fills": detail,
        "yes_qty": str(yes_qty),
        "no_qty": str(no_qty),
        "matched_qty": str(min(yes_qty, no_qty)),
        "residual_qty": str(abs(yes_qty - no_qty)),
        "maker_turnover": str(yes_cost + no_cost),
        "maker_fees": str(fees),
    }


async def _exact_active(client_id: str, symbol: str) -> bool:
    """Authoritatively test whether one exact client ID remains active."""
    response = await private_rest_request(
        "POST",
        "/v1/prediction-markets/orders/active",
        params={"symbol": symbol, "limit": 100, "offset": 0},
        timeout=5.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"active-order verification failed http={response.status_code}")
    body = response.json()
    rows = body.get("orders", []) if isinstance(body, Mapping) else []
    return any(
        isinstance(row, Mapping) and str(row.get("clientOrderId") or "") == client_id
        for row in rows
    )


def _current_rest_is_safe(
    *,
    missing: str,
    filled_price: D,
    current_price: D,
    bid: D,
    ask: D,
    min_edge: D,
) -> bool:
    try:
        passive = completion.maker_passive_ceiling(
            missing_outcome=missing, yes_bid=bid, yes_ask=ask, tick=TICK
        )
        edge = completion.completed_edge(
            filled_price=filled_price, complement_price=current_price
        )
    except Exception:
        return False
    return MIN_PRICE <= current_price <= MAX_PRICE and current_price <= passive and edge >= min_edge


async def _post_replacement(
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
        "id": ladder.cid(sid, missing, sequence),
        "row": None,
    }
    payload = {
        "symbol": event.symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(spec["price"]),
        "quantity": "1",
        "eventOutcome": missing,
        "clientOrderId": spec["id"],
    }
    ack = await maker.request("order.place", payload, timeout=4.0)
    accepted = int(ack.get("status", 0)) == 200
    emit(
        "COMPLEMENT_REPOST",
        side=missing,
        sequence=sequence,
        client_id=spec["id"],
        price=str(spec["price"]),
        accepted=accepted,
        ack_status=ack.get("status"),
        maker_only_tif="MOC",
        order_entry=True,
    )
    if not accepted:
        return None
    spec["row"] = await ladder.base._find_row(spec["id"], event.symbol)
    if spec["row"] is None:
        raise RuntimeError("accepted replacement complement not discoverable")
    return spec


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
    """Reprice only the missing q1 outcome, cancel-before-repost, with hard bounds."""
    missing = "NO" if filled_side == "YES" else "YES"
    initial_fills = await ladder.fills(specs, event.symbol)
    sm = summarize(specs, initial_fills)
    yq, nq = D(sm["yes_qty"]), D(sm["no_qty"])
    if yq == ONE and nq == ONE:
        return "MATCHED"
    filled_qty = yq if filled_side == "YES" else nq
    missing_qty = nq if missing == "NO" else yq
    if filled_qty != ONE or missing_qty != ZERO:
        return "UNEXPECTED_FIRST_FILL_SHAPE"

    filled_specs = [s for s in specs if s["side"] == filled_side and D(str(initial_fills.get(s["id"], ZERO))) > ZERO]
    if len(filled_specs) != 1:
        return "AMBIGUOUS_FILLED_ORDER_IDENTITY"
    filled_price = D(str(filled_specs[0]["price"]))
    current_candidates = [s for s in specs if s["side"] == missing]
    if len(current_candidates) != 1:
        return "AMBIGUOUS_INITIAL_COMPLEMENT_IDENTITY"
    current = current_candidates[0]
    current_price = D(str(current["price"]))
    sequence = 0
    next_reprice = time.monotonic()
    deadline = min(
        time.monotonic() + float(complement_wait),
        time.monotonic() + max(1.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS),
    )

    emit(
        "COMPLEMENT_RESCUE_START",
        filled_side=filled_side,
        filled_price=str(filled_price),
        missing_side=missing,
        current_price=str(current_price),
        min_final_edge=str(min_edge),
        max_reprices=MAX_COMPLEMENT_REPRICES,
        order_entry=True,
    )

    while time.monotonic() < deadline:
        fs = await ladder.fills(specs, event.symbol)
        sm = summarize(specs, fs)
        yq, nq = D(sm["yes_qty"]), D(sm["no_qty"])
        if yq > ONE or nq > ONE:
            return "Q1_FILL_BOUND_VIOLATION"
        if yq == ONE and nq == ONE:
            return "MATCHED"
        mq = nq if missing == "NO" else yq
        if mq > ZERO:
            return "UNEXPECTED_FRACTIONAL_COMPLEMENT_FILL"

        now = time.monotonic()
        if now < next_reprice:
            await asyncio.sleep(min(0.25, max(0.05, deadline - now)))
            continue
        next_reprice = now + REPRICE_INTERVAL_SECONDS

        try:
            bid, _, ask, _ = await ladder.fresh_top_retry(event.symbol, attempts=2, delay_s=0.15)
        except RuntimeError:
            emit(
                "COMPLEMENT_DEPTH_UNAVAILABLE",
                current_id=current["id"],
                current_price=str(current_price),
                action="KEEP_EXISTING_COMPLEMENT",
                order_entry=True,
            )
            continue
        plan = completion.complement_plan(
            filled_outcome=filled_side,
            filled_price=filled_price,
            current_complement_price=current_price,
            yes_bid=bid,
            yes_ask=ask,
            min_final_edge=min_edge,
            tick=TICK,
            min_price=MIN_PRICE,
            max_price=MAX_PRICE,
        )
        target = plan.get("target_price")
        target_d = None if target is None else D(str(target))
        if plan.get("action") != "REPRICE" or target_d is None or target_d <= current_price:
            continue
        if sequence >= MAX_COMPLEMENT_REPRICES:
            continue

        cancel = await ladder.cancel(maker, current, event.symbol)
        await asyncio.sleep(0.12)
        fs_after_cancel = await ladder.fills(specs, event.symbol)
        sm_after_cancel = summarize(specs, fs_after_cancel)
        yq, nq = D(sm_after_cancel["yes_qty"]), D(sm_after_cancel["no_qty"])
        if yq == ONE and nq == ONE:
            emit(
                "COMPLEMENT_FILLED_DURING_CANCEL",
                prior_id=current["id"],
                prior_price=str(current_price),
                order_entry=True,
            )
            return "MATCHED"
        mq = nq if missing == "NO" else yq
        if mq > ZERO:
            return "UNEXPECTED_FRACTIONAL_COMPLEMENT_FILL"
        active = await _exact_active(current["id"], event.symbol)
        emit(
            "COMPLEMENT_REPRICE_CANCEL",
            side=missing,
            prior_id=current["id"],
            prior_price=str(current_price),
            proposed_target=str(target_d),
            cancel=cancel,
            exact_old_id_active=active,
            order_entry=True,
        )
        if active or not cancel.get("ok"):
            return "COMPLEMENT_CANCEL_AMBIGUOUS"

        # The old exact ID is now authoritatively inactive.  Re-read the book and
        # recompute both passivity and economics before creating a new ID.
        try:
            bid2, _, ask2, _ = await ladder.fresh_top_retry(event.symbol, attempts=3, delay_s=0.15)
        except RuntimeError:
            return "NO_FRESH_DEPTH_AFTER_COMPLEMENT_CANCEL"
        plan2 = completion.complement_plan(
            filled_outcome=filled_side,
            filled_price=filled_price,
            current_complement_price=current_price,
            yes_bid=bid2,
            yes_ask=ask2,
            min_final_edge=min_edge,
            tick=TICK,
            min_price=MIN_PRICE,
            max_price=MAX_PRICE,
        )
        target2 = plan2.get("target_price")
        target2_d = None if target2 is None else D(str(target2))
        if plan2.get("action") == "REPRICE" and target2_d is not None and target2_d > current_price:
            repost_price = target2_d
        elif _current_rest_is_safe(
            missing=missing,
            filled_price=filled_price,
            current_price=current_price,
            bid=bid2,
            ask=ask2,
            min_edge=min_edge,
        ):
            repost_price = current_price
        else:
            return "NO_SAFE_COMPLEMENT_REPOST_AFTER_CANCEL"

        sequence += 1
        replacement = await _post_replacement(
            maker,
            event=event,
            sid=sid,
            missing=missing,
            price=repost_price,
            sequence=sequence,
        )
        if replacement is None:
            return "COMPLEMENT_REPOST_REJECTED"
        specs.append(replacement)
        state["orders"] = [
            {"side": s["side"], "price": str(s["price"]), "id": s["id"]}
            for s in specs
        ]
        state["completion_reprices"] = sequence
        _write_state(state)
        emit(
            "COMPLEMENT_REPRICE_ACTIVE",
            side=missing,
            sequence=sequence,
            prior_price=str(current_price),
            price=str(repost_price),
            modeled_final_edge=str(
                completion.completed_edge(
                    filled_price=filled_price,
                    complement_price=repost_price,
                )
            ),
            order_entry=True,
        )
        current = replacement
        current_price = D(str(repost_price))

    cancel = await ladder.cancel(maker, current, event.symbol)
    await asyncio.sleep(0.15)
    fs = await ladder.fills(specs, event.symbol)
    sm = summarize(specs, fs)
    yq, nq = D(sm["yes_qty"]), D(sm["no_qty"])
    if yq == ONE and nq == ONE:
        return "MATCHED"
    active = await _exact_active(current["id"], event.symbol)
    if active or not cancel.get("ok"):
        return "RESCUE_TIMEOUT_CANCEL_AMBIGUOUS"
    return "RESCUE_EXPIRED_ONE_SIDED"


async def run(
    *,
    min_edge: D = DEFAULT_MIN_PAIR_EDGE,
    first_wait: float = DEFAULT_FIRST_WAIT,
    complement_wait: float = DEFAULT_COMPLEMENT_WAIT,
    min_tte: float = DEFAULT_MIN_TTE,
) -> None:
    if not armed():
        emit("DISARMED", q_per_side=1, max_new_residual_qty=1, order_entry=False)
        return
    if not await _gemini_terms_ok():
        raise RuntimeError("terms not accepted")
    if await _gemini_available_usd() < D("4"):
        raise RuntimeError("insufficient bounded pair balance")

    event = await discover_eth5m()
    if event is None or event.seconds_to_expiry < float(min_tte):
        emit("STOP", reason="NO_ETH5_WITH_ENOUGH_TTE", order_entry=False, realized_pnl=None)
        return

    bid, bid_size, ask, ask_size = await ladder.fresh_top_retry(event.symbol)
    sid = os.getenv(SID_ENV, "").strip()
    min_edge = D(str(min_edge))
    model = choose_pair(
        bid,
        ask,
        min_edge=min_edge,
        yes_queue=bid_size,
        no_queue=ask_size,
        tie_seed=sid,
    )
    if model is None:
        emit(
            "STOP",
            reason="NO_SAFE_PAIR_WITH_COMPLETION_RESERVE",
            symbol=event.symbol,
            event_ticker=event.ticker,
            bid=str(bid),
            ask=str(ask),
            min_pair_edge=str(min_edge),
            completion_reserve_ticks=COMPLETION_RESERVE_TICKS,
            order_entry=False,
            realized_pnl=None,
        )
        return

    specs = [
        {"side": "YES", "price": D(str(model["yes"])), "id": ladder.cid(sid, "YES", 0), "row": None},
        {"side": "NO", "price": D(str(model["no"])), "id": ladder.cid(sid, "NO", 0), "row": None},
    ]
    state: dict[str, Any] = {
        "terminal": False,
        "sid": sid,
        "symbol": event.symbol,
        "event": event.ticker,
        "yes_backoff": str(model["yes_backoff"]),
        "no_backoff": str(model["no_backoff"]),
        "total_backoff": str(model["total_backoff"]),
        "preferred_backoff_side": str(model["preferred_backoff_side"]),
        "entry_edge": str(model["edge"]),
        "completion_reserve_ticks": COMPLETION_RESERVE_TICKS,
        "completion_reprices": 0,
        "orders": [
            {"side": spec["side"], "price": str(spec["price"]), "id": spec["id"]}
            for spec in specs
        ],
    }
    _write_state(state)
    emit(
        "START",
        symbol=event.symbol,
        event_ticker=event.ticker,
        yes=str(model["yes"]),
        no=str(model["no"]),
        yes_backoff=str(model["yes_backoff"]),
        no_backoff=str(model["no_backoff"]),
        total_backoff=str(model["total_backoff"]),
        preferred_backoff_side=str(model["preferred_backoff_side"]),
        pair_edge=str(model["edge"]),
        completion_reserve_ticks=COMPLETION_RESERVE_TICKS,
        max_complement_reprices=MAX_COMPLEMENT_REPRICES,
        yes_queue_hint=str(bid_size),
        no_queue_hint=str(ask_size),
        max_new_residual_qty=1,
        max_new_residual_notional=str(MAX_PRICE),
        order_entry=True,
        realized_pnl=None,
    )

    maker = ladder.base.GeminiMakerSession()
    await maker.connect()
    try:
        posted: list[dict[str, Any]] = []
        for spec in specs:
            payload = {
                "symbol": event.symbol,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "MOC",
                "price": str(spec["price"]),
                "quantity": "1",
                "eventOutcome": spec["side"],
                "clientOrderId": spec["id"],
            }
            ack = await maker.request("order.place", payload, timeout=4.0)
            if int(ack.get("status", 0)) != 200:
                await asyncio.gather(
                    *(ladder.cancel(maker, item, event.symbol) for item in posted),
                    return_exceptions=True,
                )
                await asyncio.sleep(0.25)
                fills = await ladder.fills(specs, event.symbol)
                summary = summarize(specs, fills)
                state.update({"terminal": True, "status": "POST_REJECT", **summary})
                _write_state(state)
                emit("COMPLETE", status="POST_REJECT", **summary, order_entry=False, realized_pnl=None)
                return
            spec["row"] = await ladder.base._find_row(spec["id"], event.symbol)
            if spec["row"] is None:
                raise RuntimeError("accepted exact pair order not discoverable")
            posted.append(spec)

        emit("PAIR_POSTED", n_orders=2, order_entry=True)
        ids = {spec["id"]: spec["side"] for spec in specs}
        first_side: str | None = None
        first_deadline = min(
            time.monotonic() + float(first_wait),
            time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0),
        )
        while time.monotonic() < first_deadline and first_side is None:
            try:
                update = await asyncio.wait_for(maker.events.get(), timeout=0.4)
            except asyncio.TimeoutError:
                continue
            if not isinstance(update, Mapping):
                continue
            client = str(update.get("c") or "")
            qty = D(str(update.get("Z") or "0"))
            status = str(update.get("X") or "").upper()
            if client in ids and qty > ZERO and status in {"FILLED", "PARTIALLY_FILLED"}:
                first_side = ids[client]
                emit(
                    "FIRST_FILL",
                    side=first_side,
                    client_id=client,
                    reported_qty=str(qty),
                    action="DYNAMIC_COMPLEMENT_RESCUE",
                    order_entry=True,
                )

        if first_side is None:
            await asyncio.gather(
                *(ladder.cancel(maker, spec, event.symbol) for spec in specs),
                return_exceptions=True,
            )
            await asyncio.sleep(0.3)
            fills = await ladder.fills(specs, event.symbol)
            summary = summarize(specs, fills)
            yes_qty, no_qty = D(summary["yes_qty"]), D(summary["no_qty"])
            status = (
                "MATCHED"
                if yes_qty > ZERO and yes_qty == no_qty
                else "ONE_SIDED_RESIDUAL"
                if yes_qty > ZERO or no_qty > ZERO
                else "NO_FILL_TIMEOUT"
            )
            state.update({"terminal": True, "status": status, **summary})
            _write_state(state)
            emit("COMPLETE", status=status, **summary, order_entry=False, realized_pnl=None)
            return

        rescue_status = await _dynamic_complement_rescue(
            maker,
            event=event,
            sid=sid,
            specs=specs,
            state=state,
            filled_side=first_side,
            min_edge=min_edge,
            complement_wait=complement_wait,
        )
        # Cancel any exact orders still represented by this cycle before final
        # authoritative reconciliation.  Filled/cancelled rows are harmless; the
        # cancel helper targets captured exact order IDs and never broad-cancels
        # while a row is present.
        await asyncio.gather(
            *(ladder.cancel(maker, spec, event.symbol) for spec in specs),
            return_exceptions=True,
        )
        await asyncio.sleep(0.35)
        fills = await ladder.fills(specs, event.symbol)
        summary = summarize(specs, fills)
        yes_qty, no_qty = D(summary["yes_qty"]), D(summary["no_qty"])
        if yes_qty > ONE or no_qty > ONE:
            raise RuntimeError(f"q1 pair fill bound violated yes={yes_qty} no={no_qty}")
        status = (
            "MATCHED_DYNAMIC_COMPLETION"
            if yes_qty == ONE and no_qty == ONE
            else "ONE_SIDED_RESIDUAL"
            if yes_qty > ZERO or no_qty > ZERO
            else "NO_AUTHORITATIVE_FILL"
        )
        state.update({"terminal": True, "status": status, "rescue_status": rescue_status, **summary})
        _write_state(state)
        emit(
            "COMPLETE",
            status=status,
            rescue_status=rescue_status,
            **summary,
            settlement_required=(yes_qty > ZERO or no_qty > ZERO),
            order_entry=False,
            realized_pnl=None,
        )
    finally:
        await maker.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
