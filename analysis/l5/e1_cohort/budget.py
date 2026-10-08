"""THE PER-PROBE AUTHORIZATION BUDGET. Owner values that may only NARROW.

WHY THIS EXISTS. The frozen spec authorizes twelve windows and twenty
attempts per window -- 240 placements -- because that is the cohort E1 was
designed as. The owner's 2026-10-08 authorization is far smaller than that:
ONE bounded live probe, meaning one first-leg submission ATTEMPT and one
market window, total. Before this module there was no way to say so. The
bound lived in frozen constants, `main()` read no argument, and the only
lever was pre-creating latch files on the volume -- which writes a false
pending intent, sends a real preview, and then raises an uncaught
`WindowExhausted`. A safety limit whose enforcement mechanism can strand a
filled residual is not a safety limit.

So the limit becomes an AUTHORIZATION fact, in E1's own namespace, read from
the environment the owner controls:

    PMUS_E1_MAX_ATTEMPTS   how many first-leg submission ATTEMPTS, total
    PMUS_E1_MAX_WINDOWS    how many NEW market windows this process may open

THREE PROPERTIES, EACH DELIBERATE.

NARROWING ONLY. A value above the frozen maximum is refused, not clamped.
These keys can make a probe smaller than the frozen spec and can never make
one larger, so no environment variable can widen authorized risk.

FAIL CLOSED. Missing, blank, non-integer, zero, negative and excessive are
all the same answer: refused. There is no default, because a default is
exactly the silent 20/12 this exists to prevent.

COUNTED FROM DURABLE STATE, NEVER FROM MEMORY. `attempts_used` reads the
LATCH FILES -- the same O_EXCL slots that are consumed immediately before
the POST and never released -- plus any unretired local submission intent.
That means a restart cannot reset the budget, and an AMBIGUOUS submission
counts as a spent attempt, because a slot is consumed before the POST and an
unknown outcome is spent. "One attempt total, including ambiguous
submissions" is therefore a property of the filesystem, not of a counter.

ATTEMPTS ARE COUNTED AGAINST THE PROBE'S OWN BOUND WINDOW, NOT THE SESSION.

The first version counted every first-leg slot the SESSION had ever spent.
That was wrong in a way only the real authorization exposed: the existing
cohort session carries forty historical submissions, so `MAX_ATTEMPTS=1`
refused on the first iteration with `ATTEMPT_BUDGET_EXHAUSTED_40_OF_1` and
the authorized probe could never run. Lowering the session total to "one"
would have meant erasing history; raising the limit to forty-one would have
meant the one-attempt property rested on an arithmetic coincidence rather
than on the value.

So a probe authorized for exactly ONE window BINDS that window's identity to
a durable file, O_EXCL, before anything can be submitted, and counts its
attempts against that window alone. The bound window starts at zero spent
slots however long the session's history is; the slot files are durable, so
a restart reads the same binding and the same count; and because the binding
is immutable, a restart cannot quietly select a different window and get a
fresh allowance. The session's forty historical slots are read, never
touched, and never recounted.

WINDOWS ARE COUNTED RELATIVE TO THIS PROCESS. The cohort ledger already
holds three spent windows and that history is not rewritten here: the budget
records `windows_used_at_start` at boot and allows `max_windows` NEW ones on
top of it. The existing append-only ledger is read and never altered.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterable, Optional

from analysis.l5.e1_cohort.spec import (MAX_ATTEMPTS_PER_WINDOW, PROBE,
                                        WINDOWS, assert_frozen)
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort.order_state import LOCAL_INTENT

MAX_ATTEMPTS_ENV = "PMUS_E1_MAX_ATTEMPTS"
MAX_WINDOWS_ENV = "PMUS_E1_MAX_WINDOWS"
BUDGET_KEYS = (MAX_ATTEMPTS_ENV, MAX_WINDOWS_ENV)

#: the frozen ceilings these keys may narrow and can never exceed.
FROZEN_MAX_ATTEMPTS = WINDOWS * MAX_ATTEMPTS_PER_WINDOW
FROZEN_MAX_WINDOWS = WINDOWS


#: the durable file that pins a one-window probe to ONE window identity.
BINDING_SUFFIX = ".probe_window_binding.json"

#: THE PROBE EPOCH, and why a second authorization needs one.
#:
#: The window binding is immutable and never released, so that a probe
#: cannot collect a fresh attempt allowance in each window it chooses.
#: That is exactly right WITHIN one authorization -- and it means a SECOND
#: authorization inherits the first one's bound window and refuses every
#: window thereafter. The 22:00z one-attempt probe bound window 3; the
#: three-attempt probe that followed it read that binding, found window 4
#: did not match, and refused with PROBE_IS_BOUND_TO_WINDOW_3 having
#: placed nothing.
#:
#: The fix is a NAMESPACE, not a reset. Each authorization names its own
#: epoch and binds inside it. The previous epoch's file is never read,
#: never written and never deleted -- it stays on the volume as the
#: evidence of what that probe was authorized to do. Nothing is reused
#: silently, because an epoch is explicit: set it and only that namespace
#: exists for this process; leave it unset and the legacy path is used,
#: which is what the already-completed probes bound within.
EPOCH_ENV = "PMUS_E1_PROBE_EPOCH"

#: an epoch is a short durable label, not free text: it becomes a filename.
EPOCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BudgetNotAuthorized(RuntimeError):
    """The per-probe budget could not be established. Nothing may submit."""


def _narrow(env: str, frozen_max: int) -> int:
    raw = (os.getenv(env) or "").strip()
    if not raw:
        raise BudgetNotAuthorized(
            "%s is not set. This probe's budget has no default: the frozen "
            "maximum is %d and a missing value would silently authorize it. "
            "Set %s to the number this probe is actually authorized for."
            % (env, frozen_max, env))
    if not raw.isdigit():
        raise BudgetNotAuthorized(
            "%s=%r is not a non-negative integer. Refused; no partial "
            "interpretation is attempted." % (env, raw))
    val = int(raw)
    if val < 1:
        raise BudgetNotAuthorized(
            "%s=%d authorizes nothing. If the intent is to place nothing, "
            "leave E1 disarmed rather than arming it with a zero budget."
            % (env, val))
    if val > frozen_max:
        raise BudgetNotAuthorized(
            "%s=%d exceeds the frozen maximum %d. These keys may only "
            "NARROW the frozen specification; they can never widen it."
            % (env, val, frozen_max))
    return val


class LatchScopeUnreadable(RuntimeError):
    """The bound window's latch could not be read. Counted as spent."""


