"""Keep the optional read-only diagnostics OFF the window-roll path.

WHAT THIS EXISTS TO FIX, measured rather than argued.

`Runner.roll` is called first in every tick and is synchronous, so whatever
it does the window cannot trade until it returns. On 2026-11-06 it returned
after 93 seconds, every window, for hours:

    L5_LIVE_TICK_STAGES  total_s 93.651  slowest "roll"  slowest_s 93.649
    L5_LIVE_TICK_STAGES  total_s 93.244  slowest "roll"  slowest_s 93.242
    L5_LIVE_TICK_STAGES  total_s 92.979  slowest "roll"  slowest_s 92.978

The blocking operation was NOT an order, a cancellation, a settlement or a
reconciliation. It was `L5_COST_BASIS_AUDIT`, a read-only diagnostic that
walks the venue's whole account-activity feed. Its own emitted record says
how:

    L5_COST_BASIS_AUDIT {"ok":true,"writes":"NONE",
      "activity_walk":{"pages":133,"ended":"eof","complete":true}, ...}

133 paginated GETs, paced by the process's one shared limiter at
`PMUS_RPS` = 1.5 requests a second, is 133 / 1.5 = 88.7 seconds of sleeping
inside `roll`, plus round trips. The platform's own DNS stream shows it
directly: `api.polymarket.us` resolved twice every 0.666 s -- one request per
1/1.5 s -- continuously from the roll until the audit's record was emitted
1.5 ms before `L5_LIVE_GATES`, which is the last line `roll` writes.

WHAT IT COST, also measured. The first completion decision of the 0300z and
0315z windows -- the two windows that settled -$0.97 between them -- landed
at +98.5 s and +97.5 s after the window opened. Not because the book was
thin: because nothing in the tick after `roll` had run yet. In 0315z the
worst-case improvement an executable completion offered decayed from $0.35 at
the first evaluation to $0.04 by the end of the window, so the time the
diagnostic spent was not free time.

AND IT RAN EVERY WINDOW. The audit's own comment in the runner says it
"reads the account activity feed ONCE" and the apply beneath it says "Boot
only, so it cannot run twice in a session". Both describe a once-per-process
pass. `roll` fires on every window roll, so both ran ninety-six times a day:
about 12,800 venue reads a day for a diagnostic that writes nothing.

WHAT THIS MODULE DOES. It is a scheduler and nothing else. A job is enqueued
by name, runs at most once per process, runs only outside the window's
entry-critical opening, and is given a wall-clock budget per attempt so that
a job which needs 89 seconds of paced venue reads takes them a few pages at a
time across later ticks instead of all at once at the worst possible moment.

WHAT IT DELIBERATELY DOES NOT DO.

  * It knows nothing about venues, orders, prices or money. Every job is a
    closure the caller builds; this file cannot submit, cancel, arm, price or
    refuse anything, and holds no transport and no store.
  * It never drops a job to save time. A job that is not ready is still
    queued; deferring is not skipping. The only thing that removes a job is
    the job itself reporting that it is done.
  * It never runs a job it was not given. Settlement, reconciliation,
    cancellation and the window summary are not jobs here -- they stay
    exactly where they are, on the roll path, unchanged and undeferred.
  * It cannot make a failure fatal. Every job runs inside a swallow and a
    raising job is recorded, retired and never retried, because a diagnostic
    that crashes must not become a diagnostic that crashes forever.

A JOB'S CONTRACT. `fn(deadline_monotonic) -> Mapping`. The mapping is the
record to emit. Two keys are read by the scheduler and both are optional:

    "_resume": True   -- not finished; call me again on a later tick
    "_emit":   False  -- do not emit this record (a quiet resume step)

`deadline_monotonic` is when the job should stop and return a resume, taken
from `time.monotonic()`. A job that ignores it is not cut off -- nothing here
can interrupt a running call -- so a job that must be bounded has to read it.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

#: How far into a window a deferred job may first run, in seconds. The
#: default puts every job past the two-sided probe's entry cutoff and past
#: the first completion evaluations, so a diagnostic can never again be the
#: reason an opening or a completion was not considered.
AFTER_S_ENV = "L5_ROLL_DIAG_AFTER_S"
DEFAULT_AFTER_S = 240.0

#: Wall clock a single attempt may spend. One tick of the live loop is about
#: 3.5 s, so 5 s keeps a deferred job's footprint comparable to the stages
#: already in the tick and far below the 93 s it replaces.
BUDGET_S_ENV = "L5_ROLL_DIAG_BUDGET_S"
DEFAULT_BUDGET_S = 5.0

#: Jobs per drain. One, always: two jobs in a tick is two budgets in a tick.
JOBS_PER_DRAIN = 1

#: Emitted when `roll` itself exceeds this, so the regression this module was
#: written for becomes visible in one line instead of being reconstructed
#: from stage timings and a DNS stream.
ROLL_BUDGET_S_ENV = "L5_ROLL_BUDGET_S"
DEFAULT_ROLL_BUDGET_S = 10.0

DONE, RESUMED, FAILED, DEFERRED = "DONE", "RESUMED", "FAILED", "DEFERRED"


def _f(env: str, default: float) -> float:
    """A positive float from the environment, or the default.

    Unreadable, blank and non-positive all mean the default rather than zero:
    a zero budget would make every job report a resume for ever and a zero
    `after_s` would put the jobs back on the window's opening, which is the
    defect this module removes.
    """
    raw = str(os.getenv(env, "") or "").strip()
    if not raw:
        return float(default)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return v if v > 0 else float(default)


def after_s() -> float:
    return _f(AFTER_S_ENV, DEFAULT_AFTER_S)


def budget_s() -> float:
    return _f(BUDGET_S_ENV, DEFAULT_BUDGET_S)


def roll_budget_s() -> float:
    return _f(ROLL_BUDGET_S_ENV, DEFAULT_ROLL_BUDGET_S)


class DeferredDiagnostics:
    """A once-per-process queue of read-only jobs that may not block a roll.

    The latch is on the NAME and it is set at enqueue time, so `roll`
    enqueuing the same job on every window roll adds it exactly once --
    which is what both of the moved jobs' own comments already claimed to do.
    """

    def __init__(self, *, now_fn: Optional[Callable[[], float]] = None
                 ) -> None:
        self._now = now_fn or time.monotonic
        self._queue: List[Dict[str, Any]] = []
        #: every name ever enqueued, so a second enqueue is a no-op and a
        #: finished job can never be re-queued by a later roll
        self._seen: set = set()
        self.history: List[Dict[str, Any]] = []
        self.attempts: Dict[str, int] = {}
        self.deferrals = 0

    # ----------------------------------------------------------- enqueue
    def enqueue(self, name: str, fn: Callable[[float], Mapping[str, Any]], *,
                after_s_override: Optional[float] = None) -> bool:
        """Queue `fn` under `name`. True if it was queued, False if latched.

        Returns a bool rather than raising on a repeat because the caller is
        `roll`, which runs every fifteen minutes and must not have to
        remember what it has already done.
        """
        key = str(name)
        if not key or key in self._seen or not callable(fn):
            return False
        self._seen.add(key)
        self._queue.append({"name": key, "fn": fn,
                            "after_s": after_s_override})
        return True

    # -------------------------------------------------------------- state
    @property
    def pending(self) -> List[str]:
        return [j["name"] for j in self._queue]

    def report(self) -> Dict[str, Any]:
        return {
            "pending": list(self.pending),
            "completed": [h["name"] for h in self.history
                          if h["outcome"] == DONE],
            "failed": [h["name"] for h in self.history
                       if h["outcome"] == FAILED],
            "attempts": dict(self.attempts),
            "deferrals": int(self.deferrals),
            "after_s": after_s(),
            "budget_s": budget_s(),
            "labels": ("read-only diagnostics deferred off the window-roll "
                       "path. Deferring is not skipping: a job stays queued "
                       "until it reports done. No settlement, reconciliation "
                       "or cancellation is ever queued here."),
        }

    # -------------------------------------------------------------- drain
    def ready(self, *, t_in_window_s: Optional[float],
              entry_critical: bool = False) -> Dict[str, Any]:
        """May a job run right now, and if not, why not.

        FAIL CLOSED TOWARD NOT RUNNING. An unknown window age defers: we
        cannot establish that we are past the opening, and the cost of
        waiting one tick is a tick, while the cost of running at the wrong
        moment is the 93 seconds this module exists to recover.
        """
        if not self._queue:
            return {"ready": False, "reason": "NOTHING_QUEUED"}
        if entry_critical:
            return {"ready": False, "reason": "ENTRY_CRITICAL"}
        if t_in_window_s is None:
            return {"ready": False, "reason": "WINDOW_AGE_UNKNOWN"}
        try:
            age = float(t_in_window_s)
        except (TypeError, ValueError):
            return {"ready": False, "reason": "WINDOW_AGE_UNREADABLE"}
        job = self._queue[0]
        gate = (job.get("after_s") if job.get("after_s") is not None
                else after_s())
        if age < float(gate):
            return {"ready": False, "reason": "WINDOW_TOO_YOUNG",
                    "t_in_window_s": round(age, 2), "after_s": float(gate)}
        return {"ready": True, "t_in_window_s": round(age, 2),
                "after_s": float(gate)}

    def drain(self, *, t_in_window_s: Optional[float],
              entry_critical: bool = False,
              budget: Optional[float] = None) -> Dict[str, Any]:
        """Run at most one queued job, or say why none ran.

        The return is always a record and never None, because "nothing ran"
        is a fact worth having in the tape beside "something ran": the whole
        failure this module addresses was a diagnostic whose cost was
        invisible in the lines around it.
        """
        gate = self.ready(t_in_window_s=t_in_window_s,
                          entry_critical=entry_critical)
        if not gate.get("ready"):
            if gate.get("reason") != "NOTHING_QUEUED":
                self.deferrals += 1
            return dict(gate, ran=None, outcome=DEFERRED)
        job = self._queue[0]
        name = job["name"]
        self.attempts[name] = int(self.attempts.get(name, 0)) + 1
        allowance = float(budget if budget is not None else budget_s())
        started = self._now()
        deadline = started + allowance
        out: Dict[str, Any]
        outcome = DONE
        try:
            got = job["fn"](deadline)
            out = dict(got) if isinstance(got, Mapping) else {"result": got}
            if out.pop("_resume", False):
                outcome = RESUMED
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # RETIRED, NOT RETRIED. A diagnostic that raises once will raise
            # again on the same inputs, and a job retried every tick for the
            # rest of the process is a worse outage than the missing record.
            outcome = FAILED
            out = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                   "labels": ("the job raised and has been retired. It is "
                              "read-only, so nothing is half-done, and "
                              "nothing it would have reported is a "
                              "precondition for trading.")}
        elapsed = self._now() - started
        if outcome != RESUMED:
            self._queue.pop(0)
        self.history.append({"name": name, "outcome": outcome,
                             "elapsed_s": round(elapsed, 3),
                             "attempt": self.attempts[name]})
        emit = bool(out.pop("_emit", True))
        return {"ran": name, "outcome": outcome, "emit": emit,
                "record": out, "elapsed_s": round(elapsed, 3),
                "attempt": self.attempts[name],
                "budget_s": allowance,
                "over_budget": bool(elapsed > allowance),
                "t_in_window_s": gate.get("t_in_window_s"),
                "still_pending": list(self.pending)}


class PagedWalk:
    """A paginated read-only feed walk, taken a few pages at a time.

    IT IS THE SAME WALK. The pages are requested in the same order, through
    the same verb, and accumulated into the same list; completing across six
    ticks and completing in one call produce the same rows and the same
    `complete`. What changes is only how much wall clock one call may spend,
    which is what keeps 89 seconds of limiter-paced GETs off the window's
    opening.

    PROGRESS IS REQUIRED. A step that returns no continuation and is not
    complete finishes the walk as truncated rather than resuming: a resume
    that cannot say where to resume from would spin for ever re-reading page
    one, and an incomplete walk already refuses every basis downstream.
    """

    def __init__(self, walk_fn: Callable[..., Mapping[str, Any]],
                 transport: Any, *, pages_per_step: int = 12,
                 max_pages: Optional[int] = None) -> None:
        self.walk_fn = walk_fn
        self.transport = transport
        self.pages_per_step = max(1, int(pages_per_step))
        self.max_pages = max_pages
        self.rows: List[Any] = []
        self.cursor: Optional[str] = None
        self.pages = 0
        self.steps = 0
        self.done = False
        self.walk: Optional[Dict[str, Any]] = None

    def step(self, deadline: Optional[float] = None) -> Dict[str, Any]:
        """Take up to `pages_per_step` more pages. Returns the walk so far."""
        if self.done and self.walk is not None:
            return dict(self.walk)
        budget = self.pages_per_step
        if self.max_pages is not None:
            left = int(self.max_pages) - self.pages
            budget = max(0, min(budget, left))
        if budget <= 0:
            self.done = True
            self.walk = {"rows": self.rows, "pages": self.pages,
                         "ended": "max_pages", "complete": False,
                         "next_cursor": self.cursor}
            return dict(self.walk)
        got = self.walk_fn(self.transport, max_pages=self.pages + budget,
                           cursor=self.cursor, rows=self.rows,
                           pages_done=self.pages)
        self.steps += 1
        self.rows = list(got.get("rows") or self.rows)
        self.pages = int(got.get("pages") or self.pages)
        nxt = got.get("next_cursor")
        ended = str(got.get("ended") or "")
        if ended == "max_pages" and nxt:
            self.cursor = str(nxt)
            self.walk = dict(got)
            return dict(got)
        # eof, no_cursor, an error, or a truncation with nowhere to resume
        self.done = True
        self.cursor = None
        self.walk = dict(got, next_cursor=None)
        return dict(self.walk)

    def report(self) -> Dict[str, Any]:
        return {"pages": self.pages, "steps": self.steps, "done": self.done,
                "rows": len(self.rows),
                "pages_per_step": self.pages_per_step,
                "labels": ("the same walk, paced across ticks. `done` false "
                           "means more pages remain and nothing downstream "
                           "has been computed yet.")}
