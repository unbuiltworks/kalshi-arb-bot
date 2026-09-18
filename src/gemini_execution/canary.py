"""The one-order canary. It enforces the envelope; the caller does not supply it.

THE CALLER PASSES AN AUTHORIZATION IDENTIFIER AND NOTHING ELSE. Symbol, side,
outcome, price and quantity are DERIVED from a fresh preflight and then checked
against constants in this module. A caller cannot ask for a different size, a
different side, a crossing price or a second order, because it is never asked
what it wants.

ORDER OF OPERATIONS, AND WHY IT IS THIS ORDER:

  1 arm            both keys, exact identifier, unexpired
  2 not consumed   a prior latch means reconcile, never resubmit
  3 fresh preflight   re-derived now; the previous instrument has expired
  4 envelope       every rule re-checked against the FRESH values
  5 fresh BBO      re-read immediately before the mutation; a quote built
                   seconds ago is not evidence about now
  6 no crossing    refused locally AND by makerOrCancel at the venue
  7 CONSUME LATCH  atomically, BEFORE the request
  8 send           exactly once
  9 classify       accepted / rejected / ambiguous; ambiguity is terminal

Step 7 sits before step 8 deliberately: a lost order is recoverable by asking
for another authorization, a double order is not recoverable at all.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal as D
from typing import Any, Dict, Optional

from src.gemini_execution import arming, latch, spec

# ------------------------------------------------------- the frozen envelope
COHORT_PREFIX = "GEMI-BTC05M"            # BTC5m only
COHORT_SUFFIX = "-UP"
SIDE = spec.SIDE_BUY                     # BUY only, this first canary
OUTCOME = spec.OUTCOME_YES
QUANTITY = D("0.01")                     # exactly, the venue minimum
TICK_OFFSET = 0                          # the touch; never tuned for priority
MAX_BBO_AGE_S = 5.0                      # a stale quote is not evidence of now


class EnvelopeViolation(Exception):
    """The canary refused itself. Nothing was sent."""


def _check_symbol(sym: str) -> None:
    if not (sym.startswith(COHORT_PREFIX) and sym.endswith(COHORT_SUFFIX)):
        raise EnvelopeViolation(
            "symbol %r is not a BTC5m UP contract; this canary is BTC5m only"
            % sym)


def check_envelope(pf: Dict[str, Any], *, now: Optional[float] = None
                   ) -> Dict[str, Any]:
    """Every rule, re-checked against the FRESH preflight. Raises on any."""
    if not pf.get("ok"):
        raise EnvelopeViolation("preflight did not pass: %s"
                                % pf.get("verdict"))
    sym = str(pf.get("instrument") or "")
    _check_symbol(sym)

    # The preflight labels the side "BUY"; the prediction-markets API enum is
    # "buy". Compared case-insensitively and EMITTED as the API's enum, so a
    # label convention cannot become a rejected request -- or, worse, a
    # request the venue reads as something else.
    if str(pf.get("side") or "").lower() != SIDE:
        raise EnvelopeViolation("side %r is not %r" % (pf.get("side"), SIDE))

    qty = D(str(pf.get("quantity")))
    if qty != QUANTITY:
        raise EnvelopeViolation("quantity %s is not exactly %s"
                                % (qty, QUANTITY))

    rest = pf.get("restability") or {}
    if rest.get("tick_offset_from_touch") != TICK_OFFSET:
        raise EnvelopeViolation("tick offset %r is not %d"
                                % (rest.get("tick_offset_from_touch"),
                                   TICK_OFFSET))
    if not rest.get("restable"):
        raise EnvelopeViolation("the price is not restable")

    terms = pf.get("venue_terms") or {}
    if terms.get("status") != "limit_only":
        raise EnvelopeViolation(
            "venue status %r is not limit_only; the venue-level maker "
            "protection this canary relies on is not in force"
            % terms.get("status"))

    bbo = pf.get("current_bbo") or {}
    bid, ask = bbo.get("bid"), bbo.get("ask")
    if bid is None or ask is None:
        raise EnvelopeViolation("the book is not two-sided")
    price = D(str(pf.get("limit_price")))
    if price >= D(str(ask)):
        raise EnvelopeViolation(
            "price %s is at or above the ask %s: that would CROSS and take"
            % (price, ask))
    if price != D(str(bid)):
        raise EnvelopeViolation(
            "price %s is not the touch %s; tick offset 0 means join the bid"
            % (price, bid))

    ts = bbo.get("source_ts")
    t = now if now is not None else time.time()
    if ts is None:
        raise EnvelopeViolation("the book carries no timestamp; its age is "
                                "unknown and unknown is not fresh")
    age = t - float(ts)
    if age > MAX_BBO_AGE_S:
        raise EnvelopeViolation("the BBO is %.1fs old, older than %.1fs"
                                % (age, MAX_BBO_AGE_S))

    return {"symbol": sym, "side": SIDE, "outcome": OUTCOME,
            "price": str(price), "quantity": str(QUANTITY),
            "bbo_age_s": round(age, 3)}


async def submit_once(auth_id: str, *, state_dir: str = latch.DEFAULT_DIR,
                      now: Optional[float] = None,
                      preflight=None, transport=None) -> Dict[str, Any]:
    """Place exactly one order, or explain precisely why it did not.

    `preflight` and `transport` are injectable for tests ONLY; the defaults
    are the real ones.
    """
    from src import gemini_canary_preflight as PF
    from src.gemini_execution import transport as TR
    pf_mod = preflight or PF
    tr = transport or TR

    # 1 arm
    try:
        arming.require(now)
    except arming.NotArmed as exc:
        return {"submitted": False, "state": "NOT_ARMED", "why": str(exc),
                "arming": arming.state(now)}

    # 2 a prior latch is reconciliation work, never a resubmission
    if latch.consumed(auth_id, state_dir):
        rec = latch.read(auth_id, state_dir) or {}
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "latch_state": rec.get("state"),
                "venue_order_id": rec.get("venue_order_id"),
                "why": ("this authorization was already spent. Reconcile "
                        "through authoritative reads; there is no retry."),
                "record": rec}

    # 3 fresh preflight -- the previous instrument has expired by now
    pf = await pf_mod.preflight_async(now)

    # 4-6 envelope, fresh BBO, no crossing
    try:
        intent = check_envelope(pf, now=now)
    except EnvelopeViolation as exc:
        return {"submitted": False, "state": "ENVELOPE_REFUSED",
                "why": str(exc), "preflight_ok": pf.get("ok")}

    intent["intent_id"] = "canary-%s" % uuid.uuid4().hex[:16]
    intent["amount"] = intent["quantity"]

    # 7 consume BEFORE the mutation
    try:
        latch.consume(auth_id, intent, state_dir)
    except latch.AlreadyConsumed as exc:
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "why": str(exc)}

    # 8 send, exactly once
    res = await tr.place(symbol=intent["symbol"], side=intent["side"],
                         outcome=intent["outcome"], price=intent["price"],
                         quantity=intent["quantity"],
                         client_order_id=intent["intent_id"], now=now)

    # 9 classify. Ambiguity is terminal and becomes reconciliation work.
    cls = res.get("classification")
    if cls == "ACCEPTED":
        body = res.get("body") or {}
        oid = body.get(spec.ORDER_ID_FIELD)
        rec = latch.advance(auth_id, latch.SUBMITTED, venue_order_id=oid,
                            detail={"status": body.get(spec.STATUS_FIELD)},
                            directory=state_dir)
        return {"submitted": True, "state": latch.SUBMITTED,
                "venue_order_id": oid,
                "venue_status": body.get(spec.STATUS_FIELD),
                "intent_id": intent["intent_id"], "intent": intent,
                "record": rec}
    if cls == "REJECTED":
        rec = latch.advance(auth_id, latch.REJECTED,
                            detail={"status": res.get("status")},
                            directory=state_dir)
        return {"submitted": False, "state": latch.REJECTED,
                "why": ("the venue refused the order. The authorization is "
                        "still spent; that is deliberate."),
                "venue_status": res.get("status"), "record": rec}
    rec = latch.advance(auth_id, latch.SUBMISSION_UNKNOWN,
                        detail={"error": res.get("error"),
                                "status": res.get("status")},
                        directory=state_dir)
    return {"submitted": None, "state": latch.SUBMISSION_UNKNOWN,
            "why": ("the outcome of the send could not be established. "
                    "Reconcile through active-orders and trades. Do NOT "
                    "submit again."),
            "intent_id": intent["intent_id"], "record": rec}


async def reconcile(auth_id: str, *, state_dir: str = latch.DEFAULT_DIR,
                    transport=None) -> Dict[str, Any]:
    """Resolve an ambiguous or in-flight latch through authoritative READS."""
    from src.gemini_execution import transport as TR
    tr = transport or TR
    rec = latch.read(auth_id, state_dir)
    if rec is None:
        return {"resolved": False, "why": "no latch for this authorization"}
    found = await tr.find_order(symbol=rec.get("symbol"),
                                client_order_id=rec.get("intent_id"),
                                order_id=rec.get("venue_order_id"))
    return {"resolved": bool(found.get("resolved")), "latch_state":
            rec.get("state"), "venue": found,
            "labels": ("an authoritative read, never a resubmission. A latch "
                       "is never un-consumed by reconciling it.")}
