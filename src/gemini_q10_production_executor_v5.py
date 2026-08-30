"""Gemini BTC5 q10 V5: maker-first with continuously bounded single-venue rescue.

V5 changes the objective after a first fill. It no longer allows a maker-only
complement to remain directional for minutes. Instead:

- every resting q10 maker quote has a standing precondition that the full possible
  first fill can be flattened *now* inside a hard total-dollar rescue-loss budget;
- that precondition is re-evaluated continuously from a persistent Gemini book;
- a toxic BTC spot move cancels resting quotes immediately;
- after a first fill, toxic flow gets zero maker grace; calm flow gets only a tiny
  bounded maker grace while the rescue invariant remains true;
- the remaining residual is flattened with a bounded LIMIT+FOK order, choosing the
  better of COMPLETE (buy complement) and UNWIND (sell original);
- no reward/rebate estimate is allowed to relax the rescue budget;
- q10, one campaign by default, dual-key arming, durable state and settlement gates
  are inherited from the production candidate.

This module introduces no new permission. It uses the same GEMINI_Q10_PRODUCTION_*
dual-key arm as V1 and is disarmed by default.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal as D
import os
import time
from typing import Any, Mapping

from src import gemini_q10_production_executor as v1
from src import gemini_q10_rescue_policy as rescue
from src.btc_spot_toxicity_guard import BtcSpotGuard
from src.gemini_prediction_book_cache import GeminiTopCache, BookSnapshot

Q = D("10")
ZERO = D("0")
MAX_RESCUE_LOSS_ENV = "GEMINI_Q10_V5_MAX_RESCUE_LOSS_USD"
CAMPAIGN_LOSS_ENV = "GEMINI_Q10_V5_MAX_CAMPAIGN_LOSS_USD"
DAILY_LOSS_ENV = "GEMINI_Q10_V5_MAX_DAILY_LOSS_USD"
CALM_GRACE_ENV = "GEMINI_Q10_V5_CALM_MAKER_GRACE_SECONDS"
MAX_RESCUE_LOSS_DEFAULT = D("0.30")
MAX_CAMPAIGN_LOSS_DEFAULT = D("1.00")
MAX_DAILY_LOSS_DEFAULT = D("3.00")
CALM_GRACE_DEFAULT = 1.5
BOOK_MAX_AGE_MS = 500
BOOK_CHECK_SECONDS = 0.05
RESCUE_ATTEMPTS = 2


def live_armed() -> bool:
    return v1.live_armed()


def continuous_enabled() -> bool:
    return v1.continuous_enabled()


def _positive_env(name: str, default: D) -> D:
    value = D(os.getenv(name, str(default)))
    if value <= ZERO:
        raise RuntimeError(f"{name} must be positive")
    return value


def max_rescue_loss() -> D:
    return _positive_env(MAX_RESCUE_LOSS_ENV, MAX_RESCUE_LOSS_DEFAULT)


def max_campaign_loss() -> D:
    return _positive_env(CAMPAIGN_LOSS_ENV, MAX_CAMPAIGN_LOSS_DEFAULT)


def max_daily_loss() -> D:
    return _positive_env(DAILY_LOSS_ENV, MAX_DAILY_LOSS_DEFAULT)


def calm_grace_seconds() -> float:
    value = float(os.getenv(CALM_GRACE_ENV, str(CALM_GRACE_DEFAULT)))
    if value < 0 or value > 5:
        raise RuntimeError("calm maker grace must be between 0 and 5 seconds")
    return value


def _config_ok() -> None:
    # The entry invariant must itself be tighter than the campaign circuit breaker.
    if max_rescue_loss() > max_campaign_loss():
        raise RuntimeError("rescue loss bound cannot exceed campaign loss bound")
    if max_campaign_loss() > max_daily_loss():
        raise RuntimeError("campaign loss bound cannot exceed daily loss bound")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heartbeat(state: dict[str, Any], *, stage: str | None = None, **fields: Any) -> None:
    state["heartbeat_at_utc"] = _now_iso()
    if stage is not None:
        state["status"] = stage
    state.update(fields)
    v1._write_state(state)


def _fill_price(row: Mapping[str, Any] | None, fallback: D) -> D:
    if row:
        for key in ("avgExecutionPrice", "averageExecutionPrice", "price"):
            value = row.get(key)
            if value not in (None, ""):
                try:
                    p = D(str(value))
                except Exception:
                    continue
                if ZERO < p < D("1"):
                    return p
    return D(str(fallback))


def _standing(model: Mapping[str, D], snap: BookSnapshot, *, increment: D) -> rescue.StandingRescueCheck:
    return rescue.standing_rescue_check(
        yes_quote=D(str(model["yes_quote"])),
        no_quote=D(str(model["no_quote"])),
        quantity=Q,
        yes_bid=snap.bid,
        yes_bid_size=snap.bid_size,
        yes_ask=snap.ask,
        yes_ask_size=snap.ask_size,
        max_loss=max_rescue_loss(),
        increment=increment,
    )


async def _wait_candidate_or_none() -> tuple[Any, dict[str, Any], dict[str, D]] | None:
    try:
        return await v1.probe._wait_candidate()
    except RuntimeError as exc:
        if "no qualifying zero-competition BTC5m reward market" in str(exc):
            return None
        raise


async def _cancel_original_pair(maker: Any, *, yes_id: str, no_id: str, symbol: str,
                                yes_row: Mapping[str, Any] | None,
                                no_row: Mapping[str, Any] | None) -> tuple[D, D, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    await v1._cancel_pair(
        maker, yes_id=yes_id, no_id=no_id, symbol=symbol, yes_row=yes_row, no_row=no_row,
    )
    yrow, nrow, yf, nf = await v1.probe._reconcile(yes_id, no_id, symbol)
    return yf, nf, yrow, nrow


async def _bounded_fok(maker: Any, *, cache: GeminiTopCache, symbol: str,
                       filled_outcome: str, filled_price: D, quantity: D,
                       increment: D, trade_id: str, state: dict[str, Any]) -> dict[str, Any]:
    """Flatten exact residual with at most two bounded LIMIT+FOK attempts."""
    if not (ZERO < quantity <= Q):
        return {"status": "INVALID_RESCUE_QUANTITY", "hard_halt": True, "flattened": False}

    for attempt in range(1, RESCUE_ATTEMPTS + 1):
        try:
            snap = await cache.wait_fresh(timeout=0.75, max_age_ms=BOOK_MAX_AGE_MS)
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
            max_loss=max_rescue_loss(),
            increment=increment,
        )
        v1.emit(
            "V5_RESCUE_ROUTE",
            attempt=attempt,
            filled_outcome=filled_outcome,
            filled_price=str(filled_price),
            route=route.as_dict(),
            book_age_ms=round(snap.age_ms, 3),
            order_entry=False,
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
        _heartbeat(
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
                "flattened": False, "attempt": attempt, "error_type": type(exc).__name__,
            }
        ack_ms = (time.perf_counter_ns() - t0) / 1_000_000
        v1.emit(
            "V5_RESCUE_FOK_ACK", attempt=attempt, client_order_id=client_id,
            ack_status=ack.get("status"), ack_latency_ms=round(ack_ms, 3),
            route=route.as_dict(), order_entry=True,
        )
        if int(ack.get("status", 0)) != 200:
            return {
                "status": "RESCUE_FOK_REJECTED", "hard_halt": True,
                "flattened": False, "attempt": attempt, "ack_status": ack.get("status"),
            }

        # FOK is all-or-none. Reconcile once through bounded order-history lookup;
        # a non-zero partial fill is treated as an exchange/state ambiguity and halts.
        row = await v1.base._find_row(client_id, symbol)
        filled = v1.base._filled(row)
        _heartbeat(state, rescue_last_client_id=client_id, rescue_last_fill=str(filled))
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
        "attempts": RESCUE_ATTEMPTS,
    }


async def _calm_maker_grace(maker: Any, *, cache: GeminiTopCache, guard: BtcSpotGuard,
                            symbol: str, filled_outcome: str, filled_price: D,
                            quantity: D, increment: D, trade_id: str,
                            state: dict[str, Any]) -> dict[str, Any]:
    """Try the missing maker side briefly, never at the expense of rescue safety."""
    spot = guard.decision()
    grace = rescue.maker_grace_seconds(
        spot_dangerous=(not spot.ready or spot.dangerous),
        calm_grace_seconds=calm_grace_seconds(),
    )
    if grace <= 0:
        return {"status": "ZERO_GRACE_TO_RESCUE", "maker_filled": "0", "remaining": str(quantity)}

    snap = await cache.wait_fresh(timeout=0.75, max_age_ms=BOOK_MAX_AGE_MS)
    route = rescue.best_route_after_fill(
        filled_outcome=filled_outcome, filled_price=filled_price, quantity=quantity,
        yes_bid=snap.bid, yes_bid_size=snap.bid_size, yes_ask=snap.ask, yes_ask_size=snap.ask_size,
        max_loss=max_rescue_loss(), increment=increment,
    )
    if not route.safe:
        return {"status": "GRACE_SKIPPED_RESCUE_NOT_SAFE", "maker_filled": "0", "remaining": str(quantity)}

    missing = "NO" if filled_outcome == "YES" else "YES"
    maker_price = (D("1") - snap.ask) if missing == "NO" else snap.bid
    if not (D("0.01") <= maker_price <= D("0.99")):
        return {"status": "GRACE_SKIPPED_BAD_PRICE", "maker_filled": "0", "remaining": str(quantity)}
    cid = f"{trade_id}-grace"[:36]
    ack, row, ack_ms = await v1._post(
        maker, symbol=symbol, outcome=missing, price=maker_price, quantity=quantity, client_id=cid,
    )
    v1.emit(
        "V5_GRACE_POST", client_order_id=cid, missing_outcome=missing,
        price=str(maker_price), quantity=str(quantity), grace_seconds=grace,
        ack_status=ack.get("status"), ack_latency_ms=round(ack_ms, 3), order_entry=True,
    )
    if int(ack.get("status", 0)) != 200:
        return {"status": "GRACE_MAKER_REJECTED", "maker_filled": "0", "remaining": str(quantity)}

    _heartbeat(state, stage="CALM_MAKER_GRACE", grace_client_id=cid)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        spot_now = guard.decision()
        if not spot_now.ready or spot_now.dangerous:
            break
        try:
            snap_now = await cache.wait_fresh(timeout=0.20, max_age_ms=BOOK_MAX_AGE_MS)
        except Exception:
            break
        route_now = rescue.best_route_after_fill(
            filled_outcome=filled_outcome, filled_price=filled_price, quantity=quantity,
            yes_bid=snap_now.bid, yes_bid_size=snap_now.bid_size,
            yes_ask=snap_now.ask, yes_ask_size=snap_now.ask_size,
            max_loss=max_rescue_loss(), increment=increment,
        )
        if not route_now.safe:
            break
        try:
            item = await asyncio.wait_for(maker.events.get(), timeout=BOOK_CHECK_SECONDS)
        except asyncio.TimeoutError:
            continue
        if not isinstance(item, Mapping) or str(item.get("c") or "") != cid:
            continue
        if str(item.get("X") or "").upper() in {"PARTIALLY_FILLED", "FILLED"}:
            q = D(str(item.get("Z") or "0"))
            if q >= quantity:
                return {"status": "GRACE_COMPLEMENT_FILLED", "maker_filled": str(quantity), "remaining": "0"}

    row = await v1.base._order_by_client(cid, symbol) or row
    cancel = await v1.base._cancel_maker(maker, row=row)
    await asyncio.sleep(0.08)
    row2 = await v1.base._order_by_client(cid, symbol) or row
    filled = min(quantity, v1.base._filled(row2))
    remaining = max(ZERO, quantity - filled)
    v1.emit(
        "V5_GRACE_CANCEL", client_order_id=cid, maker_fill=str(filled),
        remaining=str(remaining), cancel=cancel, order_entry=True,
    )
    return {
        "status": "GRACE_ENDED",
        "maker_filled": str(filled),
        "remaining": str(remaining),
        "cancel": cancel,
    }


async def _run_campaign(*, guard: BtcSpotGuard, campaign_no: int) -> dict[str, Any]:
    available, flat = await v1._preflight_flat()
    if guard.decision().dangerous:
        return {"status": "SPOT_DANGEROUS_PRE_CANDIDATE", "order_entry": False}

    found = await _wait_candidate_or_none()
    if found is None:
        v1.emit("V5_NO_CANDIDATE", order_entry=False, classification="NO_OP_NOT_FAILURE")
        return {"status": "NO_CANDIDATE", "order_entry": False}
    event, reward, _ = found
    if not (v1.ENTRY_MIN_SECONDS <= event.seconds_to_expiry <= v1.ENTRY_MAX_SECONDS):
        return {"status": "EXPIRY_GATE_CHANGED", "order_entry": False}

    cache = GeminiTopCache(event.symbol, stale_ms=750)
    cache_stop = asyncio.Event()
    cache_task = asyncio.create_task(cache.run(cache_stop))
    maker = v1.base.GeminiMakerSession()
    yes_row = no_row = None
    yes_id = no_id = ""
    state: dict[str, Any] = {}
    try:
        snap = await cache.wait_fresh(timeout=3.0, max_age_ms=BOOK_MAX_AGE_MS)
        spot = guard.decision()
        if not spot.ready or spot.dangerous:
            return {"status": "SPOT_DANGEROUS_PRE_MUTATION", "order_entry": False}
        model = v1.probe._model(snap.bid, snap.ask)
        if not v1._entry_model_ok(model):
            return {"status": "FRESH_ENTRY_GATE_FAILED", "order_entry": False}
        standing = _standing(model, snap, increment=event.price_increment)
        if not standing.safe:
            v1.emit("V5_ENTRY_RESCUE_GATE_FAILED", rescue=standing.as_dict(), order_entry=False)
            return {"status": "ENTRY_RESCUE_GATE_FAILED", "order_entry": False, "rescue": standing.as_dict()}

        fresh_reward = await v1.probe._reward_snapshot(event.ticker)
        if fresh_reward is None:
            return {"status": "REWARD_EVENT_INACTIVE", "order_entry": False}
        rr = fresh_reward["event"]
        makers = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
        if int(makers) != 0:
            return {"status": "REWARD_COMPETITION_CHANGED", "order_entry": False}

        run_id = v1._clean(os.environ[v1.ARM_ID_ENV])
        trade_id = f"gq10v5-{run_id}-{campaign_no}"[:24]
        yes_id = f"{trade_id}-yes"[:36]
        no_id = f"{trade_id}-no"[:36]
        state = {
            "classification": "LIVE_Q10_V5_OPEN_NOT_REALIZED",
            "run_id": run_id,
            "campaign_no": campaign_no,
            "ticker": event.ticker,
            "symbol": event.symbol,
            "yes_id": yes_id,
            "no_id": no_id,
            "yes_quote": str(model["yes_quote"]),
            "no_quote": str(model["no_quote"]),
            "quantity": "10",
            "max_rescue_loss": str(max_rescue_loss()),
            "max_campaign_loss": str(max_campaign_loss()),
            "available_usd_before": str(available),
            "standing_rescue_at_entry": standing.as_dict(),
            "owned_client_ids": [yes_id, no_id],
            "status": "PRE_MUTATION",
            "open": True,
            "realized_pnl": None,
            "rewards_realized": None,
        }
        _heartbeat(state, stage="PRE_MUTATION")
        v1.emit("V5_CAMPAIGN_START", **state, spot=asdict(spot), flat=flat)

        await maker.connect()
        yes_ack, yes_row, yes_ms = await v1._post(
            maker, symbol=event.symbol, outcome="YES", price=model["yes_quote"], quantity=Q, client_id=yes_id,
        )
        if int(yes_ack.get("status", 0)) != 200:
            _heartbeat(state, stage="YES_REJECTED", open=False)
            return {"status": "YES_REJECTED", "order_entry": True}
        v1.emit("V5_YES_POST", ack_latency_ms=round(yes_ms, 3), order_entry=True)

        # The first sequential post is itself exposed. Recheck the YES rescue path
        # before submitting NO; cancel immediately if the standing invariant broke.
        snap_after_yes = await cache.wait_fresh(timeout=0.75, max_age_ms=BOOK_MAX_AGE_MS)
        yes_route = rescue.best_route_after_fill(
            filled_outcome="YES", filled_price=model["yes_quote"], quantity=Q,
            yes_bid=snap_after_yes.bid, yes_bid_size=snap_after_yes.bid_size,
            yes_ask=snap_after_yes.ask, yes_ask_size=snap_after_yes.ask_size,
            max_loss=max_rescue_loss(), increment=event.price_increment,
        )
        spot2 = guard.decision()
        if not yes_route.safe or not spot2.ready or spot2.dangerous:
            await v1.base._cancel_maker(maker, row=yes_row)
            await asyncio.sleep(0.08)
            yrow = await v1.base._order_by_client(yes_id, event.symbol) or yes_row
            yf = v1.base._filled(yrow)
            if yf == ZERO:
                _heartbeat(state, stage="FIRST_POST_PULLED_ZERO_FILL", open=False)
                return {"status": "FIRST_POST_PULLED_ZERO_FILL", "order_entry": True}
            # A cancel race produced the first fill: skip maker grace if toxic and rescue below.
            no_row = None
            nf = ZERO
            yfinal = yrow
            nfinal = None
        else:
            no_ack, no_row, no_ms = await v1._post(
                maker, symbol=event.symbol, outcome="NO", price=model["no_quote"], quantity=Q, client_id=no_id,
            )
            if int(no_ack.get("status", 0)) != 200:
                await v1.base._cancel_maker(maker, row=yes_row)
                await asyncio.sleep(0.08)
                yfinal = await v1.base._order_by_client(yes_id, event.symbol) or yes_row
                yf = v1.base._filled(yfinal)
                nf = ZERO
                nfinal = None
                if yf == ZERO:
                    _heartbeat(state, stage="NO_REJECTED_ZERO_FILL", open=False)
                    return {"status": "NO_REJECTED_ZERO_FILL", "order_entry": True}
            else:
                v1.emit("V5_NO_POST", ack_latency_ms=round(no_ms, 3), order_entry=True)
                _heartbeat(state, stage="BOTH_POSTED")
                yf = nf = ZERO
                yfinal, nfinal = yes_row, no_row
                deadline = min(
                    time.monotonic() + v1.REST_SECONDS,
                    time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0),
                )
                last_heartbeat = 0.0
                pull_reason = None
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now - last_heartbeat >= 1.0:
                        _heartbeat(state, stage="BOTH_POSTED")
                        last_heartbeat = now
                    spot_now = guard.decision()
                    try:
                        snap_now = await cache.wait_fresh(timeout=0.20, max_age_ms=BOOK_MAX_AGE_MS)
                    except Exception:
                        pull_reason = "BOOK_STALE"
                        break
                    standing_now = _standing(model, snap_now, increment=event.price_increment)
                    if not spot_now.ready or spot_now.dangerous:
                        pull_reason = "SPOT_TOXIC"
                        break
                    if not standing_now.safe:
                        pull_reason = "RESCUE_BOUND_BROKE"
                        break
                    try:
                        item = await asyncio.wait_for(maker.events.get(), timeout=BOOK_CHECK_SECONDS)
                    except asyncio.TimeoutError:
                        continue
                    if not isinstance(item, Mapping) or str(item.get("c") or "") not in {yes_id, no_id}:
                        continue
                    qty = D(str(item.get("Z") or "0"))
                    status = str(item.get("X") or "").upper()
                    if qty > ZERO and status in {"PARTIALLY_FILLED", "FILLED"}:
                        pull_reason = "FIRST_FILL"
                        break

                yf, nf, yfinal, nfinal = await _cancel_original_pair(
                    maker, yes_id=yes_id, no_id=no_id, symbol=event.symbol,
                    yes_row=yes_row, no_row=no_row,
                )
                v1.emit("V5_PAIR_PULLED", reason=pull_reason or "REST_WINDOW_END", yes_fill=str(yf), no_fill=str(nf), order_entry=True)

        if yf > Q or nf > Q:
            raise RuntimeError("q10 quantity bound violated")
        if yf == ZERO and nf == ZERO:
            _heartbeat(state, stage="ZERO_FILL_CLEAN_EXIT", open=False)
            return {"status": "ZERO_FILL_CLEAN_EXIT", "order_entry": True}
        if yf == nf:
            _heartbeat(state, stage="MATCHED_NOT_SETTLED", yes_fill=str(yf), no_fill=str(nf), residual="0")
            return {"status": "MATCHED_NOT_SETTLED", "order_entry": True, "matched": str(yf), "residual": "0"}

        residual_qty = abs(yf - nf)
        if residual_qty > Q:
            raise RuntimeError("residual cap violated")
        filled_outcome = "YES" if yf > nf else "NO"
        filled_row = yfinal if filled_outcome == "YES" else nfinal
        fallback_price = model["yes_quote"] if filled_outcome == "YES" else model["no_quote"]
        filled_price = _fill_price(filled_row, fallback_price)
        _heartbeat(
            state,
            stage="RESIDUAL_IDENTIFIED",
            yes_fill=str(yf), no_fill=str(nf), residual_outcome=filled_outcome,
            residual_quantity=str(residual_qty), residual_fill_price=str(filled_price),
        )

        grace_result = await _calm_maker_grace(
            maker, cache=cache, guard=guard, symbol=event.symbol,
            filled_outcome=filled_outcome, filled_price=filled_price,
            quantity=residual_qty, increment=event.price_increment,
            trade_id=trade_id, state=state,
        )
        remaining = D(str(grace_result.get("remaining") or residual_qty))
        if remaining == ZERO:
            _heartbeat(state, stage="GRACE_COMPLETED_PAIR", completion=grace_result)
            return {"status": "GRACE_COMPLETED_PAIR", "order_entry": True, "completion": grace_result}

        fok = await _bounded_fok(
            maker, cache=cache, symbol=event.symbol,
            filled_outcome=filled_outcome, filled_price=filled_price,
            quantity=remaining, increment=event.price_increment,
            trade_id=trade_id, state=state,
        )
        _heartbeat(state, stage=fok["status"], completion=grace_result, rescue=fok)
        return {
            "status": fok["status"],
            "order_entry": True,
            "completion": grace_result,
            "rescue": fok,
            "hard_halt": bool(fok.get("hard_halt")),
        }
    finally:
        # Process-level safety net. Prediction-market API docs expose GTC/IOC/FOK/MOC
        # but no GTD/TTL, so we also build an external watchdog separately. This
        # session cancel is still valuable for exceptions and normal SIGTERM paths.
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


async def run() -> None:
    _config_ok()
    existing = v1._read_state()
    if existing and bool(existing.get("open")):
        # V5 never guesses past an unresolved durable campaign. The separate startup
        # reconciler/dead-man path owns recovery before new exposure is admitted.
        v1.emit("V5_RECOVERY_REQUIRED", state=existing, order_entry=False)
        return

    if not live_armed():
        try:
            available, snap = await v1._preflight_flat()
        except Exception as exc:
            v1.emit("V5_DISARMED_PREFLIGHT", armed=False, error_type=type(exc).__name__, order_entry=False)
        else:
            v1.emit(
                "V5_DISARMED_PREFLIGHT", armed=False, available_usd=str(available), flat=snap,
                max_rescue_loss=str(max_rescue_loss()), max_campaign_loss=str(max_campaign_loss()),
                max_daily_loss=str(max_daily_loss()), order_entry=False,
                classification="READ_ONLY_V5_PRODUCTION_PREFLIGHT_NOT_REALIZED",
            )
        return

    max_campaigns = v1._max_campaigns()
    start_available, _ = await v1._preflight_flat()
    stop_event = asyncio.Event()
    guard = BtcSpotGuard()
    spot_task = asyncio.create_task(guard.run_coinbase(stop_event))
    try:
        if not await v1._warm_spot(guard):
            return
        campaign = 0
        while max_campaigns == 0 or campaign < max_campaigns:
            current_available, _ = await v1._preflight_flat()
            drawdown = max(ZERO, start_available - current_available)
            if drawdown >= max_daily_loss():
                v1.emit(
                    "V5_DAILY_LOSS_STOP", start_available=str(start_available),
                    current_available=str(current_available), drawdown=str(drawdown),
                    loss_stop=str(max_daily_loss()), order_entry=False,
                )
                return
            result = await _run_campaign(guard=guard, campaign_no=campaign + 1)
            v1.emit("V5_CAMPAIGN_RESULT", campaign_no=campaign + 1, result=result, order_entry=bool(result.get("order_entry")))
            if result.get("status") == "NO_CANDIDATE":
                if not continuous_enabled():
                    return
                await asyncio.sleep(1.0)
                continue
            campaign += 1
            if result.get("hard_halt"):
                v1.emit("V5_HARD_STOP", reason="CAMPAIGN_HARD_HALT", result=result, order_entry=False)
                return
            if not continuous_enabled():
                return
            if not await v1._wait_flat_after_settlement():
                v1.emit("V5_HARD_STOP", reason="SETTLEMENT_DID_NOT_CLEAR_WITHIN_BOUND", order_entry=False)
                return
            settled_available = D(str(await v1.base._gemini_available_usd()))
            prior = v1._read_state() or {}
            prior.update(
                open=False,
                status="SETTLED_ACCOUNT_FLAT",
                available_usd_after=str(settled_available),
                realized_account_delta_from_process_start=str(settled_available - start_available),
                settled_at_utc=_now_iso(),
            )
            v1._write_state(prior)
            v1.emit(
                "V5_SETTLEMENT_RECONCILED", available_usd=str(settled_available),
                process_account_delta=str(settled_available - start_available),
                rewards_realized="SEPARATE_ACCOUNTING_REQUIRED", order_entry=False,
            )
    finally:
        stop_event.set()
        spot_task.cancel()
        await asyncio.gather(spot_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
