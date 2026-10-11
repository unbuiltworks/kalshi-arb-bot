"""The bounded EXECUTION-SEMANTICS probe. A DRIVER, NOT AN EXECUTOR.

WHAT THIS IS FOR. One question, and it is not a profitability question:

    when our book parser says the favored side IS / IS NOT executable,
    does Gemini's actual order and fill behaviour agree?

Specifically it tests two interpretations we have acted on but never confirmed
by acting:

    "UP ask absent"    == we cannot immediately buy UP
    "DOWN acquisition" == 1 - UP bid

NO SECOND EXECUTOR EXISTS HERE. Every byte that reaches Gemini is built,
signed, armed and sent by the already-proven path:

    transport.place / transport.cancel_and_confirm   (arm-gated mutations)
      -> payload.build_order / validate_structure / assert_types
      -> spec                                        (field names, paths)
      -> gemini_prediction_auth.private_rest_json    (the reviewed signer)

and every read by `metadata` (outcome_book, contract_depth, terms_for,
positions, order_history). This module contributes NO HTTP call, NO signing
and NO payload construction of its own. It decides WHICH ONE order to send,
records the chain, and stops.

TWO CASES, ONE ORDER EACH.

  CASE A -- the favored side HAS a visible resting offer. We send a LIMIT
  order priced to cross it, with allow_taker=True. This is the only path in
  the repository that may set `makerOrCancel: False`, and it is gated twice:
  the caller must ask for it AND the live authorization must be
  `payload.SEMANTICS_PROBE_AUTH_ID`. Every strategy stays maker-only.

  CASE B -- the favored side has NO offer. We send one tiny resting
  maker-or-cancel order at a PREDECLARED price below the favored outcome's own
  bid, watch whether anyone trades into it, and cancel on a predeclared
  timeout.

WHAT THIS PROBE WILL NOT DO, structurally rather than by intention:
no EXTEND, no averaging down, no chasing a moved price, no FLIP, no
complement, no second order of any kind, and no price chosen by code. The
price is an argument the human predeclares; if the book has moved so that the
predeclared price no longer does what the case requires, the probe STOPS.

ONE ORDER, ONE AUTHORIZATION, GIT-BOUND. The latch discipline is
`run_canary`'s and is not reimplemented: consume and persist the latch BEFORE
the venue sees anything, so a retried runner cannot send a second order.

ANY ANOMALY HALTS EVERYTHING. A semantic contradiction, an unexplained
partial fill, a side/price/quantity mismatch or a reconciliation failure
writes a HALT file. While that file exists no further probe can submit. It is
cleared by a human, never by this code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import time
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.gemini_execution import arming, latch, metadata, payload, transport

#: case B holds a resting order for at most this long, then cancels. Declared
#: here, before any run, because a timeout chosen after seeing the book is not
#: a predeclared timeout.
REST_TIMEOUT_S = 90.0

#: the probe's own cash ceiling per market, independent of any envelope.
MAX_CASH_PER_MARKET_USD = D("5.00")

#: exactly one order per probe. Not a budget to spend down -- a count to
#: refuse past.
MAX_ORDERS_PER_PROBE = 1

#: while this file exists, no probe may submit. Written on any anomaly,
#: cleared only by a human.
HALT_FILE = pathlib.Path(
    os.getenv("GEMINI_SEMANTICS_HALT_FILE",
              "analysis/gemini/info_edge/SEMANTICS_PROBE_HALT"))


class ProbeRefusal(Exception):
    """A predeclared rule refused the probe. Never retried, never relaxed."""


# --------------------------------------------------------------------------
# halt latch
# --------------------------------------------------------------------------

def halted() -> Optional[str]:
    """The halt reason, or None. Read before anything is submitted."""
    try:
        return HALT_FILE.read_text(encoding="utf-8").strip() or "halted"
    except FileNotFoundError:
        return None
    except OSError as exc:                                     # noqa: BLE001
        # an unreadable halt file is treated AS a halt: fail closed.
        return "halt file unreadable (%s) -- treated as halted" % exc


def halt(reason: str) -> str:
    """Record an anomaly and block every later submission."""
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = "%s  %s\n" % (stamp, reason)
    with open(HALT_FILE, "a", encoding="utf-8") as fh:
        fh.write(text)
    return text.strip()


# --------------------------------------------------------------------------
# our interpretation, written down before the venue answers
# --------------------------------------------------------------------------

def classify(book: Dict[str, Any], favored: str) -> Dict[str, Any]:
    """`favored` is "yes" (UP) or "no" (DOWN). Executability is read from the
    outcome's OWN published sell side, never from the complement identity.
    """
    if favored not in ("yes", "no"):
        raise ProbeRefusal("favored must be yes or no, got %r" % (favored,))
    # `metadata.outcome_book` publishes each outcome's OWN ladder under its
    # own name: {"yes": {"ask", "bid"}, "no": {"ask", "bid"}}. It does NOT
    # return the raw contract `prices.buy/prices.sell` mapping.
    #
    # THIS WAS A REAL DEFECT, caught by the first public preflight: reading
    # `prices.buy[favored]` returned None on a live two-sided book and the
    # probe classified GEMI-BTC05M2610031200-UP as case B ("nothing buyable")
    # while 750 contracts were offered at 0.50. A probe whose job is to test
    # our book interpretation must not misread the book, so the shape is read
    # from the documented keys and a missing side is DISTINGUISHED from a
    # misparsed one below.
    side = book.get(favored)
    if not isinstance(side, dict):
        raise ProbeRefusal(
            "outcome_book has no %r ladder -- keys were %s. Refusing rather "
            "than reading absence into a shape we do not recognise."
            % (favored, sorted(book)))
    ask = side.get("ask")           # what it costs to BUY the favored outcome
    bid = side.get("bid")           # what a SELL of it receives
    executable = ask is not None and D(str(ask)) > 0
    return {
        "favored": favored,
        "case": "A" if executable else "B",
        "our_claim": ("EXECUTABLE: an offer exists, so a crossing LIMIT buy "
                      "should trade immediately"
                      if executable else
                      "UNAVAILABLE: no offer, so no immediate buy is possible "
                      "and only a resting maker order can be placed"),
        "favored_ask": None if ask is None else str(ask),
        "favored_bid": None if bid is None else str(bid),
        "identity_consistent": book.get("identity_consistent"),
        "complement_identity": book.get("complement_identity"),
        "read_from": "contract prices.buy/prices.sell -- NOT derived",
    }


def _cash(price: str, qty: str) -> D:
    return (D(price) * D(qty)).quantize(D("0.0001"))


def limit_is_tied_to_the_approved_ask(limit_price: str, approved_ask: str,
                                      tick: Optional[str]) -> Dict[str, Any]:
    """CASE A ONLY. The approved limit must be the ask the human actually saw
    and approved, or at most ONE TICK above it.

    WHY THIS REPLACED A UNIVERSAL 0.99. A limit of 0.99 crosses any ask on the
    grid, so a fill proves only that SOME liquidity exists somewhere in the
    book -- it does not prove that the specific offer we recorded was takeable
    at the price we recorded. Tying the limit to the observed ask makes the
    claim the probe can support the one we actually want:

        visible ask -> marketable limit BUY -> fill at or below that ask.

    One tick of headroom is allowed because the venue may round or the touch
    may tick once between approval and submission; more than that would be
    chasing by another name.
    """
    if tick in (None, ""):
        return {"ok": False,
                "why": "no priceIncrement in the live terms, so 'one tick "
                       "above' is undefined. Refusing rather than assuming."}
    p, a, t = D(limit_price), D(approved_ask), D(str(tick))
    ceiling = a + t
    ok = a <= p <= ceiling
    return {"ok": ok, "limit": str(p), "approved_ask": str(a),
            "tick": str(t), "max_allowed_limit": str(ceiling),
            "why": ("the limit is the approved ask, or one tick above it"
                    if ok else
                    "the limit %s is not tied to the approved ask %s: it must "
                    "lie in [%s, %s]. A limit further above would prove only "
                    "that some liquidity exists somewhere in the book."
                    % (p, a, a, ceiling))}


def still_executable_at_the_frozen_limit(book: Dict[str, Any],
                                         depth: Dict[str, Any],
                                         favored: str, limit_price: str,
                                         qty: str) -> Dict[str, Any]:
    """CASE A ONLY, immediately before submission. The approved liquidity must
    STILL be there: an ask at or below the frozen limit, with enough displayed
    size for the whole order.

    If it moved above the limit or disappeared, this returns ok=False and the
    probe STOPS. It does not pick another price, and it does not move to
    another market.
    """
    side = book.get(favored) or {}
    ask = side.get("ask")
    if ask is None:
        return {"ok": False, "current_ask": None,
                "why": "the approved offer DISAPPEARED before submission. "
                       "Stopping rather than chasing."}
    if D(str(ask)) > D(limit_price):
        return {"ok": False, "current_ask": str(ask),
                "limit": str(limit_price),
                "why": "the ask MOVED UP to %s, above the frozen limit %s. "
                       "Stopping rather than chasing." % (ask, limit_price)}
    asks = (depth or {}).get("asks") or {}
    levels = asks.get("levels") or []
    takeable = D("0")
    for lv in levels:
        try:
            if D(str(lv.get("price"))) <= D(limit_price):
                takeable += D(str(lv.get("qty") or "0"))
        except Exception:                                      # noqa: BLE001
            continue
    want = D(str(qty))
    if takeable < want:
        return {"ok": False, "current_ask": str(ask),
                "displayed_size_at_or_below_limit": str(takeable),
                "required": str(want),
                "why": "insufficient displayed size: %s available at or below "
                       "%s, %s required. Stopping rather than taking a "
                       "partial we did not approve." % (takeable, limit_price,
                                                        want)}
    return {"ok": True, "current_ask": str(ask), "limit": str(limit_price),
            "displayed_size_at_or_below_limit": str(takeable),
            "required": str(want),
            "why": "the approved liquidity is still executable at or below "
                   "the frozen limit, with enough displayed size for the "
                   "whole order"}


def terms_are_live(terms: Dict[str, Any]) -> Dict[str, Any]:
    """A successful LIVE terms read is a hard pre-submit requirement.

    Nothing is defaulted and nothing is inferred: the instrument must publish
    its own quantity minimum/increment and price minimum/increment through the
    proven `metadata.terms_for` path, and must be open for trading.
    """
    missing = list(terms.get("missing_fields") or [])
    needed = {k: terms.get(k) for k in
              ("quantityMinimum", "quantityIncrement",
               "priceMinimum", "priceIncrement")}
    blank = [k for k, v in needed.items() if v in (None, "")]
    status = terms.get("contract_status")
    state = terms.get("market_state")
    ok = (bool(terms.get("readable")) and not missing and not blank
          and status == "active" and state == "open")
    return {"ok": ok, "readable": terms.get("readable"),
            "missing_fields": missing, "blank_required": blank,
            "contract_status": status, "market_state": state,
            "terms": needed, "tick": needed.get("priceIncrement"),
            "why": (None if ok else
                    "the live terms read did not succeed: readable=%r "
                    "missing=%s blank=%s status=%r state=%r. Nothing is "
                    "defaulted, so this is a hard pre-submit failure."
                    % (terms.get("readable"), missing, blank, status, state))}


def price_does_what_the_case_needs(case: str, limit_price: str,
                                  cls: Dict[str, Any]) -> Dict[str, Any]:
    """Case B only: the limit must rest, strictly below the favored outcome's
    own bid. Case A's price rule is `limit_is_tied_to_the_approved_ask` plus
    `still_executable_at_the_frozen_limit`.
    """
    p = D(limit_price)
    if case == "A":
        ask = cls.get("favored_ask")
        if ask is None:
            return {"ok": False, "why": "case A but no visible ask to cross"}
        ok = p >= D(ask)
        return {"ok": ok, "limit": str(p), "visible_ask": ask,
                "why": ("crosses the visible ask, so it should trade "
                        "immediately" if ok else
                        "the book MOVED: the limit %s is below the visible "
                        "ask %s and would rest instead of crossing. Refusing "
                        "rather than chasing." % (p, ask))}
    bid = cls.get("favored_bid")
    if bid is None:
        return {"ok": False, "why": "case B but no bid to rest behind"}
    ok = p < D(bid)
    return {"ok": ok, "limit": str(p), "favored_bid": bid,
            "why": ("below the favored outcome's own bid, so it rests"
                    if ok else
                    "the predeclared limit %s is not below the bid %s and "
                    "could cross. Refusing rather than repricing." % (p, bid))}


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------

async def run(*, symbol: str, favored: str, auth_id: str,
              limit_price: Optional[str] = None,
              quantity: Optional[str] = None,
              approved_ask: Optional[str] = None,
              case_b_authorized: bool = False,
              state_dir: str = latch.DEFAULT_DIR,
              out: Optional[str] = None) -> Dict[str, Any]:
    """ONE probe. Records the full chronological chain and returns it.

    Fails closed at every stage. Nothing is labelled realized.
    """
    rec: Dict[str, Any] = {
        "label": "GEMINI_EXECUTION_SEMANTICS_PROBE_V1",
        "auth_id": auth_id,
        "signal_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": symbol,
        "mutations": [],
        "orders_submitted": 0,
        "max_orders_per_probe": MAX_ORDERS_PER_PROBE,
        "no_extend": True, "no_averaging": True, "no_chasing": True,
        "no_flip": True, "no_complement": True,
        "realized": "NOT_REALIZED: no settlement cashflow confirmed",
    }

    # ---- 0. halt latch, before anything else ----------------------------
    reason = halted()
    if reason:
        rec["stop"] = "HALTED: %s" % reason
        return _finish(rec, out)

    # ---- 1. state, all of it, before any intent exists -------------------
    rec["terms"] = metadata.terms_for(symbol)
    rec["book"] = metadata.outcome_book(symbol)
    rec["depth"] = metadata.contract_depth(symbol)
    rec["resolution"] = metadata.resolution_for(symbol)
    rec["positions_before"] = await metadata.positions()
    rec["active_orders_before"] = await transport.active_orders(symbol)

    if not rec["book"].get("readable", True):
        rec["stop"] = "UNREADABLE_BOOK"
        return _finish(rec, out)
    if rec["book"].get("crossed"):
        rec["stop"] = "CROSSED_BOOK"
        return _finish(rec, out)

    # ONE OUTSTANDING ORDER AT A TIME, checked against the venue rather than
    # assumed from our own bookkeeping.
    live_before = transport._orders_of(rec["active_orders_before"])
    if live_before:
        rec["stop"] = ("ALREADY_OUTSTANDING: %d live order(s) on %s. One "
                       "probe at a time." % (len(live_before), symbol))
        return _finish(rec, out)

    # ---- 2. our interpretation, written down before the venue answers ----
    rec["classification"] = classify(rec["book"], favored)
    case = rec["classification"]["case"]

    # ONLY CASE A IS AUTHORIZED. Case B is explicitly NOT authorized, so a
    # market that classifies as B is not a fallback -- it is a stop.
    if case != "A" and not case_b_authorized:
        rec["stop"] = ("CASE_B_NOT_AUTHORIZED: this market classifies as case "
                       "%s and only case A is authorized. Not selecting "
                       "another market." % case)
        return _finish(rec, out)

    # A SUCCESSFUL LIVE TERMS READ IS A HARD PRE-SUBMIT REQUIREMENT.
    rec["terms_live"] = terms_are_live(rec["terms"])
    if not rec["terms_live"]["ok"]:
        rec["stop"] = "TERMS_READ_FAILED: %s" % rec["terms_live"]["why"]
        return _finish(rec, out)

    # ---- 3. the one order, fully specified before it is built -------------
    # PREDECLARED quantity, or the contract's own minimum. Either way it is a
    # constant, never sized from the book or from any signal.
    qty = quantity if quantity is not None else \
        metadata.smallest_valid_quantity(rec["terms"])
    if qty is None:
        rec["stop"] = "NO_VALID_QUANTITY_FROM_TERMS"
        return _finish(rec, out)
    if limit_price is None:
        # NO AUTOMATIC PRICE SELECTION. The price is predeclared by a human or
        # there is no probe.
        rec["stop"] = ("NO_PREDECLARED_LIMIT_PRICE: the price must be stated "
                       "before the book is read, never chosen from it")
        return _finish(rec, out)

    # CASE A: the limit must be TIED to the ask the human approved.
    if case == "A":
        if approved_ask is None:
            rec["stop"] = ("NO_APPROVED_ASK: case A requires the ask that was "
                           "recorded and approved at preflight. Without it "
                           "the limit is untethered.")
            return _finish(rec, out)
        rec["limit_tied_to_approved_ask"] = limit_is_tied_to_the_approved_ask(
            limit_price, approved_ask, rec["terms_live"]["tick"])
        if not rec["limit_tied_to_approved_ask"]["ok"]:
            rec["stop"] = ("LIMIT_NOT_TIED_TO_APPROVED_ASK: %s"
                           % rec["limit_tied_to_approved_ask"]["why"])
            return _finish(rec, out)

    fit = price_does_what_the_case_needs(case, limit_price, rec["classification"])
    pv = metadata.price_is_valid(limit_price, rec["terms"])
    qv = metadata.quantity_is_valid(qty, rec["terms"])
    cash = _cash(limit_price, qty)
    rec["intended_action"] = {
        "case": case,
        "side": "buy", "outcome": favored,
        "order_type": "limit",
        "maker_or_cancel": (case == "B"),
        "allow_taker": (case == "A"),
        "price": limit_price, "quantity": qty,
        "cash_at_risk_usd": str(cash),
        "price_valid": pv, "quantity_valid": qv,
        "price_fits_case": fit,
        "rest_timeout_s": (REST_TIMEOUT_S if case == "B" else None),
    }
    if not fit.get("ok"):
        rec["stop"] = "PRICE_NO_LONGER_FITS_CASE: %s" % fit.get("why")
        return _finish(rec, out)
    if not pv.get("valid") or not qv.get("valid"):
        rec["stop"] = "TERMS_REFUSED_PRICE_OR_QUANTITY"
        return _finish(rec, out)
    if cash > MAX_CASH_PER_MARKET_USD:
        rec["stop"] = ("CASH_CAP_BREACH: %s > %s. The cap is NEVER raised to "
                       "admit a venue minimum." % (cash,
                                                   MAX_CASH_PER_MARKET_USD))
        return _finish(rec, out)

    # the arm is checked HERE, explicitly, so a disarmed run reports rather
    # than raising out of the middle of the transport.
    rec["arming_state"] = arming.state()
    if not arming.armed():
        rec["stop"] = ("NOT_ARMED: zero mutations possible. Preflight only. %s"
                       % rec["arming_state"].get("why_not"))
        return _finish(rec, out)

    # ---- 4. ONE-SHOT AUTHORIZATION, CONSUMED BEFORE SUBMISSION -----------
    try:
        rec["latch"] = latch.consume(auth_id, dict(rec["intended_action"]),
                                     directory=state_dir)
    except latch.AlreadyConsumed as exc:
        rec["stop"] = "AUTHORIZATION_ALREADY_SPENT: %s" % exc
        return _finish(rec, out)

    # ---- 4b. IMMEDIATELY BEFORE SUBMISSION: re-read the book -------------
    # The approved liquidity must still be executable at or below the frozen
    # limit, with enough displayed size for the whole order. If it moved up or
    # vanished we STOP: no reprice, no retry, no other market.
    #
    # The latch is already spent at this point, deliberately. A refusal here
    # costs the authorization and sends nothing, which is the safe direction:
    # the alternative is checking after the venue could already have seen an
    # order.
    if case == "A":
        rec["book_at_submission"] = metadata.outcome_book(symbol)
        rec["depth_at_submission"] = metadata.contract_depth(symbol)
        rec["still_executable"] = still_executable_at_the_frozen_limit(
            rec["book_at_submission"], rec["depth_at_submission"],
            favored, limit_price, qty)
        if not rec["still_executable"]["ok"]:
            latch.advance(auth_id, latch.REJECTED, directory=state_dir)
            rec["stop"] = ("APPROVED_LIQUIDITY_GONE: %s"
                           % rec["still_executable"]["why"])
            rec["authorization_note"] = (
                "the one-shot authorization was consumed and is now SPENT. "
                "Nothing was submitted. A further probe needs a fresh "
                "authorization from the owner.")
            return _finish(rec, out)

    # ---- 5. the single order ---------------------------------------------
    t0 = time.monotonic()
    ack = await transport.place(symbol=symbol, side="buy", outcome=favored,
                                price=limit_price, quantity=qty,
                                allow_taker=(case == "A"))
    rec["orders_submitted"] = 1
    rec["submitted_body"] = ack.get("request")
    rec["acknowledgement"] = {k: ack.get(k) for k in
                              ("status", "classification", "ok", "body")}
    rec["latency_s"] = round(time.monotonic() - t0, 4)
    rec["mutations"].append("place")

    if ack.get("classification") == transport.AMBIGUOUS:
        latch.advance(auth_id, latch.SUBMISSION_UNKNOWN, directory=state_dir)
        rec["halt"] = halt("AMBIGUOUS send on %s: we do not know whether the "
                           "venue saw the order. Reconcile by hand." % symbol)
        rec["stop"] = "SUBMISSION_UNKNOWN_RECONCILE_REQUIRED"
        return _finish(rec, out)
    if ack.get("classification") != "ACCEPTED":
        latch.advance(auth_id, latch.REJECTED, directory=state_dir)
        rec["stop"] = "PLACE_NOT_ACCEPTED: %s" % ack.get("classification")
        rec["positions_after"] = await metadata.positions()
        return _finish(rec, out)

    body = ack.get("body") or {}
    order_id = body.get("orderId") or body.get("order_id")
    rec["order_id"] = order_id
    latch.advance(auth_id, latch.SUBMITTED, venue_order_id=order_id,
                  directory=state_dir)

    # THE ACKNOWLEDGEMENT IS ITSELF A FILL REPORT, and on a crossing order it
    # is the AUTHORITATIVE one.
    #
    # THIS WAS A REAL DEFECT. The first armed probe filled 1 @ 0.50 on the ack
    # -- status 201, "status": "filled", filledQuantity "1",
    # remainingQuantity "0", avgExecutionPrice "0.5" -- and the probe reported
    # "did NOT trade", latched UNFILLED, tried to cancel the filled order
    # (404 OrderNotFound) and raised a false SEMANTIC_CONTRADICTION. The cause:
    # fill status was read ONLY from active-orders, and a filled order is not
    # an active order, so that read is STRUCTURALLY INCAPABLE of seeing a fill.
    # Absence from the active list means filled, cancelled or never accepted,
    # and the venue's own words decide which -- never our inference.
    rec["ack_fill"] = {k: body.get(k) for k in
                       ("status", "filledQuantity", "remainingQuantity",
                        "avgExecutionPrice", "price", "quantity")}
    ack_filled = D("0")
    try:
        ack_filled = D(str(body.get("filledQuantity") or "0"))
    except Exception:                                          # noqa: BLE001
        ack_filled = D("0")
    ack_status = body.get("status")

    # ---- 6. what the venue actually did ----------------------------------
    rec["observations"] = []
    filled_qty = D("0")
    if case == "A":
        # A crossing order either traded or it did not. One read, promptly,
        # then the authoritative order history. No waiting, no re-sending.
        await asyncio.sleep(2.0)
        found = await transport.find_order(symbol=symbol, order_id=order_id)
        rec["observations"].append({
            "t_s": round(time.monotonic() - t0, 2),
            "book": metadata.outcome_book(symbol), "order": found})
    else:
        deadline = time.monotonic() + REST_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(10.0)
            live = await transport.active_orders(symbol)
            found = await transport.find_order(symbol=symbol,
                                               order_id=order_id)
            rec["observations"].append({
                "t_s": round(time.monotonic() - t0, 2),
                "book": metadata.outcome_book(symbol),
                "active_count": len(transport._orders_of(live)),
                "order": found})
            st = ((found or {}).get("order") or {}).get("status")
            if st == "filled":
                break

    rec["order_history"] = await metadata.order_history()
    rec["positions_after"] = await metadata.positions()

    # ---- 7. predeclared cancel, fail-closed ------------------------------
    last = (rec["observations"][-1] if rec["observations"] else {}) or {}
    row = ((last.get("order") or {}).get("order") or {})
    status = row.get("status")
    try:
        filled_qty = D(str(row.get("filledQuantity", "0") or "0"))
    except Exception:                                          # noqa: BLE001
        filled_qty = D("0")
    # The ack wins where it is more informative: a filled order vanishes from
    # the active list, so a 0 from there never overrides a fill the venue
    # already reported.
    if ack_filled > filled_qty:
        filled_qty = ack_filled
    if not status:
        status = ack_status
    rec["venue_status"] = status
    rec["filled_quantity"] = str(filled_qty)
    rec["fill_source"] = ("venue acknowledgement (201 + status/filledQuantity)"
                          if ack_filled > 0 else "active-orders / history read")

    # NEVER CANCEL A FILLED ORDER. The first probe sent a cancel for an order
    # the venue had already filled and got 404 OrderNotFound, which the
    # fail-closed cancel correctly reported as OUTSTANDING -- noise standing in
    # for a non-event.
    if status not in ("filled",) and filled_qty == 0 and order_id is not None:
        rec["cancel"] = await transport.cancel_and_confirm(order_id, symbol)
        rec["mutations"].append("cancel")

    # ---- 8. semantic reconciliation. Disagreement is the VALUABLE result,
    #         and it stops the programme rather than feeding the next probe.
    rec["reconciliation"] = _reconcile(rec, case, favored, limit_price, qty,
                                       filled_qty, ack_row=rec["ack_fill"])
    if rec["reconciliation"]["anomaly"]:
        rec["halt"] = halt("%s on %s: %s" % (
            rec["reconciliation"]["kind"], symbol,
            rec["reconciliation"]["detail"]))

    latch.advance(auth_id,
                  latch.FILLED if filled_qty > 0 else latch.UNFILLED,
                  venue_order_id=order_id, directory=state_dir)
    rec["filled"] = filled_qty > 0
    rec["trade_claim"] = ("A TRADE OCCURRED" if filled_qty > 0 else
                          "NO TRADE: an acknowledged order is not a trade")
    rec["verdict"] = _verdict(rec, case)
    return _finish(rec, out)


def _reconcile(rec, case, favored, limit_price, qty, filled_qty,
               ack_row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every way the venue could disagree with our model, checked explicitly.

    An anomaly here is not a probe failure -- it is the most valuable outcome
    this probe can produce. It halts further submissions so the contradiction
    is investigated rather than averaged over.
    """
    body = rec.get("submitted_body") or {}
    last = (rec.get("observations") or [{}])[-1] or {}
    row = ((last.get("order") or {}).get("order") or {})

    # side / outcome / price / quantity mapping, as the VENUE echoes them
    for field, ours in (("side", body.get("side")),
                        ("outcome", body.get("outcome")),
                        ("price", body.get("price")),
                        ("quantity", body.get("quantity"))):
        theirs = row.get(field)
        if theirs is not None and str(theirs) != str(ours):
            return {"anomaly": True, "kind": "MAPPING_MISMATCH",
                    "detail": "we sent %s=%r, the venue echoes %r"
                              % (field, ours, theirs)}

    # the headline contradiction: case B says nothing was buyable
    if case == "B" and filled_qty > 0:
        # a RESTING order being filled is NOT a contradiction -- it is the
        # expected way to be taken. Only an immediate fill would be.
        t = last.get("t_s")
        if t is not None and t <= 3.0:
            return {"anomaly": True, "kind": "SEMANTIC_CONTRADICTION",
                    "detail": "our parser reported NO executable offer on the "
                              "favored side, yet a maker-or-cancel buy filled "
                              "within %.1fs. 'Unavailable' does not mean what "
                              "we think it means." % t}

    # an unexplained partial
    try:
        want = D(str(qty))
    except Exception:                                          # noqa: BLE001
        want = None
    if want is not None and D("0") < filled_qty < want:
        return {"anomaly": True, "kind": "UNEXPLAINED_PARTIAL",
                "detail": "filled %s of %s" % (filled_qty, want)}

    # THE CLAIM THIS PROBE EXISTS TO SUPPORT: a fill at or below the approved
    # limit. A fill ABOVE it would mean the venue priced us worse than the
    # limit we sent, which is a semantic contradiction, not a cost.
    if filled_qty > 0:
        px_src = dict(row)
        # the ack carries the execution price on a crossing order; the
        # active-orders row usually does not, because the order is gone.
        for k, v in (ack_row or {}).items():
            if v not in (None, "") and k not in px_src:
                px_src[k] = v
        for key in ("avgExecutionPrice", "averagePrice", "avgFillPrice",
                    "executedPrice"):
            got = px_src.get(key)
            if got in (None, ""):
                continue
            try:
                if D(str(got)) > D(str(limit_price)):
                    return {"anomaly": True, "kind": "FILL_ABOVE_LIMIT",
                            "detail": "filled at %s (%s) against a limit of "
                                      "%s" % (got, key, limit_price)}
            except Exception:                                  # noqa: BLE001
                pass

    # case A crossed a visible offer and did not trade
    if case == "A" and filled_qty == 0:
        return {"anomaly": True, "kind": "SEMANTIC_CONTRADICTION",
                "detail": "a LIMIT buy at %s crossed a visible ask of %s with "
                          "makerOrCancel False and did NOT trade. 'Executable'"
                          " does not mean what we think it means."
                          % (limit_price,
                             (rec.get("classification") or {}).get(
                                 "favored_ask"))}

    return {"anomaly": False, "kind": "AGREES",
            "detail": "the venue behaved as our interpretation predicts"}


