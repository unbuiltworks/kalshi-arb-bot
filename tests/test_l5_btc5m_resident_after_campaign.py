"""After a campaign ends the lane stays up, disarmed, readable, and settles.

Observed 2026-11-21: the lane exited at NOT_AFTER, the watchdog could not
read its registry for six hours, and the window's settlement was recorded
only at the next boot.
"""
import datetime as dt

import pytest

from src import l5_btc5m_runner as RUN
from src import l5_persistent_mode as PMODE

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 21, 6, 25, 30, tzinfo=UTC)


class T:
    def __init__(self, armed=False, sticky=False, verdicts=None):
        self.armed = armed
        self.sticky = sticky
        self.verdicts = verdicts or {}
        self.calls = []

    def disarm(self, reason=""):
        self.calls.append(("disarm", reason))
        if not self.sticky:
            self.armed = False

    def settlement(self, m):
        self.calls.append(("settlement", m))
        return self.verdicts.get(m, {"terminal": False})

    def __getattr__(self, name):
        raise AssertionError("resident loop touched transport.%s" % name)


class Ex:
    def __init__(self, t, pending=()):
        self.transport = t
        self._pending = list(pending)
        self.settled = []
        self.stopped = []
        self.synced = 0

    def pending_settlement(self):
        return list(self._pending)

    def settle(self, m, up_won):
        self.settled.append((m, up_won))
        self._pending.remove(m)
        return {"settlement_pnl": "0.0195", "already_settled": False}

    def sync_reservation(self):
        self.synced += 1
        return {"ok": True}

    def stop(self, reason, now):
        self.stopped.append(reason)
        return {"ok": True}

    def __getattr__(self, name):
        raise AssertionError("resident loop touched executor.%s" % name)


@pytest.fixture
def state(monkeypatch):
    box = {"state": PMODE.MONITOR, "ok": True, "writes": []}
    monkeypatch.setattr(PMODE, "read_state", lambda: {
        "ok": box["ok"], "state": box["state"], "reason": "UNREADABLE"})

    def write(state, why="", now=None):
        box["writes"].append((state, why))
        box["state"] = state
        return {"ok": True}
    monkeypatch.setattr(PMODE, "write_state", write)
    return box


def run(ex, state, passes=1, halt=None, on_sleep=None):
    out = []
    code = RUN.resident_after_campaign(
        ex, lambda k, d: out.append((k, dict(d))),
        sleep=(on_sleep or (lambda s: None)), clock=lambda: 0.0,
        now=lambda: NOW, halt=halt or (lambda: {"ok": True}),
        max_passes=passes)
    return code, out


M = "cpc-btc-updown-5m-2026-11-21-0620z"


def test_it_stays_and_reports_that_it_cannot_submit(state):
    ex = Ex(T(), pending=[M])
    code, out = run(ex, state, passes=3)
    assert code == 0
    kinds = [k for k, _ in out]
    assert kinds == ["L5_BTC5M_RESIDENT_AFTER_CAMPAIGN"]   # unchanged: once
    d = out[0][1]
    assert d["can_submit"] is False and d["transport_armed"] is False
    assert d["pending_count"] == 1
    assert state["writes"] == []                          # never writes ARMED


def test_the_window_is_settled_when_the_venue_settles_it(state):
    t = T(verdicts={})
    ex = Ex(t, pending=[M])
    n = {"i": 0}

    def on_sleep(_):
        n["i"] += 1
        if n["i"] == 2:
            t.verdicts[M] = {"terminal": True, "settlement_px": "0"}
    code, out = run(ex, state, passes=4, on_sleep=on_sleep)
    assert ex.settled == [(M, False)]
    settled = [d for _, d in out if d.get("settlement", {}).get("settled")]
    assert settled and settled[0]["settlement"]["settled"][0]["market"] == M
    assert ex.synced == 4


def test_only_reads_through_the_transport(state):
    t = T()
    run(Ex(t, pending=[M]), state, passes=2)
    assert {c[0] for c in t.calls} == {"disarm", "settlement"}


def test_a_transport_that_stays_armed_ends_the_process(state):
    code, out = run(Ex(T(armed=True, sticky=True)), state)
    assert out == [("L5_BTC5M_RESIDENT_EXIT",
                    {"reason": "TRANSPORT_STILL_ARMED",
                     "utc": NOW.isoformat()})]


def test_an_armed_transport_is_disarmed_first(state):
    t = T(armed=True)
    code, out = run(Ex(t), state)
    assert t.armed is False
    assert out[0][1]["transport_armed"] is False


@pytest.mark.parametrize("st", sorted(PMODE.LATCHED))
def test_a_latch_exits_rather_than_idling(state, st):
    state["state"] = st
    code, out = run(Ex(T()), state, passes=5)
    assert out[-1][0] == "L5_BTC5M_RESIDENT_EXIT"
    assert out[-1][1]["reason"] == "STATE_%s" % st


def test_unreadable_state_exits(state):
    state["ok"] = False
    code, out = run(Ex(T()), state)
    assert out[-1][0] == "L5_BTC5M_RESIDENT_EXIT"


def test_a_state_someone_else_set_to_armed_is_not_traded_on(state):
    state["state"] = PMODE.ARMED
    ex = Ex(T())
    code, out = run(ex, state, passes=5)
    assert out[-1][1]["reason"] == "STATE_NOT_MONITOR:%s" % PMODE.ARMED


def test_a_watchdog_halt_latches_and_unwinds(state):
    ex = Ex(T())
    code, out = run(ex, state, halt=lambda: {"ok": False, "reason": "HALT_SET"})
    assert state["writes"] == [(PMODE.HALTED, "WATCHDOG:HALT_SET")]
    assert ex.stopped == ["WATCHDOG:HALT_SET"]
    assert out[-1][0] == "L5_BTC5M_PERSISTENT_HALTED"


def test_no_transport_still_stays_readable(state):
    ex = Ex(None)
    code, out = run(ex, state)
    assert out[0][1]["settlement"]["error"] == "NO_TRANSPORT"
