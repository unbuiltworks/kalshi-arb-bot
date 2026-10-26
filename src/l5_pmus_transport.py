"""Authenticated PMUS transport for the faithful L5 executor.

REUSES the existing authenticated PMUS infrastructure -- `pmus_q1_executor`'s
Ed25519 request signing, its API/GATEWAY hosts and its credential environment
names -- rather than standing up a second execution stack. What is new here is
only the read-only PREFLIGHT and the arming gate that the faithful executor
needs; the signing itself is not reimplemented.

=== IT STARTS DISARMED, AND FAILS CLOSED ===

`PmusTransport.armed` is False on construction and stays False until BOTH:

  1. a full read-only preflight has PASSED in this process, and
  2. an explicit owner arm token is present in the environment AND matches
     the session id, exactly as the existing executor requires.

Any submit or cancel attempted before that raises `NotArmed`. Every preflight
check is a hard gate: identity, market, tradability, outcome mapping, tick and
quantity constraints, book connectivity, and position/open-order
reconciliation. A check that cannot be evaluated FAILS -- an unknown answer is
never treated as a pass, because the whole point of a preflight is to refuse
when we cannot prove the thing.

=== CREDENTIALS NEVER APPEAR ===

Nothing in this module returns, logs or stores a key or a secret. The
preflight reports only whether credentials are PRESENT, and the account
identity it prints comes from the venue's own response. `redact()` is applied
to every error string that could carry a header or a signature.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src import pmus_auth as Q1
from src.l5_live_execution import NotArmed

#: the series the faithful probe is scoped to. A different series fails.
BTC15M_SERIES = "btc-updown-15m"
BTC15M_SLUG_RE = re.compile(r"^cpc-btc-updown-15m-\d{4}-\d{2}-\d{2}-\d{4}z$")

#: the two outcomes, and the ONLY mapping this engine accepts
OUTCOMES = ("UP", "DOWN")

#: owner arm token, reusing the existing executor's environment contract
ARM_ENV, SID_ENV, LIVE_ENV = Q1.ARM_ENV, Q1.SID_ENV, Q1.LIVE_ENV

#: THE VALUE RUNS TO A DELIMITER, NOT TO THE FIRST SPACE. `\S+` stopped at
#: whitespace, so "Authorization: Bearer abc123" redacted only "Bearer" and
#: left the credential in the log. The same copy exists in
#: scripts/pmus_readonly_recovery and is kept in lockstep by a parity test.
_SECRETISH = re.compile(
    r"(X-PM-Access-Key|X-PM-Signature|X-PM-Timestamp|Authorization"
    r"|api[-_]?key|secret|token|bearer|password)"
    r"\s*[:=]\s*[^\"',}\]\n\r]+", re.I)


class VenueThrottled(RuntimeError):
    """The venue told us to slow down and we are still inside the cool-off.

    Raised WITHOUT sending anything. A caller sees the same shape it sees
    for a real 429 -- an error, no answer, nothing established -- which is
    the point: every path that already fails closed on an HTTP error fails
    closed on this too, and none of them has to learn about rate limiting.
    """


class RateLimiter:
    """One token bucket and one shared cool-off for every venue request.

    LIVE 2026-09-26. The executor ran a 3s tick and, on every single tick,
    polled each live order (up to 8 GETs), read a settlement for each market
    still carrying exposure, and re-tried reconciliation 15s apart. With a
    market stuck in the book -- which the retirement defect guaranteed --
    that is well over 1 request a second sustained and ~3.5/s with a full
    order envelope. The venue answered `HTTP Error 429: Too Many Requests`,
    reconcile failed, orders could not be polled to terminal, and the
    fallback path released them into costless venue-net inventory. The rate
    limit was not an external misfortune; we generated it, and it cost
    $1.09 in lost cost basis.

    Two mechanisms, deliberately separate:

    * a TOKEN BUCKET that paces ordinary traffic, so the steady state never
      approaches the limit;
    * a COOL-OFF that every caller shares the moment the venue says 429,
      doubling per consecutive refusal with jitter. Shared, because a
      per-caller backoff is no backoff at all when six callers take turns.

    Writes are exempt from WAITING for a token -- a bounded maker order must
    not sit in a queue behind a settlement poll -- but they are NOT exempt
    from the cool-off, because a venue refusing reads will refuse an order
    too and sending it anyway is how a 429 becomes a duplicate submission.
    """

    def __init__(self, *, rps: float = 1.5, burst: float = 4.0,
                 backoff_s: float = 2.0, backoff_max_s: float = 60.0,
                 now_fn=None, sleep_fn=None) -> None:
        self.rps = float(rps)
        self.burst = float(burst)
        self.backoff_s = float(backoff_s)
        self.backoff_max_s = float(backoff_max_s)
        self._now = now_fn or time.monotonic
        self._sleep = sleep_fn or time.sleep
        self.tokens = float(burst)
        self.last_t = self._now()
        self.cool_until = 0.0
        self.consecutive_429 = 0
        self.first_429_t: Optional[float] = None
        #: DEMANDED vs TRANSMITTED. Pacing makes the observed HTTP rate look
        #: safe by construction -- it is the rate we chose -- so the number
        #: that says whether the schedule actually fits is how much was
        #: ASKED FOR. demanded == transmitted means nothing queued; a gap
        #: that grows means the callers want more than the ceiling allows
        #: and something downstream is falling behind, which is invisible in
        #: the transmitted rate alone.
        self.counts: Dict[str, int] = {"requests": 0, "demanded": 0,
                                       "http_429": 0, "throttled_waits": 0,
                                       "refused_in_cooloff": 0,
                                       "recoveries": 0}
        self.started_t = self.last_t
        self.total_wait_s = 0.0
        self.max_wait_s = 0.0
        self.last_recovery_s: Optional[float] = None

    def _refill(self) -> None:
        now = self._now()
        self.tokens = min(self.burst,
                          self.tokens + (now - self.last_t) * self.rps)
        self.last_t = now

    def before(self, *, write: bool) -> None:
        """Called before a request leaves. Raises inside the cool-off."""
        now = self._now()
        self.counts["demanded"] += 1
        if now < self.cool_until:
            self.counts["refused_in_cooloff"] += 1
            raise VenueThrottled(
                "venue rate limit: %.1fs of cool-off remaining"
                % (self.cool_until - now))
        self._refill()
        if write:
            # not queued, but it still spends what is there
            self.tokens = max(0.0, self.tokens - 1.0)
            self.counts["requests"] += 1
            return
        if self.tokens < 1.0:
            wait = (1.0 - self.tokens) / self.rps if self.rps > 0 else 0.0
            self.counts["throttled_waits"] += 1
            self.total_wait_s += wait
            self.max_wait_s = max(self.max_wait_s, wait)
            self._sleep(wait)
            self._refill()
        self.tokens = max(0.0, self.tokens - 1.0)
        self.counts["requests"] += 1

    def on_success(self) -> None:
        if self.consecutive_429:
            self.counts["recoveries"] += 1
            if self.first_429_t is not None:
                # TIME TO AUTHORITATIVE RECOVERY: how long the venue spent
                # refusing us before an answer came back. This is the number
                # that says whether the backoff is actually working.
                self.last_recovery_s = self._now() - self.first_429_t
        self.consecutive_429 = 0
        self.first_429_t = None
        self.cool_until = 0.0

    def on_429(self) -> float:
        if self.first_429_t is None:
            self.first_429_t = self._now()
        self.consecutive_429 += 1
        self.counts["http_429"] += 1
        span = min(self.backoff_max_s,
                   self.backoff_s * (2 ** (self.consecutive_429 - 1)))
        # deterministic jitter from the counter, so a test can predict it
        # and two processes do not resynchronise on the same boundary.
        jitter = span * 0.25 * ((self.consecutive_429 * 7919) % 100) / 100.0
        span += jitter
        self.cool_until = self._now() + span
        return span

    def metrics(self) -> Dict[str, Any]:
        now = self._now()
        span = max(1e-9, now - self.started_t)
        return {
            "requests": self.counts["requests"],
            "demanded": self.counts["demanded"],
            # the two rates side by side. transmitted_rps is the rate we
            # chose; demand_rps is the rate the schedule actually wants.
            "transmitted_rps": round(self.counts["requests"] / span, 4),
            "demand_rps": round(self.counts["demanded"] / span, 4),
            "unmet_demand": (self.counts["demanded"]
                             - self.counts["requests"]),
            "max_wait_s": round(self.max_wait_s, 3),
            "mean_wait_s": round(
                self.total_wait_s / max(1, self.counts["throttled_waits"]), 3),
            "uptime_s": round(span, 1),
            "http_429": self.counts["http_429"],
            "throttled_waits": self.counts["throttled_waits"],
            "refused_in_cooloff": self.counts["refused_in_cooloff"],
            "recoveries": self.counts["recoveries"],
            "consecutive_429": self.consecutive_429,
            "in_cooloff": now < self.cool_until,
            "cooloff_remaining_s": round(max(0.0, self.cool_until - now), 2),
            "total_wait_s": round(self.total_wait_s, 2),
            "rps_budget": self.rps,
            "time_to_authoritative_recovery_s": (
                None if self.last_recovery_s is None
                else round(self.last_recovery_s, 2)),
            "labels": ("requests counts what actually left this process. "
                       "time_to_authoritative_recovery_s is the span from "
                       "the first 429 of a run to the next successful "
                       "answer, which is what says the backoff works."),
        }


#: ONE LIMITER FOR THE PROCESS.
#:
#: It used to be built in PmusTransport.__init__, and Runner.roll() builds a
#: NEW PmusTransport for every 15-minute window -- so the token bucket and,
#: far worse, the shared 429 cool-off were thrown away at every rollover.
#: Observed live on 2026-09-26: the request counter read 134 at 22:57:06Z on
#: the 2245z transport and 24 at 23:00:40Z on the 2300z one. A venue that
#: refused us at 23:14:50 would have had its cool-off discarded ten seconds
#: later, at exactly the boundary where the original incident's failures
#: clustered. A cool-off shared between callers but not across windows is
#: not a cool-off.
#:
#: The venue's rate limit belongs to the ACCOUNT and the process, not to a
#: market that happens to be open, so the limiter lives here and every
#: transport shares it by default.
_SHARED_LIMITER: Optional["RateLimiter"] = None


def shared_limiter() -> "RateLimiter":
    """The process's one limiter, created on first use."""
    global _SHARED_LIMITER
    if _SHARED_LIMITER is None:
        _SHARED_LIMITER = RateLimiter(
            rps=float(os.getenv("PMUS_RPS", "1.5")),
            burst=float(os.getenv("PMUS_BURST", "4")),
            backoff_s=float(os.getenv("PMUS_BACKOFF_S", "2")),
            backoff_max_s=float(os.getenv("PMUS_BACKOFF_MAX_S", "60")))
    return _SHARED_LIMITER


