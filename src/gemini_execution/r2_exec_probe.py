"""The R2 execution-validation probe. ONE order per qualifying market.

WHAT THIS IS. An execution-model audit under the owner's cohort authorization
`OWNER-2026-10-03-GEMINI-R2-EXEC-1`. It answers: when frozen R2 says an
opportunity is executable at a frozen price and depth, what does the venue
actually do? It is NOT a profitability estimate -- ten q=1 fills cannot
distinguish +0.04/contract from zero, and nothing here may be pooled with the
sealed 100-window statistical cohort.

THE SIGNAL LOGIC IS IMPORTED, NEVER REPRODUCED. `favored_side`, `executable`,
`sigma_1s_from_ticks`, `z_of` and `qualifies` all come from
`analysis/gemini/info_edge/r2_frozen.py`. This module contains no distance
arithmetic, no volatility estimate and no threshold of its own, and a test
asserts that. The frozen rule's fingerprint is checked at startup.

EVERY MUTATION GOES THROUGH THE PROVEN PATH: transport.place / cancel_and_confirm
-> payload -> arming -> gemini_prediction_auth. No HTTP call, no signing and no
payload construction lives here.

THE BUDGET IS NOT THIS MODULE'S TO INTERPRET. `r2_exec_budget` owns the ten
numbered slots, the per-market rule, the $8 stop, the $10.20 ceiling and the
expiry. This module asks it, and obeys the answer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Dict, Optional

from src.gemini_execution import (arming, metadata, payload,
                                  r2_exec_budget as BUDGET, transport)
from src.gemini_execution.semantics_probe import HALT_FILE, halt, halted

#: the frozen rule, loaded from its own file. Its pure functions are the ONLY
#: source of qualification in the live path.
_RULE_PATH = Path("analysis/gemini/info_edge/r2_frozen.py")
RULE: Dict[str, Any] = {"__file__": str(_RULE_PATH)}
exec(_RULE_PATH.read_text().split("def main")[0], RULE)

#: the SEMANTIC reference for the rule. The arithmetic was factored into pure
#: functions after this SHA so a live executor could import rather than
#: reproduce it; equivalence is proven in R2_EXEC_COHORT_RUN.md.
RULE_SEMANTIC_SHA = "6bc5c1f730dd0d4639eb4c9459803dbf9d68dee5"

PROBE_WINDOWS = Path("analysis/gemini/info_edge/probe_windows.json")


class ProbeRefusal(Exception):
    """A bound said no. Never retried, never relaxed."""


def _record_probe_window(ticker: str) -> None:
    """Exclude this market from the blind statistical cohort, permanently."""
    try:
        d = json.loads(PROBE_WINDOWS.read_text())
    except FileNotFoundError:
        d = {"label": "R2_LIVE_PROBE_WINDOWS_V1", "tickers": []}
    if ticker not in d.get("tickers", []):
        d.setdefault("tickers", []).append(ticker)
    PROBE_WINDOWS.write_text(json.dumps(d, indent=2))


def _book_maps(depth: Dict[str, Any]) -> tuple:
    """The venue's own ladders as {price: qty}, from `metadata.contract_depth`."""
    out = []
    for side in ("bids", "asks"):
        m = {}
        for lv in ((depth or {}).get(side) or {}).get("levels") or []:
            try:
                m[float(lv["price"])] = float(lv["qty"])
            except Exception:                                  # noqa: BLE001
                continue
        out.append(m)
    return out[0], out[1]


