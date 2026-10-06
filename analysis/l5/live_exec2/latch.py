"""The durable one-submission latch.

WHAT WENT WRONG WITH THE OLD ONE. EXEC-1's `OneOrderOnly` lives in memory.
Within one process it refuses a second order, including one made to repair a
loss on the first. Across a restart it refuses nothing, because the object is
gone -- so the "exactly one order" guarantee actually rested on
restartPolicy=NEVER, a control-plane setting outside the experiment. A latch
whose guarantee depends on something it does not own is not a latch.

THIS ONE LIVES ON THE VOLUME. `/state/pmus-ws` is a mounted Railway volume on
the credentialed service, so the file outlives the process and the container.

CONSUMED AT THE ATTEMPT, NOT AT SUCCESS. The latch is taken BEFORE the POST
and is NOT released if the POST fails, times out, or the process dies
mid-flight. That is deliberate and it is the only safe direction: a submission
whose outcome we do not know may well have reached the venue and rested a real
order. Re-trying it could leave TWO live orders and double the authorized
exposure. So an unknown outcome is treated as spent, and the experiment stops
and is reported rather than retried. The cost of that choice is a possible
wasted run; the cost of the other choice is an unauthorized second order.

SESSION-SCOPED. The file is keyed by the owner's PMUS_EXEC1_SESSION_ID, so a
future, separately authorized run gets a fresh latch by the owner choosing a
new session id -- and cannot get one any other way. Claude never clears a
latch, and there is no code here that deletes one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, Optional

from analysis.l5.live_exec2.spec import LATCH_DIR, PROBE

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class LatchSpent(RuntimeError):
    """The one authorized submission attempt has already been made."""


class LatchUnavailable(RuntimeError):
    """The latch could not be established, so nothing may be submitted.

    Fail CLOSED: if the durable store is unwritable we do not fall back to an
    in-memory latch, because that is exactly the weaker guarantee this exists
    to replace.
    """


def _session_id() -> str:
    sid = os.getenv("PMUS_EXEC1_SESSION_ID", "").strip()
    if not sid:
        raise LatchUnavailable("PMUS_EXEC1_SESSION_ID is not set; the latch "
                               "has no session to scope itself to")
    return sid


def latch_path(session_id: Optional[str] = None) -> str:
    sid = session_id or _session_id()
    # the id goes through a digest as well as a sanitiser, so two different
    # owner ids can never collide on one file after sanitising
    stem = _SAFE.sub("-", sid)[:48]
    digest = hashlib.sha256(sid.encode()).hexdigest()[:16]
    return os.path.join(LATCH_DIR, "%s.%s.submitted" % (stem, digest))


def state(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only. Never creates, never consumes."""
    try:
        path = latch_path(session_id)
    except LatchUnavailable as exc:
        return {"available": False, "spent": None, "path": None,
                "why": str(exc)}
    spent = os.path.exists(path)
    rec = None
    if spent:
        try:
            with open(path, "r", encoding="utf8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = {"unreadable": True}
    return {"available": True, "spent": spent, "path": path, "record": rec,
            "dir": LATCH_DIR, "dir_exists": os.path.isdir(LATCH_DIR)}


def consume(*, slug: str, side: str, price: Any, qty: Any,
            session_id: Optional[str] = None) -> Dict[str, Any]:
    """Take the one submission. Call this IMMEDIATELY BEFORE the POST.

    Atomic: O_CREAT|O_EXCL means exactly one caller can win, even if two
    processes race. Raises LatchSpent if it was already taken -- including by
    an earlier process whose POST outcome is unknown.
    """
    path = latch_path(session_id)
    try:
        os.makedirs(LATCH_DIR, exist_ok=True)
    except OSError as exc:
        raise LatchUnavailable("latch directory %s is not writable: %s"
                               % (LATCH_DIR, type(exc).__name__)) from exc
    record = {"probe": PROBE, "slug": slug, "side": side,
              "price": str(price), "qty": str(qty),
              "consumed_unix": time.time(),
              "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
              "pid": os.getpid(),
              "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
              "MEANING": ("one submission ATTEMPT was made. This is NOT a "
                          "claim that an order rested or filled."),
              "NOT_RELEASED_ON_FAILURE": True}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise LatchSpent(
            "LIVE-L5-EXEC-1-X has already made its one authorized submission "
            "attempt (%s). A second is refused, including one made to repair "
            "a loss or to retry an unknown outcome." % path) from exc
    except OSError as exc:
        raise LatchUnavailable("could not create the latch at %s: %s"
                               % (path, type(exc).__name__)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf8") as fh:
            json.dump(record, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        # the file exists, so the latch IS spent. We do not unlink it: a
        # half-written latch still means an attempt is about to happen.
        raise LatchUnavailable(
            "latch taken but not durably written (%s); refusing to submit"
            % type(exc).__name__) from exc
    return record
