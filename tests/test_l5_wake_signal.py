"""The wake must shorten the sleep without ever deciding anything.

WHY THIS EXISTS. The engine's decision cadence was `time.sleep(3)` while the
authenticated book arrived ~4.5x a second. These pin the three properties
that make replacing that sleep safe: a feed thread can only RAISE, bursts
coalesce to one wake, and the floor delays a wake rather than discarding it.
"""
from __future__ import annotations

import threading

import pytest

from src import l5_wake_signal as W


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def _sig(min_interval_s=0.25):
    c = FakeClock()
    return W.WakeSignal(min_interval_s=min_interval_s,
                        clock=c.now, sleep=c.sleep), c


# ------------------------------------------------------------- the timer path

def test_no_event_means_the_timer_ends_the_sleep_exactly_as_before():
    sig, _ = _sig()
    out = sig.wait(0.0)
    assert out["woken"] is False
    assert out["latency_s"] is None
    assert sig.stats()["timer_expiries"] == 1


def test_an_unreadable_timeout_does_not_raise():
    sig, _ = _sig()
    out = sig.wait("not-a-number")
    assert out["woken"] in (True, False)


# ------------------------------------------------------------- the wake path

def test_a_raise_wakes_the_loop_and_reports_its_latency():
    sig, c = _sig(min_interval_s=0)
    sig.raise_("TOUCH_MOVED")
    c.t += 0.040                      # 40ms between raise and service
    out = sig.wait(5.0)
    assert out["woken"] is True
    assert out["latency_s"] == pytest.approx(0.04, abs=1e-6)
    st = sig.stats()
    assert st["wakes"] == 1
    assert st["reasons"] == {"TOUCH_MOVED": 1}
    assert st["latency_p50_s"] == pytest.approx(0.04, abs=1e-6)


def test_a_burst_coalesces_into_one_wake():
    """Level-triggered: a thousand raises between ticks is one wake, so a
    burst cannot queue up a thousand decisions.
    """
    sig, c = _sig(min_interval_s=0)
    for _ in range(1000):
        sig.raise_("TOUCH_MOVED")
    out = sig.wait(5.0)
    assert out["woken"] is True
    st = sig.stats()
    assert st["raises"] == 1000
    assert st["wakes"] == 1


def test_latency_is_measured_from_the_OLDEST_unserved_raise():
    """The oldest pending signal is the one that has been waiting; using the
    newest would flatter the number.
    """
    sig, c = _sig(min_interval_s=0)
    sig.raise_("A")
    c.t += 0.100
    sig.raise_("B")
    c.t += 0.010
    out = sig.wait(5.0)
    assert out["latency_s"] == pytest.approx(0.11, abs=1e-6)


def test_the_signal_is_rearmed_after_being_served():
    sig, c = _sig(min_interval_s=0)
    sig.raise_("A")
    assert sig.wait(5.0)["woken"] is True
    out = sig.wait(0.0)
    assert out["woken"] is False, "a served wake must not wake again"
    sig.raise_("B")
    assert sig.wait(5.0)["woken"] is True


# ------------------------------------------- the floor delays, never discards

def test_the_floor_delays_a_rapid_second_wake():
    sig, c = _sig(min_interval_s=0.25)
    sig.raise_("A")
    sig.wait(5.0)                      # serves at t0, sets last_served
    sig.raise_("B")                    # immediately after
    out = sig.wait(5.0)
    assert out["woken"] is True, "the second signal must still be served"
    assert out["floor_wait_s"] > 0
    assert c.slept and abs(c.slept[0] - 0.25) < 1e-9


def test_the_floor_does_not_discard_the_signal():
    """The raise stays pending across the floor sleep, so the count of
    wakes equals the count of distinct raise groups -- none is dropped.
    """
    sig, c = _sig(min_interval_s=0.25)
    sig.raise_("A")
    sig.wait(5.0)
    sig.raise_("B")
    sig.wait(5.0)
    st = sig.stats()
    assert st["raises"] == 2
    assert st["wakes"] == 2
    assert st["floor_waits"] == 1


