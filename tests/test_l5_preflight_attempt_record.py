"""Every preflight attempt leaves a record, including the silent failures.

WHY THIS EXISTS. `maybe_preflight` emits L5_LIVE_PREFLIGHT on a PASS, and
on a FAIL only when the blockers CHANGED. At a window roll every early
attempt fails the same way, so they all collapse into one line or none --
and that is why eighty seconds of the most expensive interval in this
engine has no record.

MEASURED 2026-10-27 on the public gateway: the venue flips a new BTC15m
window to MARKET_STATUS_OPEN / active / not-closed -- exactly what
preflight's market_open_tradable gate reads -- at **t=+0.6s** after the
boundary, and serves its metadata from **t=-5.9s**. Our preflight passed
at **+80.1s to +107.8s** across the nine campaign-2 windows. The delay is
ours, and attempts 1..5 are precisely what is unaccounted.

These tests pin the record's SHAPE, not a cause. The cause is what the
record is for.
"""
from __future__ import annotations

import pytest

from src import l5_pmus_live_runner as R

WS = 1793474100.0        # 2026-10-31T19:15:00Z
SLUG = "cpc-btc-updown-15m-2026-10-31-1915z"


class Book:
    """Stands in for the authenticated websocket mirror."""

    def __init__(self, snap=None, raises=False):
        self._snap, self._raises = snap, raises

    def snapshot(self, _slug):
        if self._raises:
            raise RuntimeError("socket not connected")
        return self._snap


class Runner:
    """The three attributes emit_preflight_attempt touches, and nothing else."""

    emit_preflight_attempt = R.Runner.emit_preflight_attempt

    def __init__(self, book=None, attempt=1, ws=WS):
        self.book = book or Book()
        self.preflight_attempts = attempt
        self.ws = ws
        self.transport = type("T", (), {"limiter": type(
            "L", (), {"tick_wait_s": 12.5})()})()
        self.stats = __import__("collections").Counter()


def capture(monkeypatch):
    seen = []
    monkeypatch.setattr(R, "emit", lambda k, d: seen.append((k, d)))
    return seen


def rep(verdict="FAILED", checks=(), **kw):
    out = {"preflight": verdict, "market_slug": SLUG, "checks": list(checks)}
    out.update(kw)
    return out


def chk(name, passed, blocking=None, detail=None):
    c = {"check": name, "passed": passed, "detail": detail}
    if blocking is not None:
        c["blocking"] = blocking
    return c


# ------------------------------------------------------- it records a failure

def test_a_failed_attempt_is_recorded_at_all(monkeypatch):
    """The whole point: L5_LIVE_PREFLIGHT would suppress this one."""
    seen = capture(monkeypatch)
    Runner().emit_preflight_attempt(WS + 3.2, rep(), 1.4)
    assert [k for k, _ in seen] == ["L5_LIVE_PREFLIGHT_ATTEMPT"]
    d = seen[0][1]
    assert d["verdict"] == "FAILED"
    assert d["attempt"] == 1
    assert d["t_in_window_s"] == 3.2
    assert d["attempt_duration_s"] == 1.4


def test_the_first_blocking_gate_is_named(monkeypatch):
    seen = capture(monkeypatch)
    Runner().emit_preflight_attempt(WS + 5, rep(checks=[
        chk("credentials_present", True),
        chk("book_two_sided_at_preflight", False, blocking=False),   # skipped
        chk("book_readable", False),                                 # this one
        chk("market_open_tradable", False)]), 0.5)
    assert seen[0][1]["first_blocking"] == "book_readable"


def test_a_non_blocking_failure_is_never_named_as_the_blocker(monkeypatch):
    seen = capture(monkeypatch)
    Runner().emit_preflight_attempt(WS + 5, rep(verdict="PASSED", checks=[
        chk("book_two_sided_at_preflight", False, blocking=False)]), 0.2)
    assert seen[0][1]["first_blocking"] is None
    assert seen[0][1]["verdict"] == "PASSED"


def test_the_book_retry_ladder_is_carried_through(monkeypatch):
    """The exact detail shape the live records carry."""
    seen = capture(monkeypatch)
    ladder = ["attempt1:HTTPError", "attempt2:HTTPError",
              "attempt3:HTTPError", "ws_fallback:accepted"]
    Runner().emit_preflight_attempt(WS + 84.2, rep(
        verdict="PASSED",
        checks=[chk("book_read_attempts", True, blocking=False,
                    detail=ladder)]), 3.1)
    assert seen[0][1]["book_read_attempts"] == ladder