class ProbeBindingConflict(BudgetNotAuthorized):
    """This probe is already bound to a DIFFERENT window identity.

    Raised rather than resolved. A probe that could rebind would get a fresh
    attempt allowance every time it chose another window, which is the exact
    hole the binding exists to close.
    """


def probe_epoch(*, epoch: Optional[str] = None) -> Optional[str]:
    """This authorization's binding namespace, or None for the legacy one.

    A malformed epoch is a REFUSAL rather than a sanitised filename: it
    decides which durable binding this process may write, and guessing
    what the operator meant is how two authorizations end up sharing one.
    """
    raw = epoch if epoch is not None else (os.getenv(EPOCH_ENV) or "")
    raw = str(raw).strip()
    if not raw:
        return None
    if not EPOCH_RE.match(raw):
        raise BudgetNotAuthorized(
            "%s=%r is not a valid probe epoch. It must be 1-64 characters "
            "of letters, digits, dot, dash or underscore, starting "
            "alphanumeric, because it names a durable binding file."
            % (EPOCH_ENV, raw))
    return raw


def binding_path(*, session_id: Optional[str] = None,
                 epoch: Optional[str] = None) -> str:
    sid = session_id or os.getenv("PMUS_E1_SESSION_ID", "").strip()
    if not sid:
        raise BudgetNotAuthorized(
            "PMUS_E1_SESSION_ID is not set, so this probe has no session to "
            "bind a window identity within. Refused.")
    ep = probe_epoch(epoch=epoch)
    stem = L.stem(sid) if ep is None else "%s.epoch-%s" % (L.stem(sid), ep)
    return os.path.join(L.LATCH_DIR, stem + BINDING_SUFFIX)


def read_binding(*, session_id: Optional[str] = None,
                 epoch: Optional[str] = None
                 ) -> Optional[Dict[str, Any]]:
    """The bound window identity, or None when nothing is bound yet.

    FAILS CLOSED on a file that exists but cannot be read or parsed: a
    binding we cannot read is a binding we must not contradict.
    """
    path = binding_path(session_id=session_id, epoch=epoch)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BudgetNotAuthorized(
            "the probe's window binding at %s exists but could not be read "
            "(%s). Refusing: a binding we cannot read is a binding we must "
            "not contradict." % (path, type(exc).__name__)) from exc
    if not isinstance(rec, dict) or not isinstance(rec.get("window"), int):
        raise BudgetNotAuthorized(
            "the probe's window binding at %s is malformed. Refused."
            % (path,))
    return rec


