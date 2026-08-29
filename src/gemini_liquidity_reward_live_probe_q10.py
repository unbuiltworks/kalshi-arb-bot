"""One-market Gemini BTC5m q=10 liquidity-reward live probe.

Fresh explicit approval is required through a dual-key arm. The probe waits for a
new BTC 5m market that is publicly reward-eligible, reports zero qualifying makers,
and has safe quote geometry. It posts exactly one maker-only YES order and one
maker-only NO order, each q=10. No repricing, no taker rescue, no second campaign.
On the first observed fill it cancels both orders immediately. Any residual position
is left to settle; maximum submitted quantity on either outcome is 10 contracts.
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

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src import gemini_short_crypto_local_budget as local
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee

Q = D("10")
ONE = D("1")
ZERO = D("0")
REST = "https://api.gemini.com"
LIVE_ENV = "GEMINI_LIQUIDITY_REWARD_LIVE_Q10"
PROBE_ID_ENV = "GEMINI_LIQUIDITY_REWARD_PROBE_ID"
PROBE_ARM_ENV = "GEMINI_LIQUIDITY_REWARD_PROBE_ARM"
MIN_SECONDS_TO_EXPIRY = 240.0
MAX_SECONDS_TO_EXPIRY = 330.0
QUALIFIER_WAIT_SECONDS = 600.0
QUALIFIER_POLL_SECONDS = 5.0
REST_SECONDS = 180.0
STOP_BEFORE_EXPIRY_SECONDS = 30.0
MIN_TRADE_EDGE = D("0.50")
MAX_ONE_SIDED_PREMIUM = D("6.50")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
STATE_ENV = "GEMINI_LIQUIDITY_REWARD_Q10_STATE_DIR"


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_LIQUIDITY_REWARD_Q10 " + json.dumps({
        "stage": stage,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        **fields,
    }, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    pid = os.getenv(PROBE_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and pid and os.getenv(PROBE_ARM_ENV, "").strip() == pid)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "probe"


def _state_file() -> Path:
    root = Path(os.getenv(STATE_ENV, "/state"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "gemini-liquidity-reward-q10-probe.json"


def _write_state(payload: Mapping[str, Any]) -> None:
    path = _state_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")
    tmp.replace(path)


async def _reward_snapshot(ticker: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent":"arb-q10-reward-probe/1.0"}) as http:
        cfg_r, ev_r = await asyncio.gather(
            http.get(f"{REST}/v1/prediction-markets/liquidity-rewards/config"),
            http.get(f"{REST}/v1/prediction-markets/liquidity-rewards/events", params={"category":"Crypto","limit":100,"offset":0}),
        )
    if cfg_r.status_code != 200 or ev_r.status_code != 200:
        return None
    cfg = cfg_r.json() if cfg_r.content else {}
    payload = ev_r.json() if ev_r.content else {}
    rows = payload.get("data") or payload.get("events") or payload.get("liquidity_reward_events") or []
    target = None
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("event_ticker") or row.get("eventTicker") or "").upper() == ticker.upper():
            target = dict(row)
            break
    if not bool(cfg.get("enabled")) or target is None:
        return None
    return {"config": cfg, "event": target}


def _model(bid: D, ask: D) -> dict[str, D]:
    yes = D(str(bid))
    no = ONE - D(str(ask))
    yes_fee = gemini_predictions_maker_fee(yes, Q)
    no_fee = gemini_predictions_maker_fee(no, Q)
    locked = Q * (ONE - yes - no) - yes_fee - no_fee
    mid = (D(str(bid)) + D(str(ask))) / D("2")
    return {
        "yes_quote": yes,
        "no_quote": no,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "locked_edge": locked,
        "yes_distance": mid - yes,
        "no_distance": (ONE - mid) - no,
        "max_one_sided_premium": max(yes * Q, no * Q),
    }


async def _candidate() -> tuple[base.Event, dict[str, Any], dict[str, D]] | None:
    event = await base.discover_btc5m()
    if event is None or not (MIN_SECONDS_TO_EXPIRY <= event.seconds_to_expiry <= MAX_SECONDS_TO_EXPIRY):
        return None
    reward = await _reward_snapshot(event.ticker)
    if reward is None:
        return None
    row = reward["event"]
    makers_raw = row.get("qualifying_maker_count") if "qualifying_maker_count" in row else row.get("qualifyingMakerCount")
    try:
        makers = int(makers_raw)
    except Exception:
        return None
    if makers != 0:
        return None
    bid, _, ask, _ = await base.fresh_top(event.symbol)
    model = _model(bid, ask)
    cfg = reward["config"]
    max_distance = D(str(cfg.get("max_spread_cents") or 10)) / D("100")
    if model["yes_distance"] > max_distance or model["no_distance"] > max_distance:
        return None
    if not (MIN_PRICE <= model["yes_quote"] <= MAX_PRICE and MIN_PRICE <= model["no_quote"] <= MAX_PRICE):
        return None
    if model["locked_edge"] < MIN_TRADE_EDGE:
        return None
    if model["max_one_sided_premium"] > MAX_ONE_SIDED_PREMIUM:
        return None
    return event, reward, model


async def _wait_candidate() -> tuple[base.Event, dict[str, Any], dict[str, D]]:
    deadline = time.monotonic() + QUALIFIER_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            found = await _candidate()
        except Exception as exc:
            emit("QUALIFIER_RETRY", error_type=type(exc).__name__, order_entry=False)
            found = None
        if found is not None:
            return found
        await asyncio.sleep(QUALIFIER_POLL_SECONDS)
    raise RuntimeError("no qualifying zero-competition BTC5m reward market within bounded wait")


async def _reconcile(yes_id: str, no_id: str, symbol: str) -> tuple[Any, Any, D, D]:
    yes_row = await base._order_by_client(yes_id, symbol)
    no_row = await base._order_by_client(no_id, symbol)
    return yes_row, no_row, base._filled(yes_row), base._filled(no_row)


async def run() -> None:
    if not live_armed():
        emit("DISARMED", quantity=str(Q), max_one_sided_contracts=str(Q), order_entry=False, credentials_read=False,
             classification="DISARMED_NO_VENUE_CALLS")
        return

    if _state_file().exists():
        raise RuntimeError("q10 reward probe state file already exists; refusing a second campaign")
    if local.local_open_state() is not None:
        raise RuntimeError("unresolved short-crypto local reservation blocks q10 reward probe")
    exposure = await local.account_exposure_snapshot()
    if int(exposure.get("active_short_crypto_orders") or 0) != 0 or int(exposure.get("active_short_crypto_positions") or 0) != 0:
        raise RuntimeError("live short-crypto exposure is not flat")
    if not await base._gemini_terms_ok():
        raise RuntimeError("Gemini Prediction Markets terms not accepted")
    available = D(str(await base._gemini_available_usd()))
    if available < D("15"):
        raise RuntimeError("available Gemini USD below $15 q10 probe reserve")

    event, reward, model = await _wait_candidate()
    # Fresh public book immediately before mutation.
    bid, _, ask, _ = await base.fresh_top(event.symbol)
    fresh = _model(bid, ask)
    if fresh["locked_edge"] < MIN_TRADE_EDGE or fresh["max_one_sided_premium"] > MAX_ONE_SIDED_PREMIUM:
        emit("STOP", reason="FRESH_RISK_OR_EDGE_GATE_FAILED", model={k:str(v) for k,v in fresh.items()}, order_entry=False)
        return
    if not (MIN_PRICE <= fresh["yes_quote"] <= MAX_PRICE and MIN_PRICE <= fresh["no_quote"] <= MAX_PRICE):
        emit("STOP", reason="FRESH_PRICE_BAND_FAILED", model={k:str(v) for k,v in fresh.items()}, order_entry=False)
        return
    fresh_reward = await _reward_snapshot(event.ticker)
    if fresh_reward is None:
        emit("STOP", reason="REWARD_EVENT_NO_LONGER_ACTIVE", order_entry=False)
        return
    rr = fresh_reward["event"]
    maker_count = rr.get("qualifying_maker_count") if "qualifying_maker_count" in rr else rr.get("qualifyingMakerCount")
    if int(maker_count) != 0:
        emit("STOP", reason="QUALIFYING_MAKER_COUNT_NO_LONGER_ZERO", qualifying_maker_count=maker_count, order_entry=False)
        return
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE", seconds_to_expiry=event.seconds_to_expiry, order_entry=False)
        return

    pid = _clean(os.environ[PROBE_ID_ENV])
    trade_id = f"gem-q10-reward-{pid}"[:32]
    yes_id = f"{trade_id}-yes"[:36]
    no_id = f"{trade_id}-no"[:36]
    yes_params = {"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(fresh["yes_quote"]),"quantity":"10","eventOutcome":"YES","clientOrderId":yes_id}
    no_params = {"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(fresh["no_quote"]),"quantity":"10","eventOutcome":"NO","clientOrderId":no_id}
    state = {
        "classification":"LIVE_Q10_LIQUIDITY_REWARD_PROBE_NOT_REALIZED",
        "probe_id":pid,"trade_id":trade_id,"ticker":event.ticker,"symbol":event.symbol,
        "yes_client_order_id":yes_id,"no_client_order_id":no_id,"quantity_per_outcome":"10",
        "yes_quote":str(fresh["yes_quote"]),"no_quote":str(fresh["no_quote"]),
        "modeled_trade_only_locked_edge":str(fresh["locked_edge"]),
        "max_one_sided_premium_at_risk":str(fresh["max_one_sided_premium"]),
        "reported_daily_pool_usd":str(rr.get("daily_pool_usd") or rr.get("dailyPoolUsd") or ""),
        "reported_qualifying_maker_count":0,"status":"ARMED_PRE_MUTATION","order_entry":False,
    }
    _write_state(state)
    emit("START", **state)

    maker = base.GeminiMakerSession()
    yes_row = no_row = None
    try:
        await maker.connect()
        yes_ack = await maker.request("order.place", yes_params, timeout=4.0)
        if int(yes_ack.get("status",0)) != 200:
            state.update(status="YES_MAKER_REJECTED", order_entry=True, yes_ack_status=yes_ack.get("status"))
            _write_state(state); emit("STOP", **state); return
        yes_row = await base._find_row(yes_id, event.symbol)
        no_ack = await maker.request("order.place", no_params, timeout=4.0)
        if int(no_ack.get("status",0)) != 200:
            cancel = await base._cancel_maker(maker, row=yes_row)
            _, _, yf, _ = await _reconcile(yes_id, no_id, event.symbol)
            state.update(status="NO_MAKER_REJECTED_AFTER_YES", order_entry=True, yes_fill=str(yf), yes_cancel=cancel)
            _write_state(state); emit("HARD_STOP", **state); return
        no_row = await base._find_row(no_id, event.symbol)
        state.update(status="BOTH_MAKERS_POSTED", order_entry=True, posted_at_utc=datetime.now(timezone.utc).isoformat())
        _write_state(state); emit("BOTH_MAKERS_POSTED", **state)

        deadline = min(time.monotonic()+REST_SECONDS, time.monotonic()+max(1.0,event.seconds_to_expiry-STOP_BEFORE_EXPIRY_SECONDS))
        first_fill = None
        while time.monotonic() < deadline:
            try:
                item = await asyncio.wait_for(maker.events.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if not isinstance(item, Mapping) or str(item.get("c") or "") not in {yes_id,no_id}:
                continue
            qty = D(str(item.get("Z") or "0")); status = str(item.get("X") or "").upper()
            if qty > ZERO and status in {"PARTIALLY_FILLED","FILLED"}:
                first_fill = {"client_order_id":str(item.get("c")),"quantity":str(qty),"status":status}
                break

        yes_row = await base._order_by_client(yes_id,event.symbol) or yes_row
        no_row = await base._order_by_client(no_id,event.symbol) or no_row
        yes_cancel, no_cancel = await asyncio.gather(base._cancel_maker(maker,row=yes_row), base._cancel_maker(maker,row=no_row))
        await asyncio.sleep(0.2)
        _, _, yes_fill, no_fill = await _reconcile(yes_id,no_id,event.symbol)
        if yes_fill > Q or no_fill > Q:
            raise RuntimeError("q10 hard quantity bound violated")
        state.update(
            status="CANCELLED_AFTER_FIRST_FILL" if first_fill else "CANCELLED_AFTER_REWARD_REST_WINDOW",
            first_fill=first_fill, yes_fill=str(yes_fill), no_fill=str(no_fill), yes_cancel=yes_cancel, no_cancel=no_cancel,
            unmatched_quantity=str(abs(yes_fill-no_fill)), matched_quantity=str(min(yes_fill,no_fill)),
            order_entry=False, realized_pnl=None, rewards_realized=None,
            ended_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        _write_state(state); emit("COMPLETE", **state)
    finally:
        await maker.close()


if __name__ == "__main__":
    asyncio.run(run())