def test_no_floor_wait_when_enough_time_already_passed():
    sig, c = _sig(min_interval_s=0.25)
    sig.raise_("A")
    sig.wait(5.0)
    c.t += 1.0
    sig.raise_("B")
    out = sig.wait(5.0)
    assert out["floor_wait_s"] == 0.0


# ------------------------------------------------------------- percentiles

def test_percentiles_do_not_interpolate_invented_values():
    """A p99 over a handful of samples must be a number an event produced."""
    sig, c = _sig(min_interval_s=0)
    for ms in (10, 20, 30, 40, 500):
        sig.raise_("X")
        c.t += ms / 1000.0
        sig.wait(5.0)
    st = sig.stats()
    assert st["latency_samples"] == 5
    produced = (0.01, 0.02, 0.03, 0.04, 0.5)
    for key in ("latency_p50_s", "latency_p95_s", "latency_p99_s"):
        assert any(st[key] == pytest.approx(v, abs=1e-6)
                   for v in produced), (key, st[key])
    assert st["latency_max_s"] == pytest.approx(0.5, abs=1e-6)
    assert st["latency_p50_s"] == pytest.approx(0.03, abs=1e-6)


def test_percentiles_are_none_before_any_wake():
    sig, _ = _sig()
    st = sig.stats()
    assert st["latency_samples"] == 0
    assert st["latency_p50_s"] is None
    assert st["latency_p99_s"] is None


# --------------------------------------------------- raising is thread-safe

def test_concurrent_raises_are_counted_exactly_once_each():
    sig, _ = _sig(min_interval_s=0)
    n = 200

    def worker():
        for _ in range(n):
            sig.raise_("T")

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sig.stats()["raises"] == 4 * n


def test_a_raise_never_propagates_an_exception_to_the_feed():
    """A feed thread must not be broken by the wake mechanism."""
    sig, _ = _sig()

    class Hostile:
        def __str__(self):
            raise RuntimeError("no")

    sig.raise_(Hostile())             # must not raise
    assert True


# ============================================== TouchWatcher relevance filter

def test_only_a_touch_CHANGE_on_a_RELEVANT_market_wakes():
    sig, _ = _sig(min_interval_s=0)
    tw = W.TouchWatcher(sig, relevant=lambda _s: True)
    assert tw.observe("m", "0.48", "0.49") is False, "first frame is a baseline"
    assert tw.observe("m", "0.48", "0.49") is False, "unchanged touch"
    assert tw.observe("m", "0.49", "0.50") is True
    assert sig.stats()["reasons"] == {"TOUCH_MOVED": 1}


def test_an_irrelevant_market_never_wakes_the_loop():
    """The live slate is hundreds of markets; only those we hold or rest in
    can produce an action.
    """
    sig, _ = _sig(min_interval_s=0)
    tw = W.TouchWatcher(sig, relevant=lambda _s: False)
    tw.observe("m", "0.48", "0.49")
    assert tw.observe("m", "0.49", "0.50") is False
    assert sig.stats()["raises"] == 0
    assert tw.status()["skips"]["market_not_relevant"] == 1


def test_a_one_sided_book_does_not_wake():
    sig, _ = _sig(min_interval_s=0)
    tw = W.TouchWatcher(sig, relevant=lambda _s: True)
    assert tw.observe("m", None, "0.49") is False
    assert tw.observe("m", "0.48", None) is False
    assert sig.stats()["raises"] == 0


def test_a_raising_relevance_test_does_not_wake_and_does_not_escape():
    sig, _ = _sig(min_interval_s=0)

    def boom(_s):
        raise RuntimeError("inventory read failed")

    tw = W.TouchWatcher(sig, relevant=boom)
    tw.observe("m", "0.48", "0.49")
    assert tw.observe("m", "0.49", "0.50") is False
    assert sig.stats()["raises"] == 0
    assert any(k.startswith("relevance_error:")
               for k in tw.status()["skips"])


def test_markets_are_tracked_independently():
    sig, _ = _sig(min_interval_s=0)
    tw = W.TouchWatcher(sig, relevant=lambda _s: True)
    tw.observe("a", "0.10", "0.11")
    tw.observe("b", "0.90", "0.91")
    assert tw.observe("a", "0.11", "0.12") is True
    assert tw.observe("b", "0.90", "0.91") is False
    assert tw.status()["tracked_markets"] == 2
