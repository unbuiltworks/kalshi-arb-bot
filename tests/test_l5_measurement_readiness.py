"""Regressions for the four measurement-readiness defects.

NONE of these change what the engine trades, what it books, or which limits
it enforces. Each one pins a piece of TELEMETRY that was contradicting the
code it was supposed to describe, which is its own kind of defect: a reader
acting on `stream_age_s` concluded the feed was 25 seconds behind while
prints were arriving in a tenth of a second, and a reader acting on `halt`
concluded nothing was halted through 2,939 consecutive halt refusals.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_pmus_auth_book as AB
from src import l5_pmus_live_runner as RUN


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(
        ).replace("+00:00", "Z")


class _Book(AB.AuthBook):
    """A real AuthBook with its socket never started."""

    def __init__(self, rows, now=1000.0, last_frame_t=None):
        AB.AuthBook.__init__(self)
        self.clock = lambda: now
        self.connected = True
        self.last_frame_t = last_frame_t
        self.state = {"slug": {"bid": "0.50", "ask": "0.51", "trades": rows}}


# -------------------------------------------------------------- P0

def test_a_quiet_book_is_not_a_late_one():
    """The exact misreading: a big frame age with sub-second event lag."""
    rows = [{"t": 999.95, "trade_time": _iso(999.9)}]
    b = _Book(rows, now=1000.0, last_frame_t=975.0)
    st = b.status()
    assert st["stream_age_s"] == 25.0            # frame idleness, unchanged
    et = st["event_timing"]
    assert et["source_to_receipt_lag_s"]["median"] == pytest.approx(0.05,
                                                                    abs=1e-3)
    assert et["gap_suspected"] is False
    assert "NOT event lag" in st["stream_age_means"]


def test_event_timing_measures_source_to_receipt_not_frame_age():
    rows = [{"t": 100.0 + i, "trade_time": _iso(100.0 + i - 0.1)}
            for i in range(50)]
    et = _Book(rows, now=150.0).event_timing()
    assert et["prints_measured"] == 50
    assert et["source_to_receipt_lag_s"]["p90"] == pytest.approx(0.1, abs=1e-3)
    assert et["last_event_age_s"] == pytest.approx(1.0, abs=1e-3)


def test_a_reconnect_backlog_is_flagged_and_not_averaged_through():
    """Lag rising monotonically through a burst is the one pattern that
    invalidates timing, and it must be visible rather than diluted."""
    rows = [{"t": 500.0 + i * 0.1, "trade_time": _iso(500.0 - i)}
            for i in range(20)]
    et = _Book(rows, now=510.0).event_timing()
    assert et["gap_suspected"] is True
    assert et["source_to_receipt_lag_s"]["max"] > AB.GAP_LAG_S
    assert "must not be used for queue measurement" in et["labels"]


def test_an_untimeable_row_is_counted_never_guessed():
    rows = [{"t": 10.0, "trade_time": None},
            {"t": 11.0, "trade_time": "not-a-time"},
            {"t": 12.0, "trade_time": _iso(11.95)}]
    et = _Book(rows, now=12.0).event_timing()
    assert et["prints_measured"] == 1
    assert et["unstamped_rows"] == 2


def test_nanosecond_venue_timestamps_parse():
    assert AB.parse_venue_time("2026-10-10T23:02:11.154098072Z") == \
        pytest.approx(dt.datetime(2026, 10, 10, 23, 2, 11, 154098,
                                  tzinfo=dt.timezone.utc).timestamp())
    assert AB.parse_venue_time("") is None
    assert AB.parse_venue_time("rubbish") is None


def test_status_survives_a_broken_event_timing_layer():
    """Telemetry must never take down the status the executor reads."""
    b = _Book([], now=1.0, last_frame_t=0.0)
    b.event_timing = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    assert "error" in b.status()["event_timing"]


# -------------------------------------------------------------- P1

@pytest.mark.parametrize("text,expect", [
    ("HTTP 404 Not Found", "NOT_FOUND_404"),
    ("HTTP 401 Unauthorized", "AUTH_401"),
    ("HTTP 403 Forbidden", "FORBIDDEN_403"),
    ("HTTP 429 Too Many Requests", "RATE_LIMITED_429"),
    ("HTTP 503 Service Unavailable", "VENUE_5XX_503"),
    ("read timed out", "TIMEOUT"),
])
def test_settlement_read_errors_are_classified(text, expect):
    assert RUN.settle_error_class(RuntimeError(text)) == expect


def test_an_unparseable_answer_is_its_own_class():
    assert RUN.settle_error_class(ValueError("no json")).startswith(
        "UNPARSEABLE")


def test_classification_never_changes_what_is_booked():
    """Every class leaves the window PENDING -- a failed read settles
    nothing, which is the property the counter was hiding."""
    from src import l5_two_sided_shadow as TS
    rep = TS.pending_report(
        {"cells": [{"threshold": "0.99", "envelope": "1",
                    "policy": TS.COMPLEMENT_ONLY, "residual_shares": "3",
                    "net_pnl_modelled": "9.99",
                    "residual_pnl_modelled": "9.99"}]},
        reason="READ_ERROR")
    assert rep["state"] == TS.PENDING
    assert rep["state_reason"] == "READ_ERROR"
    assert rep["settlement_known"] is False
    for c in rep["cells"]:
        # a failed READ must not leave a scored-looking number behind
        assert c["net_pnl_modelled"] is None
        assert c["residual_pnl_modelled"] is None
        assert c["state"] == TS.PENDING


# -------------------------------------------------------------- P2

class _Gate:
    def __init__(self, why):
        self._why = why

    def refuse_reason(self):
        if isinstance(self._why, Exception):
            raise self._why
        return self._why


class _Runner:
    """Only the fields submission_gate and effective_cash_gate read."""
    submission_gate = RUN.Runner.submission_gate
    effective_cash_gate = RUN.Runner.effective_cash_gate

    def __init__(self, why=None, shadow=True, gates=None, per_market="5"):
        from decimal import Decimal as D

        class _E:
            pass
        self.executor = _E()
        self.executor.halt_gate = _Gate(why)
        self.executor.validation_gates = gates
        self._shadow = shadow

        class _L:
            max_usd_per_market = D(per_market)
        self.limits = _L()
        self.transport = None

    def halted(self):
        return False


def test_the_heartbeat_halt_field_now_agrees_with_the_refusing_gate(
        monkeypatch):
    """The live contradiction: 2,939 refusals on halt_asserted while the
    heartbeat printed halt false."""
    monkeypatch.setattr(RUN, "shadow_only", lambda: False)
    g = _Runner(why="halt_asserted", shadow=False).submission_gate()
    assert g["blocked"] is True
    assert g["halt_latch"] == "HALT"
    assert g["halt_why"] == "halt_asserted"
    assert "halt_asserted" in g["reason"]


def test_an_unreadable_latch_is_blocked_like_the_gate_itself(monkeypatch):
    monkeypatch.setattr(RUN, "shadow_only", lambda: False)
    g = _Runner(why=RuntimeError("store down"), shadow=False).submission_gate()
    assert g["blocked"] is True
    assert g["halt_latch"] == "UNREADABLE"


def test_the_shadow_lock_is_reported_ahead_of_everything_else(monkeypatch):
    monkeypatch.setattr(RUN, "shadow_only", lambda: True)
    g = _Runner(why=None).submission_gate()
    assert g["blocked"] is True
    assert g["reason"] == "SHADOW_ONLY_LOCK"


def test_a_clear_latch_without_arming_is_still_blocked(monkeypatch):
    monkeypatch.setattr(RUN, "shadow_only", lambda: False)
    monkeypatch.setattr(RUN.Q1, "armed", lambda: True)
    g = _Runner(why=None, shadow=False).submission_gate()
    assert g["blocked"] is True
    assert g["reason"] == "NOT_ARMED"


# -------------------------------------------------------------- P3

def test_the_cash_mandate_is_reported_from_the_object_that_enforces_it():
    from decimal import Decimal as D

    class _VG:
        max_cash_deployed_usd = D("1")
        max_unmatched_contracts = D("5")
        cash_policy = "NO_RECYCLING"
    out = _Runner(gates=_VG()).effective_cash_gate()
    assert out["max_cash_deployed_usd"] == "1"
    assert out["source"] == "ValidationGates object"
    assert out["cash_policy"] == "NO_RECYCLING"


def test_inactive_gates_report_none_and_never_a_default():
    out = _Runner(gates=None).effective_cash_gate()
    assert out["max_cash_deployed_usd"] is None
    assert out["source"] == "GATES NOT ACTIVE"


def test_the_mandate_and_the_per_market_ceiling_are_kept_distinct():
    from decimal import Decimal as D

    class _VG:
        max_cash_deployed_usd = D("1")
    out = _Runner(gates=_VG(), per_market="5").effective_cash_gate()
    assert out["max_cash_deployed_usd"] == "1"
    assert out["max_usd_per_market"] == "5"
    assert "different quantities" in out["distinction"]
