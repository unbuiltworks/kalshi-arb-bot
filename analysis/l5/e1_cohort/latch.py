"""The durable BOUNDED-COUNT latch, as NAMED SLOTS and never as a counter.

XR2W's latch is a single slot: one file, `O_CREAT|O_EXCL`, session-scoped,
consumed before the POST and never released on failure. E1 needs twenty
submissions per window, so the latch has to be bounded-count rather than
single-slot -- and the obvious implementation is the wrong one.

WHY NOT A COUNTER. A counter is read-then-write. Two processes read `7`,
both write `8`, and two orders go into slot 7. The read and the write are
not one operation, so a counter bounds nothing under a race; it only looks
like it does. Worse, the race is exactly the condition a latch exists for.

SO: TWENTY NAMED SLOTS PER WINDOW. Slot `w03.s07` is one file and one
`O_CREAT|O_EXCL` create, which exactly one caller can win even if twenty
processes start at once. Claiming means walking the slots and taking the
first one whose create succeeds; `FileExistsError` is not an error, it is
the next caller's answer. When all twenty exist the window is exhausted,
and that is a bound on the number of FILES, which no race can inflate.

CONSUMED AT THE ATTEMPT, NOT AT SUCCESS, and NEVER RELEASED. Inherited
verbatim from XR2W and for the same reason: a submission whose outcome we
do not know may have rested a real order, so retrying it could double the
authorized exposure. An unknown outcome is spent.

SESSION-SCOPED. Keyed by the owner's `PMUS_EXEC1_SESSION_ID`. A future,
separately authorized cohort gets fresh slots by the owner choosing a new
session id and cannot get them any other way. Claude never clears a slot
and there is no code here that deletes one.

TWO KINDS OF SLOT. A first leg and its complement are separate POSTs, so
each consumes its own slot, under role `first_leg` or `complement`. The
window's twenty slots are the twenty ATTEMPTS, and an attempt is a first
leg; complement slots are named off the attempt they belong to, so a
complement can never consume an attempt another first leg needed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from analysis.l5.e1_cohort.spec import (MAX_ATTEMPTS_PER_WINDOW, PROBE,
                                        WINDOWS, assert_frozen)
from analysis.l5.live_exec2.spec import LATCH_DIR

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

ROLE_FIRST_LEG = "first_leg"
ROLE_COMPLEMENT = "complement"
ROLES = (ROLE_FIRST_LEG, ROLE_COMPLEMENT)


class LatchSpent(RuntimeError):
    """This exact named slot has already been taken."""


class WindowExhausted(RuntimeError):
    """All twenty of this window's attempt slots exist. No more attempts."""


class LatchUnavailable(RuntimeError):
    """The latch could not be established, so nothing may be submitted.

    FAIL CLOSED. If the durable store is unwritable we do NOT fall back to
    an in-memory bound, because that is the weaker guarantee this exists to
    replace.
    """


def _session_id() -> str:
    sid = os.getenv("PMUS_EXEC1_SESSION_ID", "").strip()
    if not sid:
        raise LatchUnavailable(
            "PMUS_EXEC1_SESSION_ID is not set; the cohort latch has no "
            "session to scope itself to. This is an OWNER value and no code "
            "in this package sets it.")
    return sid


def _stem(session_id: str) -> str:
    stem = _SAFE.sub("-", session_id)[:40]
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return "e1.%s.%s" % (stem, digest)


def _check_window(window: int) -> None:
    if not isinstance(window, int) or isinstance(window, bool):
        raise LatchUnavailable("window must be an int, got %r" % (window,))
    if not 0 <= window < WINDOWS:
        raise LatchUnavailable(
            "window %r is outside the authorized 0..%d"
            % (window, WINDOWS - 1))


def _check_slot(slot: int) -> None:
    if not 0 <= slot < MAX_ATTEMPTS_PER_WINDOW:
        raise LatchUnavailable(
            "slot %r is outside the authorized 0..%d"
            % (slot, MAX_ATTEMPTS_PER_WINDOW - 1))


def slot_path(*, window: int, slot: int, role: str = ROLE_FIRST_LEG,
              session_id: Optional[str] = None) -> str:
    """One NAMED file per (session, window, attempt slot, role)."""
    _check_window(window)
    _check_slot(slot)
    if role not in ROLES:
        raise LatchUnavailable("unknown latch role %r" % (role,))
    sid = session_id or _session_id()
    return os.path.join(
        LATCH_DIR, "%s.w%02d.s%02d.%s.submitted"
        % (_stem(sid), window, slot, role))


