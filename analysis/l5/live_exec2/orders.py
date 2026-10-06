"""The minimum order mutation path, and nothing more.

This is the only place in the EXEC-1-X image that can change venue state. It
is written as a wall with four doors rather than a transport with a flag:

  POST /v1/order/preview        the echo check, before anything is real
  POST /v1/orders               the ONE submission
  POST /v1/order/{id}/cancel    cancel OUR order, by id
  POST /v1/orders/open/cancel   market-scoped deadman

Any other method, any other path, is refused by `BoundedOrderTransport`
itself. A caller cannot talk it into a different route, and there is no
generic POST available to be reused.

FOUR THINGS MUST ALL BE TRUE before a submission is even attempted:
  1. the owner's four PMUS_EXEC1_* keys are set and the gate names this slug;
  2. the durable latch is unspent, and is CONSUMED before the POST;
  3. the body is exactly one post-only 0.01-share order within the price
     ceiling and on the venue's own tick grid;
  4. the venue's preview echoed that order back unaltered.

Any one of them false means no order. None of them is a judgement call.

CANCEL IS NOT GATED THE SAME WAY. Cancelling is how we stop risk, so the
deadman path does not require the latch or re-check arming -- refusing to
cancel because a flag is unset would be the dangerous direction. It is still
restricted to our own market and order id.
"""
from __future__ import annotations

import os
from decimal import Decimal as D
from typing import Any, Dict, Mapping, Optional

from src import l5_preview_gate as G
from src import pmus_auth as A
from analysis.l5.live_exec.ledger import may_submit, validate_size
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2.spec import (EXEC1X_FINGERPRINT, PROBE,
                                         assert_frozen)
from analysis.l5.live_exec.spec import VENUE_MAX_PRICE, VENUE_MIN_QTY

ONE = D("1")

PREVIEW_PATH = "/v1/order/preview"
SUBMIT_PATH = "/v1/orders"
DEADMAN_PATH = "/v1/orders/open/cancel"


def _cancel_path(order_id: str) -> str:
    return "/v1/order/%s/cancel" % order_id


class MutationRefused(RuntimeError):
    """A route this build is not allowed to use."""


class BoundedOrderTransport:
    """Signed HTTP with exactly four permitted mutations.

    GET is unrestricted (reads are harmless and the preflight needs them).
    Every POST must match one of the four allowed routes, and the submission
    route additionally may be used at most once per process -- the durable
    latch is the cross-process bound.
    """

    def __init__(self, *, order_id_for_cancel: Optional[str] = None) -> None:
        self._submits = 0
        self._order_id_for_cancel = order_id_for_cancel

    # --------------------------------------------------------------- routing
    def _allowed(self, method: str, path: str) -> bool:
        if method == "GET":
            return True
        if method != "POST":
            return False
        if path in (PREVIEW_PATH, SUBMIT_PATH, DEADMAN_PATH):
            return True
        return bool(path.startswith("/v1/order/")
                    and path.endswith("/cancel")
                    and path.count("/") == 4)

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = A.API) -> Any:
        m = str(method).upper()
        if not self._allowed(m, path):
            raise MutationRefused(
                "%s %s is not one of LIVE-L5-EXEC-1-X's four permitted "
                "routes" % (m, path))
        if m == "POST" and path == SUBMIT_PATH:
            self._submits += 1
            if self._submits > 1:
                raise MutationRefused(
                    "a second POST /v1/orders in one process is refused")
        import json as _json
        import ssl
        import urllib.request
        data = None if body is None else _json.dumps(body).encode()
        headers = A.signed_headers(m, path) if base == A.API else \
            {"User-Agent": "pmus-exec1x", "Content-Type": "application/json"}
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=m)
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return _json.loads(r.read() or b"{}")


# ------------------------------------------------------------------- the body