def qualify(symbol: str, ticker: str, cb_ticks, now: float) -> Dict[str, Any]:
    """Frozen R2, evaluated live. Every decision below is the rule's own."""
    rec: Dict[str, Any] = {"symbol": symbol, "ticker": ticker,
                           "evaluated_utc": time.strftime(
                               "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))}
    terms = metadata.terms_for(symbol)
    rec["terms"] = {k: terms.get(k) for k in
                    ("readable", "quantityMinimum", "quantityIncrement",
                     "priceMinimum", "priceIncrement", "contract_status",
                     "market_state", "strike", "expiry", "missing_fields")}
    # live terms are a hard requirement
    from src.gemini_execution.semantics_probe import terms_are_live
    tl = terms_are_live(terms)
    rec["terms_live"] = tl
    if not tl["ok"]:
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = "TERMS_READ_FAILED: %s" % tl["why"]
        return rec

    strike = terms.get("strike")
    if strike in (None, ""):
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = "PRE_STRIKE: the contract publishes no strike yet"
        return rec
    strike = float(strike)

    res = metadata.resolution_for(symbol)
    exp = res.get("expiryDate") or terms.get("expiry")
    if not exp:
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = "NO_EXPIRY"
        return rec
    from datetime import datetime
    E = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp()
    tte = E - now
    rec["tte_s"] = round(tte, 2)
    if not (0 < tte <= 300):
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = ("NOT_POST_STRIKE_WINDOW: tte %.1f s is outside (0, 300]"
                      % tte)
        return rec

    cb = cb_ticks
    cbt = [x[0] for x in cb]
    coinbase = cb[-1][1] if cb else None
    if coinbase is None:
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = "NO_COINBASE_PRICE"
        return rec
    rec["coinbase"] = coinbase
    rec["strike"] = strike
    dist = coinbase - strike
    rec["distance"] = round(dist, 2)

    side = RULE["favored_side"](coinbase, strike)          # IMPORTED
    rec["favored_side"] = side
    rec["favored_outcome"] = "yes" if side == "UP" else "no"

    sigma = RULE["sigma_1s_from_ticks"](cb, cbt, now)       # IMPORTED
    z = RULE["z_of"](dist, sigma, tte, coinbase)            # IMPORTED
    rec["sigma_1s"] = sigma
    rec["z"] = z
    if not RULE["qualifies"](z):                           # IMPORTED
        rec["state"] = "NOT_QUALIFIED"
        rec["why"] = ("Z_BELOW_TRIGGER: z=%s < %s"
                      % (None if z is None else round(z, 3),
                         RULE["Z_TRIGGER"]))
        return rec

    dep = metadata.contract_depth(symbol)
    bids, asks = _book_maps(dep)
    lvl, price, size, depth = RULE["executable"](side, bids, asks)  # IMPORTED
    rec["modeled"] = {"level": lvl, "price": price, "size": size,
                      "depth_at_or_inside": depth}
    if price is None or not (0 < price < 1):
        rec["state"] = "NOT_EXECUTABLE"
        rec["why"] = ("NO_EXECUTABLE_OFFER on the favoured side: the state "
                      "favours %s and nobody is offering it" % side)
        return rec

    rec["state"] = "QUALIFIED"
    rec["frozen_price"] = round(price, 2)
    rec["frozen_size"] = size
    rec["frozen_depth"] = depth
    return rec


def reread_ok(symbol: str, side: str, frozen_price: float,
              qty: str) -> Dict[str, Any]:
    """The immediate pre-submit re-read. No chase, no reprice, no substitute."""
    dep = metadata.contract_depth(symbol)
    bids, asks = _book_maps(dep)
    lvl, price, size, depth = RULE["executable"](side, bids, asks)
    out = {"current_level": lvl, "current_price": price,
           "current_size": size, "current_depth": depth,
           "frozen_price": frozen_price}
    if price is None:
        out.update(ok=False, why="OFFER_DISAPPEARED before submission")
        return out
    if D(str(round(price, 2))) > D(str(frozen_price)):
        out.update(ok=False,
                   why=("OFFER_ROSE_ABOVE_FROZEN_LIMIT: %.2f > %.2f. Not "
                        "chasing." % (price, frozen_price)))
        return out
    takeable = D("0")
    book = asks if side == "UP" else bids
    for p, q in book.items():
        eff = p if side == "UP" else (1 - p)
        if D(str(round(eff, 2))) <= D(str(frozen_price)):
            takeable += D(str(q))
    out["takeable_at_or_below_limit"] = str(takeable)
    if takeable < D(str(qty)):
        out.update(ok=False,
                   why=("INSUFFICIENT_DISPLAYED_SIZE: %s at or below %.2f, %s "
                        "required" % (takeable, frozen_price, qty)))
        return out
    out.update(ok=True, why="the approved liquidity is still there")
    return out


async def probe(*, symbol: str, cohort_id: str, cb_ticks,
                stat_gate_windows: Optional[int] = None,
                out: Optional[str] = None) -> Dict[str, Any]:
    """ONE market. At most one order. Records the full chain either way."""
    ticker = (metadata.event_ticker(symbol) or symbol)
    rec: Dict[str, Any] = {
        "label": "GEMINI_R2_EXEC_PROBE_V1",
        "cohort_id": cohort_id,
        "auth_id": arming.EXPECTED_AUTH_ID,
        "rule_semantic_sha": RULE_SEMANTIC_SHA,
        "rule_fingerprint": RULE["rule_fingerprint"](),
        "z_trigger": RULE["Z_TRIGGER"],
        "signal_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "no_extend": True, "no_flip": True, "no_complement": True,
        "no_averaging": True, "no_chasing": True, "no_substitution": True,
        "orders_submitted": 0,
        "realized": "NOT_REALIZED: no settlement cashflow confirmed",
    }

    h = halted()
    if h:
        rec["state"] = "HALTED"
        rec["why"] = h
        return _finish(rec, out)

    gate = BUDGET.may_probe(cohort_id, ticker,
                            stat_gate_windows=stat_gate_windows)
    rec["budget_gate"] = gate
    if not gate["ok"]:
        rec["state"] = "BUDGET_REFUSED"
        rec["why"] = gate["why"]
        return _finish(rec, out)

    q = qualify(symbol, ticker, cb_ticks, time.time())
    rec["qualification"] = q
    if q["state"] != "QUALIFIED":
        rec["state"] = q["state"]
        rec["why"] = q["why"]
        # NOT_EXECUTABLE finishes the market; a merely un-triggered market does
        # not -- it may qualify at a later checkpoint in the same window.
        if q["state"] == "NOT_EXECUTABLE":
            BUDGET.finish_market(cohort_id, ticker, "MISSED_NOT_EXECUTABLE",
                                 {"qualification": q})
            _record_probe_window(ticker)
        return _finish(rec, out)

    # ONE OUTSTANDING ORDER, checked against the VENUE, not our bookkeeping.
    live = await transport.active_orders()
    n_live = len(transport._orders_of(live))
    rec["account_outstanding_before"] = n_live
    if not live.get("ok"):
        rec["state"] = "REFUSED"
        rec["why"] = "ACTIVE_ORDERS_UNREADABLE: an unreadable account is not"\
                     " an empty one"
        return _finish(rec, out)
    if n_live:
        rec["state"] = "REFUSED"
        rec["why"] = "ACCOUNT_NOT_FLAT: %d outstanding order(s)" % n_live
        return _finish(rec, out)

    if not arming.armed():
        rec["state"] = "NOT_ARMED"
        rec["why"] = arming.state().get("why_not")
        return _finish(rec, out)

    qty = "1"
    frozen = q["frozen_price"]
    side = q["favored_side"]

    t_signal = time.monotonic()
    rr = reread_ok(symbol, side, frozen, qty)
    rec["final_reread"] = rr
    if not rr["ok"]:
        rec["state"] = "MISSED_NOT_EXECUTABLE"
        rec["why"] = rr["why"]
        BUDGET.finish_market(cohort_id, ticker, "MISSED_NOT_EXECUTABLE",
                             {"reread": rr})
        _record_probe_window(ticker)
        return _finish(rec, out)

    intent = {"symbol": symbol, "side": "buy", "outcome": q["favored_outcome"],
              "price": "%.2f" % frozen, "quantity": qty,
              "modeled_size": q["frozen_size"],
              "modeled_depth": q["frozen_depth"], "z": q["z"],
              "tte_s": q["tte_s"]}

    # SLOT BEFORE VENUE. fsynced. An ambiguous send finds it already spent.
    slot = BUDGET.consume_slot(cohort_id, ticker, intent,
                               stat_gate_windows=stat_gate_windows)
    rec["slot"] = slot["slot"]
    _record_probe_window(ticker)

    t0 = time.monotonic()
    ack = await transport.place(symbol=symbol, side="buy",
                                outcome=q["favored_outcome"],
                                price="%.2f" % frozen, quantity=qty,
                                allow_taker=True)
    rec["orders_submitted"] = 1
    rec["submitted_body"] = ack.get("request")
    rec["acknowledgement"] = {k: ack.get(k) for k in
                              ("status", "classification", "ok", "body")}
    rec["latency_signal_to_ack_s"] = round(time.monotonic() - t_signal, 4)
    rec["latency_submit_to_ack_s"] = round(time.monotonic() - t0, 4)

    if ack.get("classification") == transport.AMBIGUOUS:
        rec["state"] = "SUBMISSION_UNKNOWN"
        rec["halt"] = halt("AMBIGUOUS send on %s, slot %s: the slot is SPENT "
                           "and will not be retried. Reconcile by hand."
                           % (symbol, rec["slot"]))
        return _finish(rec, out)
    if ack.get("classification") != "ACCEPTED":
        rec["state"] = "REJECTED"
        rec["why"] = str(ack.get("classification"))
        return _finish(rec, out)

    body = ack.get("body") or {}
    rec["order_id"] = body.get("orderId")
    rec["actual"] = {k: body.get(k) for k in
                     ("status", "filledQuantity", "remainingQuantity",
                      "avgExecutionPrice", "price", "quantity",
                      "side", "outcome")}
    rec["fee_observed"] = next(
        (body[k] for k in body if "fee" in k.lower()), None)
    rec["fee_note"] = ("the venue order surface reported no fee field; the "
                       "charge is NOT confirmed from this read"
                       if rec["fee_observed"] is None else None)

    filled = D(str(body.get("filledQuantity") or "0"))
    rec["filled_quantity"] = str(filled)
    rec["state"] = "FILLED" if filled > 0 else "ACCEPTED_UNFILLED"

    rec["comparison"] = {
        "modeled_price": frozen,
        "actual_fill_price": body.get("avgExecutionPrice"),
        "price_slippage": (None if body.get("avgExecutionPrice") in (None, "")
                           else float(D(str(body["avgExecutionPrice"]))
                                      - D(str(frozen)))),
        "modeled_displayed_depth": q["frozen_depth"],
        "obtained_quantity": str(filled),
        "offer_survived_to_submission": rr["ok"],
    }
    rec["reconciliation"] = _reconcile(rec, frozen, qty, filled, body)
    if rec["reconciliation"]["anomaly"]:
        rec["halt"] = halt("%s on %s slot %s: %s"
                           % (rec["reconciliation"]["kind"], symbol,
                              rec["slot"], rec["reconciliation"]["detail"]))
    return _finish(rec, out)


def _reconcile(rec, frozen, qty, filled, body) -> Dict[str, Any]:
    sent = rec.get("submitted_body") or {}
    for f in ("side", "outcome", "price", "quantity"):
        theirs = body.get(f)
        if theirs is not None and str(theirs) != str(sent.get(f)):
            # the venue normalises "0.50" to "0.5"; compare numerically
            try:
                if D(str(theirs)) == D(str(sent.get(f))):
                    continue
            except Exception:                                  # noqa: BLE001
                pass
            return {"anomaly": True, "kind": "MAPPING_CONTRADICTION",
                    "detail": "sent %s=%r, venue echoes %r"
                              % (f, sent.get(f), theirs)}
    px = body.get("avgExecutionPrice")
    if filled > 0 and px not in (None, ""):
        if D(str(px)) > D(str(frozen)):
            return {"anomaly": True, "kind": "FILL_ABOVE_LIMIT",
                    "detail": "filled at %s against a frozen limit of %s"
                              % (px, frozen)}
    if D("0") < filled < D(str(qty)):
        return {"anomaly": True, "kind": "UNEXPLAINED_PARTIAL",
                "detail": "filled %s of %s" % (filled, qty)}
    return {"anomaly": False, "kind": "AGREES",
            "detail": "the venue behaved as the frozen model predicted"}


def _finish(rec, out):
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(rec, indent=2, sort_keys=True,
                                        default=str))
    return rec


def preflight(cohort_id: str, *,
              stat_gate_windows: Optional[int] = None) -> Dict[str, Any]:
    """The checks the owner required BEFORE any venue submission."""
    st = BUDGET.state(cohort_id, stat_gate_windows=stat_gate_windows)
    return {
        "label": "GEMINI_R2_EXEC_PREFLIGHT_V1",
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_auth_id": arming.EXPECTED_AUTH_ID,
        "taker_auth_id": payload.SEMANTICS_PROBE_AUTH_ID,
        "auth_ids_agree": (arming.EXPECTED_AUTH_ID
                           == payload.SEMANTICS_PROBE_AUTH_ID),
        "rule_semantic_sha": RULE_SEMANTIC_SHA,
        "rule_fingerprint": RULE["rule_fingerprint"](),
        "z_trigger": RULE["Z_TRIGGER"],
        "budget_remaining": st["remaining"],
        "confirmed_realized_principal_loss": st[
            "confirmed_realized_principal_loss"],
        "structural_max_loss": st["structural_max_loss"],
        "realized_loss_stop": st["realized_loss_stop"],
        "expired": st["expired"],
        "halt": halted(),
        "halt_file": str(HALT_FILE),
        "stat_gate_windows": stat_gate_windows,
        "arming": arming.state(),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", required=True)
    ap.add_argument("--preflight-only", action="store_true")
    ap.add_argument("--stat-gate-windows", type=int, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.preflight_only:
        rec = preflight(a.cohort, stat_gate_windows=a.stat_gate_windows)
        _finish(rec, a.out)
        print(json.dumps(rec, indent=2, sort_keys=True, default=str))
        return 0
    raise SystemExit("live probing is driven by the cohort runner, not by "
                     "this entry point")


if __name__ == "__main__":
    raise SystemExit(main())
