"""A lifetime counter must say when it last moved.

THE CASE THIS COMES FROM. On 2026-10-18 the executor's heartbeat carried

    HALT_GATE:HALT:halt_asserted:UNMANAGED_RESTING_ORDER   65
    halt_latch  CLEAR            open_orders_at_venue  0

and every one of those was true. The 65 were counted between the process
start at 02:51 and the owner's 03:23:40 reset of the latch that order
`D4EFZNW00ZZ0` set on 2026-10-16; the counter then froze and the heartbeat
kept printing it beside a clear latch. Read live it looks like an unmanaged
resting order right now.

Nothing here changes a count or a gate. These tests pin that the heartbeat
carries the age of each count, so a frozen counter is visibly frozen.
"""
from __future__ import annotations

import collections

from src.l5_pmus_live_runner import TimedCounter


def test_it_is_still_a_counter():
    """Every existing `self.stats[k] += 1` site must keep working."""
    c = TimedCounter()
    c["x"] += 1
    c["x"] += 2
    c.update({"y": 5})
    assert dict(c) == {"x": 3, "y": 5}
    assert isinstance(c, collections.Counter)


def test_a_frozen_count_reports_its_age():
    t = [100.0]
    c = TimedCounter(clock=lambda: t[0])
    c["halt"] += 1
    t[0] = 160.0
    c["live"] += 1
    ages = c.ages(now=t[0])
    assert ages["halt"] == 60.0, "the stalled count must show its age"
    assert ages["live"] == 0.0


def test_in_place_add_is_timestamped_not_just_update():
    """Counter's += routes through __setitem__; this pins that it does."""
    t = [0.0]
    c = TimedCounter(clock=lambda: t[0])
    c["a"] += 1
    assert c.last_t["a"] == 0.0
    t[0] = 7.0
    c["a"] += 1
    assert c.last_t["a"] == 7.0


def test_a_key_never_touched_has_no_age():
    c = TimedCounter(clock=lambda: 5.0)
    assert c["never"] == 0            # Counter's missing-key contract
    assert "never" not in c.ages()


def test_the_heartbeat_reports_the_ages():
    """The field has to reach the operator, not just exist on the object."""
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.heartbeat)
    assert '"stats": dict(self.stats)' in src, "counts still reported"
    assert '"stats_age_s": self.stats.ages()' in src, "ages reported beside them"


def test_the_runner_uses_the_timed_counter():
    import inspect
    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.Runner.__init__)
    assert "TimedCounter(clock=clock)" in src
    assert "self.stats = collections.Counter()" not in src


def test_nothing_about_the_counts_changed():
    """The fix is observability only: a TimedCounter counts identically."""
    plain = collections.Counter()
    timed = TimedCounter()
    for k in ("a", "a", "b", "c", "c", "c"):
        plain[k] += 1
        timed[k] += 1
    assert dict(plain) == dict(timed)
