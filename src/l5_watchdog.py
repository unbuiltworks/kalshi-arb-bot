"""The independent watchdog: the one process that can stop the executor.

WHY THIS EXISTS AS A SEPARATE PROCESS. `src/l5_halt_gate.py` has been the
right shape for a while -- it fails closed on a present halt, an unreachable
store, an unparseable record and a stale one, and `HaltAsserter` has no code
path that clears -- but nothing ever ASSERTED it. A latch with no hand on it
is documentation. `analysis/l5/CLAUDE.md` is explicit that the watchdog
"must be independent of the trading process and able to act or fail safe, not
merely print logs", and until this module there was no actor.

THE VENUE IS THE SHARED STATE, AND THAT IS THE WHOLE DESIGN. The executor has
no volume the watchdog can read and a Railway volume attaches to one service,
so the two cannot share a filesystem; a heartbeat channel would have to be
built and could itself wedge. It is not needed. The thing the owner actually
cares about -- AN UNMANAGED RESTING ORDER -- is visible to anyone with
read-only credentials at `GET /v1/orders/open`. An order still open past the
deadline the executor promised to cancel it by IS the wedged-executor signal,
read from the authority rather than from the suspect. A dead executor, a
hung one, a crashed container and a lost cancel all present identically, and
all are caught.

TWO POWERS, DELIBERATELY ASYMMETRIC.

  * ASSERT. Writes the halt record the executor reads over HTTP. One
    direction only: toward refusing to trade. `HaltAsserter` cannot clear,
    and neither can this. Clearing is an owner action with the credentials
    directly.
  * CANCEL EXACTLY. Cancels the venue ids it actually observed resting, one
    at a time, never a market-scoped or account-wide sweep -- this PMUS
    account is SHARED, so `POST /v1/orders/open/cancel {"slugs": [...]}`
    would cancel another desk's orders, and that mechanism is the reason an
    earlier engine fingerprint was superseded. A cancel ack proves nothing:
    every id is re-read and reported by its venue-confirmed terminal state.

WHAT IT CANNOT DO, STRUCTURALLY. `CancelOnlyTransport` permits GET and
exactly one POST route, `/v1/order/{id}/cancel`. It has no submit path, no
preview path, no market-scoped cancel and no account sweep -- not disabled by
a flag, absent. It also never reads an arming key, because refusing to clean
up because a flag is unset is the dangerous direction.

AND IT NEVER UNWINDS A FILL. A cancel removes what has not traded. Shares
that already filled are inventory with a cost basis, reported as residual
and left for the ordinary settlement path. This module does not trade out of
a position at any price.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

# THE SIGNING MODULE IS OPTIONAL ON PURPOSE. The credential-free watchdog
# image omits src/pmus_auth.py entirely, so this module must import without
# it. CancelOnlyTransport is the only thing that needs it and refuses to be
# constructed when it is absent -- which is the point: an image with no
# signing code cannot reach the venue however it is configured.
try:
    from src import pmus_auth as A              # type: ignore
except Exception:                               # noqa: BLE001
    A = None                                    # type: ignore

#: the only mutating route this watchdog may ever use.
CANCEL_ROUTE = re.compile(r"^/v1/order/[A-Za-z0-9_-]+/cancel\Z")
#: \Z, not $: `$` also matches immediately BEFORE a trailing newline, so
#: "/v1/order/A1/cancel\n" would be admitted by `$` and is refused by \Z.
#: Production (claude/gemini-btc-economic-viability-h3u7zl) already had the
#: stricter anchor; this brings the two branches together on the tighter one.

#: venue states that mean the order is no longer working. Checked against
#: leavesQuantity first, because "PARTIALLY_FILLED" contains "FILLED" and a
#: substring match would retire an order that is still resting.
TERMINAL_HINTS = ("CANCEL", "REJECT", "EXPIRED", "FILLED")

#: how long an order may rest before the watchdog treats the executor as
#: wedged. The executor's own cancellation deadline must be comfortably
#: inside this, or the watchdog will race it and cancel a healthy order.
DEFAULT_MAX_ORDER_AGE_S = 300.0


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_t(value: Any) -> Optional[dt.datetime]:
    """The venue's RFC3339, including the nine-digit fractions it sends."""
    s = str(value or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*\.\d{6})\d*(\+\d\d:\d\d)$", s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        out = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=dt.timezone.utc)


class MutationRefused(PermissionError):
    """Raised for any write this transport does not structurally permit."""


