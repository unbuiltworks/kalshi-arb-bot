"""Durable always-on L5 transfer supervisor — SHADOW/DISARMED by default.

Turns the one-off bounded probes into a continuous bounded engine without
skipping source-confirmed accounting or any safety control. Reuses the
proven pieces unchanged: candidate selection (public-only selector), the
v1 policy gate, the forced-completion campaign (`gemini_l5_v1_campaign`),
the dynamic owner's arming/guard discipline for live cycles, and the
corrected settlement auditor for realization.

MODES (a `mode` field is stamped on every emit and every ledger row):
  SHADOW (default) — runs indefinitely with ZERO exchange mutation and no
      private calls. Each iteration follows the exact same decision path
      as live: public candidate scan -> v1 flat gate -> TRANSFER
      INITIALIZER side/price -> 180s simulated maker rest -> forced
      complement at the fee-safe bound -> tte stop. "Fills" are
      THEORETICAL, detected from the public trade tape (a contra taker
      print at or through our price); every shadow row carries
      {"mode":"SHADOW","theoretical":true} and never touches realized
      accounting.
  LIVE — each iteration delegates ONE campaign to the proven dynamic
      owner (`gemini_l5_dynamic_q1.run()`), which enforces its own
      dual-key arming, account guards, q=1 residual cap, and fail-closed
      semantics. The supervisor NEVER arms: without the operator-held
      LIVE/SESSION/ARM triple the owner emits DISARMED and does nothing.
      After every filled live campaign the corrected settlement auditor
      is polled until exact-identity settled rows classify the event;
      realized P&L comes ONLY from that (never balance or positions=[]).

HARD BOUNDS (env-overridable, conservative defaults; enforced here in
addition to the owner's own caps):
  BTC-05M only; q=1; one campaign at a time; residual cap 1 (owner);
  SUPERVISOR_MAX_CAMPAIGNS_PER_DAY   default 6
  SUPERVISOR_DAILY_LOSS_STOP_USD     default 1.00 (authoritative realized
                                     losses only; shadow never counts)
  PERSISTENT HALT on: any UNRESOLVED or AMBIGUOUS settlement, residual
  beyond q1, owner exception (orphan/account inconsistency raise inside
  the owner), or the daily loss stop. A halt survives restarts and
  requires the operator to clear `halt` in state.json.
  No auto-scale; no automatic risk increase ever.

DURABLE STATE under GEMINI_L5_SUPERVISOR_ROOT (default /state/l5-supervisor):
  state.json        counters, day key, halt flag+reason, last iteration
  campaigns.jsonl   compact append-only campaign ledger
  heartbeat.json    utc, mode, status, current candidate, heartbeat age
  daily_YYYYMMDD.json  end-of-day summary rows

  python3 -m src.gemini_l5_transfer_supervisor run        # shadow default
  python3 -m src.gemini_l5_transfer_supervisor status     # one-command report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import httpx

from src import gemini_l5_crypto5m_selector as selector
from src import gemini_l5_settlement_audit as audit_mod
from src import gemini_l5_v1_campaign as camp
from src import gemini_l5_v1_policy as v1_policy
from src.gemini_l5_ladder_q1 import edge

MODE_ENV = "GEMINI_L5_SUPERVISOR_MODE"
ROOT_ENV = "GEMINI_L5_SUPERVISOR_ROOT"
MAX_PER_DAY_ENV = "SUPERVISOR_MAX_CAMPAIGNS_PER_DAY"
LOSS_STOP_ENV = "SUPERVISOR_DAILY_LOSS_STOP_USD"

DEFAULT_ROOT = "/state/l5-supervisor"
DEFAULT_MAX_PER_DAY = 6
DEFAULT_LOSS_STOP = D("1.00")
IDLE_SLEEP_S = 20.0
SETTLE_POLL_S = 60.0
SETTLE_POLL_MAX = 15
MIN_EDGE = D("0.01")
MIN_TTE = 105.0
SHADOW_ASSETS = ("BTC",)
PUBLIC_TRADES = "https://api.gemini.com/v1/trades/{symbol}?limit_trades=200&since={since_ms}"


def mode() -> str:
    return ("LIVE" if os.getenv(MODE_ENV, "").strip().lower() == "live"
            else "SHADOW")


def root() -> Path:
    return Path(os.getenv(ROOT_ENV, DEFAULT_ROOT))


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def emit(stage: str, **fields: Any) -> None:
    print("L5_SUPERVISOR " + json.dumps(
        {"stage": stage, "mode": mode(), "utc": _utc(), **fields},
        separators=(",", ":"), default=str), flush=True)


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    tmp.replace(path)


def load_state() -> Dict[str, Any]:
    p = root() / "state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {"day": _day(), "campaigns_today": 0, "realized_today_usd": "0",
            "halt": False, "halt_reason": "", "iterations": 0}


def save_state(st: Dict[str, Any]) -> None:
    _write(root() / "state.json", st)


def heartbeat(status: str, **fields: Any) -> None:
    _write(root() / "heartbeat.json",
           {"utc": _utc(), "mode": mode(), "status": status, **fields})


def ledger_append(row: Dict[str, Any]) -> None:
    p = root() / "campaigns.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"mode": mode(), "utc": _utc(), **row},
                            separators=(",", ":"), default=str) + "\n")


def rollover_day(st: Dict[str, Any]) -> Dict[str, Any]:
    if st.get("day") != _day():
        _write(root() / f"daily_{st.get('day')}.json", {
            "day": st.get("day"), "campaigns": st.get("campaigns_today", 0),
            "realized_usd_authoritative": st.get("realized_today_usd", "0"),
            "halt": st.get("halt", False),
            "halt_reason": st.get("halt_reason", "")})
        st.update({"day": _day(), "campaigns_today": 0,
                   "realized_today_usd": "0"})
    return st


def bounds_allow(st: Dict[str, Any]) -> Optional[str]:
    """None if a new campaign may start; else the blocking reason."""
    if st.get("halt"):
        return f"HALTED: {st.get('halt_reason', 'unknown')}"
    if st.get("campaigns_today", 0) >= int(
            os.getenv(MAX_PER_DAY_ENV, str(DEFAULT_MAX_PER_DAY))):
        return "DAILY_CAMPAIGN_CAP"
    if D(str(st.get("realized_today_usd", "0"))) <= -D(
            os.getenv(LOSS_STOP_ENV, str(DEFAULT_LOSS_STOP))):
        return "DAILY_LOSS_STOP"
    return None


def halt(st: Dict[str, Any], reason: str) -> None:
    st["halt"] = True
    st["halt_reason"] = reason
    save_state(st)
    emit("HALT", reason=reason)
    heartbeat("halted", reason=reason)


# ---------------- shadow campaign (zero mutation, public data only) ----------

async def _public_trades(http: httpx.AsyncClient, symbol: str,
                         since_ms: int) -> List[dict]:
    try:
        r = await http.get(PUBLIC_TRADES.format(symbol=symbol,
                                                since_ms=since_ms))
        rows = r.json() if r.status_code == 200 else []
        return [x for x in rows if isinstance(x, dict)] if isinstance(rows, list) else []
    except Exception:
        return []


def shadow_fillable(trades: List[dict], side: str, price: D) -> Optional[dict]:
    """A maker YES join at `price` rests as a BID on the UP book: fillable
    when a taker SELL prints at <= price. A maker NO join (price is the NO
    price) rests as an ASK at (1 - price): fillable when a taker BUY
    prints at >= that ask. THEORETICAL: queue priority is ignored, so this
    is an upper bound on fill likelihood — labeled as such everywhere."""
    if side == "YES":
        for t in trades:
            if t.get("type") == "sell" and D(str(t.get("price", "1"))) <= price:
                return t
    else:
        ask = D("1") - price
        for t in trades:
            if t.get("type") == "buy" and D(str(t.get("price", "0"))) >= ask:
                return t
    return None


async def shadow_campaign(number: int) -> Dict[str, Any]:
    """Identical decision path to live, mutation-free, all theoretical."""
    row: Dict[str, Any] = {"campaign": number, "theoretical": True,
                           "kind": "shadow_campaign"}
    try:
        cand = await selector.select_best_candidate(
            assets=SHADOW_ASSETS, min_edge=MIN_EDGE, min_tte=MIN_TTE,
            tie_seed=f"shadow-{number}")
    except Exception as exc:
        # transient market-data failures (ws timeouts etc.) must never kill
        # an always-on loop: report, skip, retry next iteration
        row["status"] = "SCAN_ERROR"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    if cand is None:
        row["status"] = "NO_CANDIDATE"
        return row
    verdict = dict(v1_policy.gate_candidate(cand.bid, cand.ask,
                                            cand.event.seconds_to_expiry))
    row.update({"event": cand.event.ticker, "symbol": cand.event.symbol,
                "v1_state": verdict.get("state"),
                "v1_allow": verdict.get("allow")})
    if camp.decide_flat(verdict) != "OPEN_ONE_SIDE":
        row["status"] = "V1_ABSTAIN_FLAT"
        return row
    side = camp.choose_open_side(cand.bid, cand.bid_size, cand.ask,
                                 cand.ask_size)
    if side is None:
        row["status"] = "NO_FEE_SAFE_MAKER_PRICE"
        return row
    open_side, open_px = side["side"], D(str(side["price"]))
    row.update({"open_side": open_side, "open_price": str(open_px),
                "initializer": camp.INITIALIZER_LABEL})
    first_wait = camp.effective_first_wait(45.0)   # 180s under the overlay
    t0 = time.time()
    tte0 = float(cand.event.seconds_to_expiry)
    stop_at = t0 + max(1.0, tte0 - float(camp.STOP_BEFORE_EXPIRY_SECONDS))
    first_deadline = min(t0 + first_wait, stop_at)
    fill = None
    async with httpx.AsyncClient(timeout=10.0, headers={
            "User-Agent": "l5-supervisor-shadow (read-only)"}) as http:
        while time.time() < first_deadline and fill is None:
            trades = await _public_trades(http, cand.event.symbol,
                                          int(t0 * 1000))
            fill = shadow_fillable(trades, open_side, open_px)
            if fill is None:
                await asyncio.sleep(5.0)
        if fill is None:
            row["status"] = "SHADOW_NO_FILL_TIMEOUT"
            return row
        row["shadow_first_fill"] = {"price": str(open_px),
                                    "evidence_print": fill,
                                    "label": "THEORETICAL_FILL"}
        comp_px = camp.complement_price(open_side, open_px, MIN_EDGE)
        if comp_px is None:
            row["status"] = "SHADOW_COMPLETE_INFEASIBLE"
            return row
        comp_side = "NO" if open_side == "YES" else "YES"
        row["shadow_complement"] = {"side": comp_side, "price": str(comp_px),
                                    "locked_edge_if_filled":
                                        str(edge(open_px, comp_px)
                                            if open_side == "YES"
                                            else edge(comp_px, open_px))}
        comp_fill = None
        t_fill = time.time()
        while time.time() < stop_at and comp_fill is None:
            trades = await _public_trades(http, cand.event.symbol,
                                          int(t_fill * 1000))
            comp_fill = shadow_fillable(trades, comp_side, comp_px)
            if comp_fill is None:
                await asyncio.sleep(5.0)
    if comp_fill is not None:
        row["status"] = "SHADOW_MATCHED"
        row["shadow_complement"]["evidence_print"] = comp_fill
    else:
        row["status"] = "SHADOW_ONE_SIDED_HOLD"
    return row


# ---------------- live campaign + settlement reconciliation ----------------

async def live_campaign(number: int) -> Dict[str, Any]:
    from src import gemini_l5_dynamic_q1 as owner
    row: Dict[str, Any] = {"campaign": number, "kind": "live_campaign",
                           "theoretical": False}
    if not owner.armed():
        row["status"] = "DISARMED"
        emit("LIVE_DISARMED", note="operator arming absent; nothing to do")
        return row
    try:
        await owner.run()          # proven owner: own guards, one campaign
        row["status"] = "OWNER_SESSION_COMPLETE"
    except Exception as exc:       # owner fail-closed raise -> supervisor halt
        row["status"] = "OWNER_EXCEPTION"
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


# Verdicts on which the supervisor may safely continue: a fully matched
# q1 pair carries zero residual liability even when the venue netted it
# before resolution (Gemini docs: positions/settled only shows positions
# held to resolution), so those statuses are not halts. Everything
# contradictory or unmatched still halts.
SAFE_CONTINUE_STATUSES = frozenset({
    "REALIZED", "SETTLED",
    "MATCHED_NETTED_CONFIRMED",           # exact WS fee+balance reconcile
    "MATCHED_NETTING_UNCONFIRMED",        # matched pair, evidence incomplete
    "MATCHED_PAIR_NO_SETTLED_ROWS",       # matched pair, doc-consistent
})
HALT_STATUSES = frozenset({
    "UNRESOLVED",                         # unmatched residual shape
    "AMBIGUOUS_SETTLEMENT_IDENTITY",
    "NETTING_CASHFLOW_INCONSISTENT",
})


async def reconcile_event(event_ticker: str) -> Dict[str, Any]:
    """Poll the corrected auditor until a terminal classification exists,
    attaching WS netting evidence when a recorder file is available."""
    ev: Optional[Dict[str, Any]] = None
    for attempt in range(SETTLE_POLL_MAX):
        out = await audit_mod.audit(days=0.5, event_filter=event_ticker)
        ev = out["events"].get(event_ticker)
        if ev and ev["status"] == "MATCHED_PAIR_NO_SETTLED_ROWS":
            ev = _try_netting_evidence(event_ticker, ev)
        if ev and (ev["status"] in SAFE_CONTINUE_STATUSES
                   or ev["status"] in HALT_STATUSES):
            return ev
        await asyncio.sleep(SETTLE_POLL_S)
    return ev or {"status": "UNRESOLVED"}


def _try_netting_evidence(event_ticker: str,
                          classified: Dict[str, Any]) -> Dict[str, Any]:
    """Attach WS evidence (if a recorder file exists) to upgrade a matched
    pair to MATCHED_NETTED_CONFIRMED; incomplete evidence leaves the safe
    MATCHED_NETTING_UNCONFIRMED, never a fabricated confirmation."""
    try:
        from src import gemini_l5_ws_evidence as wse
        ev_dir = Path(os.getenv(wse.OUT_DIR_ENV, wse.DEFAULT_DIR))
        files = sorted(ev_dir.glob("ws_evidence_*.jsonl"))
        fills = classified.get("fills") or []
        ids = {str(f.get("clientOrderId") or "") for f in fills} - {""}
        if not files or not ids:
            out = dict(classified)
            out["status"] = "MATCHED_NETTING_UNCONFIRMED"
            out["netting_evidence"] = {"recorder_files": len(files)}
            return out
        evidence = wse.load_evidence(files[-1], ids)
        return dict(audit_mod.apply_netting_evidence(
            classified, fills, evidence))
    except Exception as exc:
        out = dict(classified)
        out["status"] = "MATCHED_NETTING_UNCONFIRMED"
        out["netting_evidence_error"] = f"{type(exc).__name__}: {exc}"
        return out


# ---------------- supervisor loop ----------------

async def run_loop(max_iterations: int = 0) -> None:
    st = load_state()
    emit("SUPERVISOR_START", root=str(root()),
         max_per_day=os.getenv(MAX_PER_DAY_ENV, str(DEFAULT_MAX_PER_DAY)),
         loss_stop=os.getenv(LOSS_STOP_ENV, str(DEFAULT_LOSS_STOP)),
         forced_completion=camp.forced_completion_enabled(),
         policy_provenance=dict(v1_policy.provenance()))
    n = 0
    while True:
        n += 1
        st = rollover_day(load_state())
        block = bounds_allow(st)
        if block:
            heartbeat("blocked", reason=block,
                      campaigns_today=st.get("campaigns_today"))
            emit("BLOCKED", reason=block)
            if st.get("halt"):
                return                       # persistent halt: stop the loop
            await asyncio.sleep(IDLE_SLEEP_S)
            if max_iterations and n >= max_iterations:
                return
            continue
        st["iterations"] = st.get("iterations", 0) + 1
        save_state(st)
        heartbeat("scanning", campaigns_today=st.get("campaigns_today"))
        if mode() == "LIVE":
            row = await live_campaign(st["iterations"])
            if row["status"] == "OWNER_EXCEPTION":
                ledger_append(row)
                halt(st, f"owner exception: {row.get('error')}")
                return
            if row["status"] == "OWNER_SESSION_COMPLETE":
                st["campaigns_today"] = st.get("campaigns_today", 0) + 1
                save_state(st)
                # settlement reconciliation from the owner's session state
                ev = _last_owner_event()
                if ev:
                    verdict = await reconcile_event(ev)
                    row["settlement"] = verdict
                    if verdict["status"] in HALT_STATUSES:
                        ledger_append(row)
                        halt(st, f"settlement {verdict['status']} for {ev}")
                        return
                    if verdict["status"] == "REALIZED":
                        st["realized_today_usd"] = str(
                            D(str(st.get("realized_today_usd", "0"))) +
                            D(str(verdict.get("realized_cash") or "0")))
                        save_state(st)
        else:
            try:
                row = await shadow_campaign(st["iterations"])
            except Exception as exc:      # belt-and-braces: loop never dies
                row = {"campaign": st["iterations"], "theoretical": True,
                       "kind": "shadow_campaign", "status": "SCAN_ERROR",
                       "error": f"{type(exc).__name__}: {exc}"}
            if row["status"] not in ("NO_CANDIDATE", "SCAN_ERROR"):
                st["campaigns_today"] = st.get("campaigns_today", 0) + 1
                save_state(st)
        ledger_append(row)
        emit("CAMPAIGN_DONE", **{k: row.get(k) for k in
                                 ("campaign", "status", "event")})
        heartbeat("idle", last_status=row["status"],
                  campaigns_today=st.get("campaigns_today"))
        if max_iterations and n >= max_iterations:
            return
        await asyncio.sleep(IDLE_SLEEP_S)


def _last_owner_event() -> str:
    """EventTicker of the owner's most recent cycle, from its state files."""
    try:
        state_file = Path(os.getenv("GEMINI_L5_DYNAMIC_STATE_FILE",
                                    "/state/gemini-l5-dynamic-q1.json"))
        cycles = sorted(state_file.parent.glob("cycle-*.json"))
        if cycles:
            return str(json.loads(cycles[-1].read_text()).get("event") or "")
    except Exception:
        pass
    return ""