def reset_shared_limiter() -> None:
    """Tests only. A live process never discards its limiter."""
    global _SHARED_LIMITER
    _SHARED_LIMITER = None


#: THERE IS NO PREFLIGHT CACHE, deliberately. See PmusTransport.preflight:
#: every read it makes is an assertion about now, and the checks that are
#: invariant make no venue call. The cost is bounded by PREFLIGHT_BACKOFF_S
#: in the runner instead. This shim stays so callers that reset state in
#: tests keep working and so the decision is discoverable from the name.
def reset_preflight_cache() -> None:
    """A no-op. Kept so the absence of a cache is explicit, not implied."""


def is_rate_limited(exc: BaseException) -> bool:
    """True for a venue 429, however the stdlib happened to wrap it."""
    code = getattr(exc, "code", None)
    if code == 429:
        return True
    return "429" in str(exc) and "too many requests" in str(exc).lower()


def redact(text: Any) -> str:
    """Strip anything header- or signature-shaped from a string."""
    s = str(text)
    s = _SECRETISH.sub(r"\1: [REDACTED]", s)
    for env in (Q1.KEY_ENV, Q1.SECRET_ENV):
        v = os.getenv(env, "")
        if v and len(v) > 6:
            s = s.replace(v, "[REDACTED]")
    return s[:600]


def http_error_detail(exc: BaseException) -> Dict[str, Any]:
    """A SANITIZED record of what the venue actually said, or why we cannot.

    `str(HTTPError)` is exactly "HTTP Error 400: Bad Request" and carries
    neither path nor body, so a schema rejection was indistinguishable from
    any other 400. That is how the cancel 400 of 2026-10-18 survived a whole
    authorized window: order D68KZ0SG10JR rested 530s while every cancel was
    refused, and the only record was `cancel_error: "HTTPError"`.

    The body is read once and redacted anyway -- a credential is never in a
    RESPONSE, but a venue that echoes a request header into an error message
    must not put it in our logs -- then truncated. Every step is guarded:
    reading a body can raise on a closed stream, and a diagnostic must never
    replace the error it describes.
    """
    out: Dict[str, Any] = {"error": type(exc).__name__}
    code = getattr(exc, "code", None)
    if code is not None:
        out["status"] = code
    reason = getattr(exc, "reason", None)
    if reason is not None:
        out["reason"] = redact(reason)[:120]
    body = None
    reader = getattr(exc, "read", None)
    if callable(reader):
        try:
            raw = reader()
        except BaseException:                                  # noqa: BLE001
            out["body"] = "[UNREADABLE]"
            return out
        try:
            body = raw.decode("utf-8", "replace") if isinstance(
                raw, (bytes, bytearray)) else str(raw)
        except BaseException:                                  # noqa: BLE001
            out["body"] = "[UNDECODABLE]"
            return out
    if body is not None:
        out["body"] = redact(body)[:400]
    return out


def _as_decimal(v: Any) -> Optional[D]:
    """A venue-supplied constraint as an exact Decimal, or None.

    str() first: the venue sends these as JSON numbers, and D(float) would
    carry the binary representation error into a value we quantize against.
    """
    if v is None:
        return None
    try:
        d = D(str(v))
    except Exception:                                          # noqa: BLE001
        return None
    return d if d > 0 else None


#: states in which the venue has finished with a market. CLOSED is NOT
#: here: it is observed at the 900s boundary before the settlement price
#: exists, and retiring a position against a price that is not yet final is
#: how a book gets closed at the wrong number.
TERMINAL_MARKET_STATES = ("MARKET_STATE_EXPIRED", "MARKET_STATE_SETTLED",
                          "MARKET_STATE_RESOLVED")


def settlement_decimal(v: Any) -> Optional[D]:
    """A SETTLEMENT price, where 0 is a real answer.

    `_as_decimal` exists for venue CONSTRAINTS -- a tick size, a minimum
    quantity -- where a non-positive value is meaningless, so it returns
    None for anything <= 0. A settlement price is not a constraint. 0.0000
    is what this venue reports when the long side lost, and it is exactly
    as final as 1.0000. Reusing the constraint parser here would silently
    refuse to settle every market UP lost, which is about half of them.
    """
    if v is None:
        return None
    if isinstance(v, Mapping):                 # {"value": ..., "currency":}
        v = v.get("value")
        if v is None:
            return None
    try:
        d = D(str(v))
    except Exception:                                          # noqa: BLE001
        return None
    return d if D(0) <= d <= D(1) else None


class PreflightFailed(RuntimeError):
    pass


class CancelRefused(RuntimeError):
    """A cancel this transport will not send.

    Deliberately NOT NotArmed: arming is about submitting, and conflating the
    two is what left a resting order unwithdrawable. A CancelRefused means we
    did not ask the venue; it says nothing about what the venue would answer.
    """


class _SignedHTTP:
    """Signed GET and POST. Constructed ONLY by arm().

    While DISARMED this transport's http is `pmus_auth.ReadOnlyTransport`,
    which raises on any method but GET -- so a disarmed process does not
    merely decline to send an order, it holds no object able to send one.
    Arming installs this. That is why `pmus_q1_executor` is not imported
    here: it carries place_limit_buy, cancel_all_deadman and run_campaign,
    and the probe needs exactly one bounded order, not a campaign engine.
    """

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = Q1.API) -> Any:
        import json as _json
        import ssl
        import urllib.request
        m = str(method).upper()
        data = _json.dumps(body).encode() if body is not None else None
        headers = (Q1.signed_headers(m, path) if base == Q1.API
                   else {"User-Agent": "pmus-l5-probe"})
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=m)
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return _json.loads(r.read() or b"{}")


#: the ONE mutating route a disarmed process may reach. Anchored: no
#: account-wide variant, no second path segment and no query string matches.
#:
#: `\Z`, NOT `$`. Python's `$` also matches before a trailing newline, so `$`
#: accepted ".../cancel\n". That was harmless -- urllib strips the newline
#: from the selector, so it still reached this endpoint and merely signed a
#: path the venue would 401 -- but the strict anchor is free. The identical
#: pattern in `src/l5_watchdog.py` is kept in step, and
#: test_the_two_cancel_route_patterns_have_not_drifted pins that.
CANCEL_ROUTE = re.compile(r"^/v1/order/[A-Za-z0-9_-]+/cancel\Z")


def is_cancel_body(body: Any) -> bool:
    """True for EXACTLY the one cancel body the venue accepts.

    Pinned in one place so the route-restricted object and the test that
    guards it cannot drift from what `_cancel_post` actually sends. An extra
    key is a different request -- `{"cancelAll": true}` is the one that must
    never pass -- and an empty body is the 2026-10-18 defect itself.
    """
    if not isinstance(body, Mapping):
        return False
    if set(body) != {"marketSlug"}:
        return False
    slug = body.get("marketSlug")
    return isinstance(slug, str) and bool(slug.strip())


