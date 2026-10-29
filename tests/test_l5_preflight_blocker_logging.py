"""A preflight that fails must say WHY, on its first failure of the window.

1730z on deployment fb15a527 failed preflight five times and emitted not a
single L5_LIVE_PREFLIGHT line. `last_blockers` started as None, and the
fatal-exception report carries no `blocking_failures` key, so the
"log only when what blocks it CHANGES" test compared None against None,
found no change, and stayed silent for the whole window.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src import l5_pmus_live_runner as RUN


class FakeT:
    market_slug = "cpc-btc-updown-15m-2026-10-12-1730z"
    tick_size = "0.01"

    def __init__(self, reports):
        self._reports = list(reports)
        self.calls = 0

    def preflight(self, expect_account=None):
        self.calls += 1
        r = self._reports[min(self.calls - 1, len(self._reports) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


class Runner:
    """Only what maybe_preflight touches."""

    maybe_preflight = RUN.Runner.maybe_preflight

    def __init__(self, transport):
        self.transport = transport
        self.preflight_status = "NOT_RUN"
        self.last_preflight_t = 0.0
        self.preflight_attempts = 0
        self.last_blockers = RUN._BLOCKERS_UNSET
        self.armed_calls = 0

    def try_arm(self):
        self.armed_calls += 1


@pytest.fixture()
def emitted(monkeypatch) -> List[Dict[str, Any]]:
    seen: List[Dict[str, Any]] = []
    monkeypatch.setattr(RUN, "emit",
                        lambda marker, payload: seen.append((marker, payload)))
    return seen


def _preflights(seen):
    return [p for m, p in seen if m == "L5_LIVE_PREFLIGHT"]


def test_the_sentinel_is_not_none():
    """None is exactly what the fatal path yields, so it cannot be the
    'nothing seen yet' marker."""
    assert RUN._BLOCKERS_UNSET is not None
    assert RUN._BLOCKERS_UNSET != None             # noqa: E711


def test_a_fatal_preflight_failure_logs_its_reason(emitted, monkeypatch):
    """The exact production case: preflight raises, the report has no
    blocking_failures key, and the reason must still reach the log."""
    r = Runner(FakeT([RuntimeError("venue has not listed the market")]))
    r.maybe_preflight(1_000.0)
    out = _preflights(emitted)
    assert len(out) == 1, "a fatal preflight failure logged nothing"
    assert out[0]["preflight"] == "FAILED"
    assert "fatal" in out[0]
    assert "RuntimeError" in out[0]["fatal"]


def test_a_blocker_list_failure_also_logs_on_the_first_attempt(emitted):
    rep = {"preflight": "FAILED", "blocking_failures": ["tick_size_unknown"]}
    r = Runner(FakeT([rep]))
    r.maybe_preflight(1_000.0)
    assert _preflights(emitted) == [rep]


def test_an_unchanged_failure_does_not_spam(emitted):
    """The anti-spam property the original condition was written for is
    preserved: only the FIRST of a run of identical failures logs."""
    rep = {"preflight": "FAILED", "blocking_failures": ["tick_size_unknown"]}
    r = Runner(FakeT([rep, rep, rep]))
    t = 1_000.0
    for _ in range(3):
        r.maybe_preflight(t)
        t += 100.0                                 # past every backoff step
    assert r.transport.calls == 3
    assert len(_preflights(emitted)) == 1, "repeat of the same blockers spammed"


def test_repeated_fatal_failures_also_log_only_once(emitted):
    exc = RuntimeError("venue has not listed the market")
    r = Runner(FakeT([exc, exc, exc]))
    t = 1_000.0
    for _ in range(3):
        r.maybe_preflight(t)
        t += 100.0
    assert r.transport.calls == 3
    assert len(_preflights(emitted)) == 1


def test_a_changed_blocker_logs_again(emitted):
    a = {"preflight": "FAILED", "blocking_failures": ["tick_size_unknown"]}
    b = {"preflight": "FAILED", "blocking_failures": ["market_not_open"]}
    r = Runner(FakeT([a, b]))
    r.maybe_preflight(1_000.0)
    r.maybe_preflight(1_100.0)
    assert _preflights(emitted) == [a, b]


def test_a_pass_still_logs(emitted):
    rep = {"preflight": "PASSED", "market_slug": FakeT.market_slug}
    r = Runner(FakeT([rep]))
    r.maybe_preflight(1_000.0)
    assert _preflights(emitted) == [rep]
    assert r.armed_calls == 1


def test_the_window_roll_rearms_the_first_log(emitted):
    """A new window must get its own first-failure line even when the
    blockers are identical to the last window's."""
    rep = {"preflight": "FAILED", "blocking_failures": ["market_not_open"]}
    r = Runner(FakeT([rep, rep]))
    r.maybe_preflight(1_000.0)
    assert len(_preflights(emitted)) == 1
    r.preflight_status = "NOT_RUN"                 # what a window roll does
    r.last_blockers = RUN._BLOCKERS_UNSET
    r.preflight_attempts = 0
    r.last_preflight_t = 0.0
    r.maybe_preflight(1_100.0)
    assert len(_preflights(emitted)) == 2


def test_logging_cannot_arm_or_submit(emitted):
    """This change is observability only: a FAILED preflight still never
    reaches try_arm."""
    r = Runner(FakeT([RuntimeError("x")]))
    r.maybe_preflight(1_000.0)
    assert r.armed_calls == 0
    assert r.preflight_status == "FAILED"