def status_report() -> Dict[str, Any]:
    st = load_state()
    hb_path = root() / "heartbeat.json"
    hb = json.loads(hb_path.read_text()) if hb_path.exists() else {}
    hb_age = None
    if hb.get("utc"):
        hb_age = round(time.time() - datetime.strptime(
            hb["utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp(), 1)
    ledger_path = root() / "campaigns.jsonl"
    last_rows: List[dict] = []
    unresolved: List[str] = []
    if ledger_path.exists():
        for line in ledger_path.read_text().splitlines()[-50:]:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            last_rows.append(r)
            if (r.get("settlement") or {}).get("status") in (
                    "UNRESOLVED", "AMBIGUOUS_SETTLEMENT_IDENTITY"):
                unresolved.append(str(r.get("event")))
    return {
        "utc": _utc(), "mode": mode(),
        "halt": st.get("halt"), "halt_reason": st.get("halt_reason"),
        "campaigns_today": st.get("campaigns_today"),
        "realized_today_usd_authoritative_only": st.get("realized_today_usd"),
        "heartbeat_age_s": hb_age, "heartbeat": hb,
        "last_campaign": last_rows[-1] if last_rows else None,
        "unresolved_settlements": unresolved,
        "note": ("realized figures come only from source-confirmed "
                 "settlement rows; shadow rows are theoretical and never "
                 "counted"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("run", "status"))
    ap.add_argument("--iterations", type=int, default=0,
                    help="stop after N loop iterations (0 = forever)")
    args = ap.parse_args()
    if args.cmd == "status":
        print(json.dumps(status_report(), indent=1, default=str))
        return 0
    asyncio.run(run_loop(args.iterations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
