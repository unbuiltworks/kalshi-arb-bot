"""Owner-controlled emergency HALT, reachable when Railway is not.

WHY THIS EXISTS. The halt latch is one-way and its only writer is
`HaltAsserter` on the watchdog's own volume -- which is correct, and is why
the executor cannot clear the latch that governs it. But it left the OWNER
with no path to assert HALT either: the dashboard, `rollback`, `redeploy`
and a variable change are all DEPLOYMENTS, and on 2026-10-21
`connect-service-source` returned "Deploys have been paused temporarily".
With deploys paused and an armed executor, the only thing that could have
stopped it was the watchdog's own autonomous decision. That is a real
protection but it is not the owner's control.

WHAT THIS IS, AND THE FOUR THINGS IT IS NOT.

It is a marker the owner publishes at a URL, which the existing watchdog
reads on its existing sweep loop and, if it validates, turns into exactly
one call to the existing `assert_halt`.

  * It is NOT a second trading engine, a new service, or a new store. It
    reuses the watchdog loop, `HaltAsserter`, and the watchdog's volume.
  * It is NOT a cancellation path. A latched HALT already makes the EXECUTOR
    cancel: `Runner.maybe_halt_deadman` says "A LATCHED HALT MUST ALSO
    CANCEL, not merely stop submitting" and runs `deadman()`, which cancels
    by exact venue id and proves it by re-reading `GET /v1/order/{id}`. This
    module latches; that existing path does the cancelling and the venue
    verification.
  * It is NOT a resume. `COMMAND` is HALT and nothing else parses. A marker
    saying RESUME, CLEAR, OK or anything but HALT is refused by name and
    acted on never. `HaltAsserter` has no clear by construction, so even a
    compromised marker cannot authorize trading -- the worst a forger can do
    is stop us trading, which is the safe direction.
  * It is NOT a venue credential. It needs no GitHub token either: integrity
    comes from the HMAC, not from the transport, so the marker may live
    behind an authenticated API or at a public URL and the watchdog stays
    credential-free with respect to the venue.

UNREACHABLE IS NOT HALT, DELIBERATELY. If the marker cannot be fetched this
returns MARKER_UNREACHABLE and changes nothing. Asserting on a failed read
would latch the system on every transient network error. The executor is
already failed closed by a different mechanism when the watchdog goes quiet:
`HaltGate`'s only path to True is a FRESH "no halt" attestation, so a
watchdog that cannot attest stops the executor by omission.

STALENESS IS REFUSED RATHER THAN OBEYED, which is the one place this moves
AWAY from halting. A marker from last week re-latching after a legitimate
owner reset is the ambiguous state the brief asks to prevent: nobody could
say whether it meant "halt now" or was litter. So a marker older than
`MAX_MARKER_AGE_S` is refused BY NAME and surfaced, and the owner re-issues
with a current timestamp. Nothing is lost: an owner who wants a halt can
always publish a fresh marker, and the refusal is loud.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
from typing import Any, Dict, Iterable, Mapping, Optional

#: where the owner publishes the marker. Unset = the mechanism is OFF.
MARKER_URL_ENV = "L5_EMERGENCY_HALT_URL"
#: the HMAC key. Unset = OFF. This is the whole of the authentication.
MARKER_SECRET_ENV = "L5_EMERGENCY_HALT_SECRET"
#: which deployment a marker may halt, so one published marker cannot halt
#: an unrelated service that happens to read the same URL.
MARKER_SERVICE_ENV = "L5_EMERGENCY_HALT_SERVICE"

#: the ONLY command that parses.
COMMAND = "HALT"

#: strict, and the same figure the other watchdog HTTP sources use.
DEFAULT_TIMEOUT_S = 3.0
#: a marker older than this is refused by name -- see the docstring.
MAX_MARKER_AGE_S = 86400.0
#: tolerance for a future-dated marker: clocks drift, forgery does not need
#: the future, so this is small.
CLOCK_SKEW_S = 120.0

#: durable record of nonces already acted on, on the watchdog's own volume,
#: so a restart cannot re-act on a spent marker and double-latch.
ACTED_KEY = "emergency_halt_acted.json"

#: the fields the HMAC covers. Listed explicitly so adding a field later
#: cannot silently fall outside the signature.
SIGNED_FIELDS = ("command", "issued_utc", "nonce", "service", "reason")


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def configured() -> bool:
    """Both the URL and the secret, or the mechanism is off.

    OFF means "cannot be triggered", never "halt". A half-configured
    mechanism is off too: a URL with no secret would accept an unsigned
    marker, which is the one thing the signature exists to prevent.
    """
    return bool((os.getenv(MARKER_URL_ENV) or "").strip()
                and (os.getenv(MARKER_SECRET_ENV) or "").strip())


def canonical(payload: Mapping[str, Any]) -> bytes:
    """The exact bytes the HMAC covers: only SIGNED_FIELDS, sorted, compact.

    Built from a fixed field list rather than from the document, so an
    attacker cannot strip a field to change the meaning of what was signed,
    and a future field cannot accidentally be unsigned.
    """
    sub = {k: ("" if payload.get(k) is None else str(payload.get(k)))
           for k in SIGNED_FIELDS}
    return json.dumps(sub, sort_keys=True, separators=(",", ":")).encode()


def sign(payload: Mapping[str, Any], secret: str) -> str:
    """The owner's side of the HMAC. Not a credential issuer: it authorizes
    nothing but a stop, and the latch it reaches has no clear."""
    return hmac.new(str(secret).encode(), canonical(payload),
                    hashlib.sha256).hexdigest()


def _parse_t(value: Any) -> Optional[dt.datetime]:
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def evaluate(raw: Any, *, secret: str, service: str,
             acted_nonces: Iterable[str] = (),
             now: Optional[dt.datetime] = None,
             max_age_s: float = MAX_MARKER_AGE_S) -> Dict[str, Any]:
    """Pure decision over marker bytes. Fetches nothing, writes nothing.

    `act` is True only for a marker that is well-formed, says HALT, carries a
    valid signature over SIGNED_FIELDS, names THIS service, is inside the
    freshness window, and whose nonce has not already been acted on. Every
    other outcome is False with a named reason.
    """
    out: Dict[str, Any] = {"act": False, "reason": None, "nonce": None,
                           "labels": ("HALT only. This can never clear a "
                                      "halt or authorize trading.")}
    try:
        doc = json.loads(raw)
    except BaseException:                                      # noqa: BLE001
        out["reason"] = "MARKER_MALFORMED"
        return out
    if not isinstance(doc, Mapping):
        out["reason"] = "MARKER_MALFORMED"
        return out

    cmd = str(doc.get("command") or "").strip().upper()
    out["command"] = cmd[:32]
    if cmd != COMMAND:
        # RESUME / CLEAR / OK / anything: refused here and never acted on.
        out["reason"] = "MARKER_COMMAND_NOT_HALT"
        return out

    got = str(doc.get("hmac") or "")
    want = sign(doc, secret)
    if not got or not hmac.compare_digest(got, want):
        out["reason"] = "MARKER_SIGNATURE_INVALID"
        return out

    want_svc = str(doc.get("service") or "").strip()
    if not want_svc or want_svc != str(service).strip():
        out["reason"] = "MARKER_WRONG_SERVICE"
        out["marker_service"] = want_svc[:64]
        return out

    nonce = str(doc.get("nonce") or "").strip()
    if not nonce:
        out["reason"] = "MARKER_NO_NONCE"
        return out
    out["nonce"] = nonce
    if nonce in set(str(n) for n in (acted_nonces or ())):
        # already latched on this command; acting again would write a second
        # halt record over the first and lose its original evidence.
        out["reason"] = "MARKER_ALREADY_ACTED"
        return out

    t = _parse_t(doc.get("issued_utc"))
    if t is None:
        out["reason"] = "MARKER_ISSUED_UTC_UNREADABLE"
        return out
    age = ((now or _utc()) - t).total_seconds()
    out["age_s"] = round(age, 1)
    if age < -float(CLOCK_SKEW_S):
        out["reason"] = "MARKER_FUTURE_DATED"
        return out
    if age > float(max_age_s):
        out["reason"] = "MARKER_STALE"
        out["max_age_s"] = float(max_age_s)
        return out

    out["act"] = True
    out["reason"] = "MARKER_HALT_VALID"
    out["marker_reason"] = str(doc.get("reason") or "")[:200]
    return out


class MarkerSource:
    """GET the marker with a strict timeout. Shaped like the watchdog's other
    HTTP sources, and like them it RAISES rather than returning a default:
    an unreachable marker is not evidence of "no halt wanted"."""

    name = "EMERGENCY_HALT_MARKER"

    def __init__(self, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 opener=None, header: Optional[Mapping[str, str]] = None
                 ) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("MarkerSource needs a url")
        self.url = url.strip()
        self.timeout_s = float(timeout_s)
        self._opener = opener
        self._header = dict(header or {})

    def fetch(self) -> bytes:
        import urllib.request
        if self._opener is not None:
            return self._opener(self.url, self.timeout_s)
        # no-cache because a cached marker is a stale marker, and the owner
        # publishing a halt should not be waiting on a CDN.
        req = urllib.request.Request(self.url, headers=dict(
            self._header, **{"Cache-Control": "no-cache", "Pragma": "no-cache"}))
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            if getattr(r, "status", 200) != 200:
                raise RuntimeError("marker_http_%s" % getattr(r, "status", "?"))
            return r.read()


def _acted(store: Any) -> list:
    try:
        raw = store.get(ACTED_KEY)
    except BaseException:                                      # noqa: BLE001
        # UNREADABLE IS TREATED AS "EVERYTHING ACTED": the only consequence
        # is that we do not latch again, and the system is already halted or
        # was never halted by a marker. Guessing the other way would re-latch
        # on every sweep and overwrite the original halt evidence.
        return ["__unreadable__"]
    if not raw:
        return []
    try:
        rec = json.loads(raw)
        return [str(n) for n in (rec.get("nonces") or [])]
    except BaseException:                                      # noqa: BLE001
        return ["__unreadable__"]


def check(source: Any, asserter: Any, store: Any, *, service: str,
          secret: str, now: Optional[dt.datetime] = None,
          max_age_s: float = MAX_MARKER_AGE_S) -> Dict[str, Any]:
    """One marker check. Latches HALT through `asserter` if it validates.

    Returns a record for the watchdog to emit. Never raises: a watchdog that
    dies is a watchdog that stops watching.
    """
    out: Dict[str, Any] = {"LABEL": "L5_EMERGENCY_HALT_MARKER",
                           "acted": False, "reason": None}
    try:
        raw = source.fetch()
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        # RETAIN THE EXISTING SAFETY STATE. Nothing is asserted and nothing
        # is attested; the executor's own staleness gate still fails it
        # closed if the watchdog stays unable to attest.
        out["reason"] = "MARKER_UNREACHABLE"
        out["error"] = type(exc).__name__
        return out

    acted = _acted(store)
    verdict = evaluate(raw, secret=secret, service=service,
                       acted_nonces=acted, now=now, max_age_s=max_age_s)
    out["verdict"] = {k: v for k, v in verdict.items() if k != "labels"}
    if not verdict.get("act"):
        out["reason"] = verdict.get("reason")
        return out

    try:
        rec = asserter.assert_halt(
            reason="OWNER_EMERGENCY_MARKER",
            evidence={"source": getattr(source, "name", "MARKER"),
                      "nonce": verdict.get("nonce"),
                      "issued_age_s": verdict.get("age_s"),
                      "owner_reason": verdict.get("marker_reason"),
                      "note": ("owner-published marker, HMAC verified. The "
                               "executor's own maybe_halt_deadman performs "
                               "the exact-order cancellation and venue "
                               "re-read.")})
    except BaseException as exc:                               # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        out["reason"] = "MARKER_LATCH_FAILED"
        out["error"] = type(exc).__name__
        return out

    # RECORDED ONLY AFTER THE LATCH SUCCEEDED, and a failure to record is
    # reported rather than swallowed: the halt stands either way, and the
    # worst case of an unrecorded nonce is a second identical latch.
    try:
        store.put(ACTED_KEY, json.dumps(
            {"nonces": sorted(set([n for n in acted
                                   if n != "__unreadable__"]
                                  + [str(verdict.get("nonce"))])),
             "t": (now or _utc()).isoformat()},
            sort_keys=True).encode())
    except BaseException as exc:                               # noqa: BLE001
        out["nonce_record_error"] = type(exc).__name__

    out["acted"] = True
    out["reason"] = "HALT_LATCHED_FROM_OWNER_MARKER"
    out["halt_record"] = {k: rec.get(k) for k in ("halt", "t", "who",
                                                  "reason")}
    return out


def from_env(store: Any, asserter: Any) -> Optional[Dict[str, Any]]:
    """Wire from the environment, or None when not configured.

    Returns the pieces rather than performing the check, so the caller
    decides when to run it and the whole thing stays testable without env.
    """
    if not configured():
        return None
    return {
        "source": MarkerSource(
            (os.getenv(MARKER_URL_ENV) or "").strip(),
            timeout_s=float(os.getenv("L5_EMERGENCY_HALT_TIMEOUT_S",
                                      DEFAULT_TIMEOUT_S) or
                            DEFAULT_TIMEOUT_S)),
        "asserter": asserter,
        "store": store,
        "service": (os.getenv(MARKER_SERVICE_ENV) or "").strip()
                   or "l5-pmus-live",
        "secret": (os.getenv(MARKER_SECRET_ENV) or "").strip(),
    }