class CancelOnlyTransport:
    """Signed HTTP that can GET, and can POST exactly one cancel route.

    The refusal lives in the transport rather than in a caller's flag,
    exactly as `pmus_auth.ReadOnlyTransport` does, because a process that
    cannot construct a submit cannot be argued into one.
    """

    name = "CANCEL_ONLY"

    def __init__(self, opener=None) -> None:
        #: injected in tests; production goes through pmus_auth
        if A is None and opener is None:
            raise RuntimeError(
                "CancelOnlyTransport is unavailable: src/pmus_auth.py is not "
                "in this image, so no request can be signed. A credential-"
                "free watchdog cancels nothing; cancellation belongs to the "
                "executor under its own credentials.")
        self._opener = opener

    def request(self, method: str, path: str,
                body: Optional[Mapping[str, Any]] = None,
                base: Optional[str] = None) -> Any:
        # `base` is resolved HERE, not in the signature. As a default argument
        # `A.API` is evaluated at class-definition time, so this module could
        # not even be IMPORTED without the signing code -- which defeats the
        # credential-free image entirely. Found by
        # test_the_entrypoint_boots_with_the_image_closure_only.
        if base is None:
            if A is None:
                raise MutationRefused(
                    "no venue base: this image carries no signing code")
            base = A.API
        m = str(method).upper()
        if m == "GET":
            return self._send("GET", path, None, base)
        if m == "POST" and CANCEL_ROUTE.match(str(path) or ""):
            return self._send("POST", path, body or {}, base)
        raise MutationRefused(
            "%s %s refused: this transport may only GET, or POST a single "
            "/v1/order/{id}/cancel" % (m, path))

    def _send(self, method: str, path: str, body, base: str) -> Any:
        if self._opener is not None:
            return self._opener(method, path, body, base)
        import ssl
        import urllib.request
        data = None if body is None else json.dumps(body).encode()
        # THE THIRD ARGUMENT IS `now_ms`, NOT A BODY. pmus_auth.signed_headers
        # is (method, path, now_ms=None), so passing the cancel body here set
        # the TIMESTAMP to the body: a cancel signed over "{}"+POST+path and
        # sent with X-PM-Timestamp: "{}" -- a guaranteed 401, meaning the
        # independent cancel would have reported an error and left the order
        # resting. It was never caught because credential-free mode never
        # constructs this transport, so the only POST this class can make had
        # no test that signed it. PMUS signs ts+method+path and does NOT sign
        # the body, so there is nothing to pass.
        headers = A.signed_headers(method, path) \
            if (A is not None and base == A.API) \
            else {"User-Agent": "l5-watchdog"}
        req = urllib.request.Request(base + path, data=data, headers=headers,
                                     method=method)
        with urllib.request.urlopen(
                req, context=ssl.create_default_context(), timeout=15) as r:
            return json.loads(r.read() or b"{}")

    # the two verbs the watchdog actually uses, named for what they mean
    #: Transient venue codes worth one bounded retry on a READ-ONLY GET, and
    #: the backoff that `pmus_q1_executor` already proved in production. Kept
    #: as local constants rather than imported so this image stays
    #: credential-light, and pinned equal to that module's values by
    #: `test_the_retry_budget_matches_the_proven_executor_values`.
    TRANSIENT_CODES = (404, 429, 500, 502, 503, 504)
    GET_RETRY_MAX = 5
    GET_RETRY_SLEEP_S = 1.0
    GET_RETRY_BACKOFF = 2.0

    def open_orders(self) -> List[Dict[str, Any]]:
        """The venue's open orders, with the SAME bounded retry every other
        caller of this endpoint already uses.

        THE 2026-11-05 FAILURE. This was a bare single request, and it is the
        only caller of `/v1/orders/open` in the repository that was. Every
        working one wraps it -- `pmus_q1_executor._get_with_retry`,
        `pmus_micro_executor`, `pmus_aws_supervisor` -- because the venue
        rate-limits this read; that module's own note records a live 429 whose
        "rate budget recovers on the order of a minute: the same read cleared
        70 s later". Polling it bare every 20 s, alongside the executor, meant
        every sweep reported `read_error: "HTTPError"`, never heartbeated, and
        the halt record aged past HaltGate's 900 s bound -- which refused the
        executor at HALT_GATE and blocked the campaign.

        PATIENCE IS 15 s PER SWEEP, NOT 31, and that is deliberate. This
        sleeps between attempts but not after the last one, so the budget is
        1+2+4+8; `pmus_q1_executor` sleeps after its final attempt too and so
        documents 31. The difference does not matter here and the shorter one
        is better: that module gets ONE pass at the read, whereas this sweep
        repeats every L5_WATCHDOG_POLL_S, giving well over a hundred
        further attempts
        inside HaltGate's 900 s staleness bound. Resilience comes from the
        loop, not from one long stall that would also lag the sweep.

        FAIL-CLOSED IS UNCHANGED. Retries apply ONLY to transient codes on
        this read-only GET. A non-transient code raises immediately, and an
        exhausted budget raises too -- so the caller still records
        `read_error`, still does NOT heartbeat, and a genuinely blind watchdog
        still stops the executor by omission. A retry must never become an
        attestation, and no mutation is retried anywhere.
        """
        last: Optional[BaseException] = None
        for attempt in range(self.GET_RETRY_MAX):
            try:
                body = self.request("GET", "/v1/orders/open") or {}
                rows = body.get("orders") if isinstance(body, Mapping) else None
                return [r for r in (rows or []) if isinstance(r, Mapping)]
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                code = getattr(exc, "code", None)
                if code not in self.TRANSIENT_CODES:
                    raise
                last = exc
                if attempt == self.GET_RETRY_MAX - 1:
                    break
                self._retry_sleep(
                    self.GET_RETRY_SLEEP_S * (self.GET_RETRY_BACKOFF ** attempt))
        # EXHAUSTED IS STILL BLIND. Re-raise the venue's own error so the
        # sweep records it, including its status, and declines to attest.
        raise last if last is not None else RuntimeError("open_orders_failed")

    #: overridable so tests do not actually sleep 31 seconds.
    _retry_sleep = staticmethod(time.sleep)

    def order(self, order_id: str) -> Dict[str, Any]:
        body = self.request("GET", "/v1/order/%s" % order_id) or {}
        row = body.get("order") if isinstance(body, Mapping) else None
        return dict(row) if isinstance(row, Mapping) else dict(body or {})

    def cancel(self, order_id: str, market_slug: str) -> Any:
        """By the VENUE's id, with the order's own market in the body.

        THE EMPTY BODY WAS THE DEFECT. On 2026-10-18 this method posted `{}`
        to the correct route with a correct signature and the venue refused
        every attempt with HTTP 400 Bad Request, for 530 seconds, on a real
        resting order. `pmus_q1_executor.cancel_exact` -- the only cancel in
        this repository ever used against a live PMUS order -- sends
        `{"marketSlug": slug}` to the same route. Route, method, identifier
        and signer were already identical; the field was the difference.

        THE SIGNATURE IS NOT AFFECTED. PMUS signs ts+method+path and does NOT
        sign the body (see `_send`), so carrying a field changes nothing about
        how this request is authenticated.

        An absent slug REFUSES. Falling back to `{}` would silently restore
        the exact failure this change removes.
        """
        oid = str(order_id or "").strip()
        if not oid:
            raise MutationRefused("cancel needs a venue order id")
        slug = str(market_slug or "").strip()
        if not slug:
            raise MutationRefused(
                "cancel needs the order's market slug: the venue refuses an "
                "empty cancel body with HTTP 400")
        return self.request("POST", "/v1/order/%s/cancel" % oid,
                            {"marketSlug": slug})


#: header- or signature-shaped text, redacted out of any venue response we
#: log. A RESPONSE should never carry a credential; this exists so that a
#: venue which echoes a request header into an error message cannot put it
#: in our logs either.
#: THE VALUE RUNS TO A DELIMITER, NOT TO THE FIRST SPACE: "authorization:
#: Bearer abc123" must not leave abc123 behind.
_SECRETISH = re.compile(
    r"(?i)\b(authorization|x-pm-access-key|x-pm-key|x-pm-signature"
    r"|x-pm-timestamp|api[-_]?key|secret|signature|token|bearer|password)"
    r"\b\s*[:=]\s*[^\"',}\]\n\r]+")


def http_error_detail(exc: BaseException) -> Dict[str, Any]:
    """A SANITIZED record of what the venue actually said, or why we cannot.

    `str(HTTPError)` is exactly "HTTP Error 400: Bad Request" and carries
    neither path nor body, so this sweep could only ever record
    `cancel_error: "HTTPError"` -- which is what left the 2026-10-18 cancel
    400 undiagnosed for a whole authorized window.

    Every step is guarded: reading a response body can raise on a closed
    stream, and a diagnostic must never replace the error it describes.
    """
    out: Dict[str, Any] = {"error": type(exc).__name__}
    code = getattr(exc, "code", None)
    if code is not None:
        out["status"] = code
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
        out["body"] = _SECRETISH.sub(r"\1: [REDACTED]", body)[:400]
    return out


def is_terminal(row: Mapping[str, Any]) -> bool:
    leaves = row.get("leavesQuantity")
    if leaves is not None:
        try:
            return float(str(leaves)) <= 0
        except (TypeError, ValueError):
            pass
    return any(h in str(row.get("state") or "").upper()
               for h in TERMINAL_HINTS)


def order_age_s(row: Mapping[str, Any], *, now: Optional[dt.datetime] = None
                ) -> Optional[float]:
    t = _parse_t(row.get("createTime") or row.get("insertTime"))
    if t is None:
        return None
    return ((now or _utc()) - t).total_seconds()


#: a report older than this means the executor has stopped publishing, which
#: is the wedged signal itself. Deliberately a small multiple of the sweep
#: interval: the executor republishes on every heartbeat.
DEFAULT_REPORT_MAX_AGE_S = 120.0