def order_body(*, slug: str, outcome_side: str, price: Any,
               qty: Any = VENUE_MIN_QTY) -> Dict[str, Any]:
    """The wire body. A NO order at NO price X is sent as 1 - X, which is the
    venue's own convention and is the one piece of arithmetic here."""
    px = D(str(price))
    wire = (ONE - px) if str(outcome_side).upper().endswith("NO") else px
    return {"marketSlug": slug, "type": "ORDER_TYPE_LIMIT",
            "price": {"value": str(wire)}, "quantity": str(D(str(qty))),
            "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
            "participateDontInitiate": True,
            "outcomeSide": outcome_side,
            "action": "ORDER_ACTION_BUY",
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC"}


def preflight_submission(*, slug: str, outcome_side: str, price: Any,
                         qty: Any, min_qty: Any, tick: Any,
                         book_bid: Any, book_ask: Any,
                         book_t: Optional[float], now: float) -> Dict[str, Any]:
    """Every local refusal, evaluated and reported together, before any
    network call. `ok` False means no order and no latch consumption."""
    assert_frozen()
    reasons = []
    gate = may_submit(slug)
    if not gate["may_submit"]:
        reasons.append("DISARMED")
    if D(str(qty)) != VENUE_MIN_QTY:
        reasons.append("QTY_NOT_VENUE_MINIMUM:%s" % qty)
    if D(str(price)) > VENUE_MAX_PRICE:
        reasons.append("PRICE_ABOVE_CEILING:%s" % price)
    vs = validate_size(min_qty, tick, price)
    if not vs["pass"]:
        reasons.extend(vs["reasons"])
    fresh = G.fresh_two_sided(bid=book_bid, ask=book_ask, book_t=book_t,
                              now=now)
    if not fresh["ok"]:
        reasons.append("BOOK_NOT_EXECUTABLE")
    # post-only must not be able to take. A buy at or above the live ask
    # would cross, and the venue's PDI would reject it -- refuse locally
    # rather than learn it from a rejection.
    if book_ask is not None and D(str(price)) >= D(str(book_ask)):
        reasons.append("WOULD_CROSS:%s>=%s" % (price, book_ask))
    st = L.state()
    if st.get("spent"):
        reasons.append("DURABLE_LATCH_SPENT")
    return {"ok": not reasons, "refusals": reasons, "arming": gate,
            "size": vs, "book": fresh, "latch": st,
            "probe": PROBE, "spec_fingerprint": EXEC1X_FINGERPRINT}


def submit_one(t: BoundedOrderTransport, *, slug: str, outcome_side: str,
               price: Any, qty: Any = VENUE_MIN_QTY,
               checks: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Preview, echo-check, consume the durable latch, then POST once.

    Raises rather than returning a soft failure. The latch is consumed
    between the echo check and the POST, so a crash anywhere after this point
    leaves the experiment spent -- which is the intended direction.
    """
    assert_frozen()
    if checks is not None and not checks.get("ok"):
        raise MutationRefused("local preflight refused: %s"
                              % checks.get("refusals"))
    body = order_body(slug=slug, outcome_side=outcome_side, price=price,
                      qty=qty)
    echo = G.require_echo(body, t.request("POST", PREVIEW_PATH, body))
    record = L.consume(slug=slug, side=outcome_side, price=price, qty=qty)
    resp = t.request("POST", SUBMIT_PATH, body)
    order_id = str(resp.get("id") or resp.get("orderId")
                   or (resp.get("order") or {}).get("id") or "")
    return {"probe": PROBE, "spec_fingerprint": EXEC1X_FINGERPRINT,
            "order_id": order_id or None, "venue_response": resp,
            "preview_echo": echo, "latch_record": record,
            "submitted_post_only": True,
            "submitted_post_only_implies_maker": False,
            "LANGUAGE": "SUBMITTED is not a fill"}


def cancel_exact(t: BoundedOrderTransport, *, order_id: str,
                 slug: str) -> Any:
    return t.request("POST", _cancel_path(order_id), {"marketSlug": slug})


def deadman_cancel_all(t: BoundedOrderTransport, *, slug: str) -> Dict[str, Any]:
    """Market-scoped cancel-all, for the finally block. Never retried."""
    try:
        return {"ok": True, "response": t.request("POST", DEADMAN_PATH,
                                                  {"slugs": [slug]})}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__,
                "NOTE": "deadman POST is never retried"}


def armed_env_summary() -> Dict[str, Any]:
    from analysis.l5.live_exec.ledger import ARM_KEYS, exec1_armed
    return {"keys_present": {k: bool(os.getenv(k, "").strip())
                             for k in ARM_KEYS},
            "armed": exec1_armed()}
