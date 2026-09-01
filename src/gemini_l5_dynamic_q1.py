"""Bounded continuous owner for adaptive Gemini ETH5 q1 maker pairs.

The owner repeatedly re-discovers the live ETH 5m contract, requires enough
fee-adjusted entry edge to reserve one tick for complement completion, runs
exactly one q1 YES+NO pair cycle, reconciles it authoritatively, and only then
decides whether another cycle is allowed.

After a first fill, the pair primitive may spend only reserved edge on bounded,
maker-only complement reprices.  Critical invariant: one cycle can create at
most q=1 directional inventory.  Any residual, orphan/active order, unexpected
position, ambiguous completion state, post reject, or account inconsistency
halts the session fail-closed. Matched pairs are locked economically but are not
reported as realized P&L until Gemini settlement.
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

from src import gemini_l5_pair_q1 as pair
from src.gemini_prediction_auth import private_rest_request

ZERO = D("0")
MATCHED_STATUSES = frozenset({"MATCHED", "MATCHED_DYNAMIC_COMPLETION"})
NO_FILL_STATUSES = frozenset({"NO_FILL_TIMEOUT", "NO_AUTHORITATIVE_FILL"})

LIVE_ENV = "GEMINI_L5_DYNAMIC_LIVE_Q1"
SID_ENV = "GEMINI_L5_DYNAMIC_SESSION_ID"
ARM_ENV = "GEMINI_L5_DYNAMIC_ARM"
STATE_ENV = "GEMINI_L5_DYNAMIC_STATE_FILE"

MAX_SESSION_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_MAX_SESSION_SECONDS", "21600"))
MAX_CAMPAIGNS = int(os.getenv("GEMINI_L5_DYNAMIC_MAX_CAMPAIGNS", "48"))
MAX_MATCHED_Q = D(os.getenv("GEMINI_L5_DYNAMIC_MAX_MATCHED_Q", "12"))
MIN_TOP_PAIR_EDGE = D(os.getenv("GEMINI_L5_DYNAMIC_MIN_TOP_PAIR_EDGE", "0.01"))
FIRST_WAIT_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_FIRST_WAIT_SECONDS", "45"))
COMPLEMENT_WAIT_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_COMPLEMENT_WAIT_SECONDS", "60"))
MIN_TTE_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_MIN_TTE_SECONDS", "105"))
IDLE_POLL_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_IDLE_POLL_SECONDS", "5"))
CYCLE_PAUSE_SECONDS = float(os.getenv("GEMINI_L5_DYNAMIC_CYCLE_PAUSE_SECONDS", "1.0"))


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_L5_DYNAMIC_Q1 "
        + json.dumps(
            {"stage": stage, "observed_at_utc": datetime.now(timezone.utc).isoformat(), **fields},
            separators=(",", ":"), default=str,
        ), flush=True,
    )


def armed() -> bool:
    sid = os.getenv(SID_ENV, "").strip()
    return (
        os.getenv(LIVE_ENV, "").strip().upper() == "YES"
        and bool(sid)
        and os.getenv(ARM_ENV, "").strip() == sid
    )


def _state_path() -> Path:
    return Path(os.getenv(STATE_ENV, "/state/gemini-l5-dynamic-q1.json"))


def _write_state(payload: Mapping[str, Any]) -> None:
    path = _state_path(); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as handle:
        json.dump(dict(payload), handle, sort_keys=True, default=str)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)


def _rows(body: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(body, Mapping): return []
    raw = body.get(key)
    return [dict(row) for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []


async def account_guard(allowed_balanced_symbols: set[str]) -> dict[str, Any]:
    active, positions = await asyncio.gather(
        private_rest_request("POST", "/v1/prediction-markets/orders/active", params={"limit":1000,"offset":0}, timeout=8.0),
        private_rest_request("POST", "/v1/prediction-markets/positions", params={"limit":1000,"offset":0,"sort":"+expiryDate"}, timeout=8.0),
    )
    if active.status_code != 200 or positions.status_code != 200:
        raise RuntimeError(f"account guard read failed active={active.status_code} positions={positions.status_code}")
    active_rows = _rows(active.json(), "orders")
    if active_rows: raise RuntimeError(f"unexpected active prediction orders={len(active_rows)}")
    grouped: dict[str, dict[str, D]] = {}; visible=[]
    for row in _rows(positions.json(), "positions"):
        qty=D(str(row.get("totalQuantity") or "0"))
        if qty <= ZERO: continue
        symbol=str(row.get("symbol") or "").strip().upper()
        outcome=str(row.get("eventOutcome") or row.get("event_outcome") or "").strip().lower()
        if not symbol or outcome not in {"yes","no"}:
            raise RuntimeError(f"unrecognized nonzero prediction position symbol={symbol!r} outcome={outcome!r}")
        if symbol not in allowed_balanced_symbols:
            raise RuntimeError(f"unexpected prediction position symbol={symbol} qty={qty} outcome={outcome}")
        bucket=grouped.setdefault(symbol,{"yes":ZERO,"no":ZERO}); bucket[outcome]+=qty
        visible.append({"symbol":symbol,"outcome":outcome,"qty":str(qty)})
    for symbol,bucket in grouped.items():
        if bucket["yes"] != bucket["no"]:
            raise RuntimeError(f"unbalanced prediction position symbol={symbol} yes={bucket['yes']} no={bucket['no']}")
    return {"active_orders":0,"positions":visible,"mutation":False}


def _read_cycle(path: Path) -> dict[str, Any] | None:
    if not path.exists(): return None
    try: payload=json.loads(path.read_text())
    except (OSError,json.JSONDecodeError) as exc: raise RuntimeError(f"cycle state unreadable path={path}") from exc
    if not isinstance(payload,dict): raise RuntimeError("cycle state is not an object")
    return payload


async def _run_cycle(session_id: str, number: int, state_dir: Path) -> dict[str, Any] | None:
    cycle_sid=f"{session_id}-c{number:03d}"; cycle_path=state_dir/f"cycle-{number:03d}.json"
    try: cycle_path.unlink()
    except FileNotFoundError: pass
    os.environ[pair.LIVE_ENV]="YES"; os.environ[pair.SID_ENV]=cycle_sid; os.environ[pair.ARM_ENV]=cycle_sid; os.environ[pair.STATE_ENV]=str(cycle_path)
    emit(
        "CYCLE_START",
        cycle=number,
        cycle_session=cycle_sid,
        min_final_pair_edge=str(MIN_TOP_PAIR_EDGE),
        completion_reserve_ticks=pair.COMPLETION_RESERVE_TICKS,
        max_complement_reprices=pair.MAX_COMPLEMENT_REPRICES,
        max_backoff_per_side="0.02",
        max_new_residual_qty=1,
        first_wait_s=FIRST_WAIT_SECONDS,
        complement_wait_s=COMPLEMENT_WAIT_SECONDS,
        order_entry=True,
        realized_pnl=None,
    )
    await pair.run(min_edge=MIN_TOP_PAIR_EDGE, first_wait=FIRST_WAIT_SECONDS, complement_wait=COMPLEMENT_WAIT_SECONDS, min_tte=MIN_TTE_SECONDS)
    return _read_cycle(cycle_path)


async def run() -> None:
    if not armed():
        emit("DISARMED", q_per_side=1, max_new_residual_qty=1, max_campaigns=MAX_CAMPAIGNS, max_matched_q=str(MAX_MATCHED_Q), max_session_seconds=MAX_SESSION_SECONDS, order_entry=False, realized_pnl=None); return
    session_id=os.getenv(SID_ENV,"").strip(); state_dir=_state_path().parent; state_dir.mkdir(parents=True,exist_ok=True)
    startup=await account_guard(set()); emit("ACCOUNT_START_FLAT", **startup, order_entry=False, realized_pnl=None)
    deadline=time.monotonic()+MAX_SESSION_SECONDS; allowed_symbols:set[str]=set(); campaigns=[]; matched_total=ZERO; attempts=0; terminal_reason="SESSION_LIMIT"
    outer={"terminal":False,"sid":session_id,"started_at_utc":datetime.now(timezone.utc).isoformat(),"matched_qty":"0","campaigns":campaigns}; _write_state(outer)
    emit(
        "LIVE_START",
        session=session_id,
        q_per_side=1,
        max_new_residual_qty=1,
        adaptive_backoff_cents=[0,1,2],
        completion_reserve_ticks=pair.COMPLETION_RESERVE_TICKS,
        dynamic_complement_reprices=pair.MAX_COMPLEMENT_REPRICES,
        cancel_before_repost=True,
        exact_inactive_before_terminal_fill_check=True,
        exact_inactive_gate=True,
        queue_aware=True,
        max_campaigns=MAX_CAMPAIGNS,
        max_matched_q=str(MAX_MATCHED_Q),
        max_session_seconds=MAX_SESSION_SECONDS,
        min_final_pair_edge=str(MIN_TOP_PAIR_EDGE),
        first_wait_s=FIRST_WAIT_SECONDS,
        complement_wait_s=COMPLEMENT_WAIT_SECONDS,
        residual_policy="HALT_ON_ANY_RESIDUAL",
        auto_restart=False,
        order_entry=True,
        realized_pnl=None,
    )
    try:
        while time.monotonic()<deadline and len(campaigns)<MAX_CAMPAIGNS and matched_total<MAX_MATCHED_Q:
            attempts+=1
            before=await account_guard(allowed_symbols); emit("ACCOUNT_GUARD_OK",attempt=attempts,**before,order_entry=False,realized_pnl=None)
            cycle=await _run_cycle(session_id,attempts,state_dir)
            if cycle is None:
                emit("CYCLE_IDLE",attempt=attempts,reason="NO_MUTATING_CYCLE_STATE",order_entry=False,realized_pnl=None); await asyncio.sleep(IDLE_POLL_SECONDS); continue
            status=str(cycle.get("status") or ""); symbol=str(cycle.get("symbol") or "").strip().upper(); yes_qty=D(str(cycle.get("yes_qty") or "0")); no_qty=D(str(cycle.get("no_qty") or "0")); matched=min(yes_qty,no_qty); residual=abs(yes_qty-no_qty)
            record={
                "attempt":attempts,
                "event":cycle.get("event"),
                "symbol":symbol,
                "status":status,
                "rescue_status":str(cycle.get("rescue_status") or ""),
                "completion_reserve_ticks":int(cycle.get("completion_reserve_ticks") or 0),
                "completion_reprices":int(cycle.get("completion_reprices") or 0),
                "yes_backoff":str(cycle.get("yes_backoff") or ""),
                "no_backoff":str(cycle.get("no_backoff") or ""),
                "total_backoff":str(cycle.get("total_backoff") or ""),
                "preferred_backoff_side":str(cycle.get("preferred_backoff_side") or ""),
                "entry_edge":str(cycle.get("entry_edge") or ""),
                "yes_qty":str(yes_qty),
                "no_qty":str(no_qty),
                "matched_qty":str(matched),
                "residual_qty":str(residual),
                "maker_turnover":str(cycle.get("maker_turnover") or "0"),
                "maker_fees":str(cycle.get("maker_fees") or "0"),
                "realized":False,
            }
            campaigns.append(record); matched_total+=matched; outer.update({"campaigns":campaigns,"matched_qty":str(matched_total)}); _write_state(outer)
            emit("CYCLE_COMPLETE",**record,session_matched_qty=str(matched_total),order_entry=False,realized_pnl=None)
            if residual>ZERO:
                terminal_reason="RESIDUAL_DETECTED"; raise RuntimeError(f"dynamic session halted on residual symbol={symbol} yes={yes_qty} no={no_qty}")
            if status in MATCHED_STATUSES:
                if not symbol: terminal_reason="MATCHED_WITHOUT_SYMBOL"; raise RuntimeError("matched cycle missing symbol")
                allowed_symbols.add(symbol)
            elif status in NO_FILL_STATUSES:
                pass
            else:
                terminal_reason=f"UNSAFE_CYCLE_STATUS_{status or 'MISSING'}"; raise RuntimeError(f"dynamic session halted on cycle status={status!r}")
            after=await account_guard(allowed_symbols); emit("POST_CYCLE_GUARD_OK",attempt=attempts,**after,order_entry=False,realized_pnl=None)
            if matched_total>=MAX_MATCHED_Q: terminal_reason="MATCHED_CAP_REACHED"; break
            await asyncio.sleep(CYCLE_PAUSE_SECONDS)
        else:
            if len(campaigns)>=MAX_CAMPAIGNS: terminal_reason="CAMPAIGN_CAP_REACHED"
            elif time.monotonic()>=deadline: terminal_reason="SESSION_TIME_REACHED"
    except Exception as exc:
        outer.update({"terminal":True,"terminal_reason":terminal_reason,"error":f"{type(exc).__name__}: {exc}","matched_qty":str(matched_total),"ended_at_utc":datetime.now(timezone.utc).isoformat()}); _write_state(outer)
        emit("HALT",reason=terminal_reason,error=f"{type(exc).__name__}: {exc}",matched_qty=str(matched_total),campaigns=len(campaigns),order_entry=False,realized_pnl=None); raise
    final_guard=await account_guard(allowed_symbols)
    outer.update({"terminal":True,"terminal_reason":terminal_reason,"matched_qty":str(matched_total),"ended_at_utc":datetime.now(timezone.utc).isoformat()}); _write_state(outer)
    emit("COMPLETE",reason=terminal_reason,matched_qty=str(matched_total),campaigns=len(campaigns),account=final_guard,settlement_required=matched_total>ZERO,realized_pnl=None,order_entry=False)


def main() -> None: asyncio.run(run())
if __name__=="__main__": main()
