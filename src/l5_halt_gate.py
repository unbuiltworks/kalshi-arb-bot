"""A durable, independent HALT that a watchdog can ASSERT and cannot CLEAR.

WHY CANCELLATION IS NOT A HALT. Cancelling owned orders removes outstanding
exposure, and the executor's own fail-closed reconcile then refuses further
submissions -- but only until the next authoritative read agrees with the
book, after which submissions resume. That is a brake, not a latch. A halt
has to outlive the process that asserted it, outlive a redeploy, and not be
clearable by the thing it is restraining.

THE MEDIUM, AND WHY THIS SHAPE. The two services share no durable store
today: `l5-pmus-live` has NO volume at all, and a Railway volume attaches to
exactly one service, which is why the monitor's own notes say it cannot read
the executor's tape. So the halt needs one shared object store, and this
module is deliberately written against the smallest possible interface -- an
object that can be PUT, GET and DELETEd -- so it can sit on a Railway bucket,
an S3 bucket, or anything else with those three verbs, without this logic
changing.

THE ASYMMETRY IS ENFORCED IN CODE, NOT BY CREDENTIALS, and that is stated
plainly because it matters: a Railway bucket issues ONE key pair per bucket,
so credential-level scoping is not available. `HaltAsserter` therefore has no
code path that deletes, exactly as `pmus_auth.CancelOnlyTransport` has no code
path that creates an order. Clearing a halt is an owner action taken with the
credentials directly, outside any automated process.

FAIL CLOSED IS THE WHOLE POINT. `HaltGate.submissions_permitted()` returns
False when the halt object exists, AND when the store cannot be reached, AND
when the stored record cannot be parsed, AND when it is older than its
freshness bound. An executor that cannot find out whether it is halted is
halted. The only path to True is a positive, fresh, parseable "no halt".

NOTHING HERE PLACES, CANCELS OR PRICES ANYTHING. It answers one question.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Any, Mapping, Optional, Protocol

#: the one object key. A single key, not a prefix: a halt that can be written
#: under a name the reader does not check is not a halt.
HALT_KEY = "l5/halt.json"

#: a halt record older than this is treated as a halt, not as stale noise.
#: It bounds how long a silently-dead asserter can leave the executor running
#: on a stale "no halt" -- but note the DEFAULT is halt, so this bound only
#: ever makes the gate MORE restrictive, never less.
DEFAULT_MAX_AGE_S = 900.0


class HaltReadUnavailable(RuntimeError):
    """`get` could not establish whether the object exists.

    THE DISTINCTION IS LOAD-BEARING and this type is how a store states it.
    Returning None for a failed read says "there is no halt", which is a
    permissive answer; `HaltAsserter.heartbeat_no_halt` acts on it and would
    write `halt: false` over a latch it never saw. So a store returns None
    ONLY for a positive absence -- a 404, a missing file -- and raises this
    for everything else: a timeout, a 500, DNS failure, a signing error.

    The gate is unaffected either way: `_classify` turns both ABSENT and
    UNREADABLE into a refusal.
    """


class ObjectStore(Protocol):
    """The three verbs. Deliberately not a Railway or S3 type.

    `get` CONTRACT: return the bytes, return None only when the object is
    positively absent, and raise (ideally `HaltReadUnavailable`) when the
    answer could not be established. A store that collapses those last two
    cannot safely back a `HaltAsserter`.
    """

    def get(self, key: str) -> Optional[bytes]: ...
    def put(self, key: str, body: bytes) -> Any: ...


def _utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class HaltAsserter:
    """Write-only, assert-only. Has no delete and no clear, by construction.

    Given to the independent watchdog. Every method here moves the system in
    exactly one direction: toward refusing to trade.
    """

    def __init__(self, store: ObjectStore, *, who: str) -> None:
        # `str(None)` is "None", which is truthy -- found by
        # test_the_asserter_requires_a_stated_identity. An unidentified
        # asserter writes an unattributable halt, so the type is checked
        # before the strip.
        if not isinstance(who, str) or not who.strip():
            raise ValueError("HaltAsserter requires a stated `who`")
        self.store = store
        self.who = str(who)

    def assert_halt(self, *, reason: str, evidence: Mapping[str, Any]) -> dict:
        """Latch HALT. Requires a reason and named evidence, like every other
        state-moving operation in this programme."""
        if not str(reason or "").strip():
            raise ValueError("assert_halt requires a stated reason")
        if not isinstance(evidence, Mapping) or not evidence:
            raise ValueError("assert_halt requires named evidence")
        rec = {"halt": True, "t": _utc().isoformat(), "who": self.who,
               "reason": str(reason), "evidence": dict(evidence)}
        self.store.put(HALT_KEY, json.dumps(rec, sort_keys=True).encode())
        return rec

    def heartbeat_no_halt(self) -> dict:
        """Refresh the positive 'no halt' attestation.

        This is the ONLY way the gate ever returns True, and it is still not a
        clear: writing `halt: false` over an EXISTING halt is refused below, so
        a watchdog cannot undo its own latch.

        AN UNREADABLE RECORD IS NOT AN ABSENT ONE. The first version read the
        record through `_read`, which collapses "no object" and "I could not
        find out" into the same None -- so a transient GET failure, a 500, or
        a torn JSON document in front of a LATCHED halt let this method write
        `halt: false` straight over it. The heartbeat is the one operation
        that moves the system toward trading, so it is the one operation that
        may not proceed on an unestablished state.
        """
        kind, cur = _classify(self.store)
        if kind == UNREADABLE:
            raise PermissionError(
                "refusing to attest 'no halt' over a halt state that could "
                "not be established: an unreadable record may be a latched "
                "HALT, and overwriting it would clear it")
        if kind == PRESENT and cur is not None and cur.get("halt") is not False:
            raise PermissionError(
                "refusing to overwrite a latched HALT: clearing is an owner "
                "action, not an automated one")
        rec = {"halt": False, "t": _utc().isoformat(), "who": self.who}
        self.store.put(HALT_KEY, json.dumps(rec, sort_keys=True).encode())
        return rec


#: the three outcomes of a halt read, kept apart because the asserter and
#: the gate need DIFFERENT things from them. The gate may treat ABSENT and
#: UNREADABLE alike -- both refuse -- but the asserter may not: writing over
#: an UNREADABLE record can clear a latch, while writing over an ABSENT one
#: cannot.
ABSENT = "ABSENT"
PRESENT = "PRESENT"
UNREADABLE = "UNREADABLE"


def _classify_bytes(store: ObjectStore) -> tuple:
    """(kind, record, raw_bytes). Only PRESENT carries a record.

    The RAW BYTES are returned so a caller can digest them without a SECOND
    read. The owner-gated reset pins the exact latch by sha256 of these
    bytes, and the executor already fetches them on every tick -- so the
    digest is free telemetry rather than new load on the watchdog.
    """
    try:
        raw = store.get(HALT_KEY)
    except BaseException:                                      # noqa: BLE001
        return (UNREADABLE, None, None)
    if raw is None:
        # a store that cannot distinguish "no object" from "read failed"
        # must report the failure as UNREADABLE, not as None -- see
        # FileStore.get, which narrows its except to FileNotFoundError for
        # exactly this reason.
        return (ABSENT, None, None)
    try:
        rec = json.loads(raw)
    except BaseException:                                      # noqa: BLE001
        return (UNREADABLE, None, raw)
    if not isinstance(rec, dict):
        return (UNREADABLE, None, raw)
    return (PRESENT, rec, raw)


def _classify(store: ObjectStore) -> tuple:
    """(kind, record). The two-value view every existing caller uses."""
    kind, rec, _raw = _classify_bytes(store)
    return (kind, rec)


def digest_of(raw: Optional[bytes]) -> Optional[str]:
    """sha256 hex of the latch bytes, or None. NEVER the bytes themselves."""
    if raw is None:
        return None
    try:
        return hashlib.sha256(raw).hexdigest()
    except BaseException:                                      # noqa: BLE001
        return None


def _read(store: ObjectStore) -> Optional[dict]:
    """None means 'could not be established', which the gate treats as halt.

    The gate's view, deliberately lossy: ABSENT and UNREADABLE both refuse,
    so collapsing them here changes no decision the gate makes. The asserter
    uses `_classify` instead, where the distinction is load-bearing.
    """
    kind, rec = _classify(store)
    return rec if kind == PRESENT else None


class HaltGate:
    """Read-only. The executor's side. Default answer is NO."""

    def __init__(self, store: ObjectStore, *,
                 max_age_s: float = DEFAULT_MAX_AGE_S,
                 env_halt: Optional[str] = None) -> None:
        self.store = store
        self.max_age_s = float(max_age_s)
        #: the owner's existing L5_LIVE_HALT, kept as an independent OR so the
        #: env-var kill switch never stops working.
        self.env_halt = env_halt
        #: SAFE TELEMETRY from the most recent read, refreshed by state().
        #: The digest is what the owner-gated reset pins the latch by, and it
        #: is obtainable here because the executor already performs this read
        #: -- so the operator never has to stop the watchdog to learn it.
        #: Only the DIGEST and a few labels are kept; see telemetry().
        self.last_sha256: Optional[str] = None
        self.last_kind: Optional[str] = None
        self._last_rec: Optional[dict] = None

    def telemetry(self) -> dict:
        """What is safe to LOG about the latch. Never its raw contents.

        The digest identifies the latch without disclosing it. `reason` is a
        short label the reset gate requires the owner to retype, and carries
        no secret. `evidence` is DELIBERATELY EXCLUDED: it can name venue
        order ids, and nothing here needs them.
        """
        rec = self._last_rec or {}
        return {"sha256": self.last_sha256, "kind": self.last_kind,
                "halt": rec.get("halt") if self._last_rec else None,
                "reason": (str(rec.get("reason"))[:80]
                           if rec.get("reason") else None),
                "record_t": str(rec.get("t")) if rec.get("t") else None,
                "labels": ("digest of the latch bytes the executor already "
                           "reads; raw contents and `evidence` are never "
                           "logged. Pass sha256 as L5_HALT_RESET_EXPECT to "
                           "clear THIS latch and no other.")}

    def state(self) -> dict:
        if str(self.env_halt or "").strip():
            return {"permitted": False, "why": "env_halt", "record": None}
        kind, rec, raw = _classify_bytes(self.store)
        self.last_kind = kind
        self.last_sha256 = digest_of(raw)
        self._last_rec = rec if kind == PRESENT else None
        if kind != PRESENT or rec is None:
            return {"permitted": False, "why": "halt_state_unavailable",
                    "record": None}
        if rec.get("halt") is not False:
            # anything but an explicit False is a halt: True, a typo, a
            # missing key, a string. The permissive reading is never taken.
            return {"permitted": False, "why": "halt_asserted", "record": rec}
        try:
            t = dt.datetime.fromisoformat(str(rec.get("t")))
            if t.tzinfo is None:
                return {"permitted": False, "why": "halt_record_naive_time",
                        "record": rec}
        except BaseException:                                  # noqa: BLE001
            return {"permitted": False, "why": "halt_record_unparseable_time",
                    "record": rec}
        age = (_utc() - t).total_seconds()
        if age < 0:
            return {"permitted": False, "why": "halt_record_in_the_future",
                    "record": rec, "age_s": age}
        if age > self.max_age_s:
            return {"permitted": False, "why": "halt_record_stale",
                    "record": rec, "age_s": age}
        return {"permitted": True, "why": None, "record": rec, "age_s": age}

    def submissions_permitted(self) -> bool:
        return bool(self.state()["permitted"])

    def refuse_reason(self) -> Optional[str]:
        """What the submission path logs. None only when genuinely permitted."""
        s = self.state()
        return None if s["permitted"] else str(s["why"])


