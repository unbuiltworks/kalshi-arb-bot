"""The feed must survive a venue-side close. RUNG A 1600z is the case.

At 2026-09-28T16:00Z the probe's authenticated book feed took

    ConnectionClosedOK: received 1001 (going away) server shutting down

`_run` ran exactly ONE session, so that close ended the feed thread. The book
went 212.662s stale, `fresh()` correctly said False, and the freshness gate
refused the remaining 38 of 92 actions. SUBMITTED 0, FILLED 0.

The gate was right. These tests pin the two halves of the fix:

  1. a close is followed by a reconnect that restores a REAL book;
  2. while the gap is open, freshness still fails closed -- reconnect never
     manufactures, extends or back-dates a book state.

Backoff is injected as 0.0 so the supervisor can be driven at full speed.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from src import l5_live_book_feed as F
from tests.test_l5_live_book_feed import book_msg


class ConnectionClosedOK(Exception):
    """Named to match what the venue actually produced."""


class _WS:
    def __init__(self, script, feed):
        self._script = list(script)
        self._feed = feed
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self._script:
            self._feed._stop.set()
            return json.dumps({})
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)


class _Connect:
    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *a):
        return False


def _sessions(monkeypatch, feed, scripts):
    """Install a websockets module that serves one script per connect()."""
    made = []

    def connect(url, **kw):
        script = scripts[min(len(made), len(scripts) - 1)]
        ws = _WS(script, feed)
        made.append(ws)
        return _Connect(ws)

    mod = types.ModuleType("websockets")
    mod.connect = connect
    monkeypatch.setitem(sys.modules, "websockets", mod)
    return made


def _credentials(monkeypatch):
    from src import pmus_ws_evidence_recorder as R
    monkeypatch.setenv(R.KEY_ENV, "test-key-id")
    monkeypatch.setenv(R.SECRET_ENV,
                       "dGVzdC1zZWNyZXQtbm90LWEtcmVhbC1rZXktMDAwMA==")
    monkeypatch.setattr(R, "auth_headers",
                        lambda k, s, path=None, now_ms=None:
                        {"X-PM-Access-Key": k})


def _feed(**kw):
    kw.setdefault("backoff_s", (0.0,))
    return F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-28-1600z", **kw)


# ------------------------------------------------------------------ recovery

def test_a_1001_close_is_followed_by_a_reconnect_that_restores_the_book(
        monkeypatch):
    """THE RUNG-A REGRESSION."""
    f = _feed(max_reconnects=3)
    made = _sessions(monkeypatch, f, [
        [ConnectionClosedOK("received 1001 (going away) server shutting down")],
        [book_msg(f.market, "0.42", "0.58")],
    ])
    _credentials(monkeypatch)

    f._supervise()

    assert f.sessions >= 2, "the feed gave up after one session"
    assert f.reconnects >= 1
    assert len(made) >= 2, "connect() was never called a second time"
    assert f.latest() is not None, "no book survived the reconnect"
    assert f.latest()["ask"] == "0.58", "the post-reconnect book is the real one"
    assert any("ConnectionClosedOK" in e for e in f.errors), f.errors


def test_repeated_closes_keep_being_retried_until_the_budget_runs_out(
        monkeypatch):
    f = _feed(max_reconnects=4)
    _sessions(monkeypatch, f, [[ConnectionClosedOK("1001")]])
    _credentials(monkeypatch)

    f._supervise()

    assert f.reconnects == 4, f.reconnects
    assert f.sessions == 5, f.sessions
    assert f.stop_reason == "reconnects_exhausted", f.stop_reason


def test_a_delivering_session_earns_a_fresh_backoff_budget(monkeypatch):
    """A long healthy stream interrupted once is not a flapping feed."""
    f = _feed(max_reconnects=3, backoff_s=(0.0, 9999.0))
    _sessions(monkeypatch, f, [
        [book_msg(f.market, "0.40", "0.60"),
         ConnectionClosedOK("1001")],
        [book_msg(f.market, "0.41", "0.59")],
    ])
    _credentials(monkeypatch)

    f._supervise()          # would hang on 9999s if the budget never reset

    assert f.latest()["ask"] == "0.59"


# ------------------------------------------- freshness is NOT weakened by any of it

def test_the_freshness_bound_is_unchanged():
    assert F.MAX_AGE_S == 2.0


def test_reconnect_appends_no_book_state_of_its_own(monkeypatch):
    """The whole safety property: a gap stays a gap."""
    f = _feed(max_reconnects=3)
    _sessions(monkeypatch, f, [[ConnectionClosedOK("1001")]])
    _credentials(monkeypatch)

    f._supervise()

    assert f.books() == [], "recovery invented a book state"
    assert f.latest() is None
    assert f.fresh() is False, "a feed that never delivered must not be fresh"


def test_a_stale_book_stays_stale_across_a_reconnect(monkeypatch):
    """Time advances, the gap is real, and the gate must keep refusing."""
    now = {"t": 1000.0}
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-28-1600z",
                                clock=lambda: now["t"],
                                max_reconnects=2, backoff_s=(0.0,))
    _sessions(monkeypatch, f, [
        [book_msg(f.market, "0.40", "0.60"), ConnectionClosedOK("1001")],
        [],
    ])
    _credentials(monkeypatch)
    f._supervise()

    assert f.latest() is not None and f.fresh() is True, "fresh while at t=0 age"
    now["t"] += 212.662                      # the gap Rung A actually suffered
    assert f.age_s() > F.MAX_AGE_S
    assert f.fresh() is False, (
        "a reconnect must not make an old book look current")


def test_freshness_is_decided_by_age_alone_not_by_connection_state(monkeypatch):
    """`connected` must not become a shortcut around the age bound."""
    now = {"t": 500.0}
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-28-1600z",
                                clock=lambda: now["t"], backoff_s=(0.0,))
    _sessions(monkeypatch, f, [[book_msg(f.market, "0.40", "0.60")]])
    _credentials(monkeypatch)
    f._supervise()
    now["t"] += 3.0
    f.connected = True                       # claim a healthy connection
    assert f.fresh() is False, "connection state overrode the age bound"


# ---------------------------------------------------------------- termination

def test_stop_ends_the_supervisor_rather_than_waiting_out_the_backoff(
        monkeypatch):
    f = _feed(max_reconnects=50, backoff_s=(9999.0,))
    _sessions(monkeypatch, f, [[ConnectionClosedOK("1001")]])
    _credentials(monkeypatch)
    f._stop.set()

    f._supervise()

    assert f.stop_reason == "local_cancellation", f.stop_reason
    assert f.sessions == 0, "a stopped feed must not open a session"


def test_the_deadline_stops_reconnecting_after_the_window(monkeypatch):
    now = {"t": 100.0}
    f = F.AuthenticatedBookFeed("cpc-btc-updown-15m-2026-09-28-1600z",
                                clock=lambda: now["t"],
                                deadline=100.0, backoff_s=(0.0,))
    _sessions(monkeypatch, f, [[ConnectionClosedOK("1001")]])
    _credentials(monkeypatch)

    f._supervise()

    assert f.stop_reason == "deadline", f.stop_reason
    assert f.sessions == 0, "a feed past its window must not reconnect"


# ------------------------------------------------- it is on the PRODUCTION path

def test_start_supervises_rather_than_running_one_session():
    """A supervisor nothing calls is not a fix.

    Rung A's lesson twice over: the defect was never the absence of code, it
    was the absence of code ON THE PATH. So this asserts the thread body.
    """
    import inspect
    src = inspect.getsource(F.AuthenticatedBookFeed.start)
    assert "_supervise" in src, src
    assert "target=self._run" not in src, (
        "start() still launches a single session")


def test_health_reports_the_recovery_counters(monkeypatch):
    """The report must be able to say a reconnect happened."""
    f = _feed(max_reconnects=1)
    _sessions(monkeypatch, f, [[ConnectionClosedOK("1001")]])
    _credentials(monkeypatch)
    f._supervise()

    h = f.health()
    for k in ("sessions", "reconnects", "last_error", "stop_reason", "fresh"):
        assert k in h, k
    assert h["reconnects"] == 1
    assert "ConnectionClosedOK" in str(h["last_error"])
