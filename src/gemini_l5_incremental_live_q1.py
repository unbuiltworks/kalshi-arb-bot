"""Bounded live q=1 incremental Gemini maker experiment across crypto + sports.

This is the first live controller shaped like the observed L5/Polymarket behavior:
small maker clips, let independent external flow match inventory naturally, refresh
only after a complete YES+NO set, and carry only tightly bounded residual inventory.

It is an execution/mechanics experiment, not proven profit and not arbitrage.
Rebates/LR are excluded from realized P&L here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal as D
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import httpx

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src.gemini_incentive_mm_live_probe_q1 import discover_eth5m
from src import gemini_sports_l5_live_probe_q1 as sports

ONE = D("1")
ZERO = D("0")
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")
MIN_EDGE = D("0.00")  # q1 mechanics test: never intentionally lock a fee-adjusted loss.
MAX_CYCLES_PER_FAMILY = 2
MAX_WORK_SECONDS = 75.0
MIN_SECONDS_TO_EXPIRY = 150.0
MAX_NEW_RESIDUAL_NOTIONAL = D("1.50")
MIN_AVAILABLE_USD = D("5")

LIVE_ENV = "GEMINI_L5_INCREMENTAL_LIVE_Q1"
SESSION_ENV = "GEMINI_L5_INCREMENTAL_SESSION_ID"
ARM_ENV = "GEMINI_L5_INCREMENTAL_ARM"
STATE_ENV = "GEMINI_L5_INCREMENTAL_STATE_FILE"
QUARANTINE_ENV = "GEMINI_L5_QUARANTINE_EVENT_TICKERS"


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_L5_INCREMENTAL_Q1 " + json.dumps({
        "stage": stage,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        **fields,
    }, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def armed() -> bool:
    sid = os.getenv(SESSION_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and sid and os.getenv(ARM_ENV, "").strip() == sid)


def _clean(value: str, limit: int = 18) -> str:
    out = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:limit]
    return out or "incq1"


def _state_path() -> Path:
    return Path(os.getenv(STATE_ENV, "/state/gemini-l5-incremental-q1.json"))


def _write_state(payload: Mapping[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(dict(payload), fh, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _existing_unfinished_state() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("incremental state unreadable") from exc
    if isinstance(body, dict) and body.get("terminal") is not True:
        return body
    return None


def _quarantine() -> set[str]:
    return {x.strip().upper() for x in os.getenv(QUARANTINE_ENV, "").split(",") if x.strip()}


def acceptable_model(model: Mapping[str, Any]) -> bool:
    try:
        y = D(str(model["yes_quote"])); n = D(str(model["no_quote"])); edge = D(str(model["modeled_edge"]))
    except Exception:
        return False
    return MIN_PRICE <= y <= MAX_PRICE and MIN_PRICE <= n <= MAX_PRICE and edge >= MIN_EDGE


@dataclass
class FamilyResult:
    family: str
    event_ticker: str | None = None
    symbol: str | None = None
    matched_pairs: int = 0
    yes_fill: D = ZERO
    no_fill: D = ZERO
    maker_turnover: D = ZERO
    residual_notional: D = ZERO
    stopped: str = ""


async def discover_sports(excluded: set[str]) -> base.Event | None:
    now_ms = int(time.time() * 1000)
    candidates: list[tuple[float, int, base.Event]] = []
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent":"arb-gemini-l5-incq1/1.0"}) as http:
        offset = 0
        while offset < 5000:
            r = await http.get(f"{base.REST}/v1/prediction-markets/events", params=[("status","active"),("category","Sports"),("limit","500"),("offset",str(offset))])
            r.raise_for_status(); payload = r.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ok, _ = sports._simple_moneyline(row)
                ticker = str(row.get("ticker") or "").strip().upper()
                if not ok or not ticker or ticker in excluded:
                    continue
                active = [c for c in (row.get("contracts") or []) if isinstance(c, Mapping) and str(c.get("marketState") or c.get("status") or "").lower() in {"open","active"}]
                if len(active) != 2:
                    continue
                vol = float(row.get("volume24h") or 0.0)
                for c in active:
                    if not sports._q1(c):
                        continue
                    symbol = str(c.get("instrumentSymbol") or "").strip()
                    expiry = base._iso_ms(c.get("expiryDate") or row.get("expiryDate"))
                    if not symbol or expiry is None:
                        continue
                    tte = (expiry - now_ms) / 1000.0
                    if not (MIN_SECONDS_TO_EXPIRY <= tte <= sports.MAX_EXPIRY_S):
                        continue
                    candidates.append((vol, expiry, base.Event(ticker=ticker, symbol=symbol, expiry_ms=expiry,
                        quantity_minimum=D(str(c.get("quantityMinimum") or "1")), quantity_increment=D(str(c.get("quantityIncrement") or "1")), price_increment=D(str(c.get("priceIncrement") or "0.01")))))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows)); offset += len(rows)
            if not rows or offset >= total:
                break
    candidates.sort(key=lambda x: (-x[0], x[1], x[2].symbol))
    for _, _, event in candidates[:40]:
        try:
            bid, _, ask, _ = await base.fresh_top(event.symbol, timeout=2.0)
            if acceptable_model(base.pair_model(bid, ask)):
                return event
        except Exception:
            continue
    return None


async def _exact_cancel(maker: base.GeminiMakerSession, row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {"ok": False, "reason": "ROW_MISSING_NO_BROAD_CANCEL"}
    return await base._cancel_maker(maker, row=row)


async def _run_family(family: str, event: base.Event, sid: str, state: dict[str, Any]) -> FamilyResult:
    out = FamilyResult(family=family, event_ticker=event.ticker, symbol=event.symbol)
    maker = base.GeminiMakerSession()
    await maker.connect()
    try:
        for cycle in range(1, MAX_CYCLES_PER_FAMILY + 1):
            if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
                out.stopped = "EXPIRY_TOO_CLOSE"; break
            bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
            model = base.pair_model(bid, ask)
            if not acceptable_model(model):
                out.stopped = "PAIR_ECONOMICS_NOT_ACCEPTABLE"; break
            y = D(str(model["yes_quote"])); n = D(str(model["no_quote"]))
            trade_id = f"l5inc-{family}-{sid}-{cycle}"[:64]
            yes_id, no_id = maker_client_ids(trade_id)
            yes_params = {"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(y),"quantity":"1","eventOutcome":"YES","clientOrderId":yes_id}
            no_params = {"symbol":event.symbol,"side":"BUY","type":"LIMIT","timeInForce":"MOC","price":str(n),"quantity":"1","eventOutcome":"NO","clientOrderId":no_id}
            state.update({"terminal":False,"family":family,"cycle":cycle,"event_ticker":event.ticker,"symbol":event.symbol,"yes_client_id":yes_id,"no_client_id":no_id,"yes_quote":str(y),"no_quote":str(n),"active":True})
            _write_state(state)

            yes_ack = await maker.request("order.place", yes_params, timeout=4.0)
            if int(yes_ack.get("status",0)) != 200:
                out.stopped = "YES_REJECTED"; state["active"] = False; _write_state(state); break
            yes_row = await base._find_row(yes_id, event.symbol)
            if yes_row is None:
                raise RuntimeError("YES maker accepted but exact order not visible")
            no_ack = await maker.request("order.place", no_params, timeout=4.0)
            if int(no_ack.get("status",0)) != 200:
                await _exact_cancel(maker, yes_row)
                out.stopped = "NO_REJECTED"; state["active"] = False; _write_state(state); break
            no_row = await base._find_row(no_id, event.symbol)
            if no_row is None:
                await _exact_cancel(maker, yes_row)
                raise RuntimeError("NO maker accepted but exact order not visible")

            emit("CYCLE_POSTED", family=family, cycle=cycle, event_ticker=event.ticker, symbol=event.symbol,
                 yes_quote=str(y), no_quote=str(n), modeled_edge=str(model["modeled_edge"]), yes_queue_hint=str(bid_size), no_queue_hint=str(ask_size), order_entry=True)
            deadline = time.monotonic() + min(MAX_WORK_SECONDS, max(1.0, event.seconds_to_expiry - 30.0))
            first_fill_side = None
            while time.monotonic() < deadline:
                try:
                    item = await asyncio.wait_for(maker.events.get(), timeout=min(0.5, max(0.05, deadline-time.monotonic())))
                except asyncio.TimeoutError:
                    continue
                if not isinstance(item, Mapping):
                    continue
                cid = str(item.get("c") or "")
                if cid not in {yes_id,no_id}:
                    continue
                status = str(item.get("X") or "").upper(); qty = D(str(item.get("Z") or "0"))
                if status in {"PARTIALLY_FILLED","FILLED"} and qty > 0 and first_fill_side is None:
                    first_fill_side = "YES" if cid == yes_id else "NO"
                    emit("FIRST_FILL_KEEP_COMPLEMENT_WORKING", family=family, cycle=cycle, side=first_fill_side, reported_qty=str(qty), order_entry=True)
                yrow, nrow, yf, nf = await base._reconcile_pair(yes_id, no_id, event.symbol)
                if yf >= ONE and nf >= ONE:
                    yes_row, no_row = yrow or yes_row, nrow or no_row
                    break

            yes_row = await base._order_by_client(yes_id, event.symbol) or yes_row
            no_row = await base._order_by_client(no_id, event.symbol) or no_row
            yc, nc = await asyncio.gather(_exact_cancel(maker, yes_row), _exact_cancel(maker, no_row))
            await asyncio.sleep(0.2)
            _, _, yf, nf = await base._reconcile_pair(yes_id, no_id, event.symbol)
            if not (ZERO <= yf <= ONE and ZERO <= nf <= ONE):
                raise RuntimeError("q1 fill bounds violated")
            out.yes_fill += yf; out.no_fill += nf; out.maker_turnover += yf*y + nf*n
            if yf == ONE and nf == ONE:
                out.matched_pairs += 1
                state.update({"active":False,"last_cycle_status":"MATCHED_COMPLETE_SET","yes_fill_total":str(out.yes_fill),"no_fill_total":str(out.no_fill),"maker_turnover":str(out.maker_turnover)})
                _write_state(state)
                emit("CYCLE_MATCHED", family=family, cycle=cycle, yes_fill=str(yf), no_fill=str(nf), cancel_yes=yc, cancel_no=nc, order_entry=False, realized=False)
                continue
            residual = abs(yf-nf) * (y if yf > nf else n)
            out.residual_notional += residual
            state.update({"active":False,"last_cycle_status":"RESIDUAL","yes_fill_total":str(out.yes_fill),"no_fill_total":str(out.no_fill),"maker_turnover":str(out.maker_turnover),"residual_notional":str(out.residual_notional)})
            _write_state(state)
            emit("CYCLE_RESIDUAL_STOP_FAMILY", family=family, cycle=cycle, yes_fill=str(yf), no_fill=str(nf), residual_notional=str(residual), order_entry=False, realized=False)
            out.stopped = "RESIDUAL_CREATED"
            break
    finally:
        await maker.close()
    return out


async def run() -> None:
    if not armed():
        emit("DISARMED", order_entry=False, classification="DISARMED_NO_VENUE_CALLS")
        return
    unfinished = _existing_unfinished_state()
    if unfinished is not None:
        raise RuntimeError("unfinished incremental state exists; reconciliation required")
    terms = await base._gemini_terms_ok(); available = await base._gemini_available_usd()
    if not terms or available < MIN_AVAILABLE_USD:
        raise RuntimeError("Gemini incremental q1 preflight failed")
    sid = _clean(os.environ[SESSION_ENV], 14)
    state: dict[str, Any] = {"session_id":sid,"terminal":False,"started_at":datetime.now(timezone.utc).isoformat(),"known_quarantine":sorted(_quarantine())}
    _write_state(state)
    emit("START", session_id=sid, available_usd=str(available), q_per_order=1, max_cycles_per_family=MAX_CYCLES_PER_FAMILY,
         max_new_residual_notional=str(MAX_NEW_RESIDUAL_NOTIONAL), maker_rewards_counted_as_realized=False,
         classification="LIVE_INCREMENTAL_MAKER_EXPERIMENT_NOT_PROVEN_PROFIT", order_entry=True)

    results: list[FamilyResult] = []
    new_residual = ZERO
    crypto = await discover_eth5m()
    if crypto is not None and crypto.ticker not in _quarantine():
        r = await _run_family("crypto_eth5m", crypto, sid, state); results.append(r); new_residual += r.residual_notional
    else:
        emit("FAMILY_SKIPPED", family="crypto_eth5m", reason="NO_ELIGIBLE_EVENT", order_entry=False)

    if new_residual <= MAX_NEW_RESIDUAL_NOTIONAL:
        sp = await discover_sports(_quarantine())
        if sp is not None:
            r = await _run_family("sports_moneyline", sp, sid, state); results.append(r); new_residual += r.residual_notional
        else:
            emit("FAMILY_SKIPPED", family="sports_moneyline", reason="NO_ELIGIBLE_EVENT", order_entry=False)
    else:
        emit("FAMILY_SKIPPED", family="sports_moneyline", reason="RESIDUAL_BUDGET_EXHAUSTED", order_entry=False)

    state.update({"terminal":True,"completed_at":datetime.now(timezone.utc).isoformat(),"results":[r.__dict__ for r in results],"new_residual_notional":str(new_residual)})
    _write_state(state)
    emit("COMPLETE", session_id=sid, families=[r.family for r in results], matched_pairs=sum(r.matched_pairs for r in results),
         maker_turnover=str(sum((r.maker_turnover for r in results), ZERO)), new_residual_notional=str(new_residual),
         realized_pnl=None, settlement_required=any((r.yes_fill or r.no_fill) for r in results), order_entry=False)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
