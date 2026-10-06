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

from src import l5_order_body as OB
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


class VenueHTTPError(RuntimeError):
    """A non-2xx answer from the venue, WITH its body.

    Attempt 5 lost the cause of a 400 because `urllib` raises
    `HTTPError` and the caller never read it, so the record held only
    "HTTP Error 400: Bad Request". The venue names the offending field in
    the body; without it a diagnosis is guesswork. This carries the
    status, the body and a header allowlist.
    """

    def __init__(self, *, method: str, path: str, status: int, reason: str,
                 body: str, headers: Dict[str, str]) -> None:
        super().__init__("%s %s -> HTTP %s %s: %s"
                         % (method, path, status, reason, body[:300]))
        self.method, self.path = method, path
        self.status, self.reason = status, reason
        self.body, self.headers = body, headers

    def detail(self) -> Dict[str, Any]:
        return {"method": self.method, "path": self.path,
                "status": self.status, "reason": self.reason,
                "response_body": self.body[:4000],
                "response_headers_safe": self.headers}


#: Response headers safe to record. An allowlist, not a denylist: a new
#: header the venue starts returning is omitted until it is listed, which
#: is the direction that cannot leak. Nothing carrying authentication,
#: session or cookie material can appear here.
SAFE_RESPONSE_HEADERS = (
    "content-type", "content-length", "date", "x-request-id",
    "x-correlation-id", "retry-after", "ratelimit-limit",
    "ratelimit-remaining", "ratelimit-reset", "grpc-message", "grpc-status",
)

#: Request-header names whose VALUES are never recorded or logged. The
#: request headers are signed and carry the API key, so the names alone go
#: into a record and the values never do.
REDACTED_REQUEST_HEADERS = (
    "authorization", "cookie", "set-cookie", "x-api-key", "x-api-secret",
    "pmus-api-key-id", "pmus-signature", "pmus-timestamp", "signature",
)


def safe_headers(raw: Any) -> Dict[str, str]:
    """Allowlisted response headers, lowercased."""
    out: Dict[str, str] = {}
    try:
        items = list(raw.items())
    except AttributeError:
        return out
    for k, v in items:
        lk = str(k).lower()
        if lk in SAFE_RESPONSE_HEADERS:
            out[lk] = str(v)[:200]
    return out


def redacted_request_header_names(headers: Mapping[str, Any]) -> Dict[str, Any]:
    """Header NAMES only, with the sensitive ones marked. No values, ever --
    including for headers that are not on the redaction list, because a
    signed request has no header whose value is worth the risk."""
    return {"names": sorted(str(k) for k in headers),
            "redacted": sorted(str(k) for k in headers
                               if str(k).lower() in REDACTED_REQUEST_HEADERS),
            "VALUES_NEVER_RECORDED": True}


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
        import urllib.error
        import urllib.request
        data = None if body is None else _json.dumps(body).encode()
        headers = A.signed_headers(m, path) if base == A.API else \
            {"User-Agent": "pmus-exec1x", "Content-Type": "application/json"}
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=m)
        try:
            with urllib.request.urlopen(
                    req, context=ssl.create_default_context(),
                    timeout=15) as r:
                return _json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            # Read the body BEFORE the handle closes. This is the whole
            # point: the venue's own words about what it rejected.
            try:
                raw = exc.read() or b""
            except (OSError, ValueError):
                raw = b"<response body could not be read>"
            raise VenueHTTPError(
                method=m, path=path, status=int(exc.code),
                reason=str(exc.reason), body=raw.decode("utf8", "replace"),
                headers=safe_headers(getattr(exc, "headers", None))) from None


# ------------------------------------------------------------------- the body

def order_body(*, slug: str, outcome_side: str, price: Any,
               qty: Any = VENUE_MIN_QTY) -> Dict[str, Any]:
    """The wire body -- DELEGATED to `src.l5_order_body`, the one serializer
    that has actually traded on this venue.

    THIS FUNCTION USED TO HAND-ROLL THE BODY, and that is what Attempt 5
    died of. Every other PMUS order path in this repository
    (`pmus_micro_executor`, `l5_pmus_transport`, `pmus_preview_oneshot`,
    `pmus_auth_preflight_readonly`) already delegates here. EXEC-1-X was
    the only one that did not, so it was the only one that could drift --
    and it drifted in three ways the venue does not accept:

      1. `price` carried no `currency`;
      2. `quantity` was a JSON STRING, where the venue documents
         number<double> and fractional quantity depends on it;
      3. the preview request was the bare body, not the `{"request": ...}`
         envelope the preview endpoint takes.

    `src.l5_order_body`'s own docstring records that this exact class of
    bug happened once before, when `l5_pmus_transport` wrote its own body.
    The correct fix is therefore not to repair the copy but to delete it.

    The NO-side 1-X mapping was NEVER wrong: A5 sent wire 0.51 for a
    semantic DOWN 0.49, which is what the proven serializer sends too.
    """
    return OB.build(slug=slug, side=outcome_side, price=price, qty=qty,
                    post_only=True)


