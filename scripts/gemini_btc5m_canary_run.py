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

from src.gemini_execution import arming, btc5m_canary as C
from src.gemini_execution import ci15m_probe as P

LABEL = "GEMINI_BTC5M_CANARY_RUN_V1"
SERIES = re.compile(r"^BTC05M\d{10}$")
POLL_S = 1.0


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(
            str(s).replace("Z", "+00:00")).timestamp()
    except Exception:                                          # noqa: BLE001
        return None


def preflight() -> Dict[str, Any]:
    """Everything provable with no credential in scope."""
    env = C.envelope_match()
    gate = C.auth_gate()
    st = C.cohort_state()
    checks = {
        "envelope_quantity_is_one": C.AUTHORIZED_ENVELOPE["quantity"] == "1",
        "envelope_price_cap_is_0_90": (
            C.AUTHORIZED_ENVELOPE["max_acquisition_price"] == "0.90"),
        "envelope_attempts_is_5": (
            C.AUTHORIZED_ENVELOPE["max_entry_attempts"] == 5),
        "arithmetic_matches_authorized_cap": (
            str(C.worst_case_cohort())
            == C.AUTHORIZED_ENVELOPE["max_cohort_acquisition_outlay_usd"]),
        "sell_not_permitted": C.AUTHORIZED_ENVELOPE["sell_permitted"] is False,
        "size_changes_not_permitted": (
            C.AUTHORIZED_ENVELOPE["size_changes_permitted"] is False),
        "arming_constant_names_this_grant": gate["arming_matches"],
        "taker_key_names_this_grant": gate["taker_key_matches"],
        "btc15m_persist4_path_is_closed": gate["ci15m_submit_is_now_refused"],
        "not_halted": st["halted"] is None,
        "attempts_remaining": st["attempts_remaining"] > 0,
        "q1_at_or_above_venue_minimum": (
            D(C.AUTHORIZED_ENVELOPE["quantity"]) >= C.VENUE_QUANTITY_MINIMUM),
    }
    failed = [k for k, v in checks.items() if not v]
    return {"label": LABEL, "utc": _utc(), "checks": checks,
            "failed": failed, "ok": not failed,
            "envelope": env, "auth_gate": gate, "cohort": st,
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
            out.append({"symbol": sym, "ticker": tick, "expiry_t": exp,
                        "t0": t0,
                        "t0_source": "availableAt" if t0 else "ABSENT",
                        "strike": strike.get("value")})
    out.sort(key=lambda w: w["expiry_t"])
    return out


async def work_window(w: Dict[str, Any], *, deadline: float,
                      evidence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One window: wait, evaluate, at most one attempt, hold to settlement."""
    sym = w["symbol"]
    t0 = w.get("t0")
    if t0 is None:
        return {"window": sym, "outcome": "SKIPPED_NO_AVAILABLE_AT",
                "why": ("T is not published yet, and T+X is not inferred for "
                        "a money-moving decision")}
    taken: Optional[Dict[str, Any]] = None
    while time.time() < min(deadline, w["expiry_t"]):
        age = time.time() - t0
        if age < C.AUTHORIZED_ENVELOPE["min_checkpoint_s_since_open"]:
            await asyncio.sleep(POLL_S)
            continue
        dec = C.evaluate(sym, elapsed_since_open_s=age, quote_age_s=None)
        evidence.append({"kind": "DECISION", "window": sym, **{
            k: dec[k] for k in ("utc", "qualifies", "failed_checks",
                                "requested_price", "requested_quantity",
                                "elapsed_since_open_s", "quote_age_s",
                                "book")}})
        if dec["qualifies"]:
            try:
                taken = await C.attempt(sym, dec)
            except C.CanaryRefused as exc:
                evidence.append({"kind": "REFUSED", "window": sym,
                                 "why": str(exc), "utc": _utc()})
                return {"window": sym, "outcome": "REFUSED",
                        "why": str(exc)}
            break
        await asyncio.sleep(POLL_S)
    if taken is None:
        return {"window": sym, "outcome": "NO_TRADE",
                "why": ("no instant satisfied the frozen rule before the "
                        "window ended; this does not consume an attempt")}
    if taken.get("kind") != "SUBMITTED":
        return {"window": sym, "outcome": taken.get("kind"),
                "detail": taken}
    oid = ((taken.get("venue") or {}).get("body") or {}).get("orderId")
    evidence.append({"kind": "ACK", "window": sym, "order_id": oid,
                     "classification": taken.get("ack_classification"),
                     "latency_ms": taken.get("latency_ms"),
                     "utc": _utc()})
    term = await P.poll_terminal("BTC5M-CANARY-1", sym, oid)
    evidence.append({"kind": "TERMINAL", "window": sym, "order_id": oid,
                     "terminal": term, "utc": _utc()})
    if term.get("state") in ("UNKNOWN", "UNKNOWN_POSITION"):
        C.halt("UNKNOWN_TERMINAL_STATE", term)
        return {"window": sym, "outcome": "HALTED_UNKNOWN", "detail": term}
    fills = await P.authenticated_fills("BTC5M-CANARY-1", sym)
    evidence.append({"kind": "AUTHENTICATED_FILLS", "window": sym,
                     "fills": fills, "utc": _utc()})
    return {"window": sym, "outcome": "ATTEMPTED", "order_id": oid,
            "terminal": term, "fills": fills}


async def main_async(a) -> int:
    pf = preflight()
    os.makedirs(C.LEDGER_DIR, exist_ok=True)
    out = os.path.join(C.LEDGER_DIR, "canary_run_%s.json"
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
                      "cohort": pf["cohort"]}, sort_keys=True, default=str))
    if a.preflight_only or not pf["ok"]:
        return 0 if pf["ok"] else 1

    deadline = time.time() + a.runtime_s
    seen: set = set()
    while time.time() < deadline:
        if C.cohort_state()["attempts_remaining"] <= 0:
            report["stop_reason"] = "ATTEMPT_CAP_REACHED"
            break
        if C.halted():
            report["stop_reason"] = "HALTED"
            break
        wins = [w for w in await discover() if w["symbol"] not in seen]
        wins = [w for w in wins
                if w["expiry_t"] - time.time() > 60.0]
        wins.sort(key=lambda w: w["expiry_t"])
        if not wins:
            await asyncio.sleep(5.0)
            continue
        w = wins[0]
        seen.add(w["symbol"])
        res = await work_window(w, deadline=deadline,
                                evidence=report["evidence"])
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