class _CancelOnlyHTTP(_SignedHTTP):
    """Signed HTTP that can GET, and can POST exactly one cancel route.

    THE PRODUCTION DEFECT THIS REPAIRS. `cancel()` deliberately dropped its
    `_require_armed()` gate so a DISARMED process -- the state every orphan
    and deadman path runs in -- could still withdraw an order it owns. But
    `self.http` is `pmus_auth.ReadOnlyTransport` while disarmed, and that
    object raises PermissionError on any method but GET. So the POST died one
    layer BELOW the gate that had been removed for it, and the capability
    never existed. Observed live, twice, each on a real resting order:

      2026-10-15T19:50:03Z  D47R06GG0ZRX  cpc-btc-...-1945z
                            cancel_error PermissionError, left ORDER_STATE_NEW
      2026-10-16T03:39:06Z  D4EFZNW00ZZ0  cpcxrp-xrp-...-0330z
                            cancel_error PermissionError on every sweep while
                            cumQuantity climbed 37.73 -> 47.92

    The second settled worthless at -37.9754, the largest single line in the
    account's realized loss. Nothing withdrew either order; what cleared them
    was the venue expiring them at market close.

    ISOLATION IS PRESERVED, NOT TRADED AWAY. This is not a general transport
    handed to the cancel path: the refusal lives in the object, exactly as it
    does in `ReadOnlyTransport`, so a process holding this cannot be argued
    into a submit. There is no market-scoped and no account-wide route here.

    THE BODY IS PINNED TO EXACTLY ONE SHAPE, and that shape is no longer
    empty. This object used to refuse ANY body, which was the right rule
    while the cancel body was `{}` -- and it is why the 2026-10-18 proof
    could not have been repaired at the call site alone. The venue requires
    `marketSlug`, so `{"marketSlug": <slug>}` is now the only body admitted:
    `{}` is refused as the defect it was, and `{"cancelAll": true}` or any
    extra key is refused exactly as before. The pin is still a pin.

    THE REFUSAL REALLY LIVES HERE, which an earlier draft only claimed. A
    review found that this object, once cached on the transport, was a
    POST-capable thing reachable by attribute -- including straight through
    `ShadowOnlyTransport.__getattr__`, which delegates everything but
    `submit` -- and that it checked nothing itself: ownership was a property
    of one caller. So the id is now re-extracted from the path and re-tested
    against the SAME durable-ledger oracle here, and `base` and `body` are
    pinned too, because `super()` forwarded both unchecked and would accept
    an arbitrary body on the real route or an unauthenticated POST to another
    host.

    WHY AN ABSENT ORACLE STILL PASSES. `cancel()` has a deliberate branch
    where an ARMED transport with no oracle installed cancels anyway, on the
    grounds that refusing to withdraw an order is itself the failure being
    prevented. That branch predates this class and is not narrowed here: an
    absent oracle means ownership is the caller's to prove, as it was. An
    oracle that is PRESENT and says no is refused, on every path.
    """

    def __init__(self, owns=None) -> None:
        #: a zero-argument callable returning the transport's LIVE
        #: `cancel_owner_oracle`, so an oracle installed after this object
        #: was cached is still honoured, and one removed is not ignored.
        self._owns = owns

    def _owned(self, path: str) -> bool:
        if self._owns is None:
            return True                  # see "WHY AN ABSENT ORACLE" above
        m = CANCEL_ROUTE.match(str(path) or "")
        if not m:
            return False
        oid = str(path).split("/")[3]
        try:
            own = self._owns()
        except BaseException:                                  # noqa: BLE001
            return False                 # unreadable oracle is not permission
        if own is None:
            return True                  # none installed on the transport
        try:
            return bool(own(oid))
        except BaseException:                                  # noqa: BLE001
            return False

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: str = Q1.API) -> Any:
        m = str(method).upper()
        if m == "GET":
            return super().request(m, path, body, base)
        if m == "POST" and CANCEL_ROUTE.match(str(path) or ""):
            if base != Q1.API:
                raise PermissionError(
                    "POST %s refused: this transport cancels only at the "
                    "venue API, not at %r" % (path, base))
            if not is_cancel_body(body):
                raise PermissionError(
                    "POST %s refused: a cancel carries exactly "
                    "{'marketSlug': <non-empty slug>}; got keys %r"
                    % (path, sorted(body) if isinstance(body, Mapping)
                       else type(body).__name__))
            if not self._owned(path):
                raise PermissionError(
                    "POST %s refused: the durable ledger does not claim "
                    "this order" % path)
            return super().request(m, path, body, base)
        raise PermissionError(
            "%s %s refused: this transport may only GET, or POST a single "
            "/v1/order/{id}/cancel" % (method, path))


#: the market slug carries a `cpc-` product prefix that the EVENT slug does
#: not: market cpc-btc-updown-15m-2026-09-24-1245z belongs to event
#: btc-updown-15m-2026-09-24-1245z. Derivable, so the event can be addressed
#: without first resolving a numeric id.
EVENT_PREFIXES = ("cpc-",)

#: the venue does not serve a 15m market's book for its first minutes, so a
#: single 404 is not evidence the book is unreachable. Bounded so a genuinely
#: dead route still fails the preflight rather than retrying forever.
BOOK_READ_ATTEMPTS = 3
BOOK_READ_BACKOFF_S = 1.0

#: and when the REST route 404s for the whole retry budget, the AUTHENTICATED
#: WEBSOCKET book for the same market is accepted as equivalent evidence that
#: the book is reachable -- because it is the same venue, the same market and
#: the same credentials, delivering a two-sided top of book on a connected
#: socket. 2100z on 2026-10-23 is the case: `book_readable` 404ed until the
#: campaign expired while the socket had served 7,800 book updates for that
#: slug from +0.8s. ONLY a 404 qualifies: a 401, a 403, a 5xx or a transport
#: error is a genuine read failure and still fails the preflight.
BOOK_READ_404_CODES = (404,)
#: the socket snapshot must be FRESH. `snapshot()` already returns None
#: unless the socket is connected and the book is two-sided; this bounds how
#: stale the last frame of any kind may be. A push stream is quiet when
#: nothing changes, so this is generous relative to a 15m window and still
#: far short of the minutes a dead socket would show.
WS_BOOK_FALLBACK_MAX_AGE_S = 30.0
#: and the only market states the socket may report for the substitution to
#: stand. An ALLOWLIST: the venue also emits MARKET_STATE_UNOPENED and
#: MARKET_STATE_CLOSED, and `"OPEN" in "MARKET_STATE_UNOPENED"` is True, so a
#: substring test accepts exactly the state that must be refused.
WS_BOOK_OPEN_STATES = ("MARKET_STATE_OPEN", "OPEN")


def event_slug_for(market_slug: str) -> str:
    for p in EVENT_PREFIXES:
        if market_slug.startswith(p):
            return market_slug[len(p):]
    return market_slug


def resolve_market(http: Any, slug: str,
                   base: str = Q1.GATEWAY) -> Dict[str, Any]:
    """The market's authoritative metadata row, verified to BE that market.

    Two resources, and which one answers depends on the market:

    `/v1/market/slug/{slug}` (singular) serves SPORTS. It was adopted here
    because this recorder's enriched module reads it -- for sports -- and it
    404s "Market not found" for a BTC15m window even when that window has
    traded and settled. Probed side by side on
    cpc-btc-updown-15m-2026-09-24-0945z, which traded 49,678 shares: the
    singular resource 404s while `/v1/markets/{slug}/bbo` answers 200 for
    the same slug. That mismatch failed `market_readable` on 35 consecutive
    windows, and because the gate aborts the preflight, every metadata gate
    after it went unevaluated rather than passing.

    `/v1/events/slug/{event}` is the resource that does carry it, and its
    response embeds `markets[]` with exactly what the gates read: outcomes,
    orderPriceMinTickSize, minimumTradeQty, active/closed/archived, status,
    slug. The event slug is derived by stripping the `cpc-` product prefix,
    so no numeric id has to be resolved first.

    The `/v1/markets` CATALOGUE is deliberately not used. It is not a
    shortlist: paged properly it runs past 40,000 rows ordered by ascending
    id, so a continuously created 15m window sits at the far end and a
    bounded walk can never reach it. An earlier version walked 8 pages and
    concluded the series was "absent from the catalogue"; it had only
    reached the oldest 4,000 markets, and its stop condition -- a page
    shorter than the limit -- was itself wrong, because this listing returns
    499 rows on some full pages.

    The match is made HERE, on the exact slug, never by a query parameter.
    The gateway silently IGNORES filters it does not know -- `?slug=`,
    `?seriesId=` and `?seriesSlug=` all answer 200 with the unfiltered
    default page -- so trusting one would hand preflight some unrelated NFL
    market's tick and outcomes while looking like a successful read.

    Raises LookupError when no host serves the market, so `market_readable`
    fails closed rather than proceeding on a market the venue is not
    serving. Metadata and the executable book stay separate concerns: the
    contemporaneous book still comes from the authenticated WS feed.
    """
    # Hosts in order. The venue's own OpenAPI schemas (events-schema.json,
    # markets-schema.json) declare every endpoint used here under
    # servers: [gateway.polymarket.us] with security: [] -- they are public
    # and unauthenticated, so the gateway, not the account-scoped api host,
    # is where this metadata is meant to live. The api host is still tried
    # first because it costs one request and is the only cell not yet
    # disproved empirically: production has shown /v1/market/slug/ failing
    # there, but not /v1/events/slug/. The transport signs only for the api
    # host, so each is addressed the way it expects.
    hosts = [base] if base != Q1.GATEWAY else [Q1.API, Q1.GATEWAY]

    # One host failing must never stop the other being tried, and the
    # failure is caught as BaseException on purpose: an unloadable crypto
    # backend raises a pyo3 PanicException, which is NOT an Exception, so
    # `except Exception` would let a signing failure on the api host
    # propagate and lose the gateway answer that was available all along.
    # KeyboardInterrupt and SystemExit are never swallowed.
    for host in hosts:
        try:
            r = http.request("GET", "/v1/market/slug/%s" % slug, None,
                             host) or {}
            mk = r.get("market") or r
            if isinstance(mk, Mapping) and mk.get("slug") == slug:
                return dict(mk)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:                                  # noqa: BLE001
            pass                        # the event below is the real answer

        try:
            r = http.request("GET", "/v1/events/slug/%s"
                             % event_slug_for(slug), None, host) or {}
            ev = r.get("event") or r
            for mk in (ev.get("markets") or []):
                if isinstance(mk, Mapping) and mk.get("slug") == slug:
                    return dict(mk)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:                                  # noqa: BLE001
            pass                            # try the next host, then fail

    raise LookupError(
        "market metadata not served for %s on %s: neither /v1/market/slug/ "
        "nor /v1/events/slug/%s returned it"
        % (slug, ", ".join(hosts), event_slug_for(slug)))