def preview_request(body: Mapping[str, Any]) -> Dict[str, Any]:
    """The preview endpoint's envelope. `POST /v1/order/preview` takes the
    order under `request`; the bare body is a 400."""
    return OB.preview_envelope(body)


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


class GuardRefused(RuntimeError):
    """A just-in-time guard said no between preview and POST."""


class PreviewRejected(RuntimeError):
    """The PREVIEW failed. Nothing crossed the order boundary.

    This exists so the lifecycle classifier never has to INFER a preview
    failure from the latch. `POST /v1/order/preview` is a read-only
    what-if: the venue creates nothing. It happens strictly before the
    latch is consumed, so when this is raised the latch is untouched and
    no order can exist. That is a fact about control flow, not a
    deduction from state.
    """

    def __init__(self, message: str, *,
                 detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.detail = detail or {}
        self.latch_consumed = False
        self.crossed_order_boundary = False


class SubmissionAmbiguous(RuntimeError):
    """`POST /v1/orders` was reached and we do not know what happened.

    TRUE ambiguity, and the only thing that earns the name: the request
    crossed the network boundary to the submission endpoint. An order may
    be resting. No retry is possible or permitted.
    """

    def __init__(self, message: str, *,
                 detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.detail = detail or {}
        self.latch_consumed = True
        self.crossed_order_boundary = True


def submit_one(t: BoundedOrderTransport, *, slug: str, outcome_side: str,
               price: Any, qty: Any = VENUE_MIN_QTY,
               checks: Optional[Mapping[str, Any]] = None,
               guard: Optional[Any] = None) -> Dict[str, Any]:
    """Preview, echo-check, consume the durable latch, then POST once.

    Raises rather than returning a soft failure. The latch is consumed
    between the echo check and the POST, so a crash anywhere after this point
    leaves the experiment spent -- which is the intended direction.

    `guard`, when given, is a zero-argument callable returning
    {"ok": bool, "reason": ...}. It is called TWICE: immediately before the
    latch is consumed, and again immediately before the POST. The first call
    is the one that protects the latch -- a refusal there costs nothing. The
    second closes the microsecond window between the two, and a refusal there
    leaves the latch spent and the experiment over, which is the safe
    direction: a spent latch loses a run, a late POST breaks the envelope.

    Time passes between the preview and the POST. Without a guard, that gap
    is unchecked, which is how a run that was eligible when it started could
    submit when it no longer is.
    """
    assert_frozen()
    if checks is not None and not checks.get("ok"):
        raise MutationRefused("local preflight refused: %s"
                              % checks.get("refusals"))
    body = order_body(slug=slug, outcome_side=outcome_side, price=price,
                      qty=qty)
    # PREVIEW. A what-if; the venue creates nothing here. Anything that
    # goes wrong is a PreviewRejected, which names itself as not having
    # crossed the order boundary -- so the caller never has to read the
    # latch to find out.
    try:
        preview_response = t.request("POST", PREVIEW_PATH,
                                     preview_request(body))
    except VenueHTTPError as exc:
        raise PreviewRejected(
            "the venue rejected the PREVIEW; no order was created",
            detail={"phase": "PREVIEW", "sent": dict(body),
                    "sent_envelope_key": "request",
                    **exc.detail()}) from None
    except MutationRefused:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise PreviewRejected(
            "the preview call failed before any order could exist: %s: %s"
            % (type(exc).__name__, exc),
            detail={"phase": "PREVIEW", "error": type(exc).__name__,
                    "sent": dict(body)}) from None
    try:
        echo = G.require_echo(body, preview_response)
    except Exception as exc:                                   # noqa: BLE001
        raise PreviewRejected(
            "the preview echo did not match what we asked for: %s" % exc,
            detail={"phase": "PREVIEW_ECHO", "sent": dict(body),
                    "echo_failure": str(exc)[:600]}) from None
    if guard is not None:
        v = guard()
        if not v.get("ok"):
            raise GuardRefused("refused before latch consumption: %s"
                               % v.get("reason"))
    record = L.consume(slug=slug, side=outcome_side, price=price, qty=qty)
    if guard is not None:
        v = guard()
        if not v.get("ok"):
            raise GuardRefused(
                "refused after latch consumption and BEFORE the POST: %s. "
                "The latch is spent; the experiment is over and no order was "
                "sent." % v.get("reason"))
    # From here the latch is SPENT and the request is about to cross the
    # order boundary. Everything after this point is genuinely ambiguous.
    try:
        resp = t.request("POST", SUBMIT_PATH, body)
    except VenueHTTPError as exc:
        raise SubmissionAmbiguous(
            "POST /v1/orders answered %s; an order may exist" % exc.status,
            detail={"phase": "SUBMIT", "sent": dict(body),
                    "latch_record": record,
                    **exc.detail()}) from None
    except Exception as exc:                                   # noqa: BLE001
        raise SubmissionAmbiguous(
            "POST /v1/orders failed after the request left this process: "
            "%s: %s" % (type(exc).__name__, exc),
            detail={"phase": "SUBMIT", "error": type(exc).__name__,
                    "sent": dict(body), "latch_record": record}) from None
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
