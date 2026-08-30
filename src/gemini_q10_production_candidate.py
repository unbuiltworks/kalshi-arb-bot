"""Bounded Gemini BTC5m q=10 production candidate.

Default/disarmed execution performs zero venue calls.  A future live run requires a
fresh dual-key approval.  The candidate keeps the proven q10 reward-entry gates,
adds a proactive BTC spot momentum darkness gate before the first fill, and after
one full-side fill keeps/reprices only the missing maker complement using the tested
time-dependent trade-profit floors.  Incentives are never used to justify a trade.

Hard bounds: BTC5m only, q=10/outcome, one campaign, maker-only LIMIT MOC, residual
<=10, <=6 complement reprices, no taker rescue, no auto restart, stop completion
>=20s before expiry.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_q10_production_risk as risk
from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_local_budget as local

Q = D("10")
ZERO = D("0")
LIVE_ENV = "GEMINI_Q10_PRODUCTION_LIVE"
RUN_ID_ENV = "GEMINI_Q10_PRODUCTION_RUN_ID"
ARM_ENV = "GEMINI_Q10_PRODUCTION_ARM"
MAX_REPRICES = 6
REPRICE_INTERVAL_SECONDS = 0.50
SPOT_POLL_SECONDS = 0.20
INITIAL_REST_SECONDS = 180.0
STOP_BEFORE_EXPIRY_SECONDS = 20.0


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_Q10_PRODUCTION " + json.dumps({
        "stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields,
    }, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    rid = os.getenv(RUN_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and rid and os.getenv(ARM_ENV, "").strip() == rid)


def client_id(run_id: str, outcome: str, seq: int = 0) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in run_id)[:18]
    side = outcome.lower()
    suffix = "i" if seq == 0 else f"r{seq}"
    return f"q10p-{safe}-{side}-{suffix}"[:36]


async def _post(maker: base.GeminiMakerSession, *, symbol: str, outcome: str,
                price: D, cid: str) -> tuple[dict[str, Any], Mapping[str, Any] | None, float]:
    params = {
        "symbol": symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC",
        "price": str(D(str(price))), "quantity": "10", "eventOutcome": outcome,
        "clientOrderId": cid,
    }
    started = time.monotonic()
    ack = await maker.request("order.place", params, timeout=4.0)
    ms = (time.monotonic() - started) * 1000.0
    row = await base._find_row(cid, symbol) if int(ack.get("status", 0)) == 200 else None
    return ack, row, ms


async def _cancel(maker: base.GeminiMakerSession, *, cid: str, symbol: str,
                  row: Mapping[str, Any] | None) -> tuple[dict[str, Any], D, float]:
    current = await base._order_by_client(cid, symbol) or row
    started = time.monotonic()
    result = await base._cancel_maker(maker, row=current)
    ms = (time.monotonic() - started) * 1000.0
    await asyncio.sleep(0.12)
    terminal = await base._order_by_client(cid, symbol)
    return result, base._filled(terminal), ms


async def _spot(http: httpx.AsyncClient) -> D:
    r = await http.get("https://api.gemini.com/v1/pubticker/btcusd")
    r.raise_for_status()
    body = r.json()
    return D(str(body.get("last")))


async def _dynamic_complete(maker: base.GeminiMakerSession, *, event: base.Event,
                            run_id: str, filled_outcome: str, filled_price: D,
                            complement_id: str, complement_price: D,
                            complement_row: Mapping[str, Any] | None) -> dict[str, Any]:
    missing = "NO" if filled_outcome == "YES" else "YES"
    current_id = complement_id
    current_price = D(str(complement_price))
    current_row = complement_row
    reprices = 0
    deadline = time.monotonic() + max(0.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS)
    next_check = 0.0

    while time.monotonic() < deadline:
        row = await base._order_by_client(current_id, event.symbol) if current_id else None
        fill = base._filled(row)
        if fill == Q:
            return {"status":"PAIRED_BY_DYNAMIC_COMPLEMENT", "paired":True, "reprices":reprices,
                    "missing_outcome":missing, "final_price":str(current_price)}
        if fill > ZERO:
            return {"status":"FRACTIONAL_COMPLEMENT_FILL", "paired":False, "hard_halt":True,
                    "reprices":reprices, "missing_outcome":missing, "fill":str(fill)}

        now = time.monotonic()
        if now < next_check:
            await asyncio.sleep(min(0.10, max(0.01, next_check-now)))
            continue
        next_check = now + REPRICE_INTERVAL_SECONDS
        if event.seconds_to_expiry < STOP_BEFORE_EXPIRY_SECONDS:
            break
        try:
            bid, _, ask, _ = await base.fresh_top(event.symbol)
            plan = risk.completion_decision(
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
        if reprices >= MAX_REPRICES:
            continue

        cancel, race_fill, cancel_ms = await _cancel(
            maker, cid=current_id, symbol=event.symbol, row=current_row,
        )
        emit("COMPLEMENT_REPRICE_CANCEL", cid=current_id, cancel=cancel,
             cancel_ms=round(cancel_ms,3), race_fill=str(race_fill), target=str(target_d), order_entry=True)
        if race_fill == Q:
            return {"status":"PAIRED_DURING_REPRICE_CANCEL", "paired":True, "reprices":reprices}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"status":"AMBIGUOUS_REPRICE_CANCEL", "paired":False, "hard_halt":True,
                    "reprices":reprices, "race_fill":str(race_fill)}

        # Cancel-before-repost, then revalidate against a fresh book.  Never post from
        # the stale pre-cancel target.
        bid2, _, ask2, _ = await base.fresh_top(event.symbol)
        try:
            plan2 = risk.completion_decision(
                filled_outcome=filled_outcome, filled_price=filled_price,
                current_complement_price=current_price, yes_bid=bid2, yes_ask=ask2,
                seconds_to_expiry=event.seconds_to_expiry, increment=event.price_increment,
            )
        except ValueError:
            current_id = ""
            current_row = None
            break
        target2 = plan2.get("target_price")
        if plan2.get("action") != "REPRICE_MAKER_COMPLEMENT" or target2 is None:
            current_id = ""
            current_row = None
            break
        reprices += 1
        new_id = client_id(run_id, missing, reprices)
        ack, row2, ack_ms = await _post(maker, symbol=event.symbol, outcome=missing,
                                        price=D(str(target2)), cid=new_id)
        emit("COMPLEMENT_REPOST", cid=new_id, outcome=missing, price=str(target2),
             ack_status=ack.get("status"), ack_ms=round(ack_ms,3), order_entry=True)
        if int(ack.get("status",0)) != 200:
            return {"status":"COMPLEMENT_REPOST_REJECTED", "paired":False, "hard_halt":True,
                    "reprices":reprices}
        current_id, current_row, current_price = new_id, row2, D(str(target2))

    if current_id:
        cancel, race_fill, cancel_ms = await _cancel(maker, cid=current_id, symbol=event.symbol, row=current_row)
        emit("COMPLEMENT_TIMEOUT_CANCEL", cid=current_id, cancel=cancel,
             cancel_ms=round(cancel_ms,3), race_fill=str(race_fill), order_entry=True)
        if race_fill == Q:
            return {"status":"PAIRED_AT_TIMEOUT", "paired":True, "reprices":reprices}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"status":"AMBIGUOUS_TIMEOUT_CANCEL", "paired":False, "hard_halt":True,
                    "reprices":reprices, "race_fill":str(race_fill)}
    return {"status":"DYNAMIC_COMPLETION_EXPIRED_ONE_SIDED", "paired":False,
            "hard_halt":False, "reprices":reprices}


async def run() -> None:
    if not live_armed():
        emit("DISARMED", classification="DISARMED_NO_VENUE_CALLS", order_entry=False,
             credentials_read=False, quantity="10", max_residual="10")
        return

    if local.local_open_state() is not None:
        raise RuntimeError("unresolved local short-crypto reservation")
    exposure = await local.account_exposure_snapshot()
    if int(exposure.get("active_short_crypto_orders") or 0) or int(exposure.get("active_short_crypto_positions") or 0):
        raise RuntimeError("Gemini short-crypto account is not flat")
    if not await base._gemini_terms_ok():
        raise RuntimeError("Gemini Prediction Markets terms not accepted")
    if D(str(await base._gemini_available_usd())) < D("15"):
        raise RuntimeError("available Gemini USD below $15")

    event, reward, model = await probe._wait_candidate()
    bid, _, ask, _ = await base.fresh_top(event.symbol)
    fresh = probe._model(bid, ask)
    if fresh["locked_edge"] < probe.MIN_TRADE_EDGE or fresh["max_one_sided_premium"] > probe.MAX_ONE_SIDED_PREMIUM:
        emit("STOP", reason="FRESH_EDGE_OR_RISK_GATE", order_entry=False); return

    run_id = os.environ[RUN_ID_ENV]
    yes_id, no_id = client_id(run_id,"YES"), client_id(run_id,"NO")
    yes_price, no_price = fresh["yes_quote"], fresh["no_quote"]
    maker = base.GeminiMakerSession()
    yes_row = no_row = None
    try:
        await maker.connect()
        yes_ack, yes_row, yes_ms = await _post(maker, symbol=event.symbol, outcome="YES", price=yes_price, cid=yes_id)
        if int(yes_ack.get("status",0)) != 200:
            emit("STOP", reason="YES_REJECTED", ack_ms=round(yes_ms,3), order_entry=True); return
        no_ack, no_row, no_ms = await _post(maker, symbol=event.symbol, outcome="NO", price=no_price, cid=no_id)
        if int(no_ack.get("status",0)) != 200:
            await _cancel(maker,cid=yes_id,symbol=event.symbol,row=yes_row)
            emit("HARD_STOP", reason="NO_REJECTED_AFTER_YES", order_entry=True); return
        emit("BOTH_POSTED", ticker=event.ticker, symbol=event.symbol, yes=str(yes_price), no=str(no_price),
             modeled_locked_edge=str(fresh["locked_edge"]), yes_ack_ms=round(yes_ms,3), no_ack_ms=round(no_ms,3), order_entry=True)

        guard = risk.SpotMomentumGuard()
        first = None
        toxic = False
        deadline = min(time.monotonic()+INITIAL_REST_SECONDS,
                       time.monotonic()+max(1.0,event.seconds_to_expiry-STOP_BEFORE_EXPIRY_SECONDS))
        async with httpx.AsyncClient(timeout=1.5, follow_redirects=True,
                                     headers={"User-Agent":"arb-q10-production/1.0"}) as http:
            next_spot = 0.0
            while time.monotonic() < deadline:
                timeout = min(0.10, max(0.01, deadline-time.monotonic()))
                try:
                    item = await asyncio.wait_for(maker.events.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    item = None
                if isinstance(item, Mapping) and str(item.get("c") or "") in {yes_id,no_id}:
                    qty = D(str(item.get("Z") or "0")); status=str(item.get("X") or "").upper()
                    if qty > ZERO and status in {"PARTIALLY_FILLED","FILLED"}:
                        cid=str(item.get("c")); outcome="YES" if cid==yes_id else "NO"
                        fallback=yes_price if outcome=="YES" else no_price
                        px=D(str(item.get("L") or item.get("p") or fallback))
                        first={"cid":cid,"outcome":outcome,"qty":qty,"price":px,"status":status}; break
                if time.monotonic() >= next_spot:
                    next_spot = time.monotonic()+SPOT_POLL_SECONDS
                    try:
                        px = await _spot(http)
                    except Exception:
                        # Loss of the proactive signal is fail-dark before any fill.
                        toxic = True; emit("TOXICITY_DARK", reason="SPOT_SIGNAL_UNAVAILABLE", order_entry=True); break
                    if guard.observe(time.monotonic(), px):
                        toxic = True
                        emit("TOXICITY_DARK", reason="FAST_BTC_MOVE", move_bps=str(guard.current_move_bps()), order_entry=True)
                        break

        if first is None:
            yc, yf, _ = await _cancel(maker,cid=yes_id,symbol=event.symbol,row=yes_row)
            nc, nf, _ = await _cancel(maker,cid=no_id,symbol=event.symbol,row=no_row)
            if not risk.residual_safe(yf,nf):
                raise RuntimeError("q10 residual hard bound violated after no-fill/toxicity cancel")
            emit("COMPLETE", status="CANCELLED_TOXIC" if toxic else "NO_FILL_TIMEOUT",
                 yes_fill=str(yf), no_fill=str(nf), residual=str(risk.residual_qty(yf,nf)),
                 yes_cancel=yc, no_cancel=nc, realized_pnl=None, rewards_realized=None, order_entry=False)
            return

        # Freeze the filled/overweight side immediately.  The unfilled complement
        # remains resting while we determine whether this was a full q10 fill.
        filled_id = yes_id if first["outcome"]=="YES" else no_id
        filled_row = yes_row if first["outcome"]=="YES" else no_row
        _, race_filled, _ = await _cancel(maker,cid=filled_id,symbol=event.symbol,row=filled_row)
        actual_fill = max(first["qty"], race_filled)
        if actual_fill != Q:
            other_id = no_id if first["outcome"]=="YES" else yes_id
            other_row = no_row if first["outcome"]=="YES" else yes_row
            _, other_fill, _ = await _cancel(maker,cid=other_id,symbol=event.symbol,row=other_row)
            emit("HARD_STOP", reason="FRACTIONAL_FIRST_FILL", first_fill=str(actual_fill),
                 other_fill=str(other_fill), residual=str(abs(actual_fill-other_fill)), order_entry=False)
            return

        complement_id = no_id if first["outcome"]=="YES" else yes_id
        complement_row = no_row if first["outcome"]=="YES" else yes_row
        complement_price = no_price if first["outcome"]=="YES" else yes_price
        outcome = await _dynamic_complete(
            maker, event=event, run_id=run_id, filled_outcome=first["outcome"],
            filled_price=first["price"], complement_id=complement_id,
            complement_price=complement_price, complement_row=complement_row,
        )
        emit("COMPLETE", **outcome, realized_pnl=None, rewards_realized=None, order_entry=False)
    finally:
        await maker.close()


if __name__ == "__main__":
    asyncio.run(run())