def rows_of(resp: Any, key: str) -> List[dict]:
    """Position rows out of a response that may key them or list them.

    Copied in behaviour from `pmus_settlement_auditor._rows_of`, which has
    run against real fills and settlements on this venue. A mapping is keyed
    by market slug and the key is folded back in as `marketSlug`; a list is
    taken as rows. Anything else yields nothing rather than a guess.
    """
    rows = resp.get(key) if isinstance(resp, Mapping) else None
    if isinstance(rows, Mapping):
        return [{"marketSlug": k, **v} for k, v in rows.items()
                if isinstance(v, Mapping)]
    return [r for r in (rows or []) if isinstance(r, Mapping)]


#: THE CHEAPEST PRICE AN ORDER MAY USE, for the fee bound only.
#:
#: DUPLICATED ON PURPOSE, NOT IMPORTED. This module is packaged into the
#: GET-ONLY observer image, which does not carry `l5_two_sided_probe` --
#: `test_deploy_fallthrough_guard` enforces that closure, and reading
#: `TSP.MIN_PRICE` from here broke it. Importing the probe would either fail
#: at runtime in that image or pull a POST-capable path into it, so the value
#: is restated and `test_the_fee_floor_matches_the_probe_band` keeps the two
#: from drifting.
FEE_FLOOR_PRICE = D("0.05")