class FileStore:
    """A durable `ObjectStore` on a filesystem path -- a mounted volume.

    WHY A FILE. The halt must outlive the process that asserted it. A latch
    held in memory is cleared by the crash it was asserting about, and an
    environment variable cannot be set by a watchdog that has no deploy
    credentials. A file on a mounted volume survives a restart, which is the
    one property a halt cannot do without.

    The write is atomic: a temp file in the same directory, then `replace`.
    A reader can therefore see the old record or the new one, never a half
    written one -- and a torn JSON document would read as unparseable, which
    `HaltGate` treats as NOT PERMITTED, so even the failure is safe.
    """

    def __init__(self, root: str) -> None:
        self.root = str(root)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key.replace("/", os.sep))

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def put(self, key: str, body: bytes) -> dict:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return {"key": key, "bytes": len(body), "path": path}


class HttpHaltStore:
    """Reads the latch over HTTP. The executor's side of a remote halt.

    WHY THIS SHAPE. The latch must be durable, the watchdog must own it, and
    the executor must never be able to change it. A Railway volume attaches
    to ONE service, so the two processes cannot share a filesystem. Giving
    the volume to the WATCHDOG and letting the executor read over private
    networking satisfies all three: the latch survives restarts on the
    watchdog's disk, only the watchdog writes it, and the executor's only
    verb is GET. The reverse arrangement -- the executor hosting the latch
    and the watchdog POSTing to it -- would put a write path into the
    money-capable service, which is the thing being guarded.

    EVERY FAILURE READS AS UNAVAILABLE, and `HaltGate` turns unavailable
    into "not permitted". A timeout, a 500, a wrong body, DNS failure or an
    unreachable host all refuse, which is the only safe direction: the
    executor must not trade because it could not ask.
    """

    #: short on purpose. This read sits in front of every order, so a slow
    #: answer must fail rather than delay the decision indefinitely.
    DEFAULT_TIMEOUT_S = 3.0

    def __init__(self, url: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 opener=None) -> None:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("HttpHaltStore needs a url")
        self.url = url.strip().rstrip("/")
        self.timeout_s = float(timeout_s)
        #: injected in tests; production uses urllib
        self._opener = opener

    def get(self, key: str) -> Optional[bytes]:
        import urllib.request

        # THIS STORE SERVES EXACTLY ONE KEY, and every other key is
        # UNREADABLE here rather than ABSENT.
        #
        # WHY THIS GUARD EXISTS. `l5_halt_server` 404s every path but
        # HALT_KEY, and `get` maps a 404 to None -- a POSITIVE absence. So an
        # admin caller that asked this store for some OTHER durable object on
        # the watchdog's volume would be told it is EMPTY by a server that
        # does not serve it at all. A one-time guard keyed on such an object
        # would then pass on a key it cannot see. That is the same
        # false-permissive read this module warns about for the latch itself,
        # one key over: absence and "not served here" must not arrive as the
        # same None.
        if str(key) != HALT_KEY:
            raise HaltReadUnavailable(
                "HttpHaltStore serves only %r: %r is not absent here, it is "
                "unreadable" % (HALT_KEY, str(key)[:60]))
        url = "%s/%s" % (self.url, str(key).lstrip("/"))
        # 404 is the ONE answer that means "there is no halt record". Every
        # other outcome is "I could not find out", and the two must not
        # arrive as the same None -- see HaltReadUnavailable. The gate's
        # behaviour is identical either way; what changes is that an asserter
        # over a store of this shape can no longer mistake a dead endpoint
        # for a clean one.
        try:
            if self._opener is not None:
                return self._opener(url, self.timeout_s)
            with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                status = getattr(r, "status", 200)
                if status == 404:
                    return None
                if status != 200:
                    raise HaltReadUnavailable("halt GET status %s" % status)
                return r.read()
        except HaltReadUnavailable:
            raise
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            if getattr(exc, "code", None) == 404:       # urllib HTTPError
                return None
            raise HaltReadUnavailable(
                "halt GET failed: %s" % type(exc).__name__) from exc

    def put(self, key: str, body: bytes) -> Any:
        # The executor holds this object. It must not be able to write the
        # latch that governs it, so there is no write path here at all.
        raise PermissionError(
            "HttpHaltStore is read-only: the executor may not write the "
            "halt record that governs it")
