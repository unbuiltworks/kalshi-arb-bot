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
#: V2: NOT a constant. The quantity is read from the contract's own
#: quantityMinimum/quantityIncrement at preflight time. This remains only as
#: the value the ENVELOPE was authorized at, for comparison in reports.
QUANTITY = D("0.01")
TICK_OFFSET = 0                          # the touch; never tuned for priority
MAX_BBO_AGE_S = 5.0                      # a stale quote is not evidence of now


class EnvelopeViolation(Exception):
    """The canary refused itself. Nothing was sent."""


def _check_symbol(sym: str) -> None:
    if not (sym.startswith(COHORT_PREFIX) and sym.endswith(COHORT_SUFFIX)):
        raise EnvelopeViolation(
            "symbol %r is not a BTC5m UP contract; this canary is BTC5m only"
            % sym)


def check_envelope(pf: Dict[str, Any], *, now: Optional[float] = None,
                   terms: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Every rule, re-checked against the FRESH preflight. Raises on any.

    `terms` is the contract's authoritative metadata. It is an INPUT so this
    stays a pure check: a validation function that reaches the network is one
    that cannot be tested without one.
    """
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

    # V2: the quantity is whatever the VENUE states as its minimum, validated
    # against the contract's own quantityMinimum/quantityIncrement. Canary #1
    # hard-coded 0.01 from an endpoint that disclaims these instruments; it
    # happened to match, which is not the same as being established.
    from src.gemini_execution import metadata as _MD
    terms = terms if terms is not None else _MD.terms_for(sym)
    if not terms.get("readable"):
        raise EnvelopeViolation(
            "instrument metadata is not readable (%s); size and price may not "
            "be assumed" % terms.get("why"))
    if terms.get("contract_status") != "active" or \
            terms.get("market_state") != "open":
        raise EnvelopeViolation(
            "contract status %r / market state %r is not tradeable"
            % (terms.get("contract_status"), terms.get("market_state")))
    smallest = _MD.smallest_valid_quantity(terms)
    qty = D(str(pf.get("quantity")))
    if str(qty) != str(D(str(smallest))):
        raise EnvelopeViolation(
            "quantity %s is not the venue's smallest valid quantity %s"
            % (qty, smallest))
    qv = _MD.quantity_is_valid(qty, terms)
    if qv.get("valid") is not True:
        raise EnvelopeViolation("quantity %s fails the contract's own rules: %s"
                                % (qty, qv.get("checks")))

    rest = pf.get("restability") or {}
    if rest.get("tick_offset_from_touch") != TICK_OFFSET:
        raise EnvelopeViolation("tick offset %r is not %d"
                                % (rest.get("tick_offset_from_touch"),
                                   TICK_OFFSET))
    if not rest.get("restable"):
        raise EnvelopeViolation("the price is not restable")

    # NOT `terms`: that name holds the CONTRACT metadata above, and reusing it
    # here silently replaced it with the preflight's venue_terms, so the price
    # check then read a dict with no `readable` key and refused every price.
    venue_terms = pf.get("venue_terms") or {}
    if venue_terms.get("status") != "limit_only":
        raise EnvelopeViolation(
            "venue status %r is not limit_only; the venue-level maker "
            "protection this canary relies on is not in force"
            % venue_terms.get("status"))

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

    pv = _MD.price_is_valid(price, terms)
    if pv.get("valid") is not True:
        raise EnvelopeViolation("price %s fails the contract's own rules: %s"
                                % (price, pv.get("checks")))

    ts = bbo.get("source_ts")
    t = now if now is not None else time.time()
    if ts is None:
        raise EnvelopeViolation("the book carries no timestamp; its age is "
                                "unknown and unknown is not fresh")
    age = t - float(ts)
    if age > MAX_BBO_AGE_S:
        raise EnvelopeViolation("the BBO is %.1fs old, older than %.1fs"
                                % (age, MAX_BBO_AGE_S))

    # the VENUE's smallest valid quantity, never the module constant. Returning
    # str(QUANTITY) here would have re-introduced exactly the hard-coding V2
    # exists to remove -- and would have sent 0.01 at a venue asking for 1.
    return {"symbol": sym, "side": SIDE, "outcome": OUTCOME,
            "price": str(price), "quantity": str(smallest),
            "authorized_envelope_quantity": str(QUANTITY),
            "quantity_source": "contract quantityMinimum/quantityIncrement",
            "bbo_age_s": round(age, 3)}


def _rejection_reason(res: Dict[str, Any]) -> Dict[str, Any]:
    """Whatever the venue said about WHY, without inventing structure.

    Gemini returns {result, reason, message} on an error; other shapes are
    reported as-is rather than forced into that one.
    """
    body = res.get("body")
    if isinstance(body, dict):
        named = {k: body.get(k) for k in ("result", "reason", "message")
                 if body.get(k) is not None}
        return named or {"body_keys": sorted(body)}
    if body is None and res.get("unparseable"):
        return {"unparseable": str(res.get("unparseable"))[:300]}
    return {"body": str(body)[:300]}


async def submit_once(auth_id: str, *, state_dir: str = latch.DEFAULT_DIR,
                      now: Optional[float] = None,
                      preflight=None, transport=None,
                      durable_check=None, make_durable=None
                      ) -> Dict[str, Any]:
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

    # 2 a prior latch is reconciliation work, never a resubmission.
    # DURABLE storage is consulted first: on an ephemeral host the local
    # filesystem has no memory of a previous run.
    if durable_check is not None and durable_check(auth_id):
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "why": ("this authorization is recorded consumed in durable "
                        "storage. Reconcile; there is no retry."),
                "durable": True}
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
    from src.gemini_execution import metadata as MD
    sym_pf = pf.get("instrument")
    terms = MD.terms_for(sym_pf) if sym_pf else {"readable": False,
                                                 "why": "no instrument"}
    try:
        intent = check_envelope(pf, now=now, terms=terms)
    except EnvelopeViolation as exc:
        return {"submitted": False, "state": "ENVELOPE_REFUSED",
                "why": str(exc), "preflight_ok": pf.get("ok")}

    intent["intent_id"] = "canary-%s" % uuid.uuid4().hex[:16]
    intent["amount"] = intent["quantity"]

    # 7 consume BEFORE the mutation
    try:
        rec0 = latch.consume(auth_id, intent, state_dir,
                             durable_check=durable_check)
    except latch.AlreadyConsumed as exc:
        return {"submitted": False, "state": "ALREADY_CONSUMED",
                "why": str(exc)}

    # 7b MAKE IT DURABLE BEFORE THE VENUE SEES ANYTHING.
    # If this fails we have NOT sent, so aborting is safe and a later retry
    # places exactly one order. The unsafe ordering is the other one: sending
    # first and failing to record it would let a retry send a second.
    if make_durable is not None:
        try:
            make_durable(auth_id, rec0)
        except Exception as exc:                               # noqa: BLE001
            return {"submitted": False, "state": "DURABILITY_REFUSED",
                    "why": ("the authorization could not be recorded in "
                            "durable storage, so nothing was sent: %s: %s"
                            % (type(exc).__name__, str(exc)[:200])),
                    "sent": False}

    # 8 send, exactly once
    res = await tr.place(symbol=intent["symbol"], side=intent["side"],
                         outcome=intent["outcome"], price=intent["price"],
                         quantity=intent["quantity"],
                         now=now)

    # 9 classify. Ambiguity is terminal and becomes reconciliation work.
    cls = res.get("classification")
    if cls == "ACCEPTED":
        body = res.get("body") or {}
        oid = body.get(spec.ORDER_ID_FIELD)
        rec = latch.advance(auth_id, latch.SUBMITTED, venue_order_id=oid,
                            detail={"status": body.get(spec.STATUS_FIELD)},
                            directory=state_dir)
        if make_durable is not None:
            try:
                make_durable(auth_id, rec)
            except Exception:                                  # noqa: BLE001
                pass          # the order is placed; the pre-send record stands
        return {"submitted": True, "state": latch.SUBMITTED,
                "venue_order_id": oid,
                "venue_status": body.get(spec.STATUS_FIELD),
                "intent_id": intent["intent_id"], "intent": intent,
                "record": rec}
    if cls == "REJECTED":
        # CAPTURE THE REASON. A rejection whose cause was not recorded teaches
        # nothing, and the cause is the single most valuable thing a rejection
        # carries. Recording only the status code was a real defect: the first
        # canary came back 400 and left no way to say why.
        reason = _rejection_reason(res)
        rec = latch.advance(auth_id, latch.REJECTED,
                            detail={"status": res.get("status"),
                                    "venue_reason": reason},
                            directory=state_dir)
        out = {"submitted": False, "state": latch.REJECTED,
               "why": ("the venue refused the order. The authorization is "
                       "still spent; that is deliberate."),
               "venue_status": res.get("status"),
               "venue_reason": reason,
               "request_sent": res.get("request"),
               "record": rec}
        if make_durable is not None:
            try:
                make_durable(auth_id, rec)
            except Exception:                                  # noqa: BLE001
                out["durable_outcome_write"] = "FAILED"
        return out
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
    # No client id to match on: the request schema does not admit one. With
    # venue_order_id unknown -- the ambiguous-send case this exists for --
    # find_order falls back to symbol scope, which the zero-orders-before
    # gate makes unambiguous.
    found = await tr.find_order(symbol=rec.get("symbol"),
                                order_id=rec.get("venue_order_id"))
    return {"resolved": bool(found.get("resolved")), "latch_state":
            rec.get("state"), "venue": found,
            "labels": ("an authoritative read, never a resubmission. A latch "
                       "is never un-consumed by reconciling it.")}
