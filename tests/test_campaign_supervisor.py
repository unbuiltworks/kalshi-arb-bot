"""A clean campaign end must not leave R5F down, must not restart through
an owner decision, and must not destroy the evidence of why it ended
(OWNER GO #167 5584132560 items 1 and 3).

06:30:06Z: the campaign ended normally, `main()` returned 0, Railway does
not restart a zero exit, and the lane was silently down for 4h10m. The
report was printed with indent=1 over the whole campaign document, hit
Railway's 500 logs/sec replica limit and dropped 117,262 messages --
including its own terminal `status`."""
from __future__ import annotations

import itertools

from src import pmus_l5_continuous_session as cs


def _drive(monkeypatch, reports, **kw):
    """Run main() over a scripted sequence of campaign outcomes."""
    seq = iter(reports)
    logged = []
    monkeypatch.setattr(cs, "_run_once", lambda: next(seq))
    monkeypatch.setattr(cs, "_persist_full_report", lambda r: "ledger")
    monkeypatch.setattr(cs, "_install_term_handler", lambda: None)
    monkeypatch.setattr(cs, "_log", lambda ev, **f: logged.append((ev, f)))
    rc = cs.main(sleep=lambda *_a: None, **kw)
    return rc, logged


def _settled(n, status="CAMPAIGN_SETTLED:REALIZED"):
    return {"status": status, "campaign_id": "c1",
            "settlements": [{"slug": f"m{i}"} for i in range(n)],
            "sessions": [], "realized_pnl": "-2.9623"}


def test_a_clean_settlement_end_restarts_instead_of_leaving_the_lane_down(
        monkeypatch):
    """The 06:30Z failure, inverted: the campaign ends cleanly after a
    settlement and the lane keeps operating."""
    rc, logged = _drive(monkeypatch, [
        _settled(1), _settled(2), {"status": "CAMPAIGN_STOP:DAILY_LOSS_STOP",
                                   "campaign_id": "c1", "sessions": []},
    ])
    ends = [f for ev, f in logged if ev == "campaign_end"]
    assert len(ends) == 3
    assert "same process" in ends[0]["next"] and "same process" in ends[1]["next"]
    assert ends[2]["next"].startswith("none:")
    assert rc == 0


def test_an_owner_decision_point_is_never_restarted_through(monkeypatch):
    """Restarting through an approval gate would consume an approval the
    owner has not given."""
    for status in ("CAMPAIGN_RUNG_COMPLETE",
                   "CAMPAIGN_WAITING:CONTINUATION_PREFLIGHT",
                   "CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS",
                   "CAMPAIGN_STOP:HALT",
                   "CAMPAIGN_STOP:STATE_NOT_DURABLE"):
        assert cs.is_recoverable(status) is False, status
    rc, logged = _drive(monkeypatch, [
        {"status": "CAMPAIGN_RUNG_COMPLETE", "campaign_id": "c1",
         "sessions": []}])
    ends = [f for ev, f in logged if ev == "campaign_end"]
    assert len(ends) == 1
    assert "owner decision" in ends[0]["next"]


def test_the_same_status_with_no_new_settlement_fails_closed(monkeypatch):
    """Never spin: a campaign that ends the same way twice with nothing
    new is stuck, and stuck is reported, not looped."""
    rc, logged = _drive(monkeypatch, [_settled(1), _settled(1), _settled(1)])
    ends = [f for ev, f in logged if ev == "campaign_end"]
    alarms = [f for ev, f in logged if ev == "PMUS_SPRING_ALARM"]
    assert len(ends) == 2 and "would spin" in ends[1]["next"]
    assert alarms and alarms[0]["alarm"] == "campaign_stuck"


def test_the_restart_budget_is_bounded(monkeypatch):
    monkeypatch.setenv(cs.SUPERVISOR_MAX_RESTARTS_ENV, "2")
    rc, logged = _drive(monkeypatch,
                        [_settled(i) for i in range(1, 8)])
    alarms = [f for ev, f in logged if ev == "PMUS_SPRING_ALARM"]
    assert alarms and alarms[0]["alarm"] == "restart_budget_exhausted"


def test_the_terminal_cause_survives_in_one_compact_row():
    """One line, not 100k. Everything the 06:30Z exit lost is in it."""
    report = {
        "status": "CAMPAIGN_SETTLED:REALIZED", "campaign_id": "c1",
        "realized_pnl": "-2.9623", "daily_stop_state": "ok",
        "filled_markets": ["m1", "m2"],
        "settlements": [{"slug": "m1", "status": "REALIZED"},
                        {"slug": "m2", "status": "PENDING_SETTLEMENT"}],
        "exit_reason": {"reason": "HORIZON"},
        "sessions": [{"fill_reconciliation": {
            "may_assert_flat": False, "recovered_qty": "4.34",
            "contradictions": []}}],
    }
    row = cs.compact_end_row(report, restart="same process, in 5.0s")
    assert row["kind"] == "campaign_end"
    assert row["status"] == "CAMPAIGN_SETTLED:REALIZED"
    assert row["reason"] == "HORIZON"
    assert row["settled"] == 2 and row["pending"] == 1
    assert row["filled_markets"] == 2
    assert row["realized_pnl"] == "-2.9623"
    assert row["fill_reconciliation"]["may_assert_flat_all"] is False
    assert row["fill_reconciliation"]["recovered_qty"] == ["4.34"]
    assert "persisted to the durable ledger" in row["full_report"]
    # the whole row must stay small enough that one log line carries it
    import json
    assert len(json.dumps(row)) < 1200