def window_state(*, window: int,
                 session_id: Optional[str] = None) -> Dict[str, Any]:
    """Read-only. Never creates, never consumes."""
    _check_window(window)
    try:
        paths = [slot_path(window=window, slot=i, role=ROLE_FIRST_LEG,
                           session_id=session_id)
                 for i in range(MAX_ATTEMPTS_PER_WINDOW)]
    except LatchUnavailable as exc:
        return {"available": False, "window": window, "why": str(exc),
                "attempts_spent": None, "attempts_remaining": None}
    spent = [i for i, p in enumerate(paths) if os.path.exists(p)]
    return {"available": True, "window": window,
            "slots_per_window": MAX_ATTEMPTS_PER_WINDOW,
            "attempts_spent": len(spent), "spent_slots": spent,
            "attempts_remaining": MAX_ATTEMPTS_PER_WINDOW - len(spent),
            "exhausted": len(spent) >= MAX_ATTEMPTS_PER_WINDOW,
            "dir": LATCH_DIR, "dir_exists": os.path.isdir(LATCH_DIR),
            "is_a_bare_counter": False}


def cohort_state(session_id: Optional[str] = None) -> Dict[str, Any]:
    windows = [window_state(window=w, session_id=session_id)
               for w in range(WINDOWS)]
    spent = sum(w.get("attempts_spent") or 0 for w in windows)
    return {"probe": PROBE, "windows": windows,
            "attempts_spent_total": spent,
            "max_placements": WINDOWS * MAX_ATTEMPTS_PER_WINDOW,
            "attempts_remaining_total":
                WINDOWS * MAX_ATTEMPTS_PER_WINDOW - spent}


def _write_slot(path: str, record: Dict[str, Any]) -> None:
    try:
        os.makedirs(LATCH_DIR, exist_ok=True)
    except OSError as exc:
        raise LatchUnavailable("latch directory %s is not writable: %s"
                               % (LATCH_DIR, type(exc).__name__)) from exc
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf8") as fh:
            json.dump(record, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        # The file exists, so the slot IS spent. It is NOT unlinked: a
        # half-written slot still means an attempt is about to happen.
        raise LatchUnavailable(
            "slot taken but not durably written (%s); refusing to submit"
            % type(exc).__name__) from exc


def _record(*, window: int, slot: int, role: str, slug: str, side: str,
            price: Any, qty: Any) -> Dict[str, Any]:
    return {"probe": PROBE, "window": window, "slot": slot, "role": role,
            "slug": slug, "side": side, "price": str(price), "qty": str(qty),
            "consumed_unix": time.time(),
            "consumed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()),
            "pid": os.getpid(),
            "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
            "MEANING": ("one submission ATTEMPT was made. This is NOT a "
                        "claim that an order rested, filled or settled."),
            "NOT_RELEASED_ON_FAILURE": True}


def consume_attempt(*, window: int, slug: str, side: str, price: Any,
                    qty: Any,
                    session_id: Optional[str] = None) -> Dict[str, Any]:
    """Claim the lowest unclaimed attempt slot in this window.

    Call IMMEDIATELY BEFORE the POST. Walks the twenty named slots and
    takes the first whose O_EXCL create wins; a `FileExistsError` is the
    next caller's answer, not an error. Raises `WindowExhausted` when all
    twenty files exist, which is a bound on FILES that no race inflates.
    """
    assert_frozen()
    _check_window(window)
    last: List[int] = []
    for i in range(MAX_ATTEMPTS_PER_WINDOW):
        path = slot_path(window=window, slot=i, role=ROLE_FIRST_LEG,
                         session_id=session_id)
        rec = _record(window=window, slot=i, role=ROLE_FIRST_LEG, slug=slug,
                      side=side, price=price, qty=qty)
        try:
            _write_slot(path, rec)
        except FileExistsError:
            last.append(i)
            continue
        except OSError as exc:
            raise LatchUnavailable("could not create the slot at %s: %s"
                                   % (path, type(exc).__name__)) from exc
        rec["path"] = path
        return rec
    raise WindowExhausted(
        "window %d has spent all %d authorized attempt slots (%s). A "
        "twenty-first attempt is refused, including one made to repair a "
        "loss or to retry an unknown outcome."
        % (window, MAX_ATTEMPTS_PER_WINDOW, last))


def consume_complement(*, window: int, slot: int, slug: str, side: str,
                       price: Any, qty: Any,
                       session_id: Optional[str] = None) -> Dict[str, Any]:
    """Claim the complement slot BELONGING TO one attempt slot.

    Named off the attempt, so a complement can never consume an attempt
    slot another first leg needed. Exactly one complement per attempt:
    the second call raises `LatchSpent`, which is what forbids a re-quote
    at the latch rather than only in the policy.
    """
    assert_frozen()
    path = slot_path(window=window, slot=slot, role=ROLE_COMPLEMENT,
                     session_id=session_id)
    rec = _record(window=window, slot=slot, role=ROLE_COMPLEMENT, slug=slug,
                  side=side, price=price, qty=qty)
    try:
        _write_slot(path, rec)
    except FileExistsError as exc:
        raise LatchSpent(
            "the complement for window %d slot %d has already been "
            "submitted once (%s). A re-quote is refused."
            % (window, slot, path)) from exc
    except OSError as exc:
        raise LatchUnavailable("could not create the slot at %s: %s"
                               % (path, type(exc).__name__)) from exc
    rec["path"] = path
    return rec
