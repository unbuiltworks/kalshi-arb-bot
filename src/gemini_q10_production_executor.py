"""Bounded Gemini BTC5m q=10 production candidate.

This is the integration target for the rewards/maker lane. It combines:
- q=10 two-sided maker entry;
- Coinbase BTC spot staleness/fast-move defense before and while resting;
- exact-residual dynamic maker completion after the first fill;
- hard account-flat, balance, quantity, price-band, expiry and daily-drawdown gates;
- durable one-campaign-at-a-time state plus append-only JSONL telemetry;
- optional continuous lifecycle that waits for settlement before selecting a new market.

Nothing mutates unless a fresh dual-key production arm is present. Continuous mode
also requires an independent flag. A crash with an unresolved durable campaign
refuses new order entry until read-only reconciliation clears the state.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from src import gemini_liquidity_reward_live_probe_q10 as probe
from src import gemini_liquidity_reward_dynamic_completion_q10 as completion
from src.btc_spot_toxicity_guard import BtcSpotGuard

base = probe.base
local = probe.local

Q = D("10")
ZERO = D("0")
ONE = D("1")
LIVE_ENV = "GEMINI_Q10_PRODUCTION_LIVE"
ARM_ID_ENV = "GEMINI_Q10_PRODUCTION_ARM_ID"
ARM_ENV = "GEMINI_Q10_PRODUCTION_ARM"
CONTINUOUS_ENV = "GEMINI_Q10_PRODUCTION_CONTINUOUS"
STATE_ENV = "GEMINI_Q10_PRODUCTION_STATE_DIR"
MAX_CAMPAIGNS_ENV = "GEMINI_Q10_PRODUCTION_MAX_CAMPAIGNS"
DAILY_LOSS_ENV = "GEMINI_Q10_PRODUCTION_MAX_DAILY_LOSS_USD"
MAX_RESIDUAL = D("10")
MIN_AVAILABLE = D("15")
MAX_ONE_SIDED_PREMIUM = D("5.00")
MIN_ENTRY_PAIR_PROFIT = D("0.50")
ENTRY_MIN_SECONDS = 240.0
ENTRY_MAX_SECONDS = 330.0
REST_SECONDS = 180.0
STOP_COMPLETION_SECONDS = 20.0
REPRICE_SECONDS = 0.50
MAX_REPRICES = 6
SETTLEMENT_WAIT_SECONDS = 600.0
SPOT_WARM_SECONDS = 10.0


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    aid = os.getenv(ARM_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and aid and os.getenv(ARM_ENV, "").strip() == aid)


def continuous_enabled() -> bool:
    return live_armed() and _enabled(CONTINUOUS_ENV)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "run"


def _root() -> Path:
    root = Path(os.getenv(STATE_ENV, "/state"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_file() -> Path:
    return _root() / "gemini-q10-production-state.json"


def _ledger_file() -> Path:
    return _root() / "gemini-q10-production-ledger.jsonl"


def emit(stage: str, **fields: Any) -> None:
    payload = {
        "stage": stage,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        **fields,
    }
    print("GEMINI_Q10_PRODUCTION " + json.dumps(payload, separators=(",", ":"), default=str), flush=True)
    try:
        with _ledger_file().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    except Exception:
        # Telemetry failure must not hide the primary safety path; durable campaign
        # state is written separately and is mandatory before order mutation.
        pass


def _write_state(payload: Mapping[str, Any]) -> None:
    path = _state_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_state() -> dict[str, Any] | None:
    path = _state_file()
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        raise RuntimeError("production state file is unreadable; refusing order entry")


def _max_campaigns() -> int:
    raw = os.getenv(MAX_CAMPAIGNS_ENV, "1").strip()
    try:
        n = int(raw)
    except ValueError:
        raise RuntimeError("invalid max campaigns")
    if n < 0:
        raise RuntimeError("max campaigns cannot be negative")
    if not continuous_enabled() and n != 1:
        raise RuntimeError("non-continuous production mode requires exactly one campaign")
    return n


def _daily_loss_stop() -> D:
    loss = D(os.getenv(DAILY_LOSS_ENV, "5.00"))
    if loss <= ZERO:
        raise RuntimeError("daily loss stop must be positive")
    return loss


async def _flat_snapshot() -> dict[str, Any]:
    snap = await local.account_exposure_snapshot()
    return {
        "orders": int(snap.get("active_short_crypto_orders") or 0),
        "positions": int(snap.get("active_short_crypto_positions") or 0),
        "order_symbols": snap.get("order_symbols") or [],
        "position_symbols": snap.get("position_symbols") or [],
    }


async def _preflight_flat() -> tuple[D, dict[str, Any]]:
    if local.local_open_state() is not None:
        raise RuntimeError("unresolved short-crypto local reservation blocks production")
    snap = await _flat_snapshot()
    if snap["orders"] or snap["positions"]:
        raise RuntimeError(f"Gemini short-crypto account not flat: {snap}")
    if not await base._gemini_terms_ok():
        raise RuntimeError("Gemini Prediction Markets terms not accepted")
    available = D(str(await base._gemini_available_usd()))
    if available < MIN_AVAILABLE:
        raise RuntimeError("available Gemini USD below production reserve")
    return available, snap


async def _wait_flat_after_settlement(timeout: float = SETTLEMENT_WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = await _flat_snapshot()
        if not snap["orders"] and not snap["positions"]:
            return True
        await asyncio.sleep(2.0)
    return False


async def _warm_spot(guard: BtcSpotGuard) -> bool:
    deadline = time.monotonic() + SPOT_WARM_SECONDS
    while time.monotonic() < deadline:
        decision = guard.decision()
        if decision.ready and not decision.dangerous:
            emit("SPOT_READY", **asdict(decision))
            return True
        await asyncio.sleep(0.05)
    emit("STOP", reason="SPOT_GUARD_NOT_READY", spot=asdict(guard.decision()), order_entry=False)
    return False


def _entry_model_ok(model: Mapping[str, D]) -> bool:
    return bool(
        model["locked_edge"] >= MIN_ENTRY_PAIR_PROFIT
        and model["max_one_sided_premium"] <= MAX_ONE_SIDED_PREMIUM
        and probe.MIN_PRICE <= model["yes_quote"] <= probe.MAX_PRICE
        and probe.MIN_PRICE <= model["no_quote"] <= probe.MAX_PRICE
    )


async def _cancel_pair(maker: Any, *, yes_id: str, no_id: str, symbol: str,
                       yes_row: Mapping[str, Any] | None, no_row: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], D, D]:
    yes_row = await base._order_by_client(yes_id, symbol) or yes_row
    no_row = await base._order_by_client(no_id, symbol) or no_row
    t0 = time.perf_counter_ns()
    yc, nc = await asyncio.gather(base._cancel_maker(maker, row=yes_row), base._cancel_maker(maker, row=no_row))
    cancel_ms = (time.perf_counter_ns() - t0) / 1_000_000
    await asyncio.sleep(0.15)
    _, _, yf, nf = await probe._reconcile(yes_id, no_id, symbol)
    emit("PAIR_CANCEL_RECONCILE", yes_cancel=yc, no_cancel=nc, yes_fill=str(yf), no_fill=str(nf),
         cancel_roundtrip_ms=round(cancel_ms, 3), order_entry=True)
    return yc, nc, yf, nf


async def _post(maker: Any, *, symbol: str, outcome: str, price: D, quantity: D,
                client_id: str) -> tuple[dict[str, Any], Mapping[str, Any] | None, float]:
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(price),
        "quantity": str(quantity),
        "eventOutcome": outcome,
        "clientOrderId": client_id,
    }
    t0 = time.perf_counter_ns()
    ack = await maker.request("order.place", params, timeout=4.0)
    ack_ms = (time.perf_counter_ns() - t0) / 1_000_000
    row = await base._find_row(client_id, symbol) if int(ack.get("status", 0)) == 200 else None
    return ack, row, ack_ms


async def _complete_residual(maker: Any, *, event: Any, run_id: str,
                             filled_outcome: str, filled_price: D, quantity: D,
                             starting_price: D) -> dict[str, Any]:
    """Work the exact missing quantity maker-only until paired or the risk deadline.

    Caller has already cancelled both original q10 orders and reconciled cancel races.
    No order larger than the current residual is ever submitted.
    """
    if not (ZERO < quantity <= MAX_RESIDUAL):
        return {"status": "INVALID_RESIDUAL", "paired": False, "hard_halt": True}
    missing = "NO" if filled_outcome == "YES" else "YES"
    current_price = D(str(starting_price))
    current_id: str | None = None
    current_row: Mapping[str, Any] | None = None
    sequence = 0
    deadline = min(
        time.monotonic() + REST_SECONDS,
        time.monotonic() + max(0.0, event.seconds_to_expiry - STOP_COMPLETION_SECONDS),
    )

    async def post_price(price: D) -> tuple[bool, str | None]:
        nonlocal current_id, current_row, current_price, sequence
        if sequence >= MAX_REPRICES + 1:
            return False, "MAX_REPRICES"
        sequence += 1
        cid = f"{run_id}-{missing.lower()}-r{sequence}"[:36]
        ack, row, ack_ms = await _post(
            maker, symbol=event.symbol, outcome=missing, price=price, quantity=quantity, client_id=cid,
        )
        emit("COMPLEMENT_POST", missing_outcome=missing, client_order_id=cid, quantity=str(quantity),
             price=str(price), ack_status=ack.get("status"), ack_latency_ms=round(ack_ms, 3),
             seconds_to_expiry=event.seconds_to_expiry, order_entry=True)
        if int(ack.get("status", 0)) != 200:
            return False, "REJECTED"
        current_id, current_row, current_price = cid, row, price
        return True, None

    while time.monotonic() < deadline:
        if current_id is not None:
            row = await base._order_by_client(current_id, event.symbol) or current_row
            filled = base._filled(row)
            if filled >= quantity:
                return {"status":"DYNAMIC_COMPLEMENT_FILLED","paired":True,"hard_halt":False,
                        "reprices":sequence,"quantity":str(quantity),"final_price":str(current_price)}
            if filled > ZERO:
                # Never layer another order over a partially filled rescue. Cancel it,
                # reduce the residual exactly, and continue with only the remainder.
                cancel = await base._cancel_maker(maker, row=row)
                await asyncio.sleep(0.1)
                row2 = await base._order_by_client(current_id, event.symbol) or row
                filled2 = base._filled(row2)
                if filled2 >= quantity:
                    return {"status":"DYNAMIC_COMPLEMENT_FILLED_DURING_CANCEL","paired":True,"hard_halt":False,
                            "reprices":sequence,"quantity":str(quantity),"final_price":str(current_price)}
                return {"status":"PARTIAL_COMPLEMENT_FILL_REQUIRES_RECONCILE","paired":False,"hard_halt":True,
                        "reprices":sequence,"requested":str(quantity),"filled":str(filled2),"cancel":cancel}

        try:
            bid, _, ask, _ = await base.fresh_top(event.symbol)
        except RuntimeError as exc:
            if "no fresh Gemini BTC5m depth" not in str(exc):
                raise
            emit("COMPLEMENT_DEPTH_UNAVAILABLE", complement_resting=current_id is not None,
                 action="KEEP_AND_RETRY" if current_id else "WAIT_NO_BLIND_REPOST", order_entry=True)
            await asyncio.sleep(REPRICE_SECONDS)
            continue

        try:
            plan = completion.completion_plan(
                filled_outcome=filled_outcome,
                filled_price=filled_price,
                current_complement_price=current_price,
                yes_bid=bid,
                yes_ask=ask,
                seconds_to_expiry=event.seconds_to_expiry,
                quantity=quantity,
                increment=event.price_increment,
                min_price=D("0.01"),
                max_price=D("0.99"),
            )
        except ValueError:
            break

        target = D(str(plan.get("target_price") or current_price))
        passive = completion.maker_passive_ceiling(
            missing_outcome=missing, yes_bid=bid, yes_ask=ask, increment=event.price_increment,
        )
        floor = completion.time_dependent_total_profit_floor(event.seconds_to_expiry, quantity=quantity)
        current_safe = (
            D("0.01") <= current_price <= D("0.99")
            and current_price <= passive
            and completion.completed_pair_profit(
                filled_price=filled_price, complement_price=current_price, quantity=quantity
            ) >= floor
        )

        if current_id is None:
            price = target if plan.get("action") == "REPRICE_MAKER_COMPLEMENT" else current_price
            if price > passive:
                price = passive
            if completion.completed_pair_profit(
                filled_price=filled_price, complement_price=price, quantity=quantity
            ) < floor:
                await asyncio.sleep(REPRICE_SECONDS)
                continue
            ok, reason = await post_price(price)
            if not ok:
                return {"status":f"COMPLEMENT_{reason}","paired":False,"hard_halt":True,"reprices":sequence}
            await asyncio.sleep(REPRICE_SECONDS)
            continue

        if plan.get("action") != "REPRICE_MAKER_COMPLEMENT" or target <= current_price:
            await asyncio.sleep(REPRICE_SECONDS)
            continue
        if sequence > MAX_REPRICES:
            await asyncio.sleep(REPRICE_SECONDS)
            continue

        row = await base._order_by_client(current_id, event.symbol) or current_row
        t0 = time.perf_counter_ns()
        cancel = await base._cancel_maker(maker, row=row)
        cancel_ms = (time.perf_counter_ns() - t0) / 1_000_000
        await asyncio.sleep(0.1)
        row2 = await base._order_by_client(current_id, event.symbol) or row
        race_fill = base._filled(row2)
        emit("COMPLEMENT_REPRICE_CANCEL", client_order_id=current_id, cancel=cancel,
             cancel_roundtrip_ms=round(cancel_ms, 3), race_fill=str(race_fill), proposed_target=str(target), order_entry=True)
        if race_fill >= quantity:
            return {"status":"COMPLEMENT_FILLED_DURING_REPRICE_CANCEL","paired":True,"hard_halt":False,"reprices":sequence}
        if race_fill > ZERO or not cancel.get("ok"):
            return {"status":"COMPLEMENT_CANCEL_AMBIGUOUS","paired":False,"hard_halt":True,
                    "reprices":sequence,"race_fill":str(race_fill)}
        current_id = None
        current_row = None
        # Never blindly repost from the pre-cancel book. Loop to fresh depth.

    if current_id is not None:
        row = await base._order_by_client(current_id, event.symbol) or current_row
        cancel = await base._cancel_maker(maker, row=row)
        await asyncio.sleep(0.1)
        row2 = await base._order_by_client(current_id, event.symbol) or row
        final_fill = base._filled(row2)
        if final_fill >= quantity:
            return {"status":"COMPLEMENT_FILLED_AT_DEADLINE","paired":True,"hard_halt":False,"reprices":sequence}
        if final_fill > ZERO or not cancel.get("ok"):
            return {"status":"COMPLETION_DEADLINE_CANCEL_AMBIGUOUS","paired":False,"hard_halt":True,
                    "reprices":sequence,"final_fill":str(final_fill)}
    return {"status":"DYNAMIC_COMPLETION_EXPIRED_RESIDUAL","paired":False,"hard_halt":False,
            "reprices":sequence,"residual_quantity":str(quantity)}


async def _run_campaign(*, guard: BtcSpotGuard, campaign_no: int) -> dict[str, Any]:
    available, flat = await _preflight_flat()
    if guard.decision().dangerous:
        return {"status":"SPOT_DANGEROUS_PRE_CANDIDATE","order_entry":False,"available_usd":str(available)}

    event, reward, _ = await probe._wait_candidate()
    if not (ENTRY_MIN_SECONDS <= event.seconds_to_expiry <= ENTRY_MAX_SECONDS):
        return {"status":"EXPIRY_GATE_CHANGED","order_entry":False}

    spot = guard.decision()
    if not spot.ready or spot.dangerous:
        emit("STOP", reason="SPOT_DANGEROUS_PRE_MUTATION", spot=asdict(spot), order_entry=False)
        return {"status":"SPOT_DANGEROUS_PRE_MUTATION","order_entry":False}

    bid, _, ask, _ = await base.fresh_top(event.symbol)
    model = probe._model(bid, ask)
    if not _entry_model_ok(model):
        emit("STOP", reason="FRESH_ENTRY_GATE_FAILED", model={k:str(v) for k,v in model.items()}, order_entry=False)
        return {"status":"FRESH_ENTRY_GATE_FAILED","order_entry":False}

    fresh_reward = await probe._reward_snapshot(event.ticker)
    if fresh_reward is None:
        return {"status":"REWARD_EVENT_INACTIVE","order_entry":False}
    rr = fresh_reward["event"]
    makers = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
    if int(makers) != 0:
        return {"status":"REWARD_COMPETITION_CHANGED","order_entry":False,"qualifying_makers":makers}

    run_id = _clean(os.environ[ARM_ID_ENV])
    trade_id = f"gq10-{run_id}-{campaign_no}"[:24]
    yes_id = f"{trade_id}-yes"[:36]
    no_id = f"{trade_id}-no"[:36]
    state = {
        "classification":"LIVE_Q10_PRODUCTION_OPEN_NOT_REALIZED",
        "run_id":run_id,"campaign_no":campaign_no,"ticker":event.ticker,"symbol":event.symbol,
        "yes_id":yes_id,"no_id":no_id,"yes_quote":str(model["yes_quote"]),"no_quote":str(model["no_quote"]),
        "quantity":"10","modeled_pair_profit":str(model["locked_edge"]),"available_usd_before":str(available),
        "status":"PRE_MUTATION","open":True,"realized_pnl":None,"rewards_realized":None,
    }
    _write_state(state)
    emit("CAMPAIGN_START", **state, spot=asdict(spot), flat=flat)

    maker = base.GeminiMakerSession()
    yes_row = no_row = None
    try:
        await maker.connect()
        yes_ack, yes_row, yes_ms = await _post(
            maker, symbol=event.symbol, outcome="YES", price=model["yes_quote"], quantity=Q, client_id=yes_id,
        )
        emit("YES_POST_ACK", ack_status=yes_ack.get("status"), ack_latency_ms=round(yes_ms,3), order_entry=True)
        if int(yes_ack.get("status",0)) != 200:
            state.update(status="YES_REJECTED",open=False); _write_state(state)
            return {"status":"YES_REJECTED","order_entry":True}

        spot2 = guard.decision()
        if not spot2.ready or spot2.dangerous:
            cancel = await base._cancel_maker(maker,row=yes_row)
            await asyncio.sleep(0.15)
            yrow = await base._order_by_client(yes_id,event.symbol) or yes_row
            yf = base._filled(yrow)
            state.update(status="SPOT_MOVED_AFTER_YES_POST",yes_fill=str(yf),yes_cancel=cancel,open=yf>ZERO)
            _write_state(state)
            return {"status":"SPOT_MOVED_AFTER_YES_POST","yes_fill":str(yf),"order_entry":True,"hard_halt":yf>ZERO}

        no_ack, no_row, no_ms = await _post(
            maker, symbol=event.symbol, outcome="NO", price=model["no_quote"], quantity=Q, client_id=no_id,
        )
        emit("NO_POST_ACK", ack_status=no_ack.get("status"), ack_latency_ms=round(no_ms,3), order_entry=True)
        if int(no_ack.get("status",0)) != 200:
            yc = await base._cancel_maker(maker,row=yes_row)
            await asyncio.sleep(0.15)
            yrow = await base._order_by_client(yes_id,event.symbol) or yes_row
            yf = base._filled(yrow)
            state.update(status="NO_REJECTED_AFTER_YES",yes_fill=str(yf),yes_cancel=yc,open=yf>ZERO)
            _write_state(state)
            return {"status":"NO_REJECTED_AFTER_YES","yes_fill":str(yf),"order_entry":True,"hard_halt":yf>ZERO}

        state.update(status="BOTH_POSTED",posted_at_utc=datetime.now(timezone.utc).isoformat()); _write_state(state)
        emit("BOTH_POSTED", symbol=event.symbol, yes_price=str(model["yes_quote"]), no_price=str(model["no_quote"]),
             modeled_pair_profit=str(model["locked_edge"]), order_entry=True)

        deadline = min(time.monotonic()+REST_SECONDS, time.monotonic()+max(1.0,event.seconds_to_expiry-30.0))
        first_fill = None
        while time.monotonic() < deadline:
            spot_now = guard.decision()
            if not spot_now.ready or spot_now.dangerous:
                yc,nc,yf,nf = await _cancel_pair(
                    maker,yes_id=yes_id,no_id=no_id,symbol=event.symbol,yes_row=yes_row,no_row=no_row,
                )
                if yf==ZERO and nf==ZERO:
                    state.update(status="TOXICITY_PULL_ZERO_FILL",open=False,spot=asdict(spot_now)); _write_state(state)
                    return {"status":"TOXICITY_PULL_ZERO_FILL","order_entry":True,"spot":asdict(spot_now)}
                first_fill={"source":"TOXICITY_CANCEL_RACE","yes_fill":yf,"no_fill":nf}
                break
            try:
                item = await asyncio.wait_for(maker.events.get(), timeout=0.10)
            except asyncio.TimeoutError:
                continue
            if not isinstance(item,Mapping) or str(item.get("c") or "") not in {yes_id,no_id}:
                continue
            qty=D(str(item.get("Z") or "0")); status=str(item.get("X") or "").upper()
            if qty>ZERO and status in {"PARTIALLY_FILLED","FILLED"}:
                first_fill={"client_order_id":str(item.get("c")),"quantity":str(qty),"status":status}
                break

        yc,nc,yf,nf = await _cancel_pair(
            maker,yes_id=yes_id,no_id=no_id,symbol=event.symbol,yes_row=yes_row,no_row=no_row,
        )
        if yf>Q or nf>Q:
            raise RuntimeError("q10 quantity bound violated")
        if yf==ZERO and nf==ZERO:
            state.update(status="NO_FILL_WINDOW",open=False,yes_fill="0",no_fill="0"); _write_state(state)
            return {"status":"NO_FILL_WINDOW","order_entry":True}
        if yf==nf:
            state.update(status="MATCHED_NOT_SETTLED",open=True,yes_fill=str(yf),no_fill=str(nf),matched=str(yf)); _write_state(state)
            return {"status":"MATCHED_NOT_SETTLED","order_entry":True,"matched":str(yf),"residual":"0"}

        residual=abs(yf-nf)
        if residual>MAX_RESIDUAL:
            raise RuntimeError("residual hard cap violated")
        filled_outcome="YES" if yf>nf else "NO"
        filled_price=model["yes_quote"] if filled_outcome=="YES" else model["no_quote"]
        start_price=model["no_quote"] if filled_outcome=="YES" else model["yes_quote"]
        rescue=await _complete_residual(
            maker,event=event,run_id=trade_id,filled_outcome=filled_outcome,filled_price=filled_price,
            quantity=residual,starting_price=start_price,
        )
        state.update(status=rescue["status"],first_fill=first_fill,yes_fill=str(yf),no_fill=str(nf),
                     residual_before_completion=str(residual),completion=rescue,open=True)
        _write_state(state)
        return {"status":rescue["status"],"order_entry":True,"completion":rescue,
                "residual_before_completion":str(residual),"hard_halt":bool(rescue.get("hard_halt"))}
    finally:
        await maker.close()


async def run() -> None:
    existing=_read_state()
    if existing and bool(existing.get("open")):
        emit("HARD_STOP",reason="DURABLE_OPEN_CAMPAIGN_REQUIRES_RECONCILIATION",state=existing,order_entry=False)
        return
    if not live_armed():
        available=None
        try:
            available,snap=await _preflight_flat()
        except Exception as exc:
            emit("DISARMED_PREFLIGHT",armed=False,error_type=type(exc).__name__,order_entry=False)
        else:
            emit("DISARMED_PREFLIGHT",armed=False,available_usd=str(available),flat=snap,order_entry=False,
                 classification="READ_ONLY_PRODUCTION_PREFLIGHT_NOT_REALIZED")
        return

    max_campaigns=_max_campaigns()
    loss_stop=_daily_loss_stop()
    start_available,_=await _preflight_flat()
    stop_event=asyncio.Event()
    guard=BtcSpotGuard()
    feed_task=asyncio.create_task(guard.run_coinbase(stop_event))
    try:
        if not await _warm_spot(guard):
            return
        campaign=0
        while max_campaigns==0 or campaign<max_campaigns:
            current_available,_=await _preflight_flat()
            drawdown=max(ZERO,start_available-current_available)
            if drawdown>=loss_stop:
                emit("DAILY_LOSS_STOP",start_available=str(start_available),current_available=str(current_available),
                     drawdown=str(drawdown),loss_stop=str(loss_stop),order_entry=False)
                return
            campaign+=1
            result=await _run_campaign(guard=guard,campaign_no=campaign)
            emit("CAMPAIGN_RESULT",campaign_no=campaign,result=result,order_entry=bool(result.get("order_entry")))
            if result.get("hard_halt"):
                emit("HARD_STOP",reason="CAMPAIGN_HARD_HALT",result=result,order_entry=False)
                return
            if not continuous_enabled():
                return
            # Any fill leaves a position until settlement; never select another market
            # while that position remains open.
            if not await _wait_flat_after_settlement():
                emit("HARD_STOP",reason="SETTLEMENT_DID_NOT_CLEAR_WITHIN_BOUND",order_entry=False)
                return
            settled_available=D(str(await base._gemini_available_usd()))
            prior=_read_state() or {}
            prior.update(
                open=False,status="SETTLED_ACCOUNT_FLAT",available_usd_after=str(settled_available),
                realized_account_delta_from_process_start=str(settled_available-start_available),
                settled_at_utc=datetime.now(timezone.utc).isoformat(),
            )
            _write_state(prior)
            emit("SETTLEMENT_RECONCILED",available_usd=str(settled_available),
                 process_account_delta=str(settled_available-start_available),order_entry=False,
                 rewards_realized="SEPARATE_ACCOUNTING_REQUIRED")
            await asyncio.sleep(0.25)
    finally:
        stop_event.set()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
