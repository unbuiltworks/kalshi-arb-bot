"""Fail-closed regression for durable recorder event preflight.

These checks do not claim that an append count alone proves venue fills.
Read-back/restart identity verification remains a separate GO gate.
"""
from src.l5_probe_preflight import preflight


def _row(calibration):
    report = preflight(heartbeat={}, plan=None, markets=[], calibration=calibration)
    return next(r for r in report["checks"] if r["check"] == "recorder_has_observed_something")


def test_recorder_rejects_order_without_durable_events():
    assert _row({"orders": 1, "events_appended": 0, "errors": {}})["ok"] is False


def test_recorder_rejects_missing_event_counter():
    assert _row({"orders": 1, "errors": {}})["ok"] is False


def test_recorder_rejects_invalid_event_counter():
    assert _row({"orders": 1, "events_appended": "unknown"})["ok"] is False


def test_recorder_rejects_events_without_orders():
    assert _row({"orders": 0, "events_appended": 5})["ok"] is False


def test_recorder_accepts_positive_counts_for_this_check_only():
    assert _row({"orders": 1, "events_appended": 1})["ok"] is True


def test_recorder_malformed_orders_counter_fails_closed():
    assert _row({"orders": "invalid", "events_appended": 1})["ok"] is False


def test_recorder_malformed_write_failure_counter_fails_closed():
    report = preflight(heartbeat={}, plan=None, markets=[],
                       calibration={"orders": 1, "events_appended": 1,
                                    "errors": {"ledger_write_failed": "invalid"}})
    row = next(x for x in report["checks"]
               if x["check"] == "recorder_has_no_write_failures")
    assert row["ok"] is False
