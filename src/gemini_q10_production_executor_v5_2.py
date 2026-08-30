"""Gemini BTC5 q10 V5.2 safety/instrumentation patch layer.

V5.2 is intentionally additive on top of the frozen V5.1 release. It changes no
live permission and does not widen admission economics. It applies the review fixes
needed before another canary can be informative:
- proven three-attempt fresh-only depth acquisition in the pre-candidate qualifier;
- qualifier retry messages include a bounded error message;
- acknowledged-but-unfindable rescue FOK is an ambiguity hard halt, never retried;
- order-history reconciliation covers the full campaign/rest window;
- BOTH_POSTED pulls immediately on maker-session loss;
- first-fill/reconcile contradictions remain open and hard-halt;
- rescue standing checks subtract our own resting q10 from displayed top depth.

Dead-man/recovery process fixes live in the companion V5.2 modules.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from decimal import Decimal as D
import time
from typing import Any, Mapping

from src import gemini_btc5m_fresh_depth_retry as depth_retry
from src import gemini_kalshi_xrp_maker_live_canary as xrp_maker
from src import gemini_q10_production_executor_v5 as v5
from src import gemini_q10_production_executor_v5_1 as v51
from src import gemini_q10_rescue_policy as rescue
from src.gemini_prediction_book_cache import BookSnapshot

ZERO = D("0")
Q = D("10")
HISTORY_WINDOW_MS = 600_000
_ORIGINAL_FRESH_TOP = v5.v1.base.fresh_top


async def reliable_fresh_top(symbol: str, timeout: float = 4.0):
    return await depth_retry.reliable_fresh_top(
        _ORIGINAL_FRESH_TOP,
        symbol,
        attempts=3,
        timeout_seconds=timeout,
        retry_delay_seconds=0.25,
    )


async def wait_candidate_v52():
    deadline = time.monotonic() + v5.v1.probe.QUALIFIER_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            found = await v5.v1.probe._candidate()
        except Exception as exc:
            v5.v1.probe.emit(
                "QUALIFIER_RETRY",
                error_type=type(exc).__name__,
                error_message=str(exc)[:240],
                order_entry=False,
            )
            found = None
        if found is not None:
            return found
        await asyncio.sleep(v5.v1.probe.QUALIFIER_POLL_SECONDS)
    raise RuntimeError("no qualifying zero-competition BTC5m reward market within bounded wait")


async def order_by_client_v52(client_id: str, symbol: str) -> dict[str, Any] | None:
    """Lookup over a history window that safely exceeds the 180s rest window."""
    now_ms = int(time.time() * 1000)
    queries = [
        ("/v1/prediction-markets/orders/active", {"symbol": symbol, "limit": 100, "offset": 0}),
        ("/v1/prediction-markets/orders/history", {
            "status": "filled", "symbol": symbol, "limit": 100,
            "from": now_ms - HISTORY_WINDOW_MS, "to": now_ms + 1000,
        }),
        ("/v1/prediction-markets/orders/history", {
            "status": "cancelled", "symbol": symbol, "limit": 100,
            "from": now_ms - HISTORY_WINDOW_MS, "to": now_ms + 1000,
        }),
    ]
    for path, params in queries:
        response = await xrp_maker.private_rest_request("POST", path, params=params, timeout=4.0)
        if response.status_code != 200:
            continue
        body = response.json()
        for row in body.get("orders", []) if isinstance(body, Mapping) else []:
            if isinstance(row, Mapping) and str(row.get("clientOrderId") or "") == client_id:
                return dict(row)
    return None


def _external_depth_snapshot(
    model: Mapping[str, D],
    snap: BookSnapshot,
    *,
    yes_resting: bool,
    no_resting: bool,
) -> tuple[D, D]:
    """Conservatively remove our own resting full q10 from displayed top depth."""
    bid_size = D(str(snap.bid_size))
    ask_size = D(str(snap.ask_size))
    if yes_resting and D(str(model["yes_quote"])) == D(str(snap.bid)):
        bid_size = max(ZERO, bid_size - Q)
    no_as_yes_ask = D("1") - D(str(model["no_quote"]))
    if no_resting and no_as_yes_ask == D(str(snap.ask)):
        ask_size = max(ZERO, ask_size - Q)
    return bid_size, ask_size


def standing_external(model: Mapping[str, D], snap: BookSnapshot, *, increment: D):
    bid_size, ask_size = _external_depth_snapshot(
        model, snap, yes_resting=True, no_resting=True,
    )
    return rescue.standing_rescue_check(
        yes_quote=D(str(model["yes_quote"])),
        no_quote=D(str(model["no_quote"])),
        quantity=Q,
        yes_bid=snap.bid,
        yes_bid_size=bid_size,
        yes_ask=snap.ask,
        yes_ask_size=ask_size,
        max_loss=v5.max_rescue_loss(),
        increment=increment,
    )


async def bounded_fok_v52(maker: Any, *, cache: Any, symbol: str,
                          filled_outcome: str, filled_price: D, quantity: D,
                          increment: D, trade_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """V5 FOK rescue with fail-closed handling for a 200 ack whose row is unresolved."""
    if not (ZERO < quantity <= Q):
        return {"status": "INVALID_RESCUE_QUANTITY", "hard_halt": True, "flattened": False}

    for attempt in range(1, v5.RESCUE_ATTEMPTS + 1):
        try:
            snap = await cache.wait_fresh(timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
        except Exception as exc:
            return {
                "status": "RESCUE_BOOK_NOT_FRESH", "hard_halt": True, "flattened": False,
                "error_type": type(exc).__name__, "attempt": attempt,
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
        v5.v1.emit(
            "V5_RESCUE_ROUTE", attempt=attempt, filled_outcome=filled_outcome,
            filled_price=str(filled_price), route=route.as_dict(),
            book_age_ms=round(snap.age_ms, 3), order_entry=False,
        )
        if not route.safe:
            return {
                "status": "RESCUE_BOUND_NO_LONGER_AVAILABLE", "hard_halt": True,
                "flattened": False, "attempt": attempt, "route": route.as_dict(),
            }

        client_id = f"{trade_id}-v5fok-{attempt}"[:36]
        params = {
            "symbol": symbol,
            "side": route.side,
            "type": "LIMIT",
            "timeInForce": "FOK",
            "price": str(route.limit_price),
            "quantity": str(quantity),
            "eventOutcome": route.outcome,
            "clientOrderId": client_id,
        }
        v5._heartbeat(
            state,
            stage="RESCUE_FOK_PENDING",
            pending_rescue={
                "client_order_id": client_id,
                "attempt": attempt,
                "route": route.as_dict(),
                "quantity": str(quantity),
            },
        )
        t0 = time.perf_counter_ns()
        try:
            ack = await maker.request("order.place", params, timeout=3.0)
        except Exception as exc:
            return {
                "status": "RESCUE_FOK_ACK_AMBIGUOUS", "hard_halt": True,
                "flattened": False, "attempt": attempt,
                "error_type": type(exc).__name__,
            }
        ack_ms = (time.perf_counter_ns() - t0) / 1_000_000
        v5.v1.emit(
            "V5_RESCUE_FOK_ACK", attempt=attempt, client_order_id=client_id,
            ack_status=ack.get("status"), ack_latency_ms=round(ack_ms, 3),
            route=route.as_dict(), order_entry=True,
        )
        if int(ack.get("status", 0)) != 200:
            return {
                "status": "RESCUE_FOK_REJECTED", "hard_halt": True,
                "flattened": False, "attempt": attempt,
                "ack_status": ack.get("status"),
            }

        row = await v5.v1.base._find_row(client_id, symbol)
        if row is None:
            # A 200 acknowledgement means the order may exist or may already have
            # executed. Never submit another full-size FOK until that ambiguity is
            # reconciled externally.
            v5._heartbeat(
                state,
                stage="RESCUE_FOK_STATUS_UNKNOWN",
                rescue_last_client_id=client_id,
                rescue_last_fill=None,
            )
            return {
                "status": "RESCUE_FOK_STATUS_UNKNOWN",
                "hard_halt": True,
                "flattened": False,
                "attempt": attempt,
                "client_order_id": client_id,
            }

        filled = v5.v1.base._filled(row)
        v5._heartbeat(
            state,
            rescue_last_client_id=client_id,
            rescue_last_fill=str(filled),
            pending_rescue=None,
        )
        if filled == quantity:
            return {
                "status": f"RESCUE_{route.action}_FILLED",
                "hard_halt": False,
                "flattened": True,
                "attempt": attempt,
                "route": route.as_dict(),
                "quantity": str(quantity),
            }
        if filled != ZERO:
            return {
                "status": "RESCUE_FOK_PARTIAL_AMBIGUITY", "hard_halt": True,
                "flattened": False, "attempt": attempt, "filled": str(filled),
            }
        await asyncio.sleep(0.03)

    return {
        "status": "RESCUE_FOK_DID_NOT_FILL_WITHIN_BOUND",
        "hard_halt": True,
        "flattened": False,
        "attempts": v5.RESCUE_ATTEMPTS,
    }


async def run_campaign_v52(*, guard: Any, campaign_no: int) -> dict[str, Any]:
    available, flat = await v5.v1._preflight_flat()
    if guard.decision().dangerous:
        return {"status": "SPOT_DANGEROUS_PRE_CANDIDATE", "order_entry": False}

    found = await v5._wait_candidate_or_none()
    if found is None:
        v5.v1.emit("V5_NO_CANDIDATE", order_entry=False, classification="NO_OP_NOT_FAILURE")
        return {"status": "NO_CANDIDATE", "order_entry": False}
    event, reward, _ = found
    if not (v5.v1.ENTRY_MIN_SECONDS <= event.seconds_to_expiry <= v5.v1.ENTRY_MAX_SECONDS):
        return {"status": "EXPIRY_GATE_CHANGED", "order_entry": False}

    cache = v5.GeminiTopCache(event.symbol, stale_ms=750)
    cache_stop = asyncio.Event()
    cache_task = asyncio.create_task(cache.run(cache_stop))
    maker = v5.v1.base.GeminiMakerSession()
    yes_row = no_row = None
    state: dict[str, Any] = {}
    try:
        snap = await cache.wait_fresh(timeout=3.0, max_age_ms=v5.BOOK_MAX_AGE_MS)
        spot = guard.decision()
        if not spot.ready or spot.dangerous:
            return {"status": "SPOT_DANGEROUS_PRE_MUTATION", "order_entry": False}
        model = v5.v1.probe._model(snap.bid, snap.ask)
        if not v5.v1._entry_model_ok(model):
            return {"status": "FRESH_ENTRY_GATE_FAILED", "order_entry": False}
        # Before mutation there is no own depth to subtract.
        standing = rescue.standing_rescue_check(
            yes_quote=model["yes_quote"], no_quote=model["no_quote"], quantity=Q,
            yes_bid=snap.bid, yes_bid_size=snap.bid_size,
            yes_ask=snap.ask, yes_ask_size=snap.ask_size,
            max_loss=v5.max_rescue_loss(), increment=event.price_increment,
        )
        if not standing.safe:
            v5.v1.emit("V5_ENTRY_RESCUE_GATE_FAILED", rescue=standing.as_dict(), order_entry=False)
            return {"status": "ENTRY_RESCUE_GATE_FAILED", "order_entry": False, "rescue": standing.as_dict()}

        fresh_reward = await v5.v1.probe._reward_snapshot(event.ticker)
        if fresh_reward is None:
            return {"status": "REWARD_EVENT_INACTIVE", "order_entry": False}
        rr = fresh_reward["event"]
        makers = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
        if int(makers) != 0:
            return {"status": "REWARD_COMPETITION_CHANGED", "order_entry": False}

        run_id = v5.v1._clean(__import__("os").environ[v5.v1.ARM_ID_ENV])
        trade_id = f"gq10v52-{run_id}-{campaign_no}"[:24]
        yes_id = f"{trade_id}-yes"[:36]
        no_id = f"{trade_id}-no"[:36]
        state = {
            "classification": "LIVE_Q10_V5_2_OPEN_NOT_REALIZED",
            "run_id": run_id,
            "trade_id": trade_id,
            "campaign_no": campaign_no,
            "ticker": event.ticker,
            "symbol": event.symbol,
            "yes_id": yes_id,
            "no_id": no_id,
            "yes_quote": str(model["yes_quote"]),
            "no_quote": str(model["no_quote"]),
            "price_increment": str(event.price_increment),
            "quantity": "10",
            "max_rescue_loss": str(v5.max_rescue_loss()),
            "max_campaign_loss": str(v5.max_campaign_loss()),
            "available_usd_before": str(available),
            "standing_rescue_at_entry": standing.as_dict(),
            "owned_client_ids": [yes_id, no_id],
            "status": "PRE_MUTATION",
            "open": True,
            "realized_pnl": None,
            "rewards_realized": None,
        }
        v5._heartbeat(state, stage="PRE_MUTATION")
        v5.v1.emit("V5_2_CAMPAIGN_START", **state, spot=asdict(spot), flat=flat)

        await maker.connect()
        yes_ack, yes_row, yes_ms = await v5.v1._post(
            maker, symbol=event.symbol, outcome="YES",
            price=model["yes_quote"], quantity=Q, client_id=yes_id,
        )
        if int(yes_ack.get("status", 0)) != 200:
            v5._heartbeat(state, stage="YES_REJECTED", open=False)
            return {"status": "YES_REJECTED", "order_entry": True}
        v5.v1.emit("V5_YES_POST", ack_latency_ms=round(yes_ms, 3), order_entry=True)

        snap_after_yes = await cache.wait_fresh(timeout=0.75, max_age_ms=v5.BOOK_MAX_AGE_MS)
        bid_size_after_yes, _ = _external_depth_snapshot(
            model, snap_after_yes, yes_resting=True, no_resting=False,
        )
        yes_route = rescue.best_route_after_fill(
            filled_outcome="YES", filled_price=model["yes_quote"], quantity=Q,
            yes_bid=snap_after_yes.bid, yes_bid_size=bid_size_after_yes,
            yes_ask=snap_after_yes.ask, yes_ask_size=snap_after_yes.ask_size,
            max_loss=v5.max_rescue_loss(), increment=event.price_increment,
        )
        spot2 = guard.decision()
        if not yes_route.safe or not spot2.ready or spot2.dangerous:
            await v5.v1.base._cancel_maker(maker, row=yes_row)
            await asyncio.sleep(0.08)
            yrow = await v5.v1.base._order_by_client(yes_id, event.symbol) or yes_row
            yf = v5.v1.base._filled(yrow)
            if yf == ZERO:
                v5._heartbeat(state, stage="FIRST_POST_PULLED_ZERO_FILL", open=False)
                return {"status": "FIRST_POST_PULLED_ZERO_FILL", "order_entry": True}
            no_row = None
            nf = ZERO
            yfinal = yrow
            nfinal = None
        else:
            no_ack, no_row, no_ms = await v5.v1._post(
                maker, symbol=event.symbol, outcome="NO",
                price=model["no_quote"], quantity=Q, client_id=no_id,
            )
            if int(no_ack.get("status", 0)) != 200:
                await v5.v1.base._cancel_maker(maker, row=yes_row)
                await asyncio.sleep(0.08)
                yfinal = await v5.v1.base._order_by_client(yes_id, event.symbol) or yes_row
                yf = v5.v1.base._filled(yfinal)
                nf = ZERO
                nfinal = None
                if yf == ZERO:
                    v5._heartbeat(state, stage="NO_REJECTED_ZERO_FILL", open=False)
                    return {"status": "NO_REJECTED_ZERO_FILL", "order_entry": True}
            else:
                v5.v1.emit("V5_NO_POST", ack_latency_ms=round(no_ms, 3), order_entry=True)
                v5._heartbeat(state, stage="BOTH_POSTED")
                yf = nf = ZERO
                yfinal, nfinal = yes_row, no_row
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
                    standing_now = standing_external(model, snap_now, increment=event.price_increment)
                    if not spot_now.ready or spot_now.dangerous:
                        pull_reason = "SPOT_TOXIC"
                        break
                    if not standing_now.safe:
                        pull_reason = "RESCUE_BOUND_BROKE"
                        break
                    try:
                        item = await asyncio.wait_for(maker.events.get(), timeout=v5.BOOK_CHECK_SECONDS)
                    except asyncio.TimeoutError:
                        continue
                    if not isinstance(item, Mapping) or str(item.get("c") or "") not in {yes_id, no_id}:
                        continue
                    qty = D(str(item.get("Z") or "0"))
                    status = str(item.get("X") or "").upper()
                    if qty > ZERO and status in {"PARTIALLY_FILLED", "FILLED"}:
                        pull_reason = "FIRST_FILL"
                        break

                yf, nf, yfinal, nfinal = await v5._cancel_original_pair(
                    maker, yes_id=yes_id, no_id=no_id, symbol=event.symbol,
                    yes_row=yes_row, no_row=no_row,
                )
                v5.v1.emit(
                    "V5_PAIR_PULLED", reason=pull_reason or "REST_WINDOW_END",
                    yes_fill=str(yf), no_fill=str(nf), order_entry=True,
                )
                if yfinal is None or nfinal is None:
                    v5._heartbeat(
                        state, stage="RECONCILE_AMBIGUOUS", open=True,
                        reconcile_reason="ACKED_ORDER_ROW_UNRESOLVED",
                        pull_reason=pull_reason,
                    )
                    return {
                        "status": "RECONCILE_AMBIGUOUS", "order_entry": True,
                        "hard_halt": True, "open": True,
                    }
                if pull_reason == "FIRST_FILL" and yf == ZERO and nf == ZERO:
                    v5._heartbeat(
                        state, stage="RECONCILE_AMBIGUOUS", open=True,
                        reconcile_reason="WS_FIRST_FILL_CONTRADICTED_BY_ZERO_RECONCILE",
                    )
                    return {
                        "status": "RECONCILE_AMBIGUOUS", "order_entry": True,
                        "hard_halt": True, "open": True,
                    }

        if yf > Q or nf > Q:
            raise RuntimeError("q10 quantity bound violated")
        if yf == ZERO and nf == ZERO:
            v5._heartbeat(state, stage="ZERO_FILL_CLEAN_EXIT", open=False)
            return {"status": "ZERO_FILL_CLEAN_EXIT", "order_entry": True}
        if yf == nf:
            v5._heartbeat(state, stage="MATCHED_NOT_SETTLED", yes_fill=str(yf), no_fill=str(nf), residual="0")
            return {"status": "MATCHED_NOT_SETTLED", "order_entry": True, "matched": str(yf), "residual": "0"}

        residual_qty = abs(yf - nf)
        if residual_qty > Q:
            raise RuntimeError("residual cap violated")
        filled_outcome = "YES" if yf > nf else "NO"
        filled_row = yfinal if filled_outcome == "YES" else nfinal
        fallback_price = model["yes_quote"] if filled_outcome == "YES" else model["no_quote"]
        filled_price = v5._fill_price(filled_row, fallback_price)
        v5._heartbeat(
            state, stage="RESIDUAL_IDENTIFIED", yes_fill=str(yf), no_fill=str(nf),
            residual_outcome=filled_outcome, residual_quantity=str(residual_qty),
            residual_fill_price=str(filled_price),
        )

        grace_result = await v5._calm_maker_grace(
            maker, cache=cache, guard=guard, symbol=event.symbol,
            filled_outcome=filled_outcome, filled_price=filled_price,
            quantity=residual_qty, increment=event.price_increment,
            trade_id=trade_id, state=state,
        )
        remaining = D(str(grace_result.get("remaining") or residual_qty))
        if remaining == ZERO:
            v5._heartbeat(state, stage="GRACE_COMPLETED_PAIR", completion=grace_result)
            return {"status": "GRACE_COMPLETED_PAIR", "order_entry": True, "completion": grace_result}

        fok = await bounded_fok_v52(
            maker, cache=cache, symbol=event.symbol,
            filled_outcome=filled_outcome, filled_price=filled_price,
            quantity=remaining, increment=event.price_increment,
            trade_id=trade_id, state=state,
        )
        v5._heartbeat(state, stage=fok["status"], completion=grace_result, rescue=fok)
        return {
            "status": fok["status"], "order_entry": True,
            "completion": grace_result, "rescue": fok,
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
        cache_stop.set()
        cache_task.cancel()
        await asyncio.gather(cache_task, return_exceptions=True)


def install() -> None:
    """Install V5.1 admission, then V5.2 process-local safety patches."""
    v51.install()
    v5.v1.base.fresh_top = reliable_fresh_top
    v5.v1.probe._wait_candidate = wait_candidate_v52
    v5.v1.base._order_by_client = order_by_client_v52
    v5._standing = standing_external
    v5._bounded_fok = bounded_fok_v52
    v5._run_campaign = run_campaign_v52


async def run() -> None:
    install()
    await v5.run()


if __name__ == "__main__":
    asyncio.run(run())
