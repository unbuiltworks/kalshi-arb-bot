"""Rollover from the clock, with the latch re-read every window.

The bounded path takes its windows from the approval's enumeration, so
it cannot roll past its own list. These tests pin the persistent
alternative: the window comes from the clock, and whether another one may
run at all is asked again before each.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_btc5m_runner as RUN
from src import l5_persistent_mode as P

UTC = dt.timezone.utc
DEP = "sha-b3ff6e4c8"
BTC = "cpc-btc-updown-5m-"


def envelope(**kw):
    base = {"approved": True, "mode": "PERSISTENT", "deployment": DEP,
            "strategies": ["STRADDLE", "LATE_FAVORITE"],
            "series": [BTC, "cpceth-eth-updown-5m-"],
            "taker_allowed": False, "max_aggregate_usd": "10.00",
            "max_per_market_usd": "5.00", "max_daily_loss_usd": "5.00",
            "nonce": "rollover-test-0001"}
    base.update(kw)
    return json.dumps(base)


@pytest.fixture
def armed(tmp_path, monkeypatch):
    monkeypatch.setenv(P.DEPLOYMENT_ENV, DEP)
    monkeypatch.setenv(P.ENVELOPE_ENV, envelope())
    monkeypatch.setenv(P.STATE_PATH_ENV, str(tmp_path / "state.json"))
    P.write_state(P.ARMED, why="test", now=dt.datetime(2026, 11, 16, 17,
                                                       tzinfo=UTC))
    return tmp_path


class TestTheWindowComesFromTheClock:

    def test_the_current_window_is_offered(self):
        now = dt.datetime(2026, 11, 16, 17, 2, 0, tzinfo=UTC)
        got = RUN.persistent_next(now=now, prefix=BTC)
        assert got["market"] == BTC + "2026-11-16-1700z"
        assert got["wait_s"] == 0.0

    def test_consecutive_windows_advance_without_a_list(self):
        """Four windows in a row, no enumeration anywhere."""
        seen = []
        for m in (0, 5, 10, 15):
            now = dt.datetime(2026, 11, 16, 17, m, 10, tzinfo=UTC)
            seen.append(RUN.persistent_next(now=now, prefix=BTC)["market"])
        assert seen == [BTC + "2026-11-16-17%02dz" % t
                        for t in (0, 5, 10, 15)]
        assert len(set(seen)) == 4

    def test_it_rolls_across_an_hour_and_a_day_boundary(self):
        a = RUN.persistent_next(
            now=dt.datetime(2026, 11, 16, 17, 58, 10, tzinfo=UTC), prefix=BTC)
        b = RUN.persistent_next(
            now=dt.datetime(2026, 11, 16, 23, 58, 10, tzinfo=UTC), prefix=BTC)
        assert a["market"] == BTC + "2026-11-16-1755z"
        assert b["market"] == BTC + "2026-11-16-2355z"

    def test_a_window_about_to_close_is_not_offered(self):
        """Entering one with seconds left buys the worst of both ends: no
        time to work an order, and a settlement immediately after."""
        now = dt.datetime(2026, 11, 16, 17, 4, 50, tzinfo=UTC)
        got = RUN.persistent_next(now=now, prefix=BTC)
        assert got["market"] == BTC + "2026-11-16-1705z"
        assert got["wait_s"] == 10.0

    def test_the_grace_boundary_is_exact(self):
        """Exactly `grace_s` left is ENOUGH time, so the boundary is
        exclusive: 20s remaining takes the current window, 19s waits."""
        at_20 = dt.datetime(2026, 11, 16, 17, 4, 40, tzinfo=UTC)
        got = RUN.persistent_next(now=at_20, prefix=BTC)
        assert got["wait_s"] == 0.0
        assert got["market"] == BTC + "2026-11-16-1700z"
        at_19 = dt.datetime(2026, 11, 16, 17, 4, 41, tzinfo=UTC)
        nxt = RUN.persistent_next(now=at_19, prefix=BTC)
        assert nxt["wait_s"] == 19.0
        assert nxt["market"] == BTC + "2026-11-16-1705z"

    def test_each_lane_gets_its_own_series(self):
        from src import l5_series_registry as REG
        now = dt.datetime(2026, 11, 16, 17, 2, 0, tzinfo=UTC)
        got = {r.strategy: RUN.persistent_next(now=now,
                                               prefix=r.prefix)["market"]
               for r in REG.REGISTRY if r.horizon_s == 300.0}
        assert len(set(got.values())) == 4
        assert got["ETH5M"].startswith("cpceth-eth-updown-5m-")

    @pytest.mark.parametrize("kw", [
        {"now": None}, {"now": dt.datetime(2026, 11, 16, 17, tzinfo=None)},
        {"prefix": ""}, {"prefix": None},
        {"horizon_s": 0}, {"horizon_s": "x"}, {"grace_s": -1},
        {"grace_s": 300.0},
    ])
    def test_an_unusable_input_offers_no_window(self, kw):
        base = dict(now=dt.datetime(2026, 11, 16, 17, 2, tzinfo=UTC),
                    prefix=BTC)
        base.update(kw)
        got = RUN.persistent_next(**base)
        assert got["ok"] is False and got["market"] is None


class TestItAsksBeforeEveryWindow:

    def test_armed_permits_another_window(self, armed):
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        assert g["ok"] is True and g["exit"] is False
        assert g["scope"]["max_aggregate_usd"] is not None

    @pytest.mark.parametrize("latched", [P.HALTED, P.STOPPED])
    def test_a_latch_exits_the_loop_rather_than_pausing_it(self, armed, latched):
        """A process that keeps looping after a safety halt resumes the
        moment somebody clears the latch for an unrelated reason."""
        P.write_state(latched, why="because",
                      now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, 6,
                                                tzinfo=UTC))
        assert g["ok"] is False and g["exit"] is True
        assert g["reason"] == "STATE_%s" % latched

    def test_monitor_waits_rather_than_exiting(self, armed):
        """An operator may arm it without restarting anything."""
        P.write_state(P.MONITOR, why="stood down",
                      now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, 6,
                                                tzinfo=UTC))
        assert g["ok"] is False and g["exit"] is False
        assert g["reason"] == "STATE_NOT_ARMED"

    def test_a_latch_written_mid_window_stops_the_next_one(self, armed):
        """Re-read EVERY window, not once at start."""
        first = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17,
                                                    tzinfo=UTC))
        assert first["ok"] is True
        P.write_state(P.STOPPED, why="operator",
                      now=dt.datetime(2026, 11, 16, 17, 3, tzinfo=UTC))
        second = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, 6,
                                                     tzinfo=UTC))
        assert second["exit"] is True

    def test_a_lost_envelope_exits(self, armed, monkeypatch):
        monkeypatch.delenv(P.ENVELOPE_ENV, raising=False)
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        assert g["ok"] is False and g["exit"] is True
        assert g["reason"] == "ENVELOPE_ABSENT"

    def test_an_expired_envelope_exits(self, armed, monkeypatch):
        monkeypatch.setenv(P.ENVELOPE_ENV,
                           envelope(expires_utc="2026-11-16T16:00:00Z"))
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        assert g["exit"] is True and g["reason"] == "ENVELOPE_EXPIRED"

    def test_another_deployments_envelope_exits(self, armed, monkeypatch):
        monkeypatch.setenv(P.DEPLOYMENT_ENV, "sha-other")
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        assert g["exit"] is True
        assert g["reason"] == "ENVELOPE_IS_FOR_ANOTHER_DEPLOYMENT"

    def test_an_unreadable_state_exits_rather_than_assuming_monitor(
            self, armed, monkeypatch, tmp_path):
        bad = tmp_path / "corrupt.json"
        bad.write_text("not json", encoding="utf-8")
        monkeypatch.setenv(P.STATE_PATH_ENV, str(bad))
        g = RUN.persistent_gate(now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        assert g["exit"] is True and g["reason"] == "STATE_CORRUPT"

    def test_nothing_rearms_after_a_halt(self, armed):
        P.write_state(P.HALTED, why="daily loss",
                      now=dt.datetime(2026, 11, 16, 17, tzinfo=UTC))
        for m in (6, 11, 16, 21):
            g = RUN.persistent_gate(
                now=dt.datetime(2026, 11, 16, 17, m, tzinfo=UTC))
            assert g["exit"] is True
        assert P.read_state()["state"] == P.HALTED
