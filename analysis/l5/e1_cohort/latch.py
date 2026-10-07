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

from analysis.l5.e1_cohort.spec import (MAX_ATTEMPTS_PER_WINDOW,
                                        MAX_COMPLEMENT_TRANCHES, PROBE,
                                        WINDOWS, assert_frozen)
from analysis.l5.e1_cohort.arming import SESSION_ID_ENV, session_id
from analysis.l5.live_exec2.spec import LATCH_DIR as XR2W_LATCH_DIR

#: E1's OWN state directory. It may share the mounted volume
#: `/state/pmus-ws` because that is operationally convenient, but it is a
#: DIFFERENT directory from XR2W's `exec1x`, and every E1 file is further
#: prefixed `e1.`. XR2W's SPENT A6 latch is EVIDENCE: nothing in this
#: module deletes, resets, renames, reads or writes it.
LATCH_DIR = "/state/pmus-ws/e1_cohort"

#: belt and braces for the boot assertion below.
E1_FILE_PREFIX = "e1."

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
    sid = session_id()
    if not sid:
        raise LatchUnavailable(
            "%s is not set; the cohort latch has no session to scope "
            "itself to. This is an OWNER value in E1's OWN namespace "
            "(PMUS_E1_*), and no code in this package sets it. E1 does NOT "
            "read PMUS_EXEC1_SESSION_ID." % SESSION_ID_ENV)
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
              tranche: int = 0,
              session_id: Optional[str] = None) -> str:
    """One NAMED file per (session, window, attempt slot, role, tranche).

    `tranche` exists because a first leg can fill in several pieces and the
    complement must track the CUMULATIVE fill. Each tranche is its own named
    slot, so a tranche is won by exactly one O_EXCL create like everything
    else, and the SAME tranche can never be submitted twice -- which is what
    forbids a re-quote at the latch rather than only in the policy.
    """
    _check_window(window)
    _check_slot(slot)
    if role not in ROLES:
        raise LatchUnavailable("unknown latch role %r" % (role,))
    if not 0 <= tranche < MAX_COMPLEMENT_TRANCHES:
        raise LatchUnavailable(
            "tranche %r is outside the bounded 0..%d"
            % (tranche, MAX_COMPLEMENT_TRANCHES - 1))
    sid = session_id or _session_id()
    return os.path.join(
        LATCH_DIR, "%s.w%02d.s%02d.%s.t%02d.submitted"
        % (_stem(sid), window, slot, role, tranche))


def assert_no_collision_with_xr2w(session_id_override: Optional[str] = None
                                  ) -> Dict[str, Any]:
    """BOOT ASSERTION: no E1 slot can resolve into XR2W's latch directory.

    XR2W's spent A6 latch is EVIDENCE. E1 must be unable to spend it, unable
    to be refused by it, and unable to touch it. This proves the first two
    structurally -- by showing that EVERY slot path E1 can generate lies
    under E1's own directory and nowhere near XR2W's -- and the third is
    true because no function in this module names XR2W's directory except
    to assert this.
    """
    e1_dir = os.path.realpath(LATCH_DIR)
    xr_dir = os.path.realpath(XR2W_LATCH_DIR)
    if e1_dir == xr_dir or e1_dir.startswith(xr_dir.rstrip("/") + "/"):
        raise LatchUnavailable(
            "E1's latch directory %s collides with XR2W's %s. XR2W's spent "
            "A6 latch is evidence and must be unreachable from here."
            % (e1_dir, xr_dir))
    sid = session_id_override or session_id() or "boot-assertion-probe"
    checked = []
    for w in range(WINDOWS):
        for i in range(MAX_ATTEMPTS_PER_WINDOW):
            for role in ROLES:
                for tr in (0, MAX_COMPLEMENT_TRANCHES - 1):
                    p = os.path.realpath(
                        slot_path(window=w, slot=i, role=role, tranche=tr,
                                  session_id=sid))
                    if p == xr_dir or os.path.dirname(p) == xr_dir \
                            or p.startswith(xr_dir.rstrip("/") + "/"):
                        raise LatchUnavailable(
                            "E1 slot %s resolves into XR2W's latch "
                            "directory %s" % (p, xr_dir))
                    if not os.path.basename(p).startswith(E1_FILE_PREFIX):
                        raise LatchUnavailable(
                            "E1 slot %s is not prefixed %r"
                            % (p, E1_FILE_PREFIX))
                    checked.append(p)
    return {"ok": True, "e1_latch_dir": e1_dir,
            "xr2w_latch_dir": xr_dir,
            "shares_the_mounted_volume": (
                os.path.dirname(e1_dir) == os.path.dirname(xr_dir)),
            "directories_are_distinct": True,
            "slot_paths_checked": len(checked),
            "every_slot_prefixed": E1_FILE_PREFIX,
            "no_e1_slot_resolves_to_the_xr2w_latch": True,
            "xr2w_a6_latch_is_evidence_and_is_never_touched": True,
            "e1_deletes_resets_or_renames_anything": False}


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
            price: Any, qty: Any, tranche: int = 0) -> Dict[str, Any]:
    return {"probe": PROBE, "window": window, "slot": slot, "role": role,
            "tranche": tranche,
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


def complement_tranches_spent(*, window: int, slot: int,
                              session_id: Optional[str] = None) -> int:
    """How many complement tranches this attempt has already submitted.
    Read-only; counted from the FILES, never from memory."""
    n = 0
    for i in range(MAX_COMPLEMENT_TRANCHES):
        if os.path.exists(slot_path(window=window, slot=slot,
                                    role=ROLE_COMPLEMENT, tranche=i,
                                    session_id=session_id)):
            n += 1
    return n


def consume_complement(*, window: int, slot: int, slug: str, side: str,
                       price: Any, qty: Any, tranche: int = 0,
                       session_id: Optional[str] = None) -> Dict[str, Any]:
    """Claim ONE complement TRANCHE slot belonging to one attempt slot.

    Named off the attempt, so a complement can never consume an attempt
    slot another first leg needed, and named off the TRANCHE, so the same
    tranche can never be submitted twice: the second call raises
    `LatchSpent`. That is what forbids a RE-QUOTE at the latch rather than
    only in the policy.

    A later tranche is a DIFFERENT slot, and it exists only because new
    first-leg inventory arrived. It is not a re-quote: it replaces nothing,
    cancels nothing, and carries the attempt's ORIGINAL complement price.
    The tranche count is bounded by MAX_COMPLEMENT_TRANCHES = 13, the
    number of 0.01 increments a 0.13 first leg can arrive in, so the bound
    comes from the venue grid rather than from a judgement.
    """
    assert_frozen()
    path = slot_path(window=window, slot=slot, role=ROLE_COMPLEMENT,
                     tranche=tranche, session_id=session_id)
    rec = _record(window=window, slot=slot, role=ROLE_COMPLEMENT, slug=slug,
                  side=side, price=price, qty=qty, tranche=tranche)
    try:
        _write_slot(path, rec)
    except FileExistsError as exc:
        raise LatchSpent(
            "complement tranche %d for window %d slot %d has already been "
            "submitted once (%s). A re-quote is refused."
            % (tranche, window, slot, path)) from exc
    except OSError as exc:
        raise LatchUnavailable("could not create the slot at %s: %s"
                               % (path, type(exc).__name__)) from exc
    rec["path"] = path
    return rec