class RegistryOrderSource:
    """The executor's own GET-only report, shaped like `open_orders()`.

    WHY THIS EXISTS, AND WHAT IT COSTS. The ONLY reason this watchdog ever
    needed venue credentials was to read `/v1/orders/open` and judge the
    executor from the authority. Removing that one input removes the
    credential entirely -- the latch, the GET-only server and the attestation
    touch no venue at all -- so the image can carry no request-signing code.
    That is permission-level isolation achieved by holding no credential to
    scope, which is stronger than any route restriction.

    WHAT IS GIVEN UP, STATED PLAINLY: independent verification. This watchdog
    believes the executor's own report, so it is a LIVENESS detector, not an
    auditor. An executor that is alive and wrong is believed. The venue-side
    audit stays with the services that are structurally read-only and already
    hold credentials.

    A STALE REPORT IS THE STRONGER SIGNAL. An executor that has stopped
    publishing has stopped managing, whatever its last orders looked like --
    and that is detectable without seeing the venue. `readable: false` is the
    same: the executor says its own ledger is unreadable, which is not a
    state to keep trading through.

    There is NO cancel method here, by construction, and no `request()`:
    cancellation belongs to the executor under its own credentials.
    """

    DEFAULT_TIMEOUT_S = 3.0
    name = "REGISTRY_READ_ONLY"

    def __init__(self, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 opener=None) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("RegistryOrderSource needs a url")
        self.url = url.strip().rstrip("/")
        self.timeout_s = float(timeout_s)
        self._opener = opener
        self._last: Optional[Dict[str, Any]] = None

    def fetch(self) -> Dict[str, Any]:
        """The report, or RAISE. A failure must reach the sweep's read_error
        path rather than become an empty list -- an unreadable report is not
        evidence of an empty book."""
        import urllib.request
        from src import l5_order_registry_server as REG
        url = "%s/%s" % (self.url, REG.REGISTRY_KEY.lstrip("/"))
        if self._opener is not None:
            raw = self._opener(url, self.timeout_s)
        else:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                if getattr(r, "status", 200) != 200:
                    raise RuntimeError("registry_http_%s" % getattr(r, "status", "?"))
                raw = r.read()
        rec = json.loads(raw)
        if not isinstance(rec, Mapping):
            raise RuntimeError("registry_malformed")
        if rec.get("readable") is not True:
            # the executor itself says its ledger is unknown
            raise RuntimeError("registry_not_readable")
        self._last = dict(rec)
        return self._last

    def open_orders(self) -> List[Dict[str, Any]]:
        """Rows in the shape `sweep()` already consumes.

        `t` is the ACK time the executor recorded, mapped onto `createTime`
        so `order_age_s` needs no special case. `leavesQuantity` is
        deliberately ABSENT rather than invented: the registry publishes only
        orders its ledger still believes live, and `is_terminal` then falls
        through to the state hint, which is also absent -- so every published
        row is treated as working, which is the conservative reading.
        """
        rec = self.fetch()
        out: List[Dict[str, Any]] = []
        for r in (rec.get("orders") or []):
            if not isinstance(r, Mapping):
                continue
            out.append({"id": str(r.get("venue_order_id") or ""),
                        "marketSlug": str(r.get("market") or ""),
                        "createTime": str(r.get("t") or "") or None,
                        "session_id": str(r.get("session_id") or "")})
        return out

    def report_age_s(self, *, now: Optional[dt.datetime] = None
                     ) -> Optional[float]:
        """Age of the LAST fetched report. None means not establishable,
        which the caller must treat as stale, never as fresh."""
        rec = self._last or {}
        t = _parse_t(rec.get("as_of_utc"))
        if t is None:
            return None
        return ((now or _utc()) - t).total_seconds()


AUTOMATIC = "MANUAL_ORDER_INDICATOR_AUTOMATIC"
#: The venue's EXPLICIT manual literal. Only this exact value exempts an order
#: from the age latch (owner 2026-11-18: the owner's own resting manual
#: orders latched UNMANAGED_RESTING_ORDER at 16:02:13Z and stopped every
#: lane). Absent, empty, unparseable or any other value still latches: the
#: cancellation guard's `!= AUTOMATIC` test is correct for "do not cancel"
#: but would be FAIL-OPEN for "do not latch", so it is deliberately not
#: reused here.
MANUAL = "MANUAL_ORDER_INDICATOR_MANUAL"


