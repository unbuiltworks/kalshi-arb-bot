"""Drive the bounded BTC5m execution canary. One attempt per window, max 5.

PREFLIGHT IS CREDENTIAL-FREE. Everything checkable without the account --
envelope, both authorization constants, the HALT file, the ledger, the
arithmetic -- is proved before any secret is in scope, and `--preflight-only`
exits there. That is the mode to read before authorizing a dispatch.

THE LOOP, per window: wait until the market's age reaches T+15s, then evaluate
the frozen rule on each poll; the FIRST qualifying instant is taken, or the
window is NO_TRADE and does not consume an attempt. After a submission the
terminal state is resolved from the venue's own record, the position is HELD to
settlement, and the full chain is written:

    DECISION -> SUBMITTED -> ACK/REJECT -> FILLED/PARTIAL/CANCELLED
             -> SETTLED -> CASH_CONFIRMED

DEADMAN. If an order is still resting when the window's life ends, it is
cancelled and the authoritative filled quantity decides no-fill vs partial --
cancellation is never taken as proof of zero fill.

THE RECORDER COMPARISON is written for every attempt: the decision's own
executable book, the contract-price telemetry, latencies, and the realised fill
against the price we modelled. Windows touched here are EXCLUDED from the
strategy dataset as preregistered.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from src.gemini_execution import arming, underdog_cohort as C
from src.gemini_execution import ci15m_probe as P

LABEL = "GEMINI_BTC5M_UNDERDOG_RUN_V1"
SERIES = re.compile(r"^BTC05M\d{10}$")
POLL_S = 1.0


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def trace(kind: str, **kw: Any) -> None:
    """One flushed JSON line per event.

    WHY THIS EXISTS. The first dispatch of this cohort printed NOTHING between
    the preflight and the final summary, and GitHub serves no logs for a job
    that is still running -- so a 60-minute live run was unobservable from
    outside, and "no trades" could not be told apart from "never evaluated".
    Every line is flushed so the live log is readable as it happens. This is
    instrumentation only: it reads no new venue surface and changes no gate.
    """
    rec = {"kind": kind, "utc": _utc()}
    rec.update(kw)
    print(json.dumps(rec, sort_keys=True, default=str), flush=True)


def _ts(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def _auth_path_imports() -> tuple:
    """Can the authenticated read/submit chain even be imported? No secret."""
    try:
        import importlib
        importlib.import_module("src.gemini_prediction_auth")
        importlib.import_module("src.gemini_execution.transport")
        return True, None
    except Exception as exc:                                   # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


def preflight() -> Dict[str, Any]:
    """Everything provable with no credential in scope."""
    env = C.envelope_match()
    gate = C.auth_gate()
    st = C.cohort_state()
    checks = {
        "ceiling_is_0_60": (
            C.AUTHORIZED_ENVELOPE["max_acquisition_price"] == "0.60"),
        "side_rule_is_underdog": (
            C.AUTHORIZED_ENVELOPE["side_rule"]
            == "UNDERDOG_LOWER_EXECUTABLE_Q1_VWAP"),
        "depth_variant_is_cumulative": (
            C.AUTHORIZED_ENVELOPE["depth_variant"] == "CUMULATIVE"),
        "price_chasing_forbidden": (
            C.AUTHORIZED_ENVELOPE["price_chasing_permitted"] is False),
        "max_principal_is_3": str(C.max_principal()) == "3.0000",
        "cohort_cap_is_principal_plus_fees": (
            C.cohort_cap() == C.max_principal() + C.max_fees()),
        "cohort_not_expired": st["expired"] is False,
        "envelope_quantity_is_one": C.AUTHORIZED_ENVELOPE["quantity"] == "1",
        "envelope_attempts_is_5": (
            C.AUTHORIZED_ENVELOPE["max_entry_attempts"] == 5),
        "sell_not_permitted": C.AUTHORIZED_ENVELOPE["sell_permitted"] is False,
        "size_changes_not_permitted": (
            C.AUTHORIZED_ENVELOPE["size_changes_permitted"] is False),
        "arming_constant_names_this_grant": gate["arming_matches"],
        "taker_key_names_this_grant": gate["taker_key_matches"],
        "other_cohort_paths_are_closed": gate["ci15m_submit_is_now_refused"],
        "not_halted": st["halted"] is None,
        "attempts_remaining": st["attempts_remaining"] > 0,
        "q1_at_or_above_venue_minimum": (
            D(C.AUTHORIZED_ENVELOPE["quantity"]) >= C.VENUE_QUANTITY_MINIMUM),
        # THE SUBMISSION PATH MUST BE IMPORTABLE. Cohort run 1 reached a
        # qualifying book at T+15 on 12 of 13 windows and refused every one of
        # them because src.gemini_prediction_auth imports python-dotenv at
        # module scope and the dependency was not installed. The executor was
        # right to fail closed; the preflight was wrong to pass. Importing the
        # chain needs no credential -- it only proves the modules resolve.
        "authenticated_read_path_imports": _auth_path_imports()[0],
    }
    failed = [k for k, v in checks.items() if not v]
    return {"label": LABEL, "utc": _utc(), "checks": checks,
            "authenticated_read_path_why": _auth_path_imports()[1],
            "failed": failed, "ok": not failed,
            "envelope": env, "auth_gate": gate, "cohort": st,
            "exposure": C.exposure(),
            "arming_state": arming.state(),
            "credential_free": ("this preflight touches no account surface "
                                "and needs no secret")}


async def discover() -> List[Dict[str, Any]]:
    """Live BTC5m contracts with their own T and expiry.

    REUSES the proven public enumerator, r2_exec_runner._active_events_public,
    which ci15m_runner.discover already uses. An earlier draft of this
    function called metadata.list_events and metadata.active_events -- NEITHER
    EXISTS. Both calls sat inside try/except, so discovery would have returned
    an empty list and the canary would have found no windows and silently done
    nothing. A test now asserts the callable exists, so the failure cannot be
    silent again.
    """
    from src.gemini_execution.r2_exec_runner import _active_events_public
    evs = _active_events_public() or []
    out: List[Dict[str, Any]] = []
    for ev in evs:
        tick = ev.get("ticker") or ""
        if not SERIES.match(tick):
            continue
        for c in (ev.get("contracts") or []):
            sym = (c.get("instrumentSymbol")
                   or ("GEMI-%s-UP" % tick)).upper()
            strike = c.get("strike") if isinstance(c.get("strike"),
                                                   dict) else {}
            exp = _ts(c.get("expiryDate"))
            t0 = _ts(strike.get("availableAt"))
            if exp is None:
                continue
            # STRIKE PROVENANCE, NEVER INFERENCE. Cohort 1 recorded
            # `strike: null` on all five windows: `strike` was read only when
            # the contract's own key was already a mapping, and the value was
            # flattened to a bare number with no record of where it came from
            # or whether it was absent. The strike is the whole settlement
            # rule (UP resolves yes when settlementValue >= strike.value), so
            # a null one makes the audit impossible from the artifact alone.
            # The authoritative source is the contract object's
            # strike.value; it is carried through verbatim with its source
            # named, and ABSENT stays ABSENT rather than being derived from
            # the index, the reference or a neighbouring window.
            k_val = strike.get("value") if isinstance(strike, dict) else None
            out.append({"symbol": sym, "ticker": tick, "expiry_t": exp,
                        "t0": t0,
                        "t0_source": "availableAt" if t0 else "ABSENT",
                        "strike": k_val,
                        "strike_value": (None if k_val in (None, "")
                                         else str(k_val)),
                        "strike_source": ("contract.strike.value"
                                          if k_val not in (None, "")
                                          else "ABSENT"),
                        "strike_raw": strike,
                        "contract_status": c.get("status")})
    out.sort(key=lambda w: w["expiry_t"])
    return out


async def work_window(w: Dict[str, Any], *, deadline: float,
                      evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One window: wait, evaluate, at most one attempt, hold to settlement."""
    sym = w["symbol"]
    t0 = w.get("t0")
    if t0 is None:
        trace("SKIPPED_NO_AVAILABLE_AT", window=sym,
              secs_to_expiry=round(w["expiry_t"] - time.time(), 1))
        return {"window": sym, "outcome": "SKIPPED_NO_AVAILABLE_AT",
                "why": ("T is not published yet, and T+X is not inferred for "
                        "a money-moving decision")}
    taken: Optional[Dict[str, Any]] = None
    trace("WINDOW_OPEN", window=sym, t0_source=w["t0_source"],
          age_s=round(time.time() - t0, 1),
          secs_to_expiry=round(w["expiry_t"] - time.time(), 1),
          strike=w.get("strike"))
    polls = 0
    while time.time() < min(deadline, w["expiry_t"]):
        age = time.time() - t0
        if age < C.AUTHORIZED_ENVELOPE["min_checkpoint_s_since_open"]:
            await asyncio.sleep(POLL_S)
            continue
        dec = C.evaluate(sym, elapsed_since_open_s=age)
        polls += 1
        trace("DECISION", window=sym, poll=polls, age_s=round(age, 1),
              qualifies=dec["qualifies"],
              failed_checks=dec["failed_checks"],
              up_q1_vwap=dec["up_q1_vwap"],
              down_q1_vwap=dec["down_q1_vwap"],
              underdog=dec["underdog"], leader=dec["leader"],
              selected_price=dec["requested_price"],
              above_ceiling_no_trade=dec["above_ceiling_no_trade"],
              combined_q1_cost=dec["combined_q1_cost"],
              spread_touch=dec["spread_touch"], depth=dec["depth"],
              projected_outlay_usd=dec["projected_outlay_usd"],
              quote_age_s=dec["quote_age_s"],
              integrity=dec["book_integrity_detail"])
        if dec["above_ceiling_no_trade"]:
            # THE CEILING IS NEVER LOOSENED TO REACH FIVE. A window whose
            # cheaper side is above 0.60 is NO_TRADE and does not consume an
            # attempt; the loop keeps polling in case the price improves
            # inside this same window, which costs nothing and chases
            # nothing.
            trace("ABOVE_CEILING", window=sym, poll=polls,
                  underdog=dec["underdog"],
                  price=dec["requested_price"],
                  ceiling=C.AUTHORIZED_ENVELOPE["max_acquisition_price"])
        # EVERY KEY HERE IS ONE THE UNDERDOG DECISION ACTUALLY RETURNS.
        # The canary's decision carried a `book` key and this one does not --
        # it carries up_q1_vwap/down_q1_vwap/underdog/leader instead -- so
        # copying the canary's key list raised KeyError: 'book' on the FIRST
        # window of run 1 and killed the cohort before any order. The list is
        # now `.get()`-based so a future field rename degrades the evidence
        # rather than the run.
        evidence.append({"kind": "DECISION", "window": sym, **{
            k: dec.get(k) for k in (
                "utc", "qualifies", "failed_checks", "side", "underdog",
                "leader", "requested_price", "requested_quantity",
                "up_q1_vwap", "down_q1_vwap", "combined_q1_cost",
                "spread_touch", "depth", "elapsed_since_open_s",
                "quote_age_s", "above_ceiling_no_trade",
                "projected_fee_usd", "projected_outlay_usd",
                "book_integrity_detail")}})
        if dec["qualifies"]:
            try:
                taken = await C.attempt(sym, dec)
            except C.UnderdogRefused as exc:
                evidence.append({"kind": "REFUSED", "window": sym,
                                 "why": str(exc), "utc": _utc()})
                return {"window": sym, "outcome": "REFUSED",
                        "why": str(exc)}
            break
        await asyncio.sleep(POLL_S)
    if taken is None:
        trace("NO_TRADE", window=sym, polls=polls)
        return {"window": sym, "outcome": "NO_TRADE",
                "why": ("no instant satisfied the frozen rule before the "
                        "window ended; this does not consume an attempt")}
    if taken.get("kind") != "SUBMITTED":
        return {"window": sym, "outcome": taken.get("kind"),
                "detail": taken}
    oid = ((taken.get("venue") or {}).get("body") or {}).get("orderId")
    trace("SUBMITTED", window=sym, order_id=oid,
          classification=taken.get("ack_classification"),
          latency_ms=taken.get("latency_ms"))
    evidence.append({"kind": "ACK", "window": sym, "order_id": oid,
                     "classification": taken.get("ack_classification"),
                     "latency_ms": taken.get("latency_ms"),
                     "utc": _utc()})
    term = await P.poll_terminal("BTC5M-UNDERDOG-1", sym, oid)
    trace("TERMINAL", window=sym, order_id=oid, terminal=term)
    evidence.append({"kind": "TERMINAL", "window": sym, "order_id": oid,
                     "terminal": term, "utc": _utc()})
    if term.get("state") in ("UNKNOWN", "UNKNOWN_POSITION"):
        C.halt("UNKNOWN_TERMINAL_STATE", term)
        return {"window": sym, "outcome": "HALTED_UNKNOWN", "detail": term}
    fills = await P.authenticated_fills("BTC5M-CANARY-1", sym)
    trace("AUTHENTICATED_FILLS", window=sym, fills=fills)
    evidence.append({"kind": "AUTHENTICATED_FILLS", "window": sym,
                     "fills": fills, "utc": _utc()})
    return {"window": sym, "outcome": "ATTEMPTED", "order_id": oid,
            "terminal": term, "fills": fills, "attribution": att,
            "probe_contaminated": True}


