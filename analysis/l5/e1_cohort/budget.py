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

WINDOWS ARE COUNTED RELATIVE TO THIS PROCESS. The cohort ledger already
holds three spent windows and that history is not rewritten here: the budget
records `windows_used_at_start` at boot and allows `max_windows` NEW ones on
top of it. The existing append-only ledger is read and never altered.
"""
from __future__ import annotations

import os
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
        try:
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
                     latch_module: Any = None) -> Dict[str, Any]:
        """Checked BEFORE the intent row, BEFORE the preview, BEFORE the
        POST. Exhaustion is NOT a reason to stop managing inventory."""
        used = self.attempts_used(session_id=session_id,
                                  pending_order_ids=pending_order_ids,
                                  latch_module=latch_module)
        remaining = self.max_attempts - used["attempts_used"]
        return {"may_attempt": remaining > 0,
                "max_attempts": self.max_attempts,
                "remaining": max(0, remaining),
                "because": (None if remaining > 0 else
                            "ATTEMPT_BUDGET_EXHAUSTED_%d_OF_%d"
                            % (used["attempts_used"], self.max_attempts)),
                "exhaustion_does_not_end_inventory_management": True,
                "usage": used}

    def window_gate(self, *, windows_used: int) -> Dict[str, Any]:
        """May this process open ANOTHER window? Relative to boot, so the
        existing cohort ledger is read and never rewritten."""
        opened = max(0, int(windows_used) - self.windows_used_at_start)
        remaining = self.max_windows - opened
        frozen_left = FROZEN_MAX_WINDOWS - int(windows_used)
        return {"may_open_window": remaining > 0 and frozen_left > 0,
                "max_windows_this_probe": self.max_windows,
                "windows_opened_this_probe": opened,
                "remaining": max(0, min(remaining, frozen_left)),
                "windows_used_total": int(windows_used),
                "windows_used_at_start": self.windows_used_at_start,
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