#: archives are named so they can never collide with a live binding.
ARCHIVE_SUFFIX = ".probe_window_binding.RETIRED.json"


def archive_binding(*, session_id: Optional[str] = None,
                    epoch: Optional[str] = None,
                    label: str,
                    reason: str,
                    evidence: Optional[Dict[str, Any]] = None
                    ) -> Dict[str, Any]:
    """COPY a completed probe's binding into an immutable archive.

    The original is NEVER touched: not rewritten, not renamed, not
    deleted. It stays exactly where the probe wrote it, because it is the
    durable record of what that probe was authorized to do and the proof
    that it bound one window and only one.

    The archive is created `O_CREAT|O_EXCL`, so an archive that already
    exists is never overwritten either -- retiring twice is refused
    rather than silently reproducing a different record under the same
    name. It carries the original's full contents plus the provenance of
    the retirement, so the pair reads as a closed authorization.

    This function authorizes nothing and releases nothing. A new probe
    becomes able to bind only because it names a NEW EPOCH, which is a
    different file; retiring is the bookkeeping that makes that honest.
    """
    if not EPOCH_RE.match(str(label or "")):
        raise BudgetNotAuthorized(
            "the archive label %r is not valid; it names a file." % (label,))
    src = binding_path(session_id=session_id, epoch=epoch)
    rec = read_binding(session_id=session_id, epoch=epoch)
    if rec is None:
        return {"archived": False,
                "why": "NO_BINDING_AT_%s" % src,
                "original_path": src,
                "original_untouched": True}
    dst = os.path.join(L.LATCH_DIR,
                       "%s.%s" % (L.stem(session_id or os.getenv(
                           "PMUS_E1_SESSION_ID", "").strip()), label)
                       + ARCHIVE_SUFFIX)
    payload = {
        "ARCHIVE_OF": src,
        "label": label,
        "retired_reason": reason,
        "retired_unix": time.time(),
        "retired_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "original_binding": dict(rec),
        "window": rec.get("window"),
        "slug": rec.get("slug"),
        "epoch": rec.get("epoch"),
        "session_id": (session_id
                       or os.getenv("PMUS_E1_SESSION_ID", "").strip()),
        "evidence_at_retirement": dict(evidence or {}),
        "ORIGINAL_WAS_NOT_DELETED_OR_REWRITTEN": True,
        "THIS_AUTHORIZES_NOTHING": True,
        "a_new_probe_binds_in_a_NEW_EPOCH_not_by_reusing_this": True,
    }
    try:
        os.makedirs(L.LATCH_DIR, exist_ok=True)
        fd = os.open(dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return {"archived": False,
                "why": "ARCHIVE_ALREADY_EXISTS_NOT_OVERWRITTEN",
                "archive_path": dst, "original_path": src,
                "original_untouched": True}
    except OSError as exc:
        raise BudgetNotAuthorized(
            "the binding archive could not be written to %s (%s)."
            % (dst, type(exc).__name__)) from exc
    with os.fdopen(fd, "w", encoding="utf8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    return {"archived": True, "archive_path": dst, "original_path": src,
            "original_untouched": os.path.exists(src),
            "window": rec.get("window"), "slug": rec.get("slug"),
            "record": payload}


def bind_window(*, window: int, slug: str,
                session_id: Optional[str] = None,
                epoch: Optional[str] = None) -> Dict[str, Any]:
    """Pin this probe to ONE window identity, once, durably.

    Called BEFORE the window is recorded as opened, and therefore before any
    intent, preview or POST can exist. The create is `O_CREAT|O_EXCL`, so
    exactly one caller writes it even if several start at once; every later
    caller READS it and must match. A mismatch raises
    `ProbeBindingConflict` and nothing is overwritten -- this function never
    deletes, rewrites or releases a binding.
    """
    if not isinstance(window, int) or isinstance(window, bool):
        raise BudgetNotAuthorized("window must be an int, got %r" % (window,))
    if not 0 <= window < WINDOWS:
        raise BudgetNotAuthorized(
            "window %r is outside the authorized 0..%d"
            % (window, WINDOWS - 1))
    existing = read_binding(session_id=session_id, epoch=epoch)
    if existing is not None:
        if int(existing["window"]) != int(window) or (
                existing.get("slug") and slug
                and str(existing["slug"]) != str(slug)):
            raise ProbeBindingConflict(
                "this probe is already bound to window %r (%s) and cannot "
                "be rebound to window %r (%s). A rebind would grant a fresh "
                "attempt allowance, which is what the binding forbids."
                % (existing.get("window"), existing.get("slug"), window,
                   slug))
        return dict(existing, already_bound=True)
    path = binding_path(session_id=session_id, epoch=epoch)
    rec = {"window": int(window), "slug": str(slug),
           "epoch": probe_epoch(epoch=epoch),
           "bound_unix": time.time(),
           "bound_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "pid": os.getpid(),
           "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
           "MEANING": ("the ONE window this probe is authorized to work. "
                       "Attempts are counted against this window alone."),
           "IMMUTABLE": True, "NEVER_RELEASED": True}
    try:
        os.makedirs(L.LATCH_DIR, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # lost the race; the winner's binding is authoritative.
        return bind_window(window=window, slug=slug,
                           session_id=session_id, epoch=epoch)
    except OSError as exc:
        raise BudgetNotAuthorized(
            "the probe's window binding could not be written to %s (%s). "
            "Refusing to submit without a durable window identity."
            % (path, type(exc).__name__)) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf8") as fh:
            json.dump(rec, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise BudgetNotAuthorized(
            "the probe's window binding was created but not durably written "
            "(%s). Refused." % type(exc).__name__) from exc
    return dict(rec, already_bound=False)


class ProbeBudget(object):
    """How much of the frozen authorization THIS process may spend."""

    def __init__(self, *, max_attempts: int, max_windows: int,
                 windows_used_at_start: int, source: str) -> None:
        assert_frozen()
        if not 1 <= int(max_attempts) <= FROZEN_MAX_ATTEMPTS:
            raise BudgetNotAuthorized(
                "max_attempts %r is outside 1..%d"
                % (max_attempts, FROZEN_MAX_ATTEMPTS))
        if not 1 <= int(max_windows) <= FROZEN_MAX_WINDOWS:
            raise BudgetNotAuthorized(
                "max_windows %r is outside 1..%d"
                % (max_windows, FROZEN_MAX_WINDOWS))
        self.max_attempts = int(max_attempts)
        self.max_windows = int(max_windows)
        self.windows_used_at_start = int(windows_used_at_start or 0)
        self.source = source
        # A PROBE AUTHORIZED FOR EXACTLY ONE WINDOW HAS ONE WINDOW IDENTITY
        # TO BIND, and its attempts are counted against that window alone.
        # A budget that still permits several windows is the frozen cohort,
        # whose bound is the session total -- so nothing about the
        # twelve-window behaviour changes here.
        self.binds_one_window = (self.max_windows == 1)

    # ------------------------------------------------------------ builders
    @classmethod
    def from_env(cls, *, windows_used_at_start: int = 0) -> "ProbeBudget":
        """THE PRODUCTION PATH. Fails closed on anything but a narrowing
        integer in each key."""
        return cls(max_attempts=_narrow(MAX_ATTEMPTS_ENV,
                                        FROZEN_MAX_ATTEMPTS),
                   max_windows=_narrow(MAX_WINDOWS_ENV, FROZEN_MAX_WINDOWS),
                   windows_used_at_start=windows_used_at_start,
                   source="env:PMUS_E1_MAX_ATTEMPTS,PMUS_E1_MAX_WINDOWS")

    @classmethod
    def frozen_maximums(cls, *, windows_used_at_start: int = 0
                        ) -> "ProbeBudget":
        """The LIBRARY default: exactly the frozen spec, narrowing nothing.

        `run_cohort` uses this when no budget is passed, so the frozen
        behaviour is unchanged for every existing caller and test. The
        DEPLOYED entrypoint does NOT use it -- `main()` requires the env
        keys -- because the whole point is that the deployed probe cannot
        inherit 240 placements by omission.
        """
        return cls(max_attempts=FROZEN_MAX_ATTEMPTS,
                   max_windows=FROZEN_MAX_WINDOWS,
                   windows_used_at_start=windows_used_at_start,
                   source="frozen_spec_defaults")

    # -------------------------------------------------------------- counts
    def attempts_used(self, *, session_id: Optional[str] = None,
                      pending_order_ids: Iterable[Any] = (),
                      window: Optional[int] = None,
                      latch_module: Any = None) -> Dict[str, Any]:
        """DURABLE. Latch slot FILES, plus unretired local intents.

        The latch slot is taken immediately before the POST and never
        released, so a slot file is the authoritative record that an
        attempt happened -- including the ambiguous case where we cannot
        prove whether an order rested. An unretired `LOCAL_INTENT:` is the
        narrower window still: an intent recorded before the slot, whose
        acknowledgement never arrived. Both count AGAINST the budget,
        because the conservative direction is to spend the attempt.
        """
        mod = latch_module or L
        bound: Optional[Dict[str, Any]] = None
        scope = "session_total"
        scope_window: Optional[int] = None
        if self.binds_one_window:
            # THE BOUND WINDOW ALONE. The session's historical slots -- forty
            # of them on the live volume -- are read, never recounted, and
            # never touched. Before the binding exists nothing can have been
            # submitted by this probe, so the count is zero plus any intent.
            bound = read_binding(session_id=session_id)
            # THE WINDOW WE ARE ABOUT TO ACT IN, which is the bound one once a
            # binding exists. Counting is window-scoped either way; the
            # binding's job is to stop the probe drifting to a DIFFERENT
            # window and collecting a fresh allowance there.
            scope_window = (int(bound["window"]) if bound is not None
                            else (None if window is None else int(window)))
            scope = ("bound_window_%d" % scope_window
                     if bound is not None else
                     ("window_%d" % scope_window if scope_window is not None
                      else "no_window_yet"))
        try:
            if scope_window is not None:
                st = mod.window_state(window=int(scope_window),
                                      session_id=session_id)
                if not st.get("available"):
                    raise LatchScopeUnreadable(str(st.get("why")))
                slots = int(st.get("attempts_spent") or 0)
            elif self.binds_one_window:
                slots = 0
            else:
                state = mod.cohort_state(session_id)
                slots = int(state.get("attempts_spent_total") or 0)
            readable = True
            why = None
        except Exception as exc:                               # noqa: BLE001
            # FAIL CLOSED: a budget we cannot count is a budget we must
            # assume is spent.
            slots = self.max_attempts
            readable = False
            why = "LATCH_UNREADABLE:%s" % type(exc).__name__
        intents = sorted({str(p) for p in (pending_order_ids or ())
                          if str(p).startswith(LOCAL_INTENT)})
        return {"latch_slots_spent": slots,
                "counted_scope": scope,
                "bound_window": (None if bound is None
                                 else int(bound["window"])),
                "bound_slug": (None if bound is None else bound.get("slug")),
                "counted_window": scope_window,
                "session_history_is_read_never_recounted": True,
                "unretired_local_intents": len(intents),
                "intent_ids": intents,
                "attempts_used": slots + len(intents),
                "latch_readable": readable, "why": why,
                "counted_from": "durable latch files + local intents",
                "ambiguous_submission_counts_as_spent": True,
                "a_restart_cannot_reset_this": True}

    # --------------------------------------------------------------- gates
    def attempt_gate(self, *, session_id: Optional[str] = None,
                     pending_order_ids: Iterable[Any] = (),
                     window: Optional[int] = None,
                     latch_module: Any = None) -> Dict[str, Any]:
        """Checked BEFORE the intent row, BEFORE the preview, BEFORE the
        POST. Exhaustion is NOT a reason to stop managing inventory."""
        bound = read_binding(session_id=session_id) \
            if self.binds_one_window else None
        if bound is not None and window is not None \
                and int(window) != int(bound["window"]):
            # a different window than the one this probe is bound to. Not a
            # budget question: this probe has no authorization there at all.
            return {"may_attempt": False,
                    "max_attempts": self.max_attempts, "remaining": 0,
                    "because": "PROBE_IS_BOUND_TO_WINDOW_%d_NOT_%d"
                               % (int(bound["window"]), int(window)),
                    "exhaustion_does_not_end_inventory_management": True,
                    "usage": {"bound_window": int(bound["window"]),
                              "bound_slug": bound.get("slug"),
                              "asked_about_window": int(window)}}
        used = self.attempts_used(session_id=session_id,
                                  pending_order_ids=pending_order_ids,
                                  window=window, latch_module=latch_module)
        remaining = self.max_attempts - used["attempts_used"]
        return {"may_attempt": remaining > 0,
                "max_attempts": self.max_attempts,
                "remaining": max(0, remaining),
                "because": (None if remaining > 0 else
                            "ATTEMPT_BUDGET_EXHAUSTED_%d_OF_%d"
                            % (used["attempts_used"], self.max_attempts)),
                "exhaustion_does_not_end_inventory_management": True,
                "usage": used}

    def window_gate(self, *, windows_used: int,
                    candidate_window: Optional[int] = None,
                    session_id: Optional[str] = None) -> Dict[str, Any]:
        """May this process open ANOTHER window? Relative to boot, so the
        existing cohort ledger is read and never rewritten."""
        # THE BINDING OVERRIDES THE ARITHMETIC. Once this probe is bound, the
        # bound window is the only one it may work -- so a restart resumes
        # that window rather than selecting a new one, and the count it
        # resumes with is the one already on the volume.
        bound = read_binding(session_id=session_id) \
            if self.binds_one_window else None
        if bound is not None and candidate_window is not None \
                and int(candidate_window) != int(bound["window"]):
            return {"may_open_window": False,
                    "because": "PROBE_IS_BOUND_TO_WINDOW_%d"
                               % int(bound["window"]),
                    "bound_window": int(bound["window"]),
                    "bound_slug": bound.get("slug"),
                    "candidate_window": int(candidate_window),
                    "a_restart_cannot_select_another_window": True,
                    "ledger_is_read_never_rewritten": True}
        opened = max(0, int(windows_used) - self.windows_used_at_start)
        if bound is not None and int(windows_used) > self.windows_used_at_start:
            # the bound window is already open on the ledger; resuming it is
            # not opening a new one.
            opened = max(0, opened - 1)
        remaining = self.max_windows - opened
        frozen_left = FROZEN_MAX_WINDOWS - int(windows_used)
        return {"may_open_window": remaining > 0 and frozen_left > 0,
                "max_windows_this_probe": self.max_windows,
                "windows_opened_this_probe": opened,
                "remaining": max(0, min(remaining, frozen_left)),
                "windows_used_total": int(windows_used),
                "windows_used_at_start": self.windows_used_at_start,
                "bound_window": (None if bound is None
                                 else int(bound["window"])),
                "binds_one_window": self.binds_one_window,
                # THE FROZEN CEILING NAMES ITSELF FIRST. When both are
                # spent the frozen twelve is the stronger statement, and it
                # is the refusal the existing cohort-budget evidence and
                # tests are written against; narrowing adds a reason, it
                # does not rename the one that was already there.
                "because": (None if remaining > 0 and frozen_left > 0 else
                            ("COHORT_WINDOW_BUDGET_SPENT"
                             if frozen_left <= 0 else
                             "PROBE_WINDOW_BUDGET_SPENT_%d_OF_%d"
                             % (opened, self.max_windows))),
                "ledger_is_read_never_rewritten": True}

    def report(self) -> Dict[str, Any]:
        return {"probe": PROBE,
                "max_attempts": self.max_attempts,
                "max_windows": self.max_windows,
                "windows_used_at_start": self.windows_used_at_start,
                "source": self.source,
                "frozen_max_attempts": FROZEN_MAX_ATTEMPTS,
                "frozen_max_windows": FROZEN_MAX_WINDOWS,
                "may_only_narrow": True,
                "binds_one_window": self.binds_one_window,
                "attempts_counted_against": (
                    "the probe's bound window alone"
                    if self.binds_one_window else "the session total"),
                "env_keys": list(BUDGET_KEYS),
                "no_default": "missing/invalid/excessive fails closed"}


def env_report() -> Dict[str, Any]:
    """Booleans and the refusal, for the boot report. Values of other keys
    are never read here."""
    out: Dict[str, Any] = {"env_keys": list(BUDGET_KEYS),
                           "present": {k: bool((os.getenv(k) or "").strip())
                                       for k in BUDGET_KEYS}}
    try:
        b = ProbeBudget.from_env()
        out["established"] = True
        out["budget"] = b.report()
    except BudgetNotAuthorized as exc:
        out["established"] = False
        out["refusal"] = str(exc)
    return out