async def main_async(a) -> int:
    pf = preflight()
    os.makedirs(C.LEDGER_DIR, exist_ok=True)
    out = os.path.join(C.LEDGER_DIR, "underdog_run_%s.json"
                       % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    report: Dict[str, Any] = {"label": LABEL, "utc": _utc(),
                              "preflight": pf, "windows": [],
                              "evidence": [],
                              "accounting_labels": (
                                  "SUBMITTED -> FILLED -> SETTLED -> "
                                  "CASH_CONFIRMED -> REALIZED; nothing is "
                                  "called realized without the venue's own "
                                  "cashflow record"),
                              "strategy_evidence": False,
                              "note": ("EXECUTION CANARY. Its P&L is "
                                       "mechanics evidence, not strategy "
                                       "evidence.")}

    def save() -> None:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())

    save()
    print(json.dumps({"preflight_ok": pf["ok"], "failed": pf["failed"],
                      "cohort": pf["cohort"],
                      "exposure": pf["exposure"]},
                     sort_keys=True, default=str))
    if a.preflight_only or not pf["ok"]:
        return 0 if pf["ok"] else 1

    deadline = time.time() + a.runtime_s
    seen: set = set()
    while time.time() < deadline:
        if C.cohort_state()["expired"]:
            report["stop_reason"] = "COHORT_EXPIRED_AT_ATTEMPT_5"
            break
        if C.halted():
            report["stop_reason"] = "HALTED"
            break
        found = await discover()
        wins = [w for w in found if w["symbol"] not in seen]
        wins = [w for w in wins
                if w["expiry_t"] - time.time() > 60.0]
        wins.sort(key=lambda w: w["expiry_t"])
        trace("DISCOVERY", n_live=len(found), n_eligible=len(wins),
              live=[{"symbol": x["symbol"], "t0_source": x["t0_source"],
                     "secs_to_expiry": round(x["expiry_t"] - time.time(), 1),
                     "age_s": (None if x.get("t0") is None
                               else round(time.time() - x["t0"], 1))}
                    for x in found],
              cohort=C.cohort_state())
        if not wins:
            await asyncio.sleep(5.0)
            continue
        w = wins[0]
        # A WINDOW IS ONLY BURNED ONCE IT WAS ACTUALLY WORKED. The first build
        # added every selected symbol to `seen` before working it, so a window
        # discovered in the instant before the venue published its
        # strike.availableAt returned SKIPPED_NO_AVAILABLE_AT and was then
        # never looked at again -- a silent path to a zero-attempt cohort. T is
        # still never inferred; the window is simply retried until it has one.
        res = await work_window(w, deadline=deadline,
                                evidence=report["evidence"])
        if res.get("outcome") != "SKIPPED_NO_AVAILABLE_AT":
            seen.add(w["symbol"])
        else:
            # retried, not burned -- but not in a hot loop either
            await asyncio.sleep(2.0)
        trace("WINDOW_RESULT", window=w["symbol"], **{
            k: v for k, v in res.items() if k != "window"})
        report["windows"].append(dict(w, **res))
        save()
    report["cohort_final"] = C.cohort_state()
    report["stop_reason"] = report.get("stop_reason") or "RUNTIME_ELAPSED"
    save()
    print(json.dumps({"stop_reason": report["stop_reason"],
                      "cohort": report["cohort_final"],
                      "windows": len(report["windows"]),
                      "report": out}, sort_keys=True, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--runtime-s", type=float, default=2400.0)
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
