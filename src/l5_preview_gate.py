"""Preview-before-submit: the venue must echo our order before we send it.

This is an execution-safety layer. It changes nothing about WHAT L5 trades --
market, side, price, quantity and maker/taker arrive already decided by the
frozen engine and are never altered here. It only decides whether the order
may be transmitted at all.

Why it exists
-------------
On 2026-09-24T14:40Z a live preview was sent with price.value 0.0150. The
venue answered 200 -- and returned price.value 0.01. It did not refuse the
off-tick price; it silently altered it. An accepted-but-altered price is
more dangerous than a refused one: a refusal fails closed, while a silent
change rests a real order at a price the engine never chose.

So the rule here is REFUSE, never reprice and never retry. If the previewed
order is not the order we asked for, the answer is no.

What the venue actually echoes
------------------------------
Measured from that same live response, not assumed: marketSlug, type, price,
quantity, cumQuantity, leavesQuantity, tif, intent, state, outcomeSide,
action, manualOrderIndicator, side.

`participateDontInitiate` is NOT echoed, so maker intent cannot be checked by
comparison. It is checked by consequence instead: a post-only order that
would match immediately is rejected by the venue's own PDI handling, so a
preview of a maker order that shows ANY filled quantity would not have
rested, and is refused here.
"""
from __future__ import annotations

from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Mapping, Optional

#: fields compared literally between the body sent and the order echoed.
ECHOED_EXACT = ("marketSlug", "type", "tif", "outcomeSide", "action",
                "manualOrderIndicator")

#: compared numerically, so "0.01" and "0.0100" agree but 0.015 and 0.01 do not.
ECHOED_NUMERIC = ("price", "quantity")

#: execution-critical but NOT echoed by this venue; each needs its own check.
NOT_ECHOED = {"participateDontInitiate":
              "checked by consequence: a maker order showing any filled "
              "quantity in preview would not have rested"}


class PreviewRefused(RuntimeError):
    """The previewed order is not the order we asked for. Never repriced."""


def _dec(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError):
        return None


def echo_verdict(sent: Mapping[str, Any], order: Mapping[str, Any]
                 ) -> Dict[str, Any]:
    """Compare the order the venue would create against the one we asked for.

    Returns the full comparison. `ok` is True only when every
    execution-critical field agrees and nothing about the order implies it
    would behave differently from the request.
    """
    mismatches = []
    for k in ECHOED_EXACT:
        want, got = sent.get(k), order.get(k)
        if want != got:
            mismatches.append({"field": k, "sent": want, "echoed": got,
                               "why": "venue returned a different value"})
    for k in ECHOED_NUMERIC:
        want, got = _dec(sent.get(k)), _dec(order.get(k))
        if want is None or got is None or want != got:
            mismatches.append({"field": k, "sent": str(sent.get(k)),
                               "echoed": str(order.get(k)),
                               "why": "venue normalized or omitted the value"})

    state = str(order.get("state") or "")
    if "REJECT" in state.upper():
        mismatches.append({"field": "state", "sent": None, "echoed": state,
                           "why": "venue would reject this order"})

    # maker intent, checked by consequence because PDI is not echoed
    maker = bool(sent.get("participateDontInitiate"))
    cum, leaves = _dec(order.get("cumQuantity")), _dec(order.get("quantity"))
    want_qty = _dec(sent.get("quantity"))
    if maker and cum is not None and cum > 0:
        mismatches.append({"field": "participateDontInitiate",
                           "sent": True, "echoed": "cumQuantity=%s" % cum,
                           "why": "a maker order that fills on preview would "
                                  "not have rested"})
    if want_qty is not None and leaves is not None and leaves != want_qty:
        mismatches.append({"field": "quantity", "sent": str(want_qty),
                           "echoed": str(leaves),
                           "why": "echoed order quantity differs"})

    return {"ok": not mismatches, "mismatches": mismatches,
            "not_echoed": dict(NOT_ECHOED),
            "LABEL": "any mismatch REFUSES; nothing is repriced or retried"}


def require_echo(sent: Mapping[str, Any], preview_response: Mapping[str, Any]
                 ) -> Dict[str, Any]:
    """Raise PreviewRefused unless the venue echoed exactly what we asked."""
    order = (preview_response or {}).get("order") or {}
    if not order:
        raise PreviewRefused("preview returned no order to compare")
    v = echo_verdict(sent, order)
    if not v["ok"]:
        raise PreviewRefused("venue would not create the order as proposed: %s"
                             % v["mismatches"])
    return v


# --------------------------------------------------------------- the book

#: how old the authenticated book may be at the decision point. The frozen
#: rule's own snapshot bound is 2.0s; this is the same discipline applied to
#: the order path and is deliberately not looser.
BOOK_MAX_AGE_S = 2.0


def fresh_two_sided(*, bid: Any, ask: Any, book_t: Optional[float],
                    now: float, max_age_s: float = BOOK_MAX_AGE_S
                    ) -> Dict[str, Any]:
    """A fresh, authenticated, two-sided book AT THE DECISION POINT.

    The periodic preflight used to answer this, and it ran hundreds of
    seconds into a window against a book that was already dying -- which
    says nothing about whether a future order, decided later, is
    executable. Executability is a property of the moment the order is
    made, so it is established then.
    """
    age = None if book_t is None else round(now - book_t, 3)
    two_sided = bid is not None and ask is not None
    fresh = age is not None and age <= max_age_s
    reasons = []
    if not two_sided:
        reasons.append("book is not two-sided (bid=%r ask=%r)" % (bid, ask))
    if age is None:
        reasons.append("no authenticated book timestamp")
    elif not fresh:
        reasons.append("book is %.3fs old, bound is %.1fs" % (age, max_age_s))
    return {"ok": bool(two_sided and fresh), "age_s": age,
            "two_sided": two_sided, "max_age_s": max_age_s,
            "refusals": reasons,
            "source": "authenticated book at the decision point"}


def require_fresh_book(**kw) -> Dict[str, Any]:
    v = fresh_two_sided(**kw)
    if not v["ok"]:
        raise PreviewRefused("book not executable: %s" % "; ".join(
            v["refusals"]))
    return v
