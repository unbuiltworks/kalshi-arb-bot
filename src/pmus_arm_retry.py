"""Bounded pre-arm retry POLICY. Contains no arming capability whatsoever.

WHY THIS FILE HOLDS NO `arm()` CALL. The 3a invariant is that exactly one
component owns the arming transition: `scripts/l5_pmus_live_oneshot.py`. An AST
sweep enforces it. So the retry LOOP lives in the one-shot, and only the
PREDICATES live here -- which is also the part worth reviewing in isolation.

WHAT THIS IS FOR. On 1545z the probe was arm-eligible with preflight PASSED and a
whole window ran `armed: false` with zero orders, because arming was attempted
once and hit an `orders_uncertain()` that cleared twelve seconds later. A refusal
that clears on its own must not cost a window.

WHAT THIS IS NOT FOR, and the distinction the whole design turns on:

    RETRYING A TRANSIENT PRE-ARM SAFETY CHECK   -- permitted, bounded, here
    REACQUIRING OR RENEWING AUTHORIZATION       -- never

A retry is only ever a second look at the SAME still-valid authorization for the
SAME market before any order exists. It can never re-claim, extend, refresh or
replace an authorization, and it stops the instant anything about the envelope
changes. Continuous-runner rearming is the 2026-09-16 breach and is what 3a
removed.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


class ArmAbort(RuntimeError):
    """A retry must stop and the probe must not arm."""

    def __init__(self, reason: str, detail: Any = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


#: The ONLY refusals a retry may look past. Each is a pre-arm safety check that
#: is expected to clear on its own within seconds, and each leaves NO trace --
#: no order, no claim, no spend. Anything not on this list is non-transient by
#: default, because an unknown refusal is not a refusal we understand.
TRANSIENT = ("orders_uncertain", "unreconciled", "book_unconfirmed")

#: Default bound. Short, because the point is to survive a 12-second clear, not
#: to keep trying for a window.
DEFAULT_DEADLINE_S = 45.0
DEFAULT_INTERVAL_S = 3.0


def is_transient(refusal: str) -> bool:
    """Whitelist, never a blacklist: unknown means non-transient."""
    r = str(refusal or "").strip().lower()
    return any(t in r for t in TRANSIENT)


class ArmRetryGate:
    """Re-reads EVERY condition on EVERY attempt. Nothing is cached."""

    def __init__(self, *, market: str,
                 authorization,
                 halted: Callable[[], bool],
                 mandate_fingerprint: Callable[[], Optional[str]],
                 submitted_count: Callable[[], int],
                 window_target: Callable[[], Optional[str]],
                 deadline_s: float = DEFAULT_DEADLINE_S,
                 interval_s: float = DEFAULT_INTERVAL_S,
                 clock: Callable[[], float] = time.time) -> None:
        self.market = str(market)
        self.auth = authorization
        self.halted = halted
        self.mandate_fingerprint = mandate_fingerprint
        self.submitted_count = submitted_count
        self.window_target = window_target
        self.deadline_s = float(deadline_s)
        self.interval_s = float(interval_s)
        self.clock = clock
        self.started = clock()
        self.attempts = 0
        # pinned ONCE so a change can be detected; never refreshed
        self._mandate0 = mandate_fingerprint()

    # ------------------------------------------------------------- gating

    def preconditions(self) -> None:
        """Every condition that must hold before ANY attempt. Raises ArmAbort."""
        if self.halted():
            raise ArmAbort("HALT_PRESENT")
        fp = self.mandate_fingerprint()
        if fp != self._mandate0:
            raise ArmAbort("MANDATE_CHANGED",
                           {"was": self._mandate0, "now": fp})
        if fp is None:
            raise ArmAbort("NO_MANDATE")
        if self.submitted_count() != 0:
            raise ArmAbort("ORDER_ALREADY_SUBMITTED",
                           {"submitted": self.submitted_count()})
        auth = self.auth
        if not getattr(auth, "claimed", False):
            raise ArmAbort("AUTHORIZATION_NOT_CLAIMED")
        if getattr(auth, "consumed", False):
            raise ArmAbort("AUTHORIZATION_CONSUMED")
        if getattr(auth, "market", None) != self.market:
            raise ArmAbort("AUTHORIZATION_BOUND_TO_MARKET",
                           {"claimed": getattr(auth, "market", None),
                            "attempting": self.market})
        target = self.window_target()
        if target is not None and target != self.market:
            raise ArmAbort("WINDOW_CHANGED",
                           {"authorized": self.market, "now": target})

    def may_retry(self, refusal: str) -> None:
        """Raise ArmAbort unless another attempt is permitted."""
        if not is_transient(refusal):
            raise ArmAbort("NON_TRANSIENT_REFUSAL", {"refusal": str(refusal)})
        if (self.clock() - self.started) >= self.deadline_s:
            raise ArmAbort("RETRY_DEADLINE_EXCEEDED",
                           {"deadline_s": self.deadline_s,
                            "attempts": self.attempts})
        self.preconditions()

    def note_attempt(self) -> int:
        self.attempts += 1
        return self.attempts

    def summary(self) -> dict:
        return {"market": self.market, "attempts": self.attempts,
                "deadline_s": self.deadline_s,
                "elapsed_s": round(self.clock() - self.started, 3),
                "mandate_fingerprint": self._mandate0,
                "transient_whitelist": list(TRANSIENT)}