class OwnershipOracle:
    """Reads the executor's own durable claim of which ids are its own.

    THE OWNER'S CORRECTION, IN CODE. Account-wide open orders plus an age test
    is not ownership on a SHARED account: it would have cancelled the owner's
    manual orders, D23N1EHTTZ7D among them. Ownership has two independent
    requirements here and both must hold:

      1. the id appears in the executor's durable live-order record, served
         read-only by `l5_order_registry_server` off the volume the executor
         writes its ACKs to;
      2. the VENUE's own row for that id agrees on the market and reports
         `manualOrderIndicator` AUTOMATIC.

    (1) without (2) means our record disagrees with the venue, which is
    uncertain identity. (2) without (1) is any other automated desk on this
    account. Neither is sufficient, which is why neither alone is accepted.

    FAILING TO READ IS FAILING TO OWN. An unreachable registry, an unparseable
    body, or a registry reporting `readable: false` all yield NO owned ids, so
    the watchdog latches the halt and cancels nothing. That is the safe
    direction: an uncancelled order with the executor already halted is
    recoverable by hand; somebody else's cancelled order is not.
    """

    DEFAULT_TIMEOUT_S = 3.0

    #: A CLAIM OF UNKNOWN AGE IS NOT OWNERSHIP PROOF. `claimed()` used to
    #: accept any document whose `readable` was true, at any age. An executor
    #: that CRASHES takes its registry server with it, so that case already
    #: failed closed on an unreachable read -- but a WEDGED executor (process
    #: alive, writer stalled) keeps serving, and its last snapshot would have
    #: been believed indefinitely. The registry publishes `as_of_utc` for
    #: exactly this reason, and `RegistryOrderSource` already bounds it at
    #: DEFAULT_REPORT_MAX_AGE_S before latching EXECUTOR_REPORT_STALE; the
    #: ownership path simply did not. The two paths now use the same bound,
    #: because "the executor has stopped publishing" must not be read as
    #: "the executor confirms these ids are mine".
    MAX_CLAIM_AGE_S = DEFAULT_REPORT_MAX_AGE_S
    #: the two services keep their own clocks; a small negative age is skew,
    #: not a forged future timestamp. The sweep routinely reports -0.011s.
    CLOCK_SKEW_S = 5.0
    #: THE PROCESS-DEATH CASE (owner 2026-11-23: "prove process-death
    #: cancellation"). An executor whose whole process is gone serves no
    #: registry, so by the rule above nothing it left resting is
    #: cancellable until it comes back. L5_WATCHDOG_REMEMBER_CLAIMS_S > 0
    #: bounds that: when the registry cannot be REACHED AT ALL, the ids of
    #: its last good, fresh claim are still treated as claimed for that
    #: many seconds after that claim was read. An order id never changes
    #: owner, and requirement (2) -- the venue's own row agreeing on the
    #: market and reporting AUTOMATIC -- is checked exactly as before, so
    #: this cannot reach a manual order or another desk's. It applies ONLY
    #: to an unreachable registry: one that answers but is stale, unreadable
    #: or says otherwise is alive, and its own statement stands.
    #: Default 0 = off (failing to read is failing to own, as before).
    REMEMBER_CLAIMS_S = 0.0
    #: HOW SOON (owner 2026-11-23: "a five-minute worst-case cancellation
    #: delay is too long for volatile in-game markets ... investigate
    #: whether it can be shortened without compromising account ownership
    #: protections"). The ordinary age rule waits `max_order_age_s` because
    #: a LIVE executor is expected to manage a young order itself. An
    #: executor whose registry has been unreachable for DEAD_AFTER_S is
    #: managing nothing, so its remembered ids are selected for cancel at
    #: once, whatever their age. Ownership is unchanged: only ids of its own
    #: last good claim, and the venue's row must still agree. A planned
    #: restart cancels its own orders on the way down, so there is normally
    #: nothing left to select. Only meaningful with REMEMBER_CLAIMS_S > 0.
    DEAD_AFTER_S = 30.0

    @staticmethod
    def _dead_after_s() -> float:
        import os
        try:
            return max(0.0, float(os.environ.get(
                "L5_WATCHDOG_DEAD_AFTER_S", "")
                or OwnershipOracle.DEAD_AFTER_S))
        except ValueError:
            return OwnershipOracle.DEAD_AFTER_S

    def dead_ids(self) -> set:
        """Ids of an executor that has been unreachable long enough to be
        treated as gone: its last good claim, while that is still inside
        the memory bound. Empty when the registry answers, when memory is
        off, or before DEAD_AFTER_S."""
        import time as _time
        since = getattr(self, "_unreachable_since", None)
        last = getattr(self, "_last_good", None)
        keep = self.REMEMBER_CLAIMS_S or self._remember_s()
        if since is None or last is None or keep <= 0:
            return set()
        now = _time.monotonic()
        if now - since < self._dead_after_s() or now - last[0] > keep:
            return set()
        return set(last[1])

    @staticmethod
    def _remember_s() -> float:
        import os
        try:
            return max(0.0, float(os.environ.get(
                "L5_WATCHDOG_REMEMBER_CLAIMS_S", "") or 0.0))
        except ValueError:
            return 0.0

    def _remembered(self, why: str) -> Dict[str, Dict[str, Any]]:
        """The last good claim, while it is still inside the bound."""
        keep = self.REMEMBER_CLAIMS_S or self._remember_s()
        last = getattr(self, "_last_good", None)
        if keep <= 0 or last is None or not last[1]:
            return {}
        import time as _time
        age = _time.monotonic() - last[0]
        if age > keep:
            return {}
        self.last_refusal = ("%s; USING_LAST_GOOD_CLAIM:%d ids read %.1fs ago"
                             % (why, len(last[1]), age))
        return {k: dict(v) for k, v in last[1].items()}

    def __init__(self, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 max_claim_age_s: Optional[float] = None,
                 now_fn=None, opener=None) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("OwnershipOracle needs a url")
        self.url = url.strip().rstrip("/")
        self.timeout_s = float(timeout_s)
        self.max_claim_age_s = float(self.MAX_CLAIM_AGE_S
                                     if max_claim_age_s is None
                                     else max_claim_age_s)
        self._now = now_fn
        self._opener = opener
        #: why the last read yielded nothing, for the sweep to report. A
        #: silent {} cannot be told apart from "we genuinely own nothing".
        self.last_refusal: Optional[str] = None

    def _age_s(self, doc: Mapping[str, Any]) -> Optional[float]:
        """Seconds since the registry built this document, or None."""
        raw = str(doc.get("as_of_utc") or "").strip()
        if not raw:
            return None
        try:
            t = dt.datetime.fromisoformat(raw)
        except BaseException:                                  # noqa: BLE001
            return None
        if t.tzinfo is None:
            return None            # a naive stamp cannot be compared safely
        now = (self._now() if self._now is not None
               else dt.datetime.now(dt.timezone.utc))
        return (now - t).total_seconds()

    def claimed(self) -> Dict[str, Dict[str, Any]]:
        """{venue_order_id: row} the executor says it owns. {} on any doubt."""
        from src import l5_order_registry_server as REG
        self.last_refusal = None
        url = "%s/%s" % (self.url, REG.REGISTRY_KEY)
        try:
            if self._opener is not None:
                raw = self._opener(url, self.timeout_s)
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                    code = getattr(r, "status", 200)
                    if code != 200:
                        # A 2xx that is not 200 does NOT raise in urllib, so
                        # this returned {} with no refusal recorded -- which
                        # reads as "we own nothing" when the fact is "we
                        # could not find out". Those are opposite facts and
                        # claim_refused exists so they cannot arrive alike.
                        self.last_refusal = "REGISTRY_HTTP:%s" % code
                        return {}
                    raw = r.read()
            doc = json.loads(raw or b"{}")
        except BaseException as exc:                           # noqa: BLE001
            # A CRASHED executor lands here: its registry server dies with
            # the process, so the read fails and ownership is unknown.
            self.last_refusal = "REGISTRY_UNREACHABLE:%s" % type(exc).__name__
            import time as _time
            if getattr(self, "_unreachable_since", None) is None:
                self._unreachable_since = _time.monotonic()
            return self._remembered(self.last_refusal)
        self._unreachable_since = None       # it answered: it is not gone
        if not isinstance(doc, dict) or doc.get("readable") is not True:
            self.last_refusal = "REGISTRY_NOT_READABLE"
            return {}
        age = self._age_s(doc)
        if age is None:
            self.last_refusal = "CLAIM_AGE_UNREADABLE"
            return {}
        if age < -self.CLOCK_SKEW_S:
            self.last_refusal = "CLAIM_IN_THE_FUTURE:%.1fs" % age
            return {}
        if age > self.max_claim_age_s:
            self.last_refusal = ("CLAIM_STALE:%.1fs>%.1fs"
                                 % (age, self.max_claim_age_s))
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for row in (doc.get("orders") or []):
            if not isinstance(row, Mapping):
                continue
            vid = str(row.get("venue_order_id") or "").strip()
            if vid:
                out[vid] = dict(row)
        import time as _time
        self._last_good = (_time.monotonic(), {k: dict(v)
                                               for k, v in out.items()})
        return out