def _verdict(rec: Dict[str, Any], case: str) -> str:
    """The one sentence this probe exists to be able to say."""
    agreed = not (rec.get("reconciliation") or {}).get("anomaly")
    if case == "A":
        if rec.get("filled"):
            return ("Our book said the favored side WAS executable at a "
                    "recorded ask. A marketable LIMIT buy tied to that ask "
                    "filled immediately at or below it. Gemini AGREES: the "
                    "visible offer we recorded was the offer we took."
                    if agreed else
                    "A marketable LIMIT buy tied to the approved ask filled, "
                    "but the reconciliation found an anomaly. Halted.")
        return ("Our book said the favored side WAS executable, but a crossing "
                "LIMIT buy did NOT trade. Gemini DISAGREES with our "
                "interpretation. Halted for investigation.")
    if rec.get("filled"):
        return ("Our book said the favored side had NO executable seller. A "
                "resting maker buy was FILLED by incoming flow%s -- we could "
                "not take, but we could be taken."
                % ("" if agreed else ", IMMEDIATELY, which contradicts the "
                   "parser and is halted"))
    return ("Our book said the favored side had NO executable seller. A resting "
            "maker buy was accepted, rested, and was NOT filled within the "
            "predeclared timeout. Consistent with the interpretation; it does "
            "not by itself confirm it.")


