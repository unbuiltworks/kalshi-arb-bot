"""Let an actionable event shorten the decision loop's sleep.

WHAT THIS REPLACES. `l5_pmus_live_runner` ends its tick with
`time.sleep(poll_s)`, `poll_s` defaulting to 3. The authenticated book
arrives ~4.5x a second with `stream_age_s` of 3-95ms and order progress
lands within ~200ms of the venue event, so the data is already fast; the
decisions waited up to 3.5s for a timer. On 2026-11-02 the complement touch
moved ten ticks in under two minutes while our cancel-replace round trip
measured 19-34s.

WHAT THIS IS NOT. It does not move any decision onto a feed thread, and it
submits nothing. A feed thread may only RAISE the signal; the single
decision loop still owns every read, every gate and every order. So this
cannot introduce a concurrent or duplicate submission -- the loop is as
single-threaded after it as before, and the only thing that changes is how
long it sleeps.

THE TIMER SURVIVES. `wait(timeout)` returns on the timeout exactly as the
sleep did, so reconciliation, settlement, the watchdog and stale-feed
recovery keep their existing cadence with no new path.

TWO PROPERTIES THE LOOP DEPENDS ON:

  * COALESCING. An `Event` is level-triggered, so a thousand raises between
    two ticks produce one wake. Bursts cannot queue up work.
  * A FLOOR ON THE TICK RATE. `MIN_WAKE_INTERVAL_S` bounds how often a wake
    may shorten the sleep, so a flapping touch cannot spin the loop. The
    floor DELAYS a wake, it never discards one: every raise is eventually
    served, which is why it is a sleep and not a suppression counter.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional

#: No wake may shorten the sleep more often than this. A tick already costs
#: ~0.5s of work, so this is a second bound rather than the only one, and it
#: caps the loop at 4 decisions a second in the worst case.
MIN_WAKE_INTERVAL_S = 0.25

#: Latency samples kept for the percentiles. Bounded so a long run cannot
#: grow this without limit.
LATENCY_SAMPLES = 2048


def _pct(sorted_vals, q: float) -> Optional[float]:
    """Nearest-rank percentile. Small samples are the norm here, so this
    does NOT interpolate: an interpolated p99 over nine samples invents a
    number no event produced.
    """
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = int(round((len(sorted_vals) - 1) * q))
    return float(sorted_vals[max(0, min(len(sorted_vals) - 1, k))])


class WakeSignal:
    """Raised by feed threads, consumed only by the decision loop."""

    def __init__(self, min_interval_s: Any = MIN_WAKE_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._ev = threading.Event()
        self._lock = threading.Lock()
        try:
            self._min = max(0.0, float(min_interval_s))
        except BaseException:                                  # noqa: BLE001
            self._min = MIN_WAKE_INTERVAL_S
        self._clock = clock
        self._sleep = sleep
        #: when the EARLIEST still-unserved raise happened. Latency is
        #: measured from that one, not the most recent, because the oldest
        #: pending signal is the one that has been waiting.
        self._armed_at: Optional[float] = None
        self._last_served_at: Optional[float] = None
        self._reasons: Dict[str, int] = {}
        self._raises = 0
        self._wakes = 0
        self._timeouts = 0
        self._floor_waits = 0
        self._floor_wait_total_s = 0.0
        self._lat: Deque[float] = deque(maxlen=LATENCY_SAMPLES)

    # ------------------------------------------------------- the feed side

    def raise_(self, reason: str = "UNSPECIFIED") -> None:
        """Signal that something actionable happened. NEVER BLOCKS and never
        raises: a feed thread must not be delayed or broken by this.
        """
        try:
            with self._lock:
                t = self._clock()
                self._raises += 1
                r = str(reason)[:48]
                self._reasons[r] = self._reasons.get(r, 0) + 1
                if self._armed_at is None:
                    self._armed_at = t
                self._ev.set()
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # a wake is an optimisation; losing one costs latency, not
            # correctness, because the timer still fires.
            return

    # ------------------------------------------------ the decision-loop side

    def wait(self, timeout: Any) -> Dict[str, Any]:
        """Sleep until an actionable event or `timeout`. LOOP THREAD ONLY.

        Returns what happened, so the caller can record it: whether a wake
        or the timer ended the sleep, and for a wake how long the oldest
        unserved signal had been waiting.
        """
        try:
            to = float(timeout)
        except BaseException:                                  # noqa: BLE001
            to = MIN_WAKE_INTERVAL_S
        woken = self._ev.wait(max(0.0, to))

        floor_slept = 0.0
        if woken and self._min > 0.0:
            with self._lock:
                last = self._last_served_at
            if last is not None:
                remain = self._min - (self._clock() - last)
                if remain > 0:
                    # DELAY, never discard. The signal stays raised across
                    # this sleep, so it is served immediately after.
                    self._sleep(remain)
                    floor_slept = remain

        with self._lock:
            t = self._clock()
            lat = (None if self._armed_at is None
                   else max(0.0, t - self._armed_at))
            if woken:
                self._wakes += 1
                if lat is not None:
                    self._lat.append(lat)
                if floor_slept > 0:
                    self._floor_waits += 1
                    self._floor_wait_total_s += floor_slept
            else:
                self._timeouts += 1
            self._ev.clear()
            self._armed_at = None
            self._last_served_at = t
            return {"woken": bool(woken),
                    "latency_s": (None if (lat is None or not woken)
                                  else round(lat, 6)),
                    "floor_wait_s": (round(floor_slept, 6)
                                     if floor_slept else 0.0)}

    # ------------------------------------------------------------ reporting

    def stats(self) -> Dict[str, Any]:
        """Raise/wake counts, reason histogram and wake-latency percentiles.

        `latency_s` is RAISE -> SERVED for the oldest unserved signal: the
        delay this mechanism removes from the old fixed sleep. It is NOT
        book-event-to-submit, which spans the tick itself and is measured
        separately on the order path.
        """
        with self._lock:
            vals = sorted(self._lat)
            n = len(vals)
            out: Dict[str, Any] = {
                "raises": self._raises,
                "wakes": self._wakes,
                "timer_expiries": self._timeouts,
                "reasons": dict(self._reasons),
                "min_interval_s": self._min,
                "floor_waits": self._floor_waits,
                "floor_wait_total_s": round(self._floor_wait_total_s, 4),
                "latency_samples": n,
                "labels": ("latency_s is raise->served for the OLDEST "
                           "unserved signal. A wake only shortens the "
                           "sleep: no decision runs on a feed thread and "
                           "nothing is submitted here."),
            }
        if n:
            out.update({
                "latency_p50_s": _pct(vals, 0.50),
                "latency_p95_s": _pct(vals, 0.95),
                "latency_p99_s": _pct(vals, 0.99),
                "latency_max_s": float(vals[-1]),
            })
        else:
            out.update({"latency_p50_s": None, "latency_p95_s": None,
                        "latency_p99_s": None, "latency_max_s": None})
        return out


class TouchWatcher:
    """Raise the wake only when a book change could change a DECISION.

    WHY NOT EVERY FRAME. The feed delivers ~4.5 frames a second per market
    across the whole live slate; waking on each would make the loop run as
    fast as it can regardless of whether anything relevant moved, which is
    the busy-loop this is explicitly not allowed to become.

    WHAT COUNTS AS MEANINGFUL. A change to the best bid or best ask -- the
    touch -- of a market the caller declares RELEVANT. Depth changes behind
    the touch do not move a maker quote's price, and a market we hold
    nothing in and rest nothing in cannot produce an action.

    THE RELEVANCE TEST IS INJECTED, so this module needs no knowledge of
    inventory or orders, and an unreadable frame simply does not wake
    anything.
    """

    __slots__ = ("_wake", "_relevant", "_last", "_skips")

    def __init__(self, wake: WakeSignal,
                 relevant: Callable[[str], bool]) -> None:
        self._wake = wake
        self._relevant = relevant
        self._last: Dict[str, Any] = {}
        self._skips: Dict[str, int] = {}

    def _bump(self, key: str) -> None:
        self._skips[key] = self._skips.get(key, 0) + 1

    def observe(self, slug: Any, bid: Any, ask: Any) -> bool:
        """Returns whether this change raised the wake."""
        try:
            s = str(slug)
        except BaseException:                                  # noqa: BLE001
            self._bump("slug_unreadable")
            return False
        if bid is None or ask is None:
            self._bump("book_not_two_sided")
            return False
        touch = (str(bid), str(ask))
        prev = self._last.get(s)
        self._last[s] = touch
        if prev == touch:
            self._bump("touch_unchanged")
            return False
        if prev is None:
            # the first frame for a market establishes the touch; there is
            # no CHANGE yet, and the timer will pick the market up.
            self._bump("first_frame")
            return False
        try:
            if not self._relevant(s):
                self._bump("market_not_relevant")
                return False
        except BaseException as exc:                           # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._bump("relevance_error:%s" % type(exc).__name__)
            return False
        self._wake.raise_("TOUCH_MOVED")
        return True

    def status(self) -> Dict[str, Any]:
        return {"tracked_markets": len(self._last),
                "skips": dict(self._skips)}
