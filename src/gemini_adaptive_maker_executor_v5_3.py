"""Gemini BTC5 adaptive maker executor V5.3.

V5.3 keeps V5.2's ambiguity-safe reconciliation/FOK hardening but removes the
liquidity-reward admission premise. It selects the largest q=1..10 MOC maker clip
that is independently safe on both first-fill paths under the unchanged $0.30 rescue
cap and preserves >=5c/contract completed-pair profit after conservative maker fees.

No liquidity rewards or maker rebates are included in modeled P&L. Live mutation
still requires V5's existing dual-key arm and one-campaign controls.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal as D
import os
import time
from typing import Any, Mapping

from src import gemini_adaptive_maker_policy_v5_3 as policy
from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_production_executor_v5_2 as v52
from src import gemini_q10_rescue_policy as rescue
from src.gemini_prediction_depth_cache_v5_3 import GeminiDepth5Cache, DepthSnapshot

ZERO = D("0")
MAX_Q = D("10")
CANDIDATE_WAIT_SECONDS = 600.0
CANDIDATE_POLL_SECONDS = 2.0


def _event_contract_ok(event: Any) -> bool:
    return bool(
        D(str(event.quantity_minimum)) == D("1")
        and D(str(event.quantity_increment)) == D("1")
        and D(str(event.price_increment)) > ZERO
    )


async def _close_cache(cache_stop: asyncio.Event | None, cache_task: asyncio.Task | None) -> None:
    if cache_stop is not None:
        cache_stop.set()
    if cache_task is not None:
        cache_task.cancel()
        await asyncio.gather(cache_task, return_exceptions=True)


async def wait_adaptive_candidate() -> tuple[Any, GeminiDepth5Cache, asyncio.Event, asyncio.Task, policy.AdaptiveMakerPlan] | None:
    """Discover a BTC5 window and keep one public depth subscription hot until safe."""
    deadline = time.monotonic() + CANDIDATE_WAIT_SECONDS
    cache: GeminiDepth5Cache | None = None
    cache_symbol: str | None = None
    cache_stop: asyncio.Event | None = None
    cache_task: asyncio.Task | None = None
    try:
        while time.monotonic() < deadline:
            try:
                event = await v5.v1.base.discover_btc5m()
            except Exception as exc:
                v5.v1.emit(
                    "V5_3_CANDIDATE_RETRY",
                    reason="EVENT_DISCOVERY_ERROR",
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:240],
                    order_entry=False,
                )
                await asyncio.sleep(CANDIDATE_POLL_SECONDS)
                continue
            if event is None:
                await asyncio.sleep(CANDIDATE_POLL_SECONDS)
                continue
            if not _event_contract_ok(event):
                v5.v1.emit(
                    "V5_3_CANDIDATE_RETRY",
                    reason="CONTRACT_METADATA_UNSUPPORTED",
                    ticker=event.ticker,
                    symbol=event.symbol,
                    quantity_minimum=str(event.quantity_minimum),
                    quantity_increment=str(event.quantity_increment),
                    price_increment=str(event.price_increment),
                    order_entry=False,
                )
                await asyncio.sleep(CANDIDATE_POLL_SECONDS)
                continue
            if not (v5.v1.ENTRY_MIN_SECONDS <= event.seconds_to_expiry <= v5.v1.ENTRY_MAX_SECONDS):
                await asyncio.sleep(CANDIDATE_POLL_SECONDS)
                continue

            if cache_symbol != event.symbol:
                await _close_cache(cache_stop, cache_task)
                cache = GeminiDepth5Cache(event.symbol, stale_ms=750)
                cache_symbol = event.symbol
                cache_stop = asyncio.Event()
                cache_task = asyncio.create_task(cache.run(cache_stop))

            assert cache is not None and cache_stop is not None and cache_task is not None
            try:
                snap = await cache.wait_fresh(timeout=4.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
            except Exception as exc:
                v5.v1.emit(
                    "V5_3_CANDIDATE_RETRY",
                    reason="DEPTH_NOT_FRESH",
                    ticker=event.ticker,
                    symbol=event.symbol,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:240],
                    cache_error=cache.last_error,
                    order_entry=False,
                )
                await asyncio.sleep(CANDIDATE_POLL_SECONDS)
                continue

            plan = policy.select_plan(
                snap=snap,
                increment=event.price_increment,
                max_loss=v5.max_rescue_loss(),
            )
            if plan is not None:
                v5.v1.emit(
                    "V5_3_CANDIDATE",
                    ticker=event.ticker,
                    symbol=event.symbol,
                    seconds_to_expiry=round(event.seconds_to_expiry, 3),
                    book_age_ms=round(snap.age_ms, 3),
                    plan=plan.as_dict(),
                    liquidity_rewards_modeled=False,
                    maker_rebates_modeled=False,
                    order_entry=False,
                )
                keep_cache = cache
                keep_stop = cache_stop
                keep_task = cache_task
                cache = None
                cache_stop = None
                cache_task = None
                return event, keep_cache, keep_stop, keep_task, plan

            v5.v1.emit(
                "V5_3_CANDIDATE_RETRY",
                reason="NO_SAFE_TRADE_EDGE_PLAN",
                ticker=event.ticker,
                symbol=event.symbol,
                seconds_to_expiry=round(event.seconds_to_expiry, 3),
                bid=str(snap.bid),
                ask=str(snap.ask),
                order_entry=False,
            )
            await asyncio.sleep(CANDIDATE_POLL_SECONDS)
        return None
    finally:
        await _close_cache(cache_stop, cache_task)


def _route_after_fill_external(
    *,
    plan: policy.AdaptiveMakerPlan,
    snap: DepthSnapshot,
    filled_outcome: str,
    increment: D,
    yes_resting: bool,
    no_resting: bool,
) -> rescue.RescueRoute | None:
    external = snap.externalized(
        own_yes_price=plan.yes_quote if yes_resting else None,
        own_yes_quantity=plan.quantity if yes_resting else ZERO,
        own_no_price=plan.no_quote if no_resting else None,
        own_no_quantity=plan.quantity if no_resting else ZERO,
    )
    if external is None:
        return None
    fill_price = plan.yes_quote if filled_outcome == "YES" else plan.no_quote
    return rescue.best_route_after_fill(
        filled_outcome=filled_outcome,
        filled_price=fill_price,
        quantity=plan.quantity,
        yes_bid=external.bid,
        yes_bid_size=external.bid_size,
        yes_ask=external.ask,
        yes_ask_size=external.ask_size,
        max_loss=v5.max_rescue_loss(),
        increment=increment,
    )


async def _lookup_exact(client_id: str, symbol: str) -> Mapping[str, Any] | None:
    return await v5.v1.base._order_by_client(client_id, symbol)


async def _cancel_one_exact(
    maker: Any,
    *,
    row: Mapping[str, Any] | None,
    client_id: str,
    symbol: str,
) -> tuple[Mapping[str, Any] | None, D, dict[str, Any]]:
    cancel = await v5.v1.base._cancel_maker(maker, row=row)
    await asyncio.sleep(0.08)
    resolved = await _lookup_exact(client_id, symbol)
    if resolved is None:
        return None, ZERO, cancel
    return resolved, v5.v1.base._filled(resolved), cancel


async def calm_maker_grace_v53(
    maker: Any,
    *,
    cache: GeminiDepth5Cache,
    guard: Any,
    symbol: str,
    filled_outcome: str,
    filled_price: D,
    quantity: D,
    increment: D,
    trade_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Brief exact-quantity maker completion with ambiguity-safe reconciliation."""
    spot = guard.decision()
    grace = rescue.maker_grace_seconds(
        spot_dangerous=(not spot.ready or spot.dangerous),
        calm_grace_seconds=v5.calm_grace_seconds(),
    )
    if grace <= 0:
        return {"status": "ZERO_GRACE_TO_RESCUE", "maker_filled": "0", "remaining": str(quantity)}

    try:
        snap = await cache.wait_fresh(timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
    except Exception as exc:
        return {
            "status": "GRACE_BOOK_NOT_FRESH",
            "maker_filled": "0",
            "remaining": str(quantity),
            "hard_halt": True,
            "error_type": type(exc).__name__,
        }
    route = rescue.best_route_after_fill(
        filled_outcome=filled_outcome,
        filled_price=filled_price,
        quantity=quantity,
        yes_bid=snap.bid,
        yes_bid_size=snap.bid_size,
        yes_ask=snap.ask,
        yes_ask_size=snap.ask_size,
        max_loss=v5.max_rescue_loss(),
        increment=increment,
    )
    if not route.safe:
        return {"status": "GRACE_SKIPPED_RESCUE_NOT_SAFE", "maker_filled": "0", "remaining": str(quantity)}

    missing = "NO" if filled_outcome == "YES" else "YES"
    maker_price = (D("1") - snap.ask) if missing == "NO" else snap.bid
    if not (D("0.01") <= maker_price <= D("0.99")):
        return {"status": "GRACE_SKIPPED_BAD_PRICE", "maker_filled": "0", "remaining": str(quantity)}

    cid = f"{trade_id}-grace"[:36]
    v5._heartbeat(
        state,
        stage="CALM_MAKER_GRACE_PENDING",
        pending_grace={
            "client_order_id": cid,
            "outcome": missing,
            "price": str(maker_price),
            "quantity": str(quantity),
        },
    )
    pre_post_sequence = snap.sequence
    ack, row, ack_ms = await v5.v1._post(
        maker,
        symbol=symbol,
        outcome=missing,
        price=maker_price,
        quantity=quantity,
        client_id=cid,
    )
    v5.v1.emit(
        "V5_3_GRACE_POST",
        client_order_id=cid,
        missing_outcome=missing,
        price=str(maker_price),
        quantity=str(quantity),
        grace_seconds=grace,
        ack_status=ack.get("status"),
        ack_latency_ms=round(ack_ms, 3),
        order_entry=True,
    )
    if int(ack.get("status", 0)) != 200:
        v5._heartbeat(state, stage="GRACE_MAKER_REJECTED", pending_grace=None)
        return {"status": "GRACE_MAKER_REJECTED", "maker_filled": "0", "remaining": str(quantity)}
    if row is None:
        v5._heartbeat(state, stage="GRACE_STATUS_UNKNOWN", open=True)
        return {
            "status": "GRACE_STATUS_UNKNOWN",
            "maker_filled": None,
            "remaining": None,
            "hard_halt": True,
        }

    try:
        await cache.wait_newer(pre_post_sequence, timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
    except Exception:
        # We know the order row exists, so cancel it exactly before deciding whether
        # any remaining directional quantity can move to FOK rescue.
        resolved, filled, cancel = await _cancel_one_exact(
            maker, row=row, client_id=cid, symbol=symbol
        )
        if resolved is None:
            v5._heartbeat(state, stage="GRACE_CANCEL_RECONCILE_AMBIGUOUS", open=True)
            return {
                "status": "GRACE_CANCEL_RECONCILE_AMBIGUOUS",
                "hard_halt": True,
                "maker_filled": None,
                "remaining": None,
                "cancel": cancel,
            }
        remaining = max(ZERO, quantity - min(quantity, filled))
        v5._heartbeat(
            state,
            stage="GRACE_ENDED_NO_DEPTH_UPDATE",
            pending_grace=None,
            grace_last_client_id=cid,
            grace_last_fill=str(filled),
        )
        return {
            "status": "GRACE_ENDED_NO_DEPTH_UPDATE",
            "maker_filled": str(filled),
            "remaining": str(remaining),
            "cancel": cancel,
            "hard_halt": remaining > ZERO,
        }

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if maker.reader is None or maker.reader.done():
            break
        spot_now = guard.decision()
        if not spot_now.ready or spot_now.dangerous:
            break
        try:
            snap_now = await cache.wait_fresh(timeout=0.20, max_age_ms=v5.BOOK_MAX_AGE_MS)
        except Exception:
            break
        external = snap_now.externalized(
            own_yes_price=maker_price if missing == "YES" else None,
            own_yes_quantity=quantity if missing == "YES" else ZERO,
            own_no_price=maker_price if missing == "NO" else None,
            own_no_quantity=quantity if missing == "NO" else ZERO,
        )
        if external is None:
            break
        route_now = rescue.best_route_after_fill(
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            quantity=quantity,
            yes_bid=external.bid,
            yes_bid_size=external.bid_size,
            yes_ask=external.ask,
            yes_ask_size=external.ask_size,
            max_loss=v5.max_rescue_loss(),
            increment=increment,
        )
        if not route_now.safe:
            break
        try:
            item = await asyncio.wait_for(maker.events.get(), timeout=v5.BOOK_CHECK_SECONDS)
        except asyncio.TimeoutError:
            continue
        if not isinstance(item, Mapping) or str(item.get("c") or "") != cid:
            continue
        if str(item.get("X") or "").upper() in {"PARTIALLY_FILLED", "FILLED"}:
            q = D(str(item.get("Z") or "0"))
            if q >= quantity:
                v5._heartbeat(
                    state,
                    stage="GRACE_COMPLEMENT_FILLED",
                    pending_grace=None,
                    grace_last_client_id=cid,
                    grace_last_fill=str(quantity),
                )
                return {
                    "status": "GRACE_COMPLEMENT_FILLED",
                    "maker_filled": str(quantity),
                    "remaining": "0",
                }

    pre_cancel = (cache.snapshot().sequence if cache.snapshot() is not None else 0)
    resolved, filled, cancel = await _cancel_one_exact(
        maker, row=row, client_id=cid, symbol=symbol
    )
    if resolved is None:
        v5._heartbeat(state, stage="GRACE_CANCEL_RECONCILE_AMBIGUOUS", open=True)
        return {
            "status": "GRACE_CANCEL_RECONCILE_AMBIGUOUS",
            "hard_halt": True,
            "maker_filled": None,
            "remaining": None,
            "cancel": cancel,
        }
    filled = min(quantity, filled)
    remaining = max(ZERO, quantity - filled)
    if remaining > ZERO:
        try:
            await cache.wait_newer(pre_cancel, timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
        except Exception:
            v5._heartbeat(
                state,
                stage="GRACE_CANCEL_DEPTH_AMBIGUOUS",
                open=True,
                pending_grace=None,
                grace_last_client_id=cid,
                grace_last_fill=str(filled),
            )
            return {
                "status": "GRACE_CANCEL_DEPTH_AMBIGUOUS",
                "hard_halt": True,
                "maker_filled": str(filled),
                "remaining": str(remaining),
                "cancel": cancel,
            }
    v5._heartbeat(
        state,
        stage="GRACE_ENDED",
        pending_grace=None,
        grace_last_client_id=cid,
        grace_last_fill=str(filled),
    )
    return {
        "status": "GRACE_ENDED",
        "maker_filled": str(filled),
        "remaining": str(remaining),
        "cancel": cancel,
    }


async def run_campaign_v53(*, guard: Any, campaign_no: int) -> dict[str, Any]:
    available, flat = await v5.v1._preflight_flat()
    if guard.decision().dangerous:
        return {"status": "SPOT_DANGEROUS_PRE_CANDIDATE", "order_entry": False}

    found = await wait_adaptive_candidate()
    if found is None:
        v5.v1.emit("V5_3_NO_CANDIDATE", order_entry=False, classification="NO_OP_NOT_FAILURE")
        return {"status": "NO_CANDIDATE", "order_entry": False}
    event, cache, cache_stop, cache_task, _candidate_plan = found
    maker = v5.v1.base.GeminiMakerSession()
    state: dict[str, Any] = {}
    yes_row = no_row = None
    try:
        # Recompute from fresh public depth immediately before mutation. The plan
        # selected during discovery is evidence only; it does not grant admission.
        snap = await cache.wait_fresh(timeout=1.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
        plan = policy.select_plan(
            snap=snap,
            increment=event.price_increment,
            max_loss=v5.max_rescue_loss(),
        )
        if plan is None:
            return {"status": "FRESH_ADAPTIVE_PLAN_FAILED", "order_entry": False}
        spot = guard.decision()
        if not spot.ready or spot.dangerous:
            return {"status": "SPOT_DANGEROUS_PRE_MUTATION", "order_entry": False}

        run_id = v5.v1._clean(os.environ[v5.v1.ARM_ID_ENV])
        trade_id = f"gadv53-{run_id}-{campaign_no}"[:24]
        yes_id = f"{trade_id}-yes"[:36]
        no_id = f"{trade_id}-no"[:36]
        qty = plan.quantity
        state = {
            "classification": "LIVE_ADAPTIVE_MAKER_V5_3_OPEN_NOT_REALIZED",
            "run_id": run_id,
            "trade_id": trade_id,
            "campaign_no": campaign_no,
            "ticker": event.ticker,
            "symbol": event.symbol,
            "yes_id": yes_id,
            "no_id": no_id,
            "yes_quote": str(plan.yes_quote),
            "no_quote": str(plan.no_quote),
            "price_increment": str(event.price_increment),
            "quantity": str(qty),
            "max_quantity": "10",
            "max_rescue_loss": str(v5.max_rescue_loss()),
            "max_campaign_loss": str(v5.max_campaign_loss()),
            "available_usd_before": str(available),
            "adaptive_plan": plan.as_dict(),
            "owned_client_ids": [yes_id, no_id],
            "liquidity_rewards_modeled": False,
            "maker_rebates_modeled": False,
            "status": "PRE_MUTATION",
            "open": True,
            "realized_pnl": None,
            "rewards_realized": None,
        }
        v5._heartbeat(state, stage="PRE_MUTATION")
        v5.v1.emit("V5_3_CAMPAIGN_START", **state, spot=asdict(spot), flat=flat)

        await maker.connect()
        pre_yes_sequence = snap.sequence
        yes_ack, yes_row, yes_ms = await v5.v1._post(
            maker,
            symbol=event.symbol,
            outcome="YES",
            price=plan.yes_quote,
            quantity=qty,
            client_id=yes_id,
        )
        if int(yes_ack.get("status", 0)) != 200:
            v5._heartbeat(state, stage="YES_REJECTED", open=False)
            return {"status": "YES_REJECTED", "order_entry": True}
        if yes_row is None:
            v5._heartbeat(state, stage="YES_ACK_ROW_UNRESOLVED", open=True)
            return {"status": "YES_ACK_ROW_UNRESOLVED", "order_entry": True, "hard_halt": True}
        v5.v1.emit("V5_3_YES_POST", ack_latency_ms=round(yes_ms, 3), quantity=str(qty), order_entry=True)

        try:
            snap_after_yes = await cache.wait_newer(
                pre_yes_sequence, timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS
            )
        except Exception:
            snap_after_yes = None
        yes_route = None if snap_after_yes is None else _route_after_fill_external(
            plan=plan,
            snap=snap_after_yes,
            filled_outcome="YES",
            increment=event.price_increment,
            yes_resting=True,
            no_resting=False,
        )
        spot2 = guard.decision()
        if yes_route is None or not yes_route.safe or not spot2.ready or spot2.dangerous:
            yfinal, yf, cancel = await _cancel_one_exact(
                maker, row=yes_row, client_id=yes_id, symbol=event.symbol
            )
            if yfinal is None:
                v5._heartbeat(state, stage="FIRST_POST_CANCEL_RECONCILE_AMBIGUOUS", open=True)
                return {
                    "status": "FIRST_POST_CANCEL_RECONCILE_AMBIGUOUS",
                    "order_entry": True,
                    "hard_halt": True,
                    "cancel": cancel,
                }
            if yf == ZERO:
                v5._heartbeat(state, stage="FIRST_POST_PULLED_ZERO_FILL", open=False)
                return {"status": "FIRST_POST_PULLED_ZERO_FILL", "order_entry": True}
            nf = ZERO
            no_row = None
            yfinal_resolved = yfinal
            nfinal_resolved = None
        else:
            pre_no_sequence = snap_after_yes.sequence
            no_ack, no_row, no_ms = await v5.v1._post(
                maker,
                symbol=event.symbol,
                outcome="NO",
                price=plan.no_quote,
                quantity=qty,
                client_id=no_id,
            )
            if int(no_ack.get("status", 0)) != 200:
                yfinal, yf, cancel = await _cancel_one_exact(
                    maker, row=yes_row, client_id=yes_id, symbol=event.symbol
                )
                if yfinal is None:
                    v5._heartbeat(state, stage="NO_REJECTED_CANCEL_RECONCILE_AMBIGUOUS", open=True)
                    return {
                        "status": "NO_REJECTED_CANCEL_RECONCILE_AMBIGUOUS",
                        "order_entry": True,
                        "hard_halt": True,
                        "cancel": cancel,
                    }
                if yf == ZERO:
                    v5._heartbeat(state, stage="NO_REJECTED_ZERO_FILL", open=False)
                    return {"status": "NO_REJECTED_ZERO_FILL", "order_entry": True}
                nf = ZERO
                yfinal_resolved = yfinal
                nfinal_resolved = None
            elif no_row is None:
                v5._heartbeat(state, stage="NO_ACK_ROW_UNRESOLVED", open=True)
                return {"status": "NO_ACK_ROW_UNRESOLVED", "order_entry": True, "hard_halt": True}
            else:
                v5.v1.emit("V5_3_NO_POST", ack_latency_ms=round(no_ms, 3), quantity=str(qty), order_entry=True)
                try:
                    await cache.wait_newer(
                        pre_no_sequence, timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS
                    )
                except Exception:
                    pass
                v5._heartbeat(state, stage="BOTH_POSTED")
                yf = nf = ZERO
                yfinal_resolved, nfinal_resolved = yes_row, no_row
                deadline = min(
                    time.monotonic() + v5.v1.REST_SECONDS,
                    time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0),
                )
                last_heartbeat = 0.0
                pull_reason = None
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now - last_heartbeat >= 1.0:
                        v5._heartbeat(state, stage="BOTH_POSTED")
                        last_heartbeat = now
                    if maker.reader is None or maker.reader.done():
                        pull_reason = "MAKER_SESSION_LOST"
                        break
                    spot_now = guard.decision()
                    try:
                        snap_now = await cache.wait_fresh(timeout=0.20, max_age_ms=v5.BOOK_MAX_AGE_MS)
                    except Exception:
                        pull_reason = "BOOK_STALE"
                        break
                    standing = policy.standing_rescue_for_plan(
                        plan=plan,
                        snap=snap_now,
                        increment=event.price_increment,
                        yes_resting=True,
                        no_resting=True,
                        max_loss=v5.max_rescue_loss(),
                    )
                    if not spot_now.ready or spot_now.dangerous:
                        pull_reason = "SPOT_TOXIC"
                        break
                    if standing is None or not standing.safe:
                        pull_reason = "RESCUE_BOUND_BROKE"
                        break
                    try:
                        item = await asyncio.wait_for(maker.events.get(), timeout=v5.BOOK_CHECK_SECONDS)
                    except asyncio.TimeoutError:
                        continue
                    if not isinstance(item, Mapping) or str(item.get("c") or "") not in {yes_id, no_id}:
                        continue
                    event_fill = D(str(item.get("Z") or "0"))
                    status = str(item.get("X") or "").upper()
                    if event_fill > ZERO and status in {"PARTIALLY_FILLED", "FILLED"}:
                        pull_reason = "FIRST_FILL"
                        break

                yf, nf, yfinal_resolved, nfinal_resolved = await v5._cancel_original_pair(
                    maker,
                    yes_id=yes_id,
                    no_id=no_id,
                    symbol=event.symbol,
                    yes_row=yes_row,
                    no_row=no_row,
                )
                v5.v1.emit(
                    "V5_3_PAIR_PULLED",
                    reason=pull_reason or "REST_WINDOW_END",
                    yes_fill=str(yf),
                    no_fill=str(nf),
                    quantity=str(qty),
                    order_entry=True,
                )
                if yfinal_resolved is None or nfinal_resolved is None:
                    v5._heartbeat(
                        state,
                        stage="RECONCILE_AMBIGUOUS",
                        open=True,
                        reconcile_reason="ACKED_ORDER_ROW_UNRESOLVED",
                        pull_reason=pull_reason,
                    )
                    return {"status": "RECONCILE_AMBIGUOUS", "order_entry": True, "hard_halt": True}
                if pull_reason == "FIRST_FILL" and yf == ZERO and nf == ZERO:
                    v5._heartbeat(
                        state,
                        stage="RECONCILE_AMBIGUOUS",
                        open=True,
                        reconcile_reason="WS_FIRST_FILL_CONTRADICTED_BY_ZERO_RECONCILE",
                    )
                    return {"status": "RECONCILE_AMBIGUOUS", "order_entry": True, "hard_halt": True}

        if yf > qty or nf > qty:
            v5._heartbeat(
                state,
                stage="SELECTED_QUANTITY_BOUND_VIOLATION",
                open=True,
                yes_fill=str(yf),
                no_fill=str(nf),
            )
            return {"status": "SELECTED_QUANTITY_BOUND_VIOLATION", "order_entry": True, "hard_halt": True}
        if yf == ZERO and nf == ZERO:
            v5._heartbeat(state, stage="ZERO_FILL_CLEAN_EXIT", open=False)
            return {"status": "ZERO_FILL_CLEAN_EXIT", "order_entry": True}
        if yf == nf:
            v5._heartbeat(
                state,
                stage="MATCHED_NOT_SETTLED",
                yes_fill=str(yf),
                no_fill=str(nf),
                residual="0",
            )
            return {"status": "MATCHED_NOT_SETTLED", "order_entry": True, "matched": str(yf), "residual": "0"}

        residual_qty = abs(yf - nf)
        if residual_qty > qty:
            v5._heartbeat(state, stage="RESIDUAL_SELECTED_QUANTITY_VIOLATION", open=True)
            return {"status": "RESIDUAL_SELECTED_QUANTITY_VIOLATION", "order_entry": True, "hard_halt": True}
        filled_outcome = "YES" if yf > nf else "NO"
        filled_row = yfinal_resolved if filled_outcome == "YES" else nfinal_resolved
        fallback_price = plan.yes_quote if filled_outcome == "YES" else plan.no_quote
        filled_price = v5._fill_price(filled_row, fallback_price)
        v5._heartbeat(
            state,
            stage="RESIDUAL_IDENTIFIED",
            yes_fill=str(yf),
            no_fill=str(nf),
            residual_outcome=filled_outcome,
            residual_quantity=str(residual_qty),
            residual_fill_price=str(filled_price),
        )

        grace_result = await calm_maker_grace_v53(
            maker,
            cache=cache,
            guard=guard,
            symbol=event.symbol,
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            quantity=residual_qty,
            increment=event.price_increment,
            trade_id=trade_id,
            state=state,
        )
        if grace_result.get("hard_halt"):
            return {
                "status": str(grace_result.get("status") or "GRACE_HARD_HALT"),
                "order_entry": True,
                "completion": grace_result,
                "hard_halt": True,
            }
        remaining = D(str(grace_result.get("remaining") or residual_qty))
        if remaining == ZERO:
            v5._heartbeat(state, stage="GRACE_COMPLETED_PAIR", completion=grace_result)
            return {"status": "GRACE_COMPLETED_PAIR", "order_entry": True, "completion": grace_result}

        fok = await v52.bounded_fok_v52(
            maker,
            cache=cache,
            symbol=event.symbol,
            filled_outcome=filled_outcome,
            filled_price=filled_price,
            quantity=remaining,
            increment=event.price_increment,
            trade_id=trade_id,
            state=state,
        )
        v5._heartbeat(state, stage=fok["status"], completion=grace_result, rescue=fok)
        return {
            "status": fok["status"],
            "order_entry": True,
            "completion": grace_result,
            "rescue": fok,
            "hard_halt": bool(fok.get("hard_halt")),
        }
    finally:
        try:
            if maker.ws is not None:
                await maker.request("order.cancel_session", {}, timeout=1.5)
        except Exception:
            pass
        try:
            await maker.close()
        except Exception:
            pass
        await _close_cache(cache_stop, cache_task)


def install() -> None:
    """Install frozen V5.2 safety patches, then replace admission/execution with V5.3."""
    v52.install()
    v5._run_campaign = run_campaign_v53


async def run() -> None:
    install()
    await v5.run()


if __name__ == "__main__":
    asyncio.run(run())