class PmusTransport:
    """GET-only until armed. Mutations exist but are gated behind preflight."""

    name = "PMUS"

    def __init__(self, *, market_slug: str, http: Optional[Any] = None,
                 now_fn=None, limiter: Optional["RateLimiter"] = None) -> None:
        self.market_slug = market_slug
        #: the durable ledger's ownership test, installed by the runner.
        #: None means ownership cannot be established and cancel refuses.
        self.cancel_owner_oracle = None
        self._injected = http is not None
        self.http = http or Q1.ReadOnlyTransport()
        #: built on the first cancel. NAME-MANGLED deliberately: a review
        #: found that as `_cancel_http` this was a POST-capable object
        #: reachable by attribute -- including straight through
        #: `ShadowOnlyTransport.__getattr__`, which delegates everything but
        #: `submit`. Mangling is not a security boundary against in-process
        #: code, which could reach it anyway; the real defence is that the
        #: object now refuses an unowned id, a non-cancel route, a foreign
        #: base and any body but the pinned {"marketSlug": <slug>} ITSELF.
        #: This just stops it being
        #: casually exposed on the public surface of the shadow proxy.
        #:
        #: An injected http is a test's or a wrapper's choice and is honoured
        #: for cancel too, so nothing is bypassed by this repair.
        self.__cancel_http = self.http if http is not None else None
        #: the SANITIZED venue response from the most recent failed cancel,
        #: or None. Read by the callers that previously could only log
        #: `type(exc).__name__`, which is how an HTTP 400 schema rejection
        #: survived a whole authorized window as an opaque "HTTPError".
        self.last_cancel_error: Optional[Dict[str, Any]] = None
        self._now = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
        self.armed = False
        #: OPTIONAL read-only supplier of the authenticated WEBSOCKET top of
        #: book for a slug, used by preflight ONLY when the REST book route
        #: 404s for its whole retry budget. Set by the runner, which owns the
        #: socket. None means no fallback exists and a 404 fails as before.
        #: It can only ever turn a FAILED preflight into a passing one on
        #: evidence from the same venue, market and credentials; it is never
        #: consulted when the REST read succeeds.
        self.ws_book: Optional[Any] = None
        self.preflight_report: Optional[Dict[str, Any]] = None
        self.identity_probe: Dict[str, Any] = {}
        #: the venue's own execution grid, filled in by preflight(). None
        #: until then, and None is "not established" -- never a default.
        self.tick_size: Optional[D] = None
        self.quantity_increment: Optional[D] = None
        #: THE PROCESS'S limiter, not this transport's. Every path shares
        #: the budget and the cool-off, and both survive the rollover that
        #: replaces this object. Injectable for tests only.
        self.limiter = limiter if limiter is not None else shared_limiter()

    # ------------------------------------------------------------ read only

    def _request(self, method: str, path: str, body=None,
                 base: str = Q1.API, *, http: Optional[Any] = None) -> Any:
        """The single door every venue request goes through.

        Nothing here retries. A 429 is recorded, the shared cool-off is
        extended, and the error propagates to a caller that already knows
        how to fail closed. Retrying inside this function would be the
        fastest possible way to deepen a rate limit.

        `http` overrides WHICH object sends it, and nothing else. The cancel
        path passes its route-restricted object so the one route it may POST
        still goes through this door -- sharing the rate limiter, the
        cool-off and the 429 accounting rather than opening a second,
        unmetered path to the venue.

        THE OVERRIDE IS NOT A GENERAL INJECTION POINT. A review observed that
        an unconstrained `http=` would let any in-process caller pass a
        general `_SignedHTTP()` and submit while disarmed and shadow-locked,
        quietly retiring the invariant that this is "the single door every
        venue request goes through". It adds no capability such a caller did
        not already have, but it does move the restraint out of the object
        graph, which is the inversion this file's own transports exist to
        prevent. So only the transport's OWN cancel object is accepted; any
        other value is refused.
        """
        if http is not None and http is not self.__cancel_http:
            raise PermissionError(
                "_request http override is limited to this transport's own "
                "cancel-only object")
        write = str(method).upper() != "GET"
        self.limiter.before(write=write)
        try:
            out = (http or self.http).request(method, path, body, base)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if is_rate_limited(exc):
                self.limiter.on_429()
            raise
        self.limiter.on_success()
        return out

    def _get(self, path: str, base: str = Q1.API) -> Any:
        return self._request("GET", path, None, base)

    def account(self) -> Dict[str, Any]:
        """/v1/account/balances is the route this venue serves.

        An earlier version read /v1/account, /v1/positions and /v1/orders and
        got 404, 404 and 501. The 501 is the tell: a collection that exists
        but does not answer GET. Those are the paths pmus_q1_executor has
        always used for live campaigns; these now match it.
        """
        return self._get("/v1/account/balances") or {}

    def market(self, slug: str) -> Dict[str, Any]:
        """Authoritative market METADATA, which /bbo does not carry.

        Resolution lives in `resolve_market` so that the preflight running
        on the credential-holding recorder reads the same way; see there for
        which resource answers and why.
        """
        return resolve_market(self.http, slug)

    def book(self, slug: str) -> Dict[str, Any]:
        r = self._get("/v1/markets/%s/book" % slug, base=Q1.GATEWAY) or {}
        return r.get("marketData") or r

    def settlement(self, slug: str) -> Dict[str, Any]:
        """What the venue says this market finally settled at, if anything.

        /v1/markets/{slug}/bbo is the route that carries `settlementPx` and
        `state` together; it answered 200 for the Probe 08 market on
        2026-09-26T14:37Z with state MARKET_STATE_EXPIRED and settlementPx
        1.0000. `terminal` is true only when BOTH a terminal state and a
        price are present -- never on the state alone.
        """
        r = self._get("/v1/markets/%s/bbo" % slug) or {}
        md = r.get("marketData") or r
        state = str((md or {}).get("state") or "")
        px = settlement_decimal((md or {}).get("settlementPx"))
        # ONLY the venue's own `settlementPx` field. The last price, the bid,
        # the ask and the mid are all on this same document and none of them
        # is a settlement: Stage A booked a phantom credit because 0.01 --
        # the price the expired book still carried -- reached the ledger as
        # if it were a payout. A binary contract pays 0 or 1, so a
        # `settlementPx` that is neither is not final, whatever the state
        # says, and this reports it as NOT terminal.
        binary = px is not None and px in (D(0), D(1))
        named = None
        for key in ("slug", "ticker", "marketSlug", "market"):
            v = (md or {}).get(key) or r.get(key)
            if isinstance(v, str) and v.strip():
                named = v.strip()
                break
        matched = named is None or named == slug
        reason = None
        if state not in TERMINAL_MARKET_STATES:
            reason = "STATE_NOT_TERMINAL"
        elif px is None:
            reason = "SETTLEMENT_ABSENT"
        elif not binary:
            reason = "SETTLEMENT_NOT_BINARY"
        elif not matched:
            reason = "RESOLUTION_MARKET_MISMATCH"
        return {"market": slug, "state": state,
                "settlement_px": px,
                "not_terminal_reason": reason,
                "resolution": {"source": "VENUE_SETTLEMENT_PX",
                               "market": named or slug,
                               "market_named": named,
                               "state": state,
                               "yes_value": (str(px) if px is not None
                                             else None)},
                "terminal": reason is None}

    #: endpoints that could carry a stable account identifier. Probed
    #: read-only and in order; the FIRST that yields one wins. This list is
    #: not a guess at what exists -- an unreachable path simply contributes
    #: nothing and is reported, so the gate stays failed until the venue
    #: actually hands us an identifier.
    IDENTITY_PATHS = ("/v1/account", "/v1/account/profile", "/v1/me",
                      "/v1/user", "/v1/account/info")

    #: keys that count as an account identifier wherever they appear
    IDENTITY_KEYS = ("accountId", "account_id", "userId", "user_id",
                     "id", "email", "walletAddress", "proxyWallet")

    @staticmethod
    def _find_identity(doc: Any, depth: int = 0) -> Optional[str]:
        """The first identifier-shaped field, anywhere in the document."""
        if depth > 4 or not isinstance(doc, (Mapping, list)):
            return None
        if isinstance(doc, list):
            for item in doc:
                got = PmusTransport._find_identity(item, depth + 1)
                if got:
                    return got
            return None
        for k in PmusTransport.IDENTITY_KEYS:
            v = doc.get(k)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
        for v in doc.values():
            got = PmusTransport._find_identity(v, depth + 1)
            if got:
                return got
        return None

    @staticmethod
    def outcome_names(mk: Mapping[str, Any]) -> tuple:
        """UP/DOWN as this venue reports them.

        `outcomes` arrives either as a JSON-ENCODED STRING, a list of plain
        names, or a list of mappings, depending on the resource. All three
        are read; nothing is inferred when none parses.
        """
        raw = mk.get("outcomes")
        if raw is None:
            raw = mk.get("tokens")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = [x.strip() for x in raw.split(",") if x.strip()]
        if not isinstance(raw, (list, tuple)):
            return ()
        out = []
        for o in raw:
            if isinstance(o, Mapping):
                v = o.get("outcome") or o.get("name") or o.get("title")
            else:
                v = o
            if v is not None:
                out.append(str(v).strip().upper())
        return tuple(out)

    @staticmethod
    def key_fingerprint() -> Optional[str]:
        """A stable, non-secret handle for the credential in use.

        sha256 of the API KEY ID, truncated. The key id is not a secret in
        the way the private key is, but it is never printed raw: a
        fingerprint is enough to pin "the same account as last time" and
        cannot be replayed.
        """
        import hashlib
        kid = os.getenv(Q1.KEY_ENV, "").strip()
        if not kid:
            return None
        return hashlib.sha256(kid.encode()).hexdigest()[:12]

    @staticmethod
    def buying_power(balances: Mapping[str, Any]) -> Optional[D]:
        """Account-scoped funds, from the venue's own answer.

        Schema per pmus_q1_executor, which has placed and settled real orders
        against it: /v1/account/balances returns `balances` as a LIST of
        entries carrying buyingPower; older mapping and top-level shapes are
        still accepted.
        """
        entries: List[Any] = []
        raw = balances.get("balances") if isinstance(balances, Mapping) else None
        if isinstance(raw, list):
            entries.extend(raw)
        elif isinstance(raw, Mapping):
            entries.append(raw)
        if isinstance(balances, Mapping):
            entries.append(balances)
        for e in entries:
            if not isinstance(e, Mapping):
                continue
            for k in ("buyingPower", "availableBalance", "cashBalance"):
                v = e.get(k)
                if isinstance(v, Mapping):
                    v = v.get("value") or v.get("amount")
                if v is None:
                    continue
                try:
                    return D(str(v))
                except Exception:                              # noqa: BLE001
                    continue
        return None

    def account_binding(self, balances: Mapping[str, Any]) -> Optional[str]:
        """What the venue actually binds this credential to.

        THIS VENUE EXPOSES NO ACCOUNT IDENTITY ENDPOINT. The working
        execution stack -- pmus_q1_executor, which has placed and settled
        real orders -- never fetches one: its preflight proves the account by
        reading ACCOUNT-SCOPED FINANCIAL STATE, buying power plus positions
        plus open orders. The credential IS the binding.

        So the gate asserts the thing that is real and checkable: a SIGNED
        request returned account-scoped funds. That is strictly more than an
        opaque id string would prove, because a wrong or unfunded account
        cannot produce it. It is paired with a fingerprint of the key id so
        the owner can pin "the same account as before" across runs.

        An earlier version demanded an `accountId` the venue does not serve
        and failed with a null detail. That was asking for something that
        does not exist, not detecting a problem.
        """
        self.identity_probe = {"mechanism": "account_scoped_financial_state"}
        power = self.buying_power(balances)
        fp = self.key_fingerprint()
        self.identity_probe.update({
            "buying_power_readable": power is not None,
            "buying_power_usd": (str(power) if power is not None else None),
            "api_key_fingerprint": fp,
            "note": ("this venue serves no identity endpoint; the working "
                     "execution stack binds the account the same way"),
        })
        if power is None or fp is None:
            return None
        return "keyfp:%s|funded:%s" % (fp, power)

    def account_identity(self, balances: Mapping[str, Any]) -> Optional[str]:
        """An identifier the VENUE gave us, or None -- never one we made up.

        `/v1/account/balances` is proven readable but carried no identifier in
        its top level or its balances entries, so the gate failed with a null
        detail and no way to tell whether the field was missing or merely
        somewhere else. This searches the whole balances document first, then
        probes the candidate account endpoints read-only. `identity_probe`
        records exactly what each one answered, so the next run binds to a
        real field instead of another guess.
        """
        self.identity_probe = {"balances_keys": sorted(
            [str(k) for k in balances.keys()])[:20] if isinstance(
                balances, Mapping) else None}
        got = self._find_identity(balances)
        if got:
            self.identity_probe["source"] = "/v1/account/balances"
            return got
        for path in self.IDENTITY_PATHS:
            try:
                doc = self._get(path) or {}
            except Exception as exc:                           # noqa: BLE001
                self.identity_probe[path] = "%s" % type(exc).__name__
                continue
            keys = sorted([str(k) for k in doc.keys()])[:20] \
                if isinstance(doc, Mapping) else "non-mapping"
            self.identity_probe[path] = {"ok": True, "keys": keys}
            got = self._find_identity(doc)
            if got:
                self.identity_probe["source"] = path
                return got
        return None

    def positions(self) -> List[dict]:
        """The account's position ROWS, whichever shape the venue used.

        LIVE 2026-09-25T13:40Z, this account: /v1/portfolio/positions answers
        {"positions": {}, "availablePositions": [], "nextCursor": "",
        "eof": true} -- `positions` is an OBJECT keyed by market slug, not an
        array. Empty, `{}` is falsy and the old `or []` yielded a list, so
        this never showed. Probe 04 was the first close-out ever to run while
        the account actually held something: the dict came back populated,
        `for p in positions` yielded string keys, and `p["market"]` raised
        TypeError -- which is why that probe could not reconcile.

        The mapping-keyed-by-slug form is not inferred here. It is what
        `pmus_settlement_auditor._rows_of` has read against real fills and
        settlements on this venue, and this matches that behaviour rather
        than inventing a second reading of the same resource.
        """
        return rows_of(self._get("/v1/portfolio/positions"), "positions")

    def available_positions(self) -> List[dict]:
        """`availablePositions`, which the venue serves beside `positions`.

        Kept separate and never merged: the settlement auditor treats it as
        explicitly NOT a held position, and collapsing the two would inflate
        what we think we own.
        """
        return rows_of(self._get("/v1/portfolio/positions"),
                       "availablePositions")

    def order(self, order_id: str) -> Dict[str, Any]:
        """One order, as the VENUE currently has it. GET, never a mutation.

        /v1/order/{id} answered 200 for CQ5NPBADMYC3 on 2026-09-25T13:40Z
        with the row below; /v1/orders/{id} 404s and /v1/orders 501s, so this
        is the route. The fields Probe 04 needed and never asked for:

            state "ORDER_STATE_FILLED", quantity 2.25, cumQuantity 2.25,
            leavesQuantity 0, avgPx {"value": "0.2900"},
            commissionNotionalTotalCollected {"value": "0.0300"},
            lastTransactTime "2026-09-25T13:01:46.847706680Z"

        The ACK for that order had said filled_qty 0. It filled two
        milliseconds later and nothing ever asked again.
        """
        r = self._get("/v1/order/%s" % order_id) or {}
        if isinstance(r, Mapping):
            inner = r.get("order")
            return dict(inner) if isinstance(inner, Mapping) else dict(r)
        return {}

    def open_orders(self) -> List[dict]:
        r = self._get("/v1/orders/open") or {}
        if isinstance(r, Mapping):
            return r.get("orders") or r.get("openOrders") or []
        return r or []

    # ------------------------------------------------------------ preflight

    def preflight(self, *, expect_account: Optional[str] = None,
                  known_positions: Optional[Mapping[str, Any]] = None
                  ) -> Dict[str, Any]:
        """Every gate, read-only. Any failure leaves the transport DISARMED."""
        checks: List[Dict[str, Any]] = []

        def gate(name: str, ok: bool, detail: Any = None) -> bool:
            checks.append({"check": name, "passed": bool(ok),
                           "detail": redact(detail) if detail is not None
                           else None})
            return bool(ok)

        ok = True
        # 1. credentials PRESENT (never their value)
        ok &= gate("credentials_present", Q1.has_credentials(),
                   "env %s / %s" % (Q1.KEY_ENV, Q1.SECRET_ENV))

        # 2. authenticated account identity, from the venue's own answer
        #
        # NOT CACHED, and the attempt to cache it is worth recording.
        #
        # The plan was to cache this read because "does this key bind this
        # account" is invariant between windows. It is -- but that is not
        # all this read carries. account_binding_proven is computed from the
        # BALANCE, and identity_probe reports buying_power_usd from it, and
        # the balance moves every time the account trades or settles. Six
        # tests in test_l5_pmus_transport caught it immediately: they vary
        # the funds and expect preflight to say what the venue says now.
        #
        # Every read preflight makes is like that. account, market, book and
        # positions are all assertions about NOW; the checks that are
        # genuinely invariant -- credentials_present, slug_is_btc15m_series,
        # outcome_mapping_is_up_down -- make no venue call at all. There is
        # nothing here to cache that would not weaken the gate, so the cost
        # is bounded by asking LESS OFTEN instead: PREFLIGHT_BACKOFF_S.
        # That alone takes a failing preflight from the measured 0.63 req/s
        # to under 0.1, which is the whole of what the cache was for.
        acct: Dict[str, Any] = {}
        if ok:
            try:
                acct = self.account()
            except Exception as exc:                           # noqa: BLE001
                return self._fail(checks, gate, "account_readable", exc)
            ident = self.account_binding(acct)
            ok &= gate("account_binding_proven", bool(ident), ident)
            if expect_account is not None:
                # pin the FINGERPRINT, not the whole binding: the binding
                # carries buying power, which moves every time the account
                # trades or settles, so comparing it would break the pin for
                # a reason that has nothing to do with identity.
                fp = self.key_fingerprint()
                ok &= gate("account_matches_expected",
                           fp is not None and str(fp) == str(expect_account),
                           "expected fingerprint %s, got %s"
                           % (expect_account, fp))

        # 3/4. intended series and current market slug
        ok &= gate("slug_is_btc15m_series",
                   bool(BTC15M_SLUG_RE.match(self.market_slug or "")),
                   self.market_slug)
        mk: Dict[str, Any] = {}
        if ok:
            try:
                mk = self.market(self.market_slug)
            except Exception as exc:                           # noqa: BLE001
                return self._fail(checks, gate, "market_readable", exc)
            ok &= gate("market_resolves", bool(mk), mk.get("slug"))
            ok &= gate("market_series_is_btc15m",
                       BTC15M_SERIES in str(mk.get("seriesSlug")
                                            or mk.get("slug") or ""),
                       mk.get("seriesSlug") or mk.get("slug"))

        # 5. open / tradable. This resource reports tradability as
        # active/closed/archived rather than a MARKET_STATE_* enum, so both
        # are accepted -- and a market that does not positively say it is
        # open still fails, which is the direction that matters.
        state = str(mk.get("state") or mk.get("status") or "")
        live = state.upper().endswith("OPEN")
        if not live and ("active" in mk or "closed" in mk):
            live = bool(mk.get("active")) and not bool(mk.get("closed")) \
                and not bool(mk.get("archived"))
        ok &= gate("market_open_tradable", live,
                   {"state": state or None, "active": mk.get("active"),
                    "closed": mk.get("closed"),
                    "archived": mk.get("archived")})

        # 6. outcome mapping
        names = self.outcome_names(mk)
        # 6. outcome mapping, established by an EVIDENCE CHAIN rather than
        # by the venue happening to spell its sides "Up" and "Down". It does
        # not: this market's sides are Yes/No, and the live preview of
        # 2026-09-24T14:40Z returned outcomeSide OUTCOME_SIDE_YES as
        # intent ORDER_INTENT_BUY_LONG. The published contract supplies the
        # other link -- "One binary instrument per window. Long side pays
        # when the close is at or above the open" -- so long == Up. Requiring
        # the literal labels tested the venue's vocabulary, not the mapping.
        from src import l5_order_body as PS
        chain = {"contract_long_is_up": PS.STATIC_CONTRACT["yes_is"] == "UP",
                 "venue_yes_is_long": True,
                 "venue_side_labels": names,
                 "yes": "OUTCOME_SIDE_YES", "no": "OUTCOME_SIDE_NO",
                 "provenance": [p["url"] for p in
                                PS.STATIC_CONTRACT["provenance"]],
                 "note": "venue labels are Yes/No; Up/Down is the contract's "
                         "own vocabulary and is not required on the wire"}
        ok &= gate("outcome_mapping_is_up_down",
                   chain["contract_long_is_up"] and chain["venue_yes_is_long"],
                   chain)

        # 7. tick and quantity constraints, read from the MARKET not assumed
        tick = mk.get(Q1.TICK_FIELD) or mk.get("tickSize")
        minq = (mk.get("orderMinQuantity") or mk.get("minimumTradeQty")
                or mk.get("minOrderSize"))
        ok &= gate("tick_constraint_known", tick is not None, tick)
        ok &= gate("quantity_constraint_known", minq is not None, minq)
        # Keep the constraints, not just the verdict that they were readable.
        # Probe 03 passed both of these gates and then sent
        # 0.4066877541798463 against an increment of 0.01: the numbers were
        # READ and never APPLIED. They are the venue's own, from this
        # market's metadata, and are never assumed.
        self.tick_size = _as_decimal(tick)
        self.quantity_increment = _as_decimal(minq)

        # 8. authenticated book / read connectivity
        bk: Dict[str, Any] = {}
        book_attempts: List[str] = []
        if ok:
            # BOUNDED RETRY ON A TRANSPORT FAILURE, not on an empty book.
            # Observed live: book_readable raised HTTP 404 at 02:45:02Z and
            # 02:45:13Z on cpc-btc-updown-15m-2026-10-16-0245z and then
            # succeeded at 02:48:50Z -- the venue does not serve the book for
            # the first minutes of a window. Each failure set
            # armed_after_preflight false, so arming could not be scheduled
            # deterministically (preflight_attempts reached 9).
            #
            # WHAT THIS DOES NOT DO: it does not make an empty book
            # executable. A 404 means the ROUTE did not answer; an answered
            # but empty book still flows into book_readable_now and
            # book_two_sided_at_preflight below exactly as before, and the
            # binding two-sided check remains at the decision point in
            # l5_preview_gate.require_fresh_book. Exhausting the retries
            # still fails the preflight.
            import time as _time
            last_exc: Optional[BaseException] = None
            for attempt in range(1, BOOK_READ_ATTEMPTS + 1):
                try:
                    bk = self.book(self.market_slug)
                    book_attempts.append("attempt%d:ok" % attempt)
                    last_exc = None
                    break
                except Exception as exc:                       # noqa: BLE001
                    last_exc = exc
                    book_attempts.append("attempt%d:%s" % (
                        attempt, type(exc).__name__))
                    if attempt < BOOK_READ_ATTEMPTS:
                        _time.sleep(BOOK_READ_BACKOFF_S * attempt)
            if last_exc is not None:
                sub = self._ws_book_substitute(last_exc)
                if sub is None:
                    return self._fail(checks, gate, "book_readable", last_exc)
                bk = {"bid": sub["bid"], "ask": sub["ask"]}
                book_attempts.append("ws_fallback:accepted")
                detail = dict(sub)
                detail["rest_error"] = "%s: %s" % (
                    type(last_exc).__name__, redact(last_exc))
                detail["note"] = (
                    "the REST book route 404ed for its whole retry budget; "
                    "the authenticated socket is serving a fresh two-sided "
                    "book for this same market, which is what this gate "
                    "asks. Executability is still decided at the decision "
                    "point by l5_preview_gate.require_fresh_book.")
                checks.append({
                    "check": "book_readable_via_authenticated_ws",
                    "passed": True, "blocking": False, "detail": detail})
            checks.append({"check": "book_read_attempts", "passed": True,
                           "blocking": False, "detail": book_attempts})
        # A periodic preflight runs hundreds of seconds into a window and
        # can meet a book that is already dying; that says nothing about
        # whether a LATER order, decided at a different moment, is
        # executable. Executability belongs to the decision point, where
        # l5_preview_gate.require_fresh_book enforces a fresh two-sided
        # authenticated book before anything is transmitted. Here the check
        # is reachability of the book, reported non-blocking so a thinning
        # late-window book cannot condemn a future fresh order.
        ok &= gate("book_readable_now", bool(bk), {"bid": bk.get("bid"),
                                                   "ask": bk.get("ask")})
        checks.append({"check": "book_two_sided_at_preflight",
                       "passed": bool(bk.get("bid")) and bool(bk.get("ask")),
                       "blocking": False,
                       "detail": {"bid": bk.get("bid"), "ask": bk.get("ask"),
                                  "note": "informational; the binding check "
                                          "is at the decision point"}})

        # 9. venue positions / open orders reconciled
        try:
            pos = self.positions()
            oo = self.open_orders()
        except Exception as exc:                               # noqa: BLE001
            return self._fail(checks, gate, "positions_readable", exc)
        ok &= gate("no_unexpected_open_orders", len(oo) == 0, len(oo))
        if known_positions is not None:
            venue = {"%s|%s" % (p.get("market"), p.get("side")): str(p.get("qty"))
                     for p in pos}
            drift = {k: {"ours": str(v), "venue": venue.get(k, "0")}
                     for k, v in known_positions.items()
                     if str(v) != venue.get(k, "0")}
            ok &= gate("positions_reconciled", not drift, drift)

        # 10. starts disarmed, and the owner arm is a separate act
        ok &= gate("executor_starts_disarmed", self.armed is False)
        owner_armed = Q1.armed()
        checks.append({"check": "owner_arm_token_present",
                       "passed": owner_armed, "blocking": False,
                       "detail": "arming is a separate owner act; preflight "
                                 "passing does NOT arm"})

        report = {
            "preflight": "PASSED" if ok else "FAILED",
            "utc": self._now().isoformat(),
            "market_slug": self.market_slug,
            "checks": checks,
            "worst_case": self.worst_case_from_venue(mk),
            "identity_probe": self.identity_probe,
            "armed_after_preflight": False,
            "labels": ("read-only; no credential value appears anywhere in "
                       "this report"),
        }
        self.preflight_report = report
        if not ok:
            raise PreflightFailed(json.dumps(report))
        return report

    def _ws_book_substitute(self, exc: BaseException
                            ) -> Optional[Dict[str, Any]]:
        """The authenticated socket book, IF it can stand in for a 404.

        Returns None -- meaning "no substitute, fail the preflight" -- unless
        every one of these holds, because each is part of what makes the
        socket book equivalent evidence rather than a way around the gate:

          * the REST failure was a 404, so the ROUTE did not answer for a
            market the venue has otherwise confirmed open. Any other status,
            and any non-HTTP error, is a genuine read failure.
          * a supplier was injected, and it yields a snapshot. `snapshot()`
            returns None unless the socket is CONNECTED and the book is
            TWO-SIDED, so a quiet or half-served book cannot qualify.
          * the last frame on that socket is recent, so a connected-but-dead
            stream cannot qualify either.
          * the market state the socket reports, when it reports one, is
            OPEN. An unopened or closed market is never tradable, which is
            the one thing this must not be able to assert.

        It never raises: a supplier that throws is simply no substitute.
        """
        if int(getattr(exc, "code", 0) or 0) not in BOOK_READ_404_CODES:
            return None
        src = self.ws_book
        if src is None:
            return None
        try:
            snap = src(self.market_slug) if callable(src) else src.snapshot(
                self.market_slug)
        except BaseException as inner:                         # noqa: BLE001
            if isinstance(inner, (KeyboardInterrupt, SystemExit)):
                raise
            return None
        if not snap:
            return None
        bid, ask = snap.get("bid"), snap.get("ask")
        if bid is None or ask is None:
            return None
        # AND THE SOCKET MUST HAVE BEEN READY BEFORE THE WINDOW OPENED.
        # A socket that subscribed LATE has only the frames sent since it
        # arrived; its two-sided book is then a partial view, and "the
        # socket has this market's book" is a weaker claim than the REST
        # route answering. The subscription is deterministic -- the slug is
        # a function of the window grid and the observer subscribes ahead of
        # open -- so requiring it costs nothing when preparation worked and
        # refuses the substitution when it did not. Unavailable is NOT
        # permission: an observer that cannot report it is no substitute.
        pre = None
        if not callable(src):
            try:
                pre = (src.window_report(self.market_slug) or {}).get(
                    "subscribed_before_open")
            except BaseException as inner:                     # noqa: BLE001
                if isinstance(inner, (KeyboardInterrupt, SystemExit)):
                    raise
                pre = None
        else:
            pre = snap.get("subscribed_before_open")
        if pre is not True:
            return None
        age = snap.get("stream_age_s")
        if age is None or float(age) > WS_BOOK_FALLBACK_MAX_AGE_S:
            return None
        # AN ALLOWLIST, NOT A SUBSTRING. "OPEN" in "MARKET_STATE_UNOPENED" is
        # True, so substring matching would have accepted the one state this
        # is here to refuse -- a market that has not opened yet. The venue's
        # own value for an open 15m market is MARKET_STATE_OPEN; anything
        # else, including a value we do not recognise, is no substitute.
        st = snap.get("market_state")
        if st is not None and str(st).upper() not in WS_BOOK_OPEN_STATES:
            return None
        return {"bid": bid, "ask": ask,
                "bid_shares": snap.get("bid_shares"),
                "ask_shares": snap.get("ask_shares"),
                "subscribed_before_open": True,
                "stream_age_s": age,
                "max_stream_age_s": WS_BOOK_FALLBACK_MAX_AGE_S,
                "book_updates": snap.get("book_updates"),
                "market_state": st,
                "source": snap.get("source") or "authenticated_ws"}

    def _fail(self, checks, gate, name, exc) -> Dict[str, Any]:
        gate(name, False, "%s: %s" % (type(exc).__name__, redact(exc)))
        report = {"preflight": "FAILED", "checks": checks,
                  "market_slug": self.market_slug,
                  "armed_after_preflight": False}
        self.preflight_report = report
        raise PreflightFailed(json.dumps(report))

    def worst_case_from_venue(self, mk: Mapping[str, Any]) -> Dict[str, Any]:
        """Worst case from the ACTUAL constraints, not from an estimate.

        Principal: every share can settle worthless, so the cap on committed
        dollars IS the principal at risk. Fee: the venue's own taker formula
        is maximised at p = 0.5, so the bound is computed there against the
        largest contract count the cap can fund at that price.
        """
        from src import pmus_fees as fees
        # THE ENFORCED CAP, NOT A LITERAL. This was `D("5")`, hardcoded, so
        # the preflight's worst case was decoupled from the limit actually in
        # force: an owner who tightened L5_MAX_CASH_DEPLOYED_USD would still
        # be shown $5 principal and $5.1738 total, and the preflight is the
        # evidence a campaign is activated on. Unreadable or absent falls back
        # to $5, which is the value it always reported.
        cap = D("5")
        cap_source = "default"
        raw = str(os.getenv("L5_MAX_CASH_DEPLOYED_USD", "")).strip()
        if raw:
            try:
                v = D(raw)
                if v > 0:
                    cap, cap_source = v, "L5_MAX_CASH_DEPLOYED_USD"
            except (ArithmeticError, ValueError):
                cap_source = "unreadable_env_default_5"
        # THE FEE IS MAXIMISED AT THE CHEAPEST ADMISSIBLE PRICE, NOT AT 0.50.
        #
        # This read `worst_p = D("0.5")`, on the reasoning that the venue's
        # taker formula Theta*C*p*(1-p) peaks at p = 0.5. It does -- PER
        # CONTRACT. Under a fixed PRINCIPAL the contract count is cap/p, so
        #
        #     fee = Theta * (cap/p) * p * (1-p) = Theta * cap * (1-p)
        #
        # which is monotone DECREASING in p and therefore maximised at the
        # cheapest price an order may use. On a $5 principal that is $0.3301
        # at the 0.05 band floor, not $0.1738 -- so the figure a campaign is
        # activated on understated the worst case by 90%.
        worst_p = FEE_FLOOR_PRICE
        contracts = cap / worst_p
        fee = fees.taker_fee(worst_p, contracts)
        # format(..., "f") not str(): Decimal renders 5/0.5 as "1E+1",
        # which is correct and unreadable in a report a human has to check
        # before money is at risk.
        f = lambda d: format(D(str(d)), "f")
        return {
            "max_committed_usd": f(cap),
            "max_committed_source": cap_source,
            "fee_maximising_price": f(worst_p),
            "fee_maximising_basis": ("cheapest admissible price: under a "
                                     "fixed principal, contracts = cap/p and "
                                     "fee = Theta*cap*(1-p), monotone "
                                     "decreasing in p"),
            "contracts_at_that_price": f(contracts),
            "max_taker_fee_usd": f(fee),
            "worst_case_total_usd": f(cap + fee),
            # SAID PLAINLY, because the two numbers were read as one and the
            # difference is real money. The cash gate counts PRINCIPAL only:
            # `committed` is own_price * qty, `pending_obligation` is
            # qty * price (or qty for a short), and the proposed order is
            # q * px -- no term carries a fee. So the configured ceiling
            # bounds principal and the taker fee is charged ON TOP of it.
            "fee_is_outside_the_cash_ceiling": True,
            "basis_fees": ("max_committed_usd is the PRINCIPAL ceiling the "
                           "cash gate enforces; max_taker_fee_usd is charged "
                           "in addition to it, so worst_case_total_usd is "
                           "the figure to compare against an all-in limit. "
                           "To hold the all-in total at X, set "
                           "L5_MAX_CASH_DEPLOYED_USD to "
                           "X / (1 + Theta*(1-floor_p))."),
            "tick_from_market": str(mk.get(Q1.TICK_FIELD)
                                    or mk.get("tickSize")),
            "basis": ("principal is the committed cap because a share can "
                      "settle worthless; no leverage exists on this venue"),
        }

    # ------------------------------------------------------------ mutations

    def _require_armed(self) -> None:
        if not self.armed:
            raise NotArmed(
                "transport is DISARMED. Preflight status: %s; owner arm: %s"
                % ((self.preflight_report or {}).get("preflight", "NOT_RUN"),
                   "present" if Q1.armed() else "absent"))

    def arm(self) -> Dict[str, Any]:
        """Arm ONLY if preflight passed in this process AND the owner armed."""
        pf = (self.preflight_report or {}).get("preflight")
        if pf != "PASSED":
            raise NotArmed("preflight has not passed in this process (%s)" % pf)
        if not Q1.armed():
            raise NotArmed("owner arm token absent or does not match the "
                           "session id (%s / %s / %s)"
                           % (LIVE_ENV, SID_ENV, ARM_ENV))
        if not self._injected:
            # only now does an object capable of POST exist in this process
            self.http = _SignedHTTP()
        self.armed = True
        return {"armed": True, "market": self.market_slug,
                "preflight": pf,
                "note": "arming is revocable by unsetting the owner token"}

    def disarm(self, reason: str = "") -> Dict[str, Any]:
        """Give up the arm, and the ability to POST with it.

        `arm()` is what puts a POST-capable object in this process; leaving
        that object in place while merely clearing a boolean would keep the
        capability and rely on the flag being checked everywhere. So this
        puts the read-only transport back, which raises on any method but
        GET. An injected transport is left alone -- it belongs to the caller.

        Always permitted, and idempotent: it can only ever REMOVE capability,
        so no gate guards it and nothing it does can fail open.
        """
        was = bool(self.armed)
        self.armed = False
        if not self._injected:
            self.http = Q1.ReadOnlyTransport()
        return {"disarmed": True, "was_armed": was,
                "market": self.market_slug,
                "reason": str(reason)[:120],
                "post_capable": False}

    def submit(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        """Preview, verify the venue's echo, then send THE SAME body.

        The body is built once by the shared serializer and used for both
        calls, so what the venue validated is byte-identical to what it
        receives. If the previewed order differs from the proposal in any
        execution-critical field -- market, side, action, price, quantity,
        type, time in force -- this REFUSES. It never reprices and never
        retries: the venue was observed silently changing an off-tick price
        to a different one rather than rejecting it, and an order resting at
        a price the engine did not choose is worse than no order.

        Book freshness is the caller's to establish at the decision point
        (see l5_preview_gate.require_fresh_book); it is not inferred here.
        """
        self._require_armed()
        from src import l5_order_body as OB
        from src import l5_preview_gate as PG
        # Which call was in flight. Probe 03 raised somewhere in here and the
        # record could not say where: str(HTTPError) is "HTTP Error 400: Bad
        # Request" and str(URLError) is "<urlopen error timed out>" -- neither
        # carries the path, so the exception alone cannot tell a failed
        # /v1/order/preview from a failed /v1/orders. These are read by the
        # executor when submit() raises; they change no control flow.
        self.submit_stage = "SERIALIZE"
        self.preview_attempted = False
        self.preview_status = None
        self.order_post_attempted = False
        body = OB.from_intent(intent, self.market_slug)
        self.submit_stage = "PREVIEW_REQUEST"
        self.preview_attempted = True
        pv = self._request("POST", "/v1/order/preview",
                               OB.preview_envelope(body), Q1.API) or {}
        self.preview_status = 200          # urlopen raises on any non-2xx
        self.submit_stage = "PREVIEW_ECHO_VALIDATION"
        self.last_preview = {"body": body, "response": pv,
                             "echo": PG.require_echo(body, pv)}
        self.submit_stage = "ORDER_POST"
        self.order_post_attempted = True
        return self._request("POST", "/v1/orders", body, Q1.API) or {}

    def cancel(self, order_id: str,
               market_slug: Optional[str] = None) -> Dict[str, Any]:
        """Cancel by the VENUE order id, via the route the venue serves.

        POST /v1/order/{id}/cancel, not DELETE /v1/orders/{id}. The argument
        is the venue's own order id returned by submit, not our deterministic
        client order id -- passing the latter would silently cancel nothing.

        THE BODY MUST CARRY `marketSlug`, AND AN EMPTY BODY IS WHY THE
        2026-10-18 PROOF FAILED. Order D68KZ0SG10JR rested for 530s while the
        watchdog and then this process both posted `{}` to the correct route
        and were both refused with HTTP 400 Bad Request; the order left only
        when the market expired. `pmus_q1_executor.cancel_exact` -- the only
        cancel in this repository ever used against a live PMUS order -- has
        always sent `{"marketSlug": slug}` to the same route with the same
        method and the same signer. The field was the whole difference.
        Signing is untouched: PMUS signs ts+method+path and not the body.

        THE SLUG IS THE ORDER'S, NOT THE TRANSPORT'S. An orphan sweep runs
        after `roll` has moved on, so `self.market_slug` can name a different
        window than the order being cancelled; callers pass the market their
        ledger row recorded, and `self.market_slug` is only the fallback.
        An empty slug REFUSES rather than posting `{}`, because posting `{}`
        is the exact failure this change exists to prevent.
        """
        # CANCEL DOES NOT REQUIRE ARMING, AND THAT IS THE WHOLE POINT.
        #
        # THE DEFECT THIS REPLACES, observed live on 2026-10-14 at the 2230z
        # window. `_require_armed()` stood here, so cancel raised NotArmed on
        # a DISARMED transport -- and disarmed is exactly the state every
        # orphan path runs in. The authorization expiry cancelled while armed
        # and the venue refused with HTTPError; the transport then disarmed;
        # and the three unarmed orphan sweeps that followed could not even
        # ASK, each failing `cancel_error: NotArmed` on a real resting order
        # with $0.2744 of exposure. What finally cleared it was the venue
        # expiring the order at market close, which our re-read observed --
        # not any withdrawal of ours.
        #
        # Both locks exist to gate SUBMISSION. `ShadowOnlyTransport` already
        # says so in its own docstring: "CANCEL IS DELIBERATELY NOT BLOCKED.
        # Cancelling only ever reduces exposure, and a lock that prevented it
        # could strand a resting order." That principle was true at the proxy
        # and false one layer below it.
        #
        # OWNERSHIP IS STILL STRICT, and is enforced here rather than trusted
        # of the caller. `cancel_owner_oracle` is the durable ledger's
        # `owns_venue_order_id`: an id this book never recorded is refused, so
        # a manual order the owner placed by hand can never be cancelled by
        # this process, armed or not. With no oracle installed the transport
        # cannot establish ownership and refuses -- absent is not permission.
        oid = str(order_id or "").strip()
        if not oid:
            raise CancelRefused("NO_VENUE_ORDER_ID")
        if not Q1.has_credentials():
            raise CancelRefused("NO_CREDENTIALS")
        slug = str(market_slug or getattr(self, "market_slug", "") or "").strip()
        if not slug:
            raise CancelRefused("NO_MARKET_SLUG:%s" % oid[:40])
        own = getattr(self, "cancel_owner_oracle", None)
        if own is None:
            # NO ORACLE. An ARMED transport cancels as it always did: it has
            # already proved preflight and the owner token, this is the
            # status quo, and REFUSING to cancel is itself the failure this
            # whole change exists to prevent -- a path that merely forgot the
            # wiring must not lose the ability to withdraw an order.
            #
            # A DISARMED transport is the new capability, so it is the one
            # that must prove ownership. Without an oracle it cannot, and
            # there it refuses: absent is not permission for a NEW power.
            if self.armed:
                return self._cancel_post(oid, slug)
            raise CancelRefused(
                "OWNERSHIP_UNESTABLISHED: this transport is disarmed and no "
                "ledger oracle is installed, so it cannot prove the order is "
                "ours")
        try:
            ours = bool(own(oid))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CancelRefused(
                "OWNERSHIP_UNREADABLE:%s" % type(exc).__name__)
        if not ours:
            raise CancelRefused("NOT_OUR_ORDER:%s" % oid[:40])
        return self._cancel_post(oid, slug)

    def _cancel_post(self, oid: str, slug: str) -> Dict[str, Any]:
        """Send the one cancel POST, through the route-restricted object.

        BOTH BRANCHES, not just the disarmed one. The first draft sent the
        ARMED cancel through `self.http`, which is a general `_SignedHTTP`,
        so the armed path -- the one that runs with the owner's token -- was
        the LOOSER of the two: a review drove
        `/v1/order/X/cancel/../../v1/orders/cancel` and `/v1/order/A B/cancel`
        straight through it. The ledger supplies the id, so that was not
        reachable in practice, but the fix was already in hand. Routing both
        through `_CancelOnlyHTTP` makes the docstring's claim -- no
        market-scoped and no account-wide route -- true of the whole cancel
        path instead of half of it.

        An INJECTED http (a test fake, or any wrapper that passed one) is
        still honoured as the sender, so nothing is bypassed by this repair.

        THE ONE PLACE THE CANCEL BODY IS BUILT, so it cannot drift between
        the armed and the oracle path. On failure the SANITIZED venue
        response is recorded on the instance BEFORE the error propagates:
        callers log only `type(exc).__name__`, which is how the schema
        rejection of 2026-10-18 read as an opaque "HTTPError" for 530s.
        """
        path = "/v1/order/%s/cancel" % oid
        if self.__cancel_http is None:
            self.__cancel_http = _CancelOnlyHTTP(
                owns=lambda: getattr(self, "cancel_owner_oracle", None))
        self.last_cancel_error = None
        try:
            return self._request("POST", path, {"marketSlug": slug}, Q1.API,
                                 http=self.__cancel_http) or {}
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            try:
                self.last_cancel_error = dict(
                    http_error_detail(exc),
                    venue_order_id=oid, market_slug=slug,
                    endpoint="/v1/order/{id}/cancel", method="POST")
            except BaseException:                              # noqa: BLE001
                self.last_cancel_error = {"error": type(exc).__name__}
            raise
