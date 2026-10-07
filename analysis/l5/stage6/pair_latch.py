"""The durable TWO-SLOT submission latch.

WHY NOT REUSE THE ONE-SLOT LATCH UNCHANGED. `analysis.l5.live_exec2.latch`
authorizes exactly ONE `POST /v1/orders` per session, which is correct for
XR2W and wrong for a matched-pair probe that must place a first leg and then
a complement. The mechanism is kept verbatim -- `O_CREAT|O_EXCL`,
session-scoped, consumed BEFORE the POST, never released on failure, never
deleted by any code here -- and only the number of slots changes, from one to
two NAMED slots:

    FIRST_LEG     at most one POST /v1/orders for the first leg
    COMPLEMENT    at most one POST /v1/orders for the complement

Two slots is not "a looser latch". It is the same guarantee stated twice,
and it is strictly stronger than one slot of capacity two would be: a spent
FIRST_LEG slot cannot pay for a complement, and a spent COMPLEMENT slot
cannot pay for a second first leg. A capacity counter would allow two first
legs, which is a different experiment.

CONSUMED AT THE ATTEMPT, NOT AT SUCCESS, for the reason the one-slot latch
records: a submission whose outcome we do not know may have rested a real
order, and retrying it could leave two live orders and double the authorized
exposure. An unknown outcome is therefore SPENT, and the probe stops and
reports rather than retrying.

ORDERING IS ENFORCED, NOT ASSUMED. `consume("COMPLEMENT")` refuses unless
the FIRST_LEG slot is already spent. A complement without a first leg is an
unhedged one-leg order under a name that implies otherwise.

A SEPARATE DIRECTORY AND A SEPARATE SESSION VARIABLE. The slot files live
under their own directory and are keyed by `PMUS_S6P_SESSION_ID`, so an
XR2W latch can never satisfy a Stage-6 slot and a Stage-6 session can never
consume XR2W's. Claude never clears a latch and there is no code here that
deletes one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, Optional

#: its own directory, parallel to live_exec2's `/state/pmus-ws/exec1x`
LATCH_DIR = "/state/pmus-ws/stage6"

#: its own session variable. XR2W uses PMUS_EXEC1_SESSION_ID.
SESSION_ENV = "PMUS_S6P_SESSION_ID"

PROBE = "STAGE-6-S6P"

SLOTS = ("FIRST_LEG", "COMPLEMENT")

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class LatchSpent(RuntimeError):
    """That slot's one authorized submission attempt has already been made."""


class LatchOutOfOrder(RuntimeError):
    """A complement slot was reached for without a spent first-leg slot."""


class LatchUnavailable(RuntimeError):
    """The latch could not be established, so nothing may be submitted.

    Fail CLOSED. No in-memory fallback: that is the weaker guarantee the
    durable latch exists to replace.
    """


def _session_id(session_id: Optional[str] = None) -> str:
    sid = (session_id or os.getenv(SESSION_ENV, "")).strip()
    if not sid:
        raise LatchUnavailable(
            "%s is not set; the latch has no session to scope itself to"
            % SESSION_ENV)
    return sid


def _check_slot(slot: str) -> str:
    s = str(slot).upper()
    if s not in SLOTS:
        raise LatchUnavailable("unknown latch slot %r; slots are %s"
                               % (slot, list(SLOTS)))
    return s


def latch_path(slot: str, session_id: Optional[str] = None,
               latch_dir: Optional[str] = None) -> str:
    s = _check_slot(slot)
    sid = _session_id(session_id)
    stem = _SAFE.sub("-", sid)[:48]
    digest = hashlib.sha256(sid.encode()).hexdigest()[:16]
    return os.path.join(latch_dir or LATCH_DIR,
                        "%s.%s.%s.submitted" % (stem, digest, s.lower()))


def slot_state(slot: str, session_id: Optional[str] = None,
               latch_dir: Optional[str] = None) -> Dict[str, Any]:
    """Read-only. Never creates, never consumes."""
    try:
        path = latch_path(slot, session_id, latch_dir)
    except LatchUnavailable as exc:
        return {"slot": str(slot).upper(), "available": False, "spent": None,
                "path": None, "why": str(exc)}
    spent = os.path.exists(path)
    rec = None
    if spent:
        try:
            with open(path, "r", encoding="utf8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = {"unreadable": True}
    return {"slot": _check_slot(slot), "available": True, "spent": spent,
            "path": path, "record": rec}


def state(session_id: Optional[str] = None,
          latch_dir: Optional[str] = None) -> Dict[str, Any]:
    slots = {s: slot_state(s, session_id, latch_dir) for s in SLOTS}
    return {"probe": PROBE, "dir": latch_dir or LATCH_DIR,
            "dir_exists": os.path.isdir(latch_dir or LATCH_DIR),
            "session_env": SESSION_ENV, "slots": slots,
            "submissions_remaining": sum(
                1 for v in slots.values() if v.get("spent") is False),
            "consumed_before_the_post": True,
            "released_on_failure": False}


def consume(slot: str, *, slug: str, side: str, price: Any, qty: Any,
            session_id: Optional[str] = None,
            latch_dir: Optional[str] = None) -> Dict[str, Any]:
    """Take one named slot. Call IMMEDIATELY BEFORE that leg's POST.

    Atomic: `O_CREAT|O_EXCL` means exactly one caller wins even if two
    processes race.
    """
    s = _check_slot(slot)
    d = latch_dir or LATCH_DIR
    if s == "COMPLEMENT":
        first = slot_state("FIRST_LEG", session_id, latch_dir)
        if not first.get("spent"):
            raise LatchOutOfOrder(
                "the COMPLEMENT slot may not be consumed before the "
                "FIRST_LEG slot. A complement without a first leg is an "
                "unhedged one-leg order under a misleading name.")
    path = latch_path(s, session_id, latch_dir)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as exc:
        raise LatchUnavailable("latch directory %s is not writable: %s"
                               % (d, type(exc).__name__)) from exc
    record = {"probe": PROBE, "slot": s, "slug": slug, "side": side,
              "price": str(price), "qty": str(qty),
              "consumed_unix": time.time(),
              "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                            time.gmtime()),
              "pid": os.getpid(),
              "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
              "MEANING": ("one submission ATTEMPT was made on this slot. "
                          "This is NOT a claim that an order rested, "
                          "filled, or settled."),
              "NOT_RELEASED_ON_FAILURE": True}
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise LatchSpent(
            "%s has already made its one authorized %s submission attempt "
            "(%s). A second is refused, including one made to repair a loss "
            "or to retry an unknown outcome." % (PROBE, s, path)) from exc
    except OSError as exc:
        raise LatchUnavailable("could not create the latch at %s: %s"
                              % (path, type(exc).__name__)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf8") as fh:
            json.dump(record, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise LatchUnavailable(
            "latch slot %s taken but not durably written (%s); refusing to "
            "submit" % (s, type(exc).__name__)) from exc
    return record
