"""E1's OWN graceful-shutdown signal, and an honest account of its limits.

WHY THIS EXISTS, STATED WITHOUT SOFTENING.

E1's handover deadman is correct and already ownership-scoped: it cancels
exactly the ids E1's own tape says E1 submitted, by
`POST /v1/order/{id}/cancel`, leaves an unattributable order alone, and
verifies the outcome per id against the venue rather than trusting a
cancel's own answer. None of that is changed here and none of it needs to
be.

What was missing is REACHABILITY. `run_cohort` reached the handover from
exactly one place -- the `past_cutoff` branch inside the iteration loop --
so every other way out of that loop left owned orders resting:

  * SIGTERM, which is how Railway stops a container, and whose default
    disposition terminates the process WITHOUT unwinding, so no `finally`
    anywhere in the program would have run either;
  * SIGINT;
  * any exception escaping the loop, since nothing wrapped it;
  * a normal exit before the cutoff.

That is not hypothetical. On 2026-10-08 E1 was disarmed and its process
stopped with twenty 0.13-share orders resting on
`cpc-btc-updown-15m-2026-10-08-1230z`; its own deadman never ran and they
were cancelled afterwards by an operator script.

WHAT THIS MODULE DOES AND DOES NOT PROTECT AGAINST.

It converts SIGTERM and SIGINT into an ORDINARY PYTHON EXCEPTION raised in
the main thread, which makes `run_cohort`'s handover reachable on the
unwind. That is all a process can do for itself.

It does NOT protect against `SIGKILL`, the OOM killer, power loss, a
container hard stop, or `SIGSTOP`. No in-process handler can: the process
is not scheduled again. Those cases are covered -- and ONLY covered -- by
something outside the process: E1's durable latch and
`run_cohort`'s mandatory startup reconciliation, which together refuse to
submit anything after a restart until pre-existing venue state has been
established, plus an operator cancellation tool. This module makes no claim
beyond the two signals it names, and `coverage()` reports the distinction in
a form a test can assert.

WHY A `BaseException`.

`ShutdownRequested` derives from `BaseException`, not `Exception`. The
iteration loop is full of `except Exception` handlers that classify venue
and local faults; a shutdown that inherited from `Exception` would be
swallowed by the first of them and recorded as a venue error, which is both
wrong and dangerous -- the loop would carry on placing orders during a
shutdown. `BaseException` passes through them untouched, exactly as
`KeyboardInterrupt` does, and is caught only where it is meant to be.
"""
from __future__ import annotations

import os
import signal
import time
from typing import Any, Dict, List, Optional

#: the two signals a process can actually do something about.
SIGNALS_COVERED = ("SIGTERM", "SIGINT")

#: the ones it cannot, named so no reader has to infer the gap. A claim that
#: an in-process handler covers any of these would be false.
NOT_COVERED = ("SIGKILL", "OOM_KILL", "POWER_LOSS",
               "CONTAINER_HARD_STOP", "SIGSTOP")

#: set by the handler, read by the runner for its record. Never consulted to
#: decide whether to cancel: the exception does that.
_REQUESTED: Optional[Dict[str, Any]] = None

_INSTALLED: List[str] = []

#: the dispositions we replaced, so they can be put back. A module that
#: rebinds SIGINT and never restores it silently changes the behaviour of
#: everything that runs afterwards in the same process -- which is not a
#: theoretical worry: it hung a full pytest session for forty minutes,
#: because `KeyboardInterrupt` no longer arrived where later tests
#: expected it.
_PREVIOUS: Dict[int, Any] = {}


class ShutdownRequested(BaseException):
    """A covered signal arrived. Unwind, run the handover, then exit.

    Deliberately NOT an `Exception` -- see the module docstring.
    """

    def __init__(self, detail: Dict[str, Any]) -> None:
        super().__init__(detail.get("signal") or "SHUTDOWN")
        self.detail = dict(detail)


def requested() -> Optional[Dict[str, Any]]:
    """What the handler recorded, or None. Read-only."""
    return None if _REQUESTED is None else dict(_REQUESTED)


def restore() -> None:
    """Put back every disposition we replaced.

    Idempotent, and it never raises: failing to restore a handler must not
    become a second failure on top of whatever is already unwinding.
    """
    for signum, previous in list(_PREVIOUS.items()):
        try:
            signal.signal(signum, previous)
        except (ValueError, OSError, RuntimeError, TypeError):
            pass
    _PREVIOUS.clear()
    del _INSTALLED[:]


def reset_for_test() -> None:
    """Clear module state AND restore the original handlers.

    The restore is the part that matters. Without it these handlers
    outlive the test that installed them and stay bound for the whole
    process.
    """
    global _REQUESTED
    _REQUESTED = None
    restore()


def _handler(signum: int, _frame: Any) -> None:
    global _REQUESTED
    name = signal.Signals(signum).name
    detail = {
        "signal": name,
        "signum": int(signum),
        "unix": time.time(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
        "deployment": os.getenv("RAILWAY_DEPLOYMENT_ID"),
        "MEANING": ("a graceful shutdown was requested. The handover has "
                    "NOT run yet; the runner runs it on the unwind."),
    }
    if _REQUESTED is None:
        _REQUESTED = detail
    # A SECOND SIGNAL IS NOT AN ESCAPE HATCH. It is recorded and otherwise
    # ignored: re-raising from inside the handover would abandon a cancel
    # sequence half-finished, which is the one outcome worse than waiting.
    else:
        _REQUESTED.setdefault("repeats", []).append(detail)
        return
    raise ShutdownRequested(detail)


def install(*, signals: Any = SIGNALS_COVERED) -> Dict[str, Any]:
    """Install the handlers. Idempotent, and never raises.

    A platform or thread that will not accept a handler is reported rather
    than fatal: the cohort must still run, it simply loses the graceful
    path and keeps the durable-latch path, which is the one that covers
    the hard cases anyway.
    """
    installed: List[str] = []
    refused: Dict[str, str] = {}
    for name in signals:
        sig = getattr(signal, name, None)
        if sig is None:
            refused[name] = "NOT_ON_THIS_PLATFORM"
            continue
        try:
            previous = signal.getsignal(sig)
            signal.signal(sig, _handler)
            _PREVIOUS.setdefault(int(sig), previous)
        except (ValueError, OSError, RuntimeError) as exc:
            # ValueError: not the main thread.
            refused[name] = type(exc).__name__
            continue
        installed.append(name)
        if name not in _INSTALLED:
            _INSTALLED.append(name)
    return coverage(installed=installed, refused=refused)


def coverage(*, installed: Optional[List[str]] = None,
             refused: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """What is and is not covered, in a shape a test can assert on."""
    return {
        "installed": list(installed if installed is not None else _INSTALLED),
        "refused": dict(refused or {}),
        "signals_covered": list(SIGNALS_COVERED),
        "NOT_COVERED": list(NOT_COVERED),
        "atexit_used": False,
        "in_process_handler_covers_sigkill": False,
        "hard_crash_is_covered_by": [
            "analysis.l5.e1_cohort.latch (durable O_EXCL named slots)",
            "analysis.l5.e1_cohort.runner.run_cohort startup reconciliation",
            "scripts/l5_e1_cancel_by_id.py (operator, tape-attested ids)",
        ],
        "LANGUAGE": ("a graceful handler is not crash protection; it only "
                     "makes the handover reachable on an orderly stop"),
    }