class Watchdog:
    """One sweep: look, latch, cancel exactly, verify.

    `cancel_enabled` is off by default. A watchdog that can only assert is
    still useful -- it stops the next order -- and the cancel power is the
    one that touches a shared account, so it is opt-in and its absence is
    reported rather than silent.
    """

    def __init__(self, transport: Any, asserter: Any, *,
                 max_order_age_s: float = DEFAULT_MAX_ORDER_AGE_S,
                 report_max_age_s: float = DEFAULT_REPORT_MAX_AGE_S,
                 cancel_enabled: bool = False, who: str = "l5-watchdog",
                 oracle: Any = None,
                 managed_lanes: Optional[List[str]] = None,
                 lane_opener: Any = None) -> None:
        self.t = transport
        #: OTHER LANES ON THE SHARED ACCOUNT that rest maker orders for
        #: minutes BY DESIGN and attest, freshly, that they are managing them
        #: (OWNER 2026-11-14: sports two-sided and BTC5m run together). See
        #: `managed_lane_ids`.
        self.managed_lanes = [u.strip().rstrip("/") for u in
                              (managed_lanes or []) if u and u.strip()]
        self._lane_opener = lane_opener
        self.asserter = asserter
        self.max_order_age_s = float(max_order_age_s)
        self.report_max_age_s = float(report_max_age_s)
        self.who = str(who)
        self.oracle = oracle
        #: NO ORACLE, NO CANCEL -- and not as a default that a flag can
        #: override. Without a durable ownership record there is nothing that
        #: distinguishes our order from the owner's manual one, so the cancel
        #: power is not merely off, it is unavailable.
        self.cancel_enabled = bool(cancel_enabled) and oracle is not None
        self.cancel_requested = bool(cancel_enabled)

    #: A lane attestation older than this is not proof of management.
    LANE_MAX_AGE_S = 60.0

    def managed_lane_ids(self, now: Optional[dt.datetime] = None) -> tuple:
        """({venue_order_id, ...}, [notes]) attested by a FRESH managed lane.

        DELEGATES to `l5_managed_lanes`, which is the single definition
        shared with the executor's preflight and the reset ceremony's venue
        precondition. Behaviour is unchanged: a lane qualifies only if its
        document is readable, says `manages_own_resting: true`, and its
        `as_of_utc` is at most MAX_AGE_S old. Anything else -- unreachable,
        stale, malformed -- contributes NOTHING, so a dead lane's orders are
        judged by age exactly as before. This EXEMPTS from the overdue/age
        latch only; it never makes an order cancellable (ownership is still
        the oracle's).
        """
        from src import l5_managed_lanes as ML
        return ML.lane_ids(self.managed_lanes, opener=self._lane_opener,
                           now=now or _utc())

    def emergency_state(self) -> tuple:
        """(emergency, reason). Is a HALT latched right now?

        Read from the SAME record the executor's gate reads, so the two
        cannot disagree about whether the system is stopped.

        AN UNREADABLE RECORD IS NOT AN EMERGENCY, and that is a deliberate
        narrowing. The trigger is a CONFIRMED latch, because cancelling is
        not purely risk-reducing here: pulling a resting COMPLETE leg strands
        the residual it was about to hedge, which is the exact shape of the
        1415z harm. Firing that automatically on every transient GET failure
        would be a worse failure mode than the gap it closes.

        Nothing is lost by waiting for a readable answer. The executor's own
        gate already refuses to trade on an unestablished latch, so the
        system is stopped either way; this watchdog re-reads every sweep, so
        a real latch fires the emergency path on the next successful read;
        and the age-based path below is untouched and still catches the
        order on its own deadline. The cost is bounded delay, and it is
        reported rather than silent.

        The first version of this returned (True, ...) on a read failure. It
        was wrong twice over: it made every stub-asserter sweep an emergency,
        and -- because the emergency branch does not re-assert -- it would
        have cancelled the book WITHOUT ever latching a halt, losing the very
        record of why the system stopped.
        """
        try:
            from src import l5_halt_gate as HG
            kind, rec = HG._classify(self.asserter.store)
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return (False, "HALT_LATCH_READ_FAILED:" + type(exc).__name__)
        if kind == "UNREADABLE":
            return (False, "HALT_LATCH_UNREADABLE_NOT_EMERGENCY")
        if kind == "PRESENT" and rec is not None and rec.get("halt") is not False:
            return (True, "HALT_LATCHED:" + str(rec.get("reason") or "?")[:80])
        return (False, None)

    def registry_notes(self) -> List[str]:
        """One `url:STATUS:count` note per configured ownership registry.

        ONE READ PER REGISTRY PER SWEEP, shared with the cancel path.
        The first version read separately and broke the invariant that
        `test_the_ownership_registry_is_read_once_per_sweep` exists to
        protect -- a second read lets the answer change mid-sweep, and it
        would also let the sweep REPORT one ownership and ACT on
        another, which is how two records of one truth start disagreeing.
        So the claims read here are memoised for this sweep and
        `_cancel_targets` consumes them.

        This exists so that "the registry is unreachable" is visible on a
        quiet sweep instead of surfacing for the first time during an
        incident.

        `OK:n` means the registry answered and claims n ids -- n may
        legitimately be 0 when that lane holds nothing. Any other status
        is the registry's own refusal code, or PROBE_RAISED when the read
        itself failed. It never raises: an observable that can fail a
        sweep is worse than no observable.
        """
        if self.oracle is None:
            return []
        #: ONE READ, through the oracle the deployment actually
        #: configured. Reading each member separately would bypass
        #: `CompositeOracle.claimed`, which is what sets the aggregated
        #: `last_refusal` the sweep already reports -- so the per-member
        #: loop below only READS the state that read just set.
        rows: Dict[str, Dict[str, Any]] = {}
        try:
            got = self.oracle.claimed()
            if isinstance(got, Mapping):
                rows = dict(got)
            self._claims_this_sweep = (
                rows, getattr(self.oracle, "last_refusal", None))
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            #: the memo stays unset, so the cancel path reads for itself
            #: rather than acting on an ownership we failed to obtain
            return ["%s:PROBE_RAISED:%s" % (str(getattr(self.oracle, "url", "?")),
                                            type(exc).__name__)]
        notes: List[str] = []
        for o in oracles_of(self.oracle):
            why = getattr(o, "last_refusal", None)
            notes.append("%s:%s" % (str(getattr(o, "url", "?")), why or "OK"))
        return notes

    def _claimed_once(self) -> Dict[str, Dict[str, Any]]:
        """Ownership for THIS sweep, from the single read the probe made.

        Falls back to reading the oracle when no probe ran -- the
        emergency HALT path reaches `_cancel_targets` without a sweep, and
        it must never be left without ownership.
        """
        memo = getattr(self, "_claims_this_sweep", None)
        if memo is not None:
            return memo[0]
        return self.oracle.claimed()

    def _claim_refusal(self) -> Optional[str]:
        memo = getattr(self, "_claims_this_sweep", None)
        if memo is not None:
            return memo[1]
        return getattr(self.oracle, "last_refusal", None)

    def sweep(self, *, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
        #: WAS `now` GIVEN, or is it ours? The lane probe below needs to
        #: know, because a freshness check must measure against the clock
        #: AT THE MOMENT OF THE READ -- see `managed_lane_ids`. Tests inject
        #: `now` and depend on it being used; production does not.
        injected_now = now is not None
        now = now or _utc()
        #: THE PER-SWEEP OWNERSHIP MEMO STARTS EMPTY. Carrying one sweep's
        #: claims into the next would be worse than reading twice: it
        #: would cancel against ownership that is a poll interval old.
        self._claims_this_sweep = None
        out: Dict[str, Any] = {
            "LABEL": "L5_WATCHDOG_SWEEP", "as_of_utc": now.isoformat(),
            "max_order_age_s": self.max_order_age_s,
            "cancel_enabled": self.cancel_enabled,
            "open_orders": 0, "overdue": [], "halted": None,
            "cancelled": [], "still_open": [], "unverified": [],
            "not_ours": [], "read_error": None,
        }
        try:
            rows = self.t.open_orders()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # A watchdog that cannot see is not evidence of safety. It says
            # so and does NOT assert -- a halt asserted on a failed read
            # would latch the system on every transient network error, and
            # the executor's own gate already refuses when IT cannot read.
            # NOT heartbeated: the positive attestation is deliberately
            # left to go stale. HaltGate's only path to True is a FRESH
            # "no halt", so a watchdog that has gone blind stops the
            # executor by omission within max_age_s -- without latching the
            # system on a single transient network error.
            out["read_error"] = type(exc).__name__
            # WHAT THE VENUE ACTUALLY SAID, sanitized. `read_error` alone is
            # the exact gap `http_error_detail` was written for on the CANCEL
            # path -- its own docstring records that `cancel_error:
            # "HTTPError"` left the 2026-10-18 cancel 400 undiagnosed for a
            # whole authorized window -- and this read path never called it.
            # On 2026-11-05 that cost the same thing again: every sweep from
            # at least 11:55 reported `read_error: "HTTPError"` with no
            # status, so the halt record went stale, the executor refused at
            # HALT_GATE, and Campaign F could not be activated or diagnosed.
            #
            # REPORTING ONLY, and deliberately ADDITIVE: `read_error` keeps
            # its exact previous value and shape so nothing reading it
            # changes, and the three lines above this one still decide. The
            # sweep still does NOT heartbeat and still returns here, so a
            # blind watchdog still stops the executor by omission within
            # max_age_s. A diagnostic must never become a permission.
            try:
                out["read_error_detail"] = http_error_detail(exc)
            except BaseException:                              # noqa: BLE001
                out["read_error_detail"] = {"error": "DETAIL_UNAVAILABLE"}
            return out
        out["open_orders"] = len(rows)
        # ---- EMERGENCY HALT CANCELLATION -------------------------------
        # A LATCHED HALT MAKES EVERY RESTING ORDER UNMANAGED, WHATEVER ITS
        # AGE. The age-based path below answers a different question -- "has
        # the executor stopped managing this one?" -- and answers it slowly
        # on purpose, because a young resting maker order is normal. Once a
        # halt is latched there is no executor still managing anything: it is
        # refusing to trade, and on the owner-marker path it may be stopped
        # outright. So the whole known book becomes cancellable immediately.
        #
        # This is why it is a SEPARATE path and not a smaller
        # `max_order_age_s`: lowering that would cancel legitimately resting
        # orders during normal trading, which is the one thing it must not do.
        emergency, why = self.emergency_state()
        out["emergency_cancel"] = bool(emergency)
        out["emergency_reason"] = why
        if emergency:
            targets = []
            for r in rows:
                if is_terminal(r):
                    continue
                age = order_age_s(r, now=now)
                targets.append({
                    "venue_order_id": str(r.get("id") or ""),
                    "market": str(r.get("marketSlug") or ""),
                    "age_s": age,
                    "age_known": age is not None,
                    "leaves": str(r.get("leavesQuantity")),
                    # the age is REPORTED but was not a condition: this is
                    # what distinguishes an emergency target from an overdue
                    # one in the record.
                    "selected_by": "EMERGENCY_HALT_LATCHED",
                })
            out["emergency_targets"] = len(targets)
            out["overdue"] = []
            # NO assert_halt: the latch is already set, and re-asserting
            # would overwrite the original reason and evidence -- losing the
            # record of WHY the system stopped. NO heartbeat either: this
            # branch never attests "no halt".
            if not targets:
                out["emergency_note"] = (
                    "halt is latched and the known book is empty; nothing "
                    "to cancel")
                return out
            return self._cancel_targets(targets, out)
        # A STALE REPORT IS THE WEDGED SIGNAL, and a stronger one than any
        # order's age: an executor that has stopped publishing has stopped
        # managing, whatever its last orders looked like. Only a source that
        # reports its own age is checked, so the venue-reading transport is
        # unaffected. None means not establishable, which is stale.
        _age_of = getattr(self.t, "report_age_s", None)
        if callable(_age_of):
            try:
                ra = _age_of(now=now)
            except BaseException:                              # noqa: BLE001
                ra = None
            out["report_age_s"] = ra
            out["report_max_age_s"] = self.report_max_age_s
            if ra is None or ra > self.report_max_age_s:
                out["halted"] = self.asserter.assert_halt(
                    reason="EXECUTOR_REPORT_STALE",
                    evidence={"report_age_s": ra,
                              "report_max_age_s": self.report_max_age_s,
                              "source": getattr(self.t, "name", "?"),
                              "note": ("the executor stopped publishing its "
                                       "own order report; it is not managing "
                                       "whatever it still has resting")})
                return out
        #: EVERY CONFIGURED REGISTRY, PROBED ON EVERY SWEEP.
        #:
        #: WHY. `claimed()` is read only inside the cancel path, which runs
        #: only once an order is already overdue. So a registry url that
        #: does not serve -- a wrong port, a renamed service, a lane that
        #: never published -- is INDISTINGUISHABLE from "we own nothing"
        #: until the emergency, and the sweep's own docstring says what
        #: happens then: the order is recorded NOT_IN_OWNERSHIP_REGISTRY,
        #: the halt is asserted, and the order is LEFT RESTING. Learning
        #: that your cancel path is blind at the moment you need it is the
        #: worst available time to learn it.
        #:
        #: This is OBSERVABILITY ONLY and cannot widen what is
        #: cancellable. It is a separate, advisory read: the cancel path
        #: keeps its own single read per sweep, because that answer must
        #: not change mid-loop and must not depend on this one.
        out["registries"] = self.registry_notes()
        overdue = []
        #: THE LANE PROBE GETS A FRESH CLOCK, NOT THE SWEEP'S `now`.
        #: LIVE 2026-11-17 21:57:04Z: this line passed `now`, captured at
        #: the top of the sweep and by then ~6.1 s old -- the venue read
        #: above plus the registry probe's blocking timeout on an
        #: unreachable registry. The lane stamps `as_of_utc` as its
        #: document is built, so comparing a document fetched NOW against
        #: a reference time from 6 s ago yields age ~= -6.1 s, outside the
        #: +/-5 s band, and `l5_managed_lanes` reported STALE on every
        #: sweep. The lane's clock was correct the whole time: measured
        #: against each sweep's own heartbeat write, the reported "skew"
        #: equalled the sweep's elapsed time to within 1.1-1.3 ms across
        #: every sample. The exemption therefore never applied, and a
        #: 315 s sports order latched UNMANAGED_RESTING_ORDER account-wide,
        #: costing three authorized BTC5m windows.
        #:
        #: Before the registry probe arrived at 14:03Z the same sweep took
        #: 0.078 s and the same lane read OK, which is why this sat latent
        #: for days: the bug was always here, and a slow sweep armed it.
        #:
        #: Passing None makes `l5_managed_lanes.lane_ids` stamp its own
        #: reference time as it reads -- exactly what the other two
        #: consumers already do -- the executor's transport preflight and
        #: the reset ceremony's own lane-claim helper -- which is why
        #: neither ever saw this. (Their names are deliberately not spelled
        #: here: an architecture test forbids one of them appearing in this
        #: file, and a comment is enough to trip it.) An injected `now` is
        #: still honoured, so tests stay deterministic.
        #:
        #: THIS WIDENS NOTHING. The +/-5 s band, the 60 s staleness bound
        #: and the `manages_own_resting` requirement are untouched; a
        #: genuinely dead lane still exempts nothing, and an exemption is
        #: still never a permission to cancel.
        lane_ids, lane_notes = (
            self.managed_lane_ids(now=now if injected_now else None)
            if self.managed_lanes else (set(), []))
        if self.managed_lanes:
            out["managed_lanes"] = lane_notes
            out["managed_lane_exempt"] = []
        #: REPORTED, never silent: an invisible exemption is the shape that
        #: looks configured while being inert or permissive.
        out["manual_exempt"] = []
        #: ids of executors that are GONE (see OwnershipOracle.dead_ids):
        #: selected below whatever their age
        dead: set = set()
        try:
            for _o in (oracles_of(self.oracle) if self.oracle is not None
                       else []):
                _f = getattr(_o, "dead_ids", None)
                if callable(_f):
                    dead |= set(_f())
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            dead = set()
        if dead:
            out["executor_unreachable_ids"] = len(dead)
        for r in rows:
            if is_terminal(r):
                continue
            if str(r.get("manualOrderIndicator") or "") == MANUAL:
                # the VENUE says a person placed this by hand: its age is
                # not evidence that an executor stopped managing it. It is
                # still never cancellable by this process (see the
                # VENUE_SAYS_MANUAL guard), and the open-order count and
                # every executor's own preflight still see it.
                out["manual_exempt"].append({
                    "venue_order_id": str(r.get("id") or ""),
                    "market": str(r.get("marketSlug") or ""),
                    "age_s": order_age_s(r, now=now)})
                continue
            if str(r.get("id") or "") in lane_ids:
                # a fresh lane is managing this resting order: its age is
                # by design, not evidence of an unmanaged order
                out["managed_lane_exempt"].append(str(r.get("id")))
                continue
            age = order_age_s(r, now=now)
            # age UNKNOWN is treated as overdue: an order whose creation time
            # we cannot read is an order we cannot vouch for.
            gone = str(r.get("id") or "") in dead
            if age is None or age > self.max_order_age_s or gone:
                overdue.append({"venue_order_id": str(r.get("id") or ""),
                                "market": str(r.get("marketSlug") or ""),
                                "age_s": age,
                                "age_known": age is not None,
                                "leaves": str(r.get("leavesQuantity")),
                                **({"selected_by": "EXECUTOR_UNREACHABLE"}
                                   if gone and not (age is None or
                                                    age > self.max_order_age_s)
                                   else {})})
        out["overdue"] = overdue
        if not overdue:
            # THE POSITIVE ATTESTATION. `HaltGate` returns True only for a
            # fresh, parseable, explicit "no halt" -- absence reads as a
            # halt -- so a watchdog that merely stayed quiet on a clean
            # sweep would stop the executor permanently. Found by
            # test_a_young_order_is_left_alone, before any of this was
            # wired to a running service.
            #
            # It is not a clear: `heartbeat_no_halt` refuses to write over
            # a latched halt, so once latched this line cannot undo it.
            try:
                out["heartbeat"] = self.asserter.heartbeat_no_halt()
            except PermissionError as exc:
                out["heartbeat"] = None
                out["heartbeat_refused"] = str(exc)[:160]
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["heartbeat"] = None
                out["heartbeat_error"] = type(exc).__name__
            return out
        # THE LATCH FIRST. If the cancel loop then dies, the executor is
        # already stopped; the other order would leave it trading on.
        try:
            out["halted"] = self.asserter.assert_halt(
                reason="UNMANAGED_RESTING_ORDER",
                evidence={"orders": overdue, "who": self.who,
                          "why": ("an order resting past the executor's own "
                                  "cancellation deadline means the executor "
                                  "is not managing it")})
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            out["halt_error"] = type(exc).__name__
        return self._cancel_targets(overdue, out)

    def _cancel_targets(self, targets: List[Dict[str, Any]],
                        out: Dict[str, Any]) -> Dict[str, Any]:
        """Cancel exactly these orders by venue id. ONE implementation.

        Both callers -- the age-based sweep and the emergency HALT path --
        come through here, so the ownership checks, the venue corroboration
        and the UNCERTAIN handling cannot drift apart between them. The
        caller decides WHICH orders; this decides whether each one may be
        cancelled and whether it actually was.
        """
        if not self.cancel_enabled:
            out["cancel_skipped"] = (
                "cancel is not enabled on this watchdog; the halt is "
                "asserted and the order is STILL RESTING")
            out["cancel_unavailable_no_oracle"] = (
                self.cancel_requested and self.oracle is None)
            return out
        # OWNERSHIP, from the executor's own durable record. Read ONCE per
        # sweep: a per-id read would let the answer change mid-loop.
        claimed = self._claimed_once()
        out["claimed_ids"] = len(claimed)
        if not claimed:
            out["cancel_skipped"] = (
                "the ownership registry named no ids, so NOTHING is "
                "cancellable; the halt is asserted and the order is STILL "
                "RESTING")
            # WHY it named none. "We own nothing" and "we could not find out"
            # are opposite facts and must not arrive as the same empty dict.
            out["claim_refused"] = self._claim_refusal()
            return out
        for o in targets:
            vid = o["venue_order_id"]
            if not vid:
                out["unverified"].append(dict(o, verify_error="NO_ID"))
                continue
            mine = claimed.get(vid)
            if mine is None:
                # Not ours as far as any durable record goes. On a SHARED
                # account that includes the owner's manual orders, so this
                # is the branch that must never be relaxed.
                out["not_ours"].append(dict(o, why="NOT_IN_OWNERSHIP_REGISTRY"))
                continue
            # VENUE CORROBORATION BEFORE ANY POST. Our record is one
            # witness; the venue is the other, and they must agree before a
            # cancel is sent. A row the venue says is MANUAL, or that names a
            # different market than our claim, is uncertain identity -- the
            # same contract l5_e1_cancel_by_id enforces -- and nothing is
            # sent for it.
            try:
                pre = self.t.order(vid)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["unverified"].append(
                    dict(o, verify_error=type(exc).__name__,
                         why="OWNERSHIP_UNCONFIRMED_NO_POST_SENT"))
                continue
            indicator = str(pre.get("manualOrderIndicator") or "")
            venue_slug = str(pre.get("marketSlug") or "")
            claim_slug = str(mine.get("market") or "")
            if indicator != AUTOMATIC:
                out["not_ours"].append(
                    dict(o, why="VENUE_SAYS_MANUAL",
                         manual_order_indicator=indicator or None,
                         labels=("a MANUAL order is never cancellable by "
                                 "this path, however it is claimed")))
                continue
            if claim_slug and venue_slug and claim_slug != venue_slug:
                out["not_ours"].append(
                    dict(o, why="MARKET_MISMATCH", venue_market=venue_slug,
                         claimed_market=claim_slug))
                continue
            ack_error = None
            ack_detail = None
            # THE ORDER'S OWN MARKET, from the venue row first and the claim
            # second. Never this process's idea of a current market: it has
            # none, and an order from another window must still be cancelled
            # with ITS slug.
            cancel_slug = venue_slug or claim_slug
            try:
                self.t.cancel(vid, cancel_slug)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                ack_error = type(exc).__name__
                try:
                    ack_detail = http_error_detail(exc)
                except BaseException:                          # noqa: BLE001
                    ack_detail = {"error": ack_error}
            # the ack decides nothing; the re-read does
            try:
                row = self.t.order(vid)
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                out["unverified"].append(dict(
                    o, cancel_error=ack_error, cancel_detail=ack_detail,
                    verify_error=type(exc).__name__))
                continue
            # cancel_detail carries the SANITIZED venue response. Without it a
            # schema rejection and a transport failure both read "HTTPError".
            rec = dict(o, cancel_error=ack_error, cancel_detail=ack_detail,
                       cancel_slug=cancel_slug,
                       venue_state=row.get("state"),
                       cum_qty=str(row.get("cumQuantity")),
                       leaves_qty=str(row.get("leavesQuantity")))
            if is_terminal(row):
                out["cancelled"].append(rec)
            else:
                out["still_open"].append(rec)
        return out



def asserter_for(store: Any, *, who: str = "l5-watchdog"):
    from src import l5_halt_gate as HG
    return HG.HaltAsserter(store, who=who)


def from_env(*, transport: Any = None, store: Any = None) -> Watchdog:
    """Build from the service's variables. Cancel needs an explicit YES."""
    from src import l5_halt_gate as HG
    st = store if store is not None else HG.FileStore(
        os.getenv("L5_WATCHDOG_STATE_DIR", "/data"))
    # CREDENTIAL-FREE MODE, and it is the default when configured. Reading the
    # executor's own report needs no venue access, so this process holds no
    # credentials and its image carries no signing code. Cancel is then
    # unavailable by construction -- there is nothing to cancel WITH -- and
    # cancellation stays with the executor under its own credentials.
    reg = str(os.getenv("L5_ORDER_REGISTRY_URL", "")).strip()
    # ...and ONLY when the venue is genuinely unreachable from here. With a
    # signing module and credentials present, the previous venue-reading
    # behaviour is preserved exactly: this must not silently take the cancel
    # path away from a deployment that was configured for it.
    _blind = A is None or not A.has_credentials()
    if transport is None and reg and _blind:
        return Watchdog(
            RegistryOrderSource(reg),
            asserter_for(st, who=os.getenv("L5_WATCHDOG_WHO", "l5-watchdog")),
            max_order_age_s=float(os.getenv("L5_WATCHDOG_MAX_ORDER_AGE_S",
                                            str(DEFAULT_MAX_ORDER_AGE_S))),
            report_max_age_s=float(os.getenv("L5_WATCHDOG_REPORT_MAX_AGE_S",
                                             str(DEFAULT_REPORT_MAX_AGE_S))),
            cancel_enabled=False,
            oracle=None,
            managed_lanes=_managed_lanes_from_env())
    return Watchdog(
        transport if transport is not None else CancelOnlyTransport(),
        asserter_for(st, who=os.getenv("L5_WATCHDOG_WHO", "l5-watchdog")),
        max_order_age_s=float(os.getenv("L5_WATCHDOG_MAX_ORDER_AGE_S",
                                        str(DEFAULT_MAX_ORDER_AGE_S))),
        # PASSED HERE TOO. The credential-free branch above read
        # L5_WATCHDOG_REPORT_MAX_AGE_S and this one did not, so a deployment
        # that tuned the bound had it silently ignored in exactly the mode
        # that can cancel. Found by testing the whole production variable
        # set at once rather than the pieces separately.
        report_max_age_s=float(os.getenv("L5_WATCHDOG_REPORT_MAX_AGE_S",
                                         str(DEFAULT_REPORT_MAX_AGE_S))),
        cancel_enabled=str(
            os.getenv("L5_WATCHDOG_CANCEL", "")).strip().upper() == "YES",
        oracle=oracle_from_env(),
        managed_lanes=_managed_lanes_from_env())


def oracles_of(oracle: Any) -> List[Any]:
    """The individual registries behind whatever `oracle_from_env` built.

    One url builds a bare `OwnershipOracle`, several a `CompositeOracle`.
    Callers that want to report per-registry health should not have to
    know which.
    """
    if oracle is None:
        return []
    inner = getattr(oracle, "oracles", None)
    if inner:
        return list(inner)
    return [oracle]


def _managed_lanes_from_env() -> List[str]:
    """L5_WATCHDOG_MANAGED_LANE_URLS: comma-separated base URLs of lanes that
    publish /l5/live_orders.json with manages_own_resting=true."""
    from src import l5_managed_lanes as ML
    return ML.from_env()


class CompositeOracle:
    """Ownership across SEVERAL executors' registries, unioned.

    WHY THIS EXISTS. The oracle read exactly one URL, and the watchdog
    cancels nothing it cannot claim: an order whose venue id is absent
    from `claimed()` is recorded `not_ours: NOT_IN_OWNERSHIP_REGISTRY`,
    the halt is asserted, and the order is LEFT RESTING. With one
    account now carrying a second executor, that single URL forced a
    choice between them -- whichever registry it named, the other
    executor's orders became uncancellable. Repointing it is not a
    configuration decision but a removal of protection from one lane.

    A UNION IS THE SAFE DIRECTION. Ownership only ever grants the power
    to cancel, so a claim that fails to arrive costs us a cancellation
    we would have made; it can never cause one we should not. An
    unreachable or stale registry therefore contributes NOTHING and is
    reported, exactly as `l5_managed_lanes.lane_ids` does for lanes --
    it never widens what is cancellable.

    `last_refusal` aggregates the sources that gave nothing, because
    "every registry says we own nothing" and "no registry could be
    read" are opposite facts and the sweep has to tell them apart.
    """

    def __init__(self, oracles: Sequence[Any]) -> None:
        if not oracles:
            raise ValueError("CompositeOracle needs at least one oracle")
        self.oracles = tuple(oracles)
        self.last_refusal: Optional[str] = None

    def claimed(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        refusals, served = [], 0
        for o in self.oracles:
            try:
                rows = o.claimed()
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                refusals.append("%s:ORACLE_RAISED:%s"
                                % (getattr(o, "url", "?"),
                                   type(exc).__name__))
                continue
            why = getattr(o, "last_refusal", None)
            if why:
                refusals.append("%s:%s" % (getattr(o, "url", "?"), why))
            if isinstance(rows, Mapping):
                served += 1
                #: FIRST CLAIM WINS. Two executors claiming one venue id
                #: would be a ledger fault, not a merge to resolve
                #: quietly, so it is recorded rather than overwritten.
                for vid, row in rows.items():
                    if vid in out and out[vid] != row:
                        refusals.append("%s:DUPLICATE_CLAIM:%s"
                                        % (getattr(o, "url", "?"), vid))
                        continue
                    out[vid] = row
        self.last_refusal = "; ".join(refusals) if refusals else None
        if served == 0:
            #: nothing could be read at all: that is not "we own nothing"
            self.last_refusal = (self.last_refusal
                                 or "NO_REGISTRY_COULD_BE_READ")
            return {}
        return out


def registry_urls(raw: Optional[str] = None) -> List[str]:
    """`L5_ORDER_REGISTRY_URL` as a list. ONE definition of the split.

    WHY THIS IS A FUNCTION. The sweep split this string on commas and the
    reset ceremony did not: it passed the whole value to a single
    `RegistryOrderSource`. With two registries configured, every reset
    therefore failed its venue precondition on an unreachable host, and
    the 2026-11-17 23:09Z clear could only be made to pass by temporarily
    rewriting the variable to one url. Two readings of one variable, in
    two guards that must agree about what the operator configured, is the
    same defect class as the two lane-freshness copies above.

    Empty entries are dropped, so a trailing comma is not a url.
    """
    if raw is None:
        raw = os.getenv("L5_ORDER_REGISTRY_URL", "")
    return [u.strip() for u in str(raw).strip().split(",") if u.strip()]


def oracle_from_env() -> Optional[Any]:
    """The executors' read-only registries, or None -- which disables cancel.

    `L5_ORDER_REGISTRY_URL` takes a comma-separated list. ONE url builds
    exactly the `OwnershipOracle` it always did, so an existing
    deployment is byte-for-byte unchanged; several build a
    `CompositeOracle` over them.
    """
    urls = registry_urls()
    if not urls:
        return None
    built = []
    for u in urls:
        try:
            built.append(OwnershipOracle(u))
        except BaseException:                                  # noqa: BLE001
            #: a url we cannot even construct an oracle for contributes
            #: nothing; it must not take the others down with it
            continue
    if not built:
        return None
    return built[0] if len(built) == 1 else CompositeOracle(built)


def wait_refreshing_claims(wd: Any, total_s: float, *, sleep=None,
                           monotonic=None) -> int:
    """Wait `total_s` between sweeps, re-reading every ownership registry
    each L5_WATCHDOG_CLAIM_POLL_S (default 2 s; 0 = plain wait, the earlier
    behaviour). Each read only refreshes what an oracle remembers
    (`_last_good`, `_unreachable_since`); it cancels nothing, asserts
    nothing and makes no venue request. Returns the number of refreshes.
    Never raises: a failed read is exactly what the next sweep reports."""
    import os
    import time as _time
    sleep = _time.sleep if sleep is None else sleep
    monotonic = _time.monotonic if monotonic is None else monotonic
    try:
        every = float(os.environ.get("L5_WATCHDOG_CLAIM_POLL_S", "") or 2.0)
    except ValueError:
        every = 2.0
    oracle = getattr(wd, "oracle", None)
    if every <= 0 or oracle is None:
        sleep(max(0.0, total_s))
        return 0
    end = monotonic() + max(0.0, total_s)
    n = 0
    while True:
        left = end - monotonic()
        if left <= 0:
            return n
        sleep(min(every, left))
        if end - monotonic() <= 0:
            return n
        try:
            for o in oracles_of(oracle):
                o.claimed()
            n += 1
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise

