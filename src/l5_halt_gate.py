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


class ObjectStore(Protocol):
    """The three verbs. Deliberately not a Railway or S3 type."""

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
        """
        cur = _read(self.store)
        if cur is not None and cur.get("halt") is True:
            raise PermissionError(
                "refusing to overwrite a latched HALT: clearing is an owner "
                "action, not an automated one")
        rec = {"halt": False, "t": _utc().isoformat(), "who": self.who}
        self.store.put(HALT_KEY, json.dumps(rec, sort_keys=True).encode())
        return rec


def _read(store: ObjectStore) -> Optional[dict]:
    """None means 'could not be established', which the gate treats as halt."""
    try:
        raw = store.get(HALT_KEY)
    except BaseException:                                      # noqa: BLE001
        return None
    if raw is None:
        return None
    try:
        rec = json.loads(raw)
    except BaseException:                                      # noqa: BLE001
        return None
    return rec if isinstance(rec, dict) else None


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

    def state(self) -> dict:
        if str(self.env_halt or "").strip():
            return {"permitted": False, "why": "env_halt", "record": None}
        rec = _read(self.store)
        if rec is None:
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

        url = "%s/%s" % (self.url, str(key).lstrip("/"))
        try:
            if self._opener is not None:
                return self._opener(url, self.timeout_s)
            with urllib.request.urlopen(url, timeout=self.timeout_s) as r:
                if getattr(r, "status", 200) != 200:
                    return None
                return r.read()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return None

    def put(self, key: str, body: bytes) -> Any:
        # The executor holds this object. It must not be able to write the
        # latch that governs it, so there is no write path here at all.
        raise PermissionError(
            "HttpHaltStore is read-only: the executor may not write the "
            "halt record that governs it")