def _finish(rec: Dict[str, Any], out: Optional[str]) -> Dict[str, Any]:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, sort_keys=True, default=str)
    return rec


def preflight(symbol: str, favored: str,
              limit_price: Optional[str] = None,
              quantity: Optional[str] = None) -> Dict[str, Any]:
    """PUBLIC-ONLY preflight. No credentials, no arm, no mutation, ever.

    `/v1/book/{symbol}` and the contract's own prices are public reads on this
    venue -- there is no separate authenticated book -- so the whole A/B
    classification and the price/quantity arithmetic can be stated before any
    secret is in scope. What credentials add is placement, fills, positions
    and reconciliation, and those stay in the armed run.
    """
    out: Dict[str, Any] = {
        "label": "GEMINI_SEMANTICS_PROBE_PREFLIGHT_PUBLIC_V1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "symbol": symbol,
        "halt": halted(),
        "arming": arming.state(),
        "authorization_expected": arming.EXPECTED_AUTH_ID,
        "taker_authorization": payload.SEMANTICS_PROBE_AUTH_ID,
        "terms": metadata.terms_for(symbol),
        "book": metadata.outcome_book(symbol),
        "depth": metadata.contract_depth(symbol),
        "resolution": metadata.resolution_for(symbol),
    }
    out["classification"] = classify(out["book"], favored)
    case = out["classification"]["case"]
    out["terms_live"] = terms_are_live(out["terms"])
    # what a human would approve: the ask we actually observed, and the only
    # two limits tied to it.
    ask = out["classification"].get("favored_ask")
    tick = out["terms_live"].get("tick")
    if case == "A" and ask is not None and tick:
        out["approved_ask_candidate"] = ask
        out["allowed_limits"] = [str(D(ask)), str(D(ask) + D(str(tick)))]
    qty = quantity if quantity is not None else \
        metadata.smallest_valid_quantity(out["terms"])
    out["quantity"] = qty
    if limit_price is not None and qty is not None:
        out["price_fits_case"] = price_does_what_the_case_needs(
            case, limit_price, out["classification"])
        out["price_valid"] = metadata.price_is_valid(limit_price, out["terms"])
        out["quantity_valid"] = metadata.quantity_is_valid(qty, out["terms"])
        cash = _cash(limit_price, qty)
        out["cash_at_risk_usd"] = str(cash)
        out["within_cash_cap"] = cash <= MAX_CASH_PER_MARKET_USD
        out["cash_cap_usd"] = str(MAX_CASH_PER_MARKET_USD)
        out["maker_or_cancel"] = (case == "B")
        out["allow_taker"] = (case == "A")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--favored", required=True, choices=("yes", "no"))
    ap.add_argument("--auth-id", default=arming.EXPECTED_AUTH_ID)
    ap.add_argument("--limit-price", default=None,
                    help="PREDECLARED limit price. Without it the probe "
                         "refuses rather than choosing one from the book.")
    ap.add_argument("--quantity", default=None,
                    help="PREDECLARED quantity. Omitted means the contract's "
                         "own minimum. Never sized from the book.")
    ap.add_argument("--approved-ask", default=None,
                    help="CASE A: the favored-side ask recorded and approved "
                         "at preflight. The limit must equal it or be at most "
                         "one tick above. Required for case A.")
    ap.add_argument("--authorize-case-b", action="store_true",
                    help="NOT AUTHORIZED as of 2026-10-03. Without it, a "
                         "market that classifies as case B stops the probe "
                         "rather than becoming a fallback.")
    ap.add_argument("--state-dir", default=latch.DEFAULT_DIR)
    ap.add_argument("--out", default=None)
    ap.add_argument("--preflight-only", action="store_true",
                    help="PUBLIC reads only. No credentials, no arm, no "
                         "mutation. Prints the whole decision and exits.")
    a = ap.parse_args(argv)
    if a.preflight_only:
        rec = preflight(a.symbol, a.favored, a.limit_price, a.quantity)
        _finish(rec, a.out)
        print(json.dumps(rec, indent=2, sort_keys=True, default=str))
        return 0
    rec = asyncio.run(run(symbol=a.symbol, favored=a.favored,
                          auth_id=a.auth_id, limit_price=a.limit_price,
                          quantity=a.quantity,
                          approved_ask=a.approved_ask,
                          case_b_authorized=a.authorize_case_b,
                          state_dir=a.state_dir, out=a.out))
    print(json.dumps({k: rec.get(k) for k in
                      ("stop", "classification", "intended_action",
                       "acknowledgement", "order_id", "latency_s",
                       "venue_status", "filled_quantity", "filled",
                       "reconciliation", "halt", "verdict", "realized")},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