def test_the_full_report_is_persisted_not_printed(monkeypatch):
    """Printing it is what destroyed the terminal evidence."""
    calls = []
    monkeypatch.setattr(cs, "_persist_full_report",
                        lambda r: calls.append(r) or "ledger")
    printed = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: printed.append(a))
    _drive(monkeypatch, [{"status": "CAMPAIGN_STOP:HALT", "sessions": []}])
    assert not printed


def test_the_heartbeat_is_positive_liveness_evidence():
    """Absence of log lines is not liveness -- that is exactly how the
    06:30-10:40 outage read as a quiet market. A heartbeat is a claim the
    process can only make while it is running."""
    from src import pmus_l5_continuous_session as m
    assert m.HEARTBEAT_S > 0
    for fn in (m._rss_now_mb, m._rss_peak_mb):
        v = fn()
        assert v is None or v > 0


def test_current_memory_is_not_reported_as_the_peak():
    """LIVE: the first heartbeat reported rss_mb 1056.6 while the platform
    measured 278 MB, because ru_maxrss is a high-water mark. Current and
    peak are now separate fields, and current can never exceed peak."""
    from src import pmus_l5_continuous_session as m
    now, peak = m._rss_now_mb(), m._rss_peak_mb()
    if now is not None and peak is not None:
        assert now <= peak + 1.0, (now, peak)
    import inspect
    src = inspect.getsource(m.run_campaign)
    assert "rss_mb=_rss_now_mb()" in src and "rss_peak_mb=_rss_peak_mb()" in src


def test_the_reconciliation_proof_reaches_the_log():
    """OWNER 5584807147 item 3 asks for the venue-vs-ledger proof. It was
    being computed and persisted but never emitted, so it could not be
    read: both session_end rows now carry it."""
    import inspect
    from src import pmus_l5_continuous_session as m
    src = inspect.getsource(m.run_campaign)
    assert src.count("fill_reconciliation=rep.get(\"fill_reconciliation\")") == 2
    assert src.count("flat_verified=rep.get(\"flat_verified\")") == 2


def test_the_heartbeat_never_consumes_the_campaign_clock():
    """Observing the loop must not change it. A heartbeat that called
    clock() would shift every deadline it is meant to report on."""
    import inspect
    from src import pmus_l5_continuous_session as m
    src = inspect.getsource(m.run_campaign)
    start = src.index('_log("heartbeat"')
    body = src[src.index('if now - _hb["t"]'):src.index("fidelity_eval(now)", start)]
    assert "clock()" not in body, body


def test_a_quiet_board_keeps_the_lane_up(monkeypatch):
    """CAMPAIGN_NO_CANDIDATE after a long run is a dead slate, not a spin.
    Stopping there would put the lane back down overnight, which is the
    outage this change exists to prevent."""
    assert cs.is_recoverable("CAMPAIGN_NO_CANDIDATE") is True
    assert cs.is_recoverable("CAMPAIGN_STOP:VENUE_UNAVAILABLE") is True
    quiet = {"status": "CAMPAIGN_NO_CANDIDATE", "sessions": [],
             "settlements": []}
    # each clock() call advances 200s, so every campaign "ran" past
    # MIN_HEALTHY_RUN_S and the lane keeps coming back until the restart
    # budget -- not the repeat check -- stops it
    monkeypatch.setenv(cs.SUPERVISOR_MAX_RESTARTS_ENV, "3")
    ticks = itertools.count(0, 200)
    rc, logged = _drive(monkeypatch, [quiet] * 8,
                        clock=lambda: float(next(ticks)))
    ends = [f for ev, f in logged if ev == "campaign_end"]
    alarms = [f for ev, f in logged if ev == "PMUS_SPRING_ALARM"]
    assert sum(1 for e in ends if "same process" in e["next"]) == 3
    assert alarms and alarms[0]["alarm"] == "restart_budget_exhausted"
    assert not any(a["alarm"] == "campaign_stuck" for a in alarms)


def test_an_instant_repeat_is_still_treated_as_spinning(monkeypatch):
    quiet = {"status": "CAMPAIGN_NO_CANDIDATE", "sessions": [],
             "settlements": []}
    # each call advances 1s, so the campaign ends almost immediately
    ticks = itertools.count(0, 1)
    rc, logged = _drive(monkeypatch, [quiet, quiet, quiet],
                        clock=lambda: float(next(ticks)))
    alarms = [f for ev, f in logged if ev == "PMUS_SPRING_ALARM"]
    assert alarms and alarms[0]["alarm"] == "campaign_stuck"