# ---------------------------------------------- the socket's own readiness

def test_a_ready_socket_is_recorded_as_ready(monkeypatch):
    """The live shape from 1500z, which passed at +107.8s with this state."""
    seen = capture(monkeypatch)
    Runner(book=Book({"bid": "0.7700", "ask": "0.7800",
                      "subscribed_before_open": True,
                      "stream_age_s": 0.053, "book_updates": 968,
                      "market_state": "MARKET_STATE_OPEN"})
           ).emit_preflight_attempt(WS + 3, rep(), 0.9)
    ws = seen[0][1]["ws_book"]
    assert ws["snapshot"] is True and ws["two_sided"] is True
    assert ws["subscribed_before_open"] is True
    assert ws["stream_age_s"] == 0.053
    assert ws["book_updates"] == 968
    assert ws["fallback_max_age_s"] == 30.0


@pytest.mark.parametrize("snap,why", [
    (None, "no snapshot at all"),
    ({"bid": None, "ask": "0.78", "subscribed_before_open": True,
      "stream_age_s": 0.1}, "one-sided"),
    ({"bid": "0.77", "ask": "0.78", "subscribed_before_open": False,
      "stream_age_s": 0.1}, "subscribed late"),
    ({"bid": "0.77", "ask": "0.78", "subscribed_before_open": True,
      "stream_age_s": 99.0}, "stale frame"),
])
def test_every_reason_the_fallback_could_refuse_is_visible(
        monkeypatch, snap, why):
    """So 'the socket was not ready' stops being indistinguishable from
    'the socket was ready and the fallback still did not fire'."""
    seen = capture(monkeypatch)
    Runner(book=Book(snap)).emit_preflight_attempt(WS + 3, rep(), 0.5)
    ws = seen[0][1]["ws_book"]
    if snap is None:
        assert ws["snapshot"] is False
    else:
        ok = (ws["two_sided"] and ws["subscribed_before_open"] is True
              and float(ws["stream_age_s"]) <= ws["fallback_max_age_s"])
        assert ok is False, why


def test_a_raising_socket_never_takes_the_record_with_it(monkeypatch):
    seen = capture(monkeypatch)
    Runner(book=Book(raises=True)).emit_preflight_attempt(WS + 3, rep(), 0.5)
    assert seen[0][1]["ws_book"]["snapshot"] is False


# -------------------------------------------------------------- the ladder

@pytest.mark.parametrize("attempt,nxt", [
    (1, 6.0), (2, 12.0), (3, 24.0), (4, 24.0), (7, 24.0)])
def test_the_next_interval_is_recorded_so_the_ladder_is_reconstructable(
        monkeypatch, attempt, nxt):
    seen = capture(monkeypatch)
    Runner(attempt=attempt).emit_preflight_attempt(WS + 1, rep(), 0.1)
    assert seen[0][1]["next_attempt_in_s"] == nxt


def test_a_fatal_attempt_is_recorded_with_its_reason(monkeypatch):
    seen = capture(monkeypatch)
    Runner().emit_preflight_attempt(
        WS + 2, rep(fatal="URLError: timed out"), 20.0)
    assert seen[0][1]["fatal"] == "URLError: timed out"
    assert seen[0][1]["attempt_duration_s"] == 20.0


def test_no_window_means_no_offset_rather_than_a_wrong_one(monkeypatch):
    seen = capture(monkeypatch)
    Runner(ws=None).emit_preflight_attempt(WS + 2, rep(), 0.1)
    assert seen[0][1]["t_in_window_s"] is None


# --------------------------------------------------- it can never break one

def test_a_junk_report_is_counted_not_raised(monkeypatch):
    capture(monkeypatch)
    r = Runner()
    r.emit_preflight_attempt(WS + 1, {"checks": "not-a-list"}, 0.1)
    assert sum(v for k, v in r.stats.items()
               if k.startswith("preflight_attempt_emit_error")) == 1


def test_an_emit_that_raises_is_counted_not_raised(monkeypatch):
    def boom(_k, _d):
        raise RuntimeError("log transport down")
    monkeypatch.setattr(R, "emit", boom)
    r = Runner()
    r.emit_preflight_attempt(WS + 1, rep(), 0.1)        # must not raise
    assert sum(v for k, v in r.stats.items()
               if k.startswith("preflight_attempt_emit_error")) == 1
