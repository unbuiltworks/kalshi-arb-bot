"""The BTC5m session retries its preflight inside the window.

Regression for 2026-11-12 03:45Z: the session preflighted 0.6 ms after the
window start, while the venue still read the 5-minute market CLOSED; the
transport raised, the runner kept only the exception's type name, made no
second attempt, and the window never armed (0 orders).

Deterministic and offline against the runner's scripted venue. Mechanism
only: nothing here is evidence about the real venue's open-flip latency.
"""
from __future__ import annotations

import pytest

from src import l5_btc5m_runner as RUN
from tests import test_l5_btc5m_runner as TR
from tests.test_l5_btc5m_runner import WS


class PreflightFailed(Exception):
    pass


def flaky(rig, fail_until_t, check="market_open_tradable", raise_=True):
    """Preflight fails (as the real transport does: it RAISES and keeps the
    report on preflight_report) until `fail_until_t` s into the window."""
    calls = []

    def pf():
        t = rig.clock() - WS.timestamp()
        calls.append(round(t, 2))
        if t < fail_until_t:
            rig.venue.pf = "FAILED"
            rep = {"preflight": "FAILED",
                   "checks": [{"check": "credentials_present", "passed": True},
                              {"check": check, "passed": False},
                              {"check": "book_two_sided_at_preflight",
                               "passed": False, "blocking": False}]}
            rig.venue.preflight_report = rep
            if raise_:
                raise PreflightFailed("{}")
            return rep
        rig.venue.pf = "PASSED"
        return {"preflight": "PASSED", "checks": [{"check": check, "passed": True}]}
    rig.venue.preflight = pf
    return calls


def test_a_late_open_flip_is_retried_and_the_window_arms(tmp_path, monkeypatch):
    rig = TR.Rig(tmp_path, monkeypatch)
    calls = flaky(rig, fail_until_t=2.0)
    st = rig.session.start()
    assert st["preflight"] == "PASSED" and st["transport_armed"] is True
    assert len(calls) == 2 and calls[0] == 0.0 and calls[1] >= 3.0
    a = st["preflight_attempts"]
    assert a[0]["failed"] == ["market_open_tradable"]     # named, not lost
    assert a[0]["error"] == "PreflightFailed"
    assert a[1]["preflight"] == "PASSED" and a[1]["failed"] == []


def test_the_failing_check_is_named_when_preflight_raises(tmp_path, monkeypatch):
    rig = TR.Rig(tmp_path, monkeypatch)
    flaky(rig, fail_until_t=10_000, check="account_readable")
    st = rig.session.start()
    assert st["preflight"] == "FAILED" and st["transport_armed"] is False
    assert st["preflight_failed"] == ["account_readable"]  # non-blocking excluded


def test_retries_stop_at_the_deadline_and_never_arm(tmp_path, monkeypatch):
    rig = TR.Rig(tmp_path, monkeypatch)
    calls = flaky(rig, fail_until_t=10_000)
    st = rig.session.start()
    assert st["transport_armed"] is False and rig.venue.armed is False
    assert calls[-1] <= RUN.PREFLIGHT_UNTIL_S
    # backoff 3, 6, 12, 24, 24 ... : 0, 3, 9, 21, 45, 69 then 93 > 90
    assert calls == [0.0, 3.0, 9.0, 21.0, 45.0, 69.0]


def test_a_full_session_after_a_late_flip_trades_and_reports(tmp_path, monkeypatch):
    rig = TR.Rig(tmp_path, monkeypatch)
    flaky(rig, fail_until_t=5.0, raise_=False)
    rep = rig.session.run()
    assert rep["transport_armed"] is True and rep["submitted"] > 0
    assert rep["close"]["clean"] is True


def test_never_failed_preflight_runs_once(tmp_path, monkeypatch):
    rig = TR.Rig(tmp_path, monkeypatch)
    calls = flaky(rig, fail_until_t=-1)
    st = rig.session.start()
    assert st["transport_armed"] is True and calls == [0.0]
    assert len(st["preflight_attempts"]) == 1


@pytest.mark.parametrize("err", [KeyboardInterrupt, SystemExit])
def test_interrupts_are_not_swallowed(tmp_path, monkeypatch, err):
    rig = TR.Rig(tmp_path, monkeypatch)

    def boom():
        raise err()
    rig.venue.preflight = boom
    with pytest.raises(err):
        rig.session.start()
