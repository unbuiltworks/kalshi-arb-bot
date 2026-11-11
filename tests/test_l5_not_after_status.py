"""`not_after_status`: the owner's deadline as a STATUS, never as a value.

WHY IT EXISTS. `authorize_arming` evaluates L5_LIVE_AUTH_NOT_AFTER at step 8,
and the shadow lock refuses at step 1 -- so whether the authorization window
had already closed was unobservable from outside the container without
lifting a lock. The owner asked to check it "without exposing credentials",
and the answer is to report the state and the time remaining while never
emitting the configured value.

These tests pin BOTH halves: that the state is correct, and that the value
does not appear in the output.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_live_authorization as A

NOW = dt.datetime(2026, 10, 18, 18, 0, tzinfo=dt.timezone.utc)


def set_env(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(A.NOT_AFTER_ENV, raising=False)
    else:
        monkeypatch.setenv(A.NOT_AFTER_ENV, value)


@pytest.mark.parametrize("value,state", [
    (None, "UNSET"),
    ("", "UNSET"),
    ("   ", "UNSET"),
    ("2026-10-18T19:00:00+00:00", "OPEN"),
    ("2026-10-18T17:00:00+00:00", "EXPIRED"),
    ("2026-10-18T18:00:00+00:00", "EXPIRED"),       # exactly now is closed
    ("2026-10-18T19:00:00", "OPEN"),                # naive reads as UTC
    ("not a timestamp", "UNREADABLE"),
    ("2026-13-45T99:00:00+00:00", "UNREADABLE"),
])
def test_the_state_is_reported_correctly(monkeypatch, value, state):
    set_env(monkeypatch, value)
    assert A.not_after_status(now=NOW)["state"] == state, value


def test_the_configured_value_never_appears_in_the_output(monkeypatch):
    """The whole point: an operator can read the state from a log line
    without the deadline itself being in that log line."""
    secret_looking = "2026-10-19T04:05:06.123456+00:00"
    set_env(monkeypatch, secret_looking)
    out = A.not_after_status(now=NOW)
    body = str({k: v for k, v in out.items() if k != "env"})
    assert secret_looking not in body
    assert "2026-10-19" not in body
    assert out["state"] == "OPEN"


def test_seconds_remaining_is_reported_so_expiry_can_be_anticipated(
        monkeypatch):
    set_env(monkeypatch, "2026-10-18T18:10:00+00:00")
    assert A.not_after_status(now=NOW)["expires_in_s"] == 600.0


def test_an_expired_deadline_reports_a_negative_remainder(monkeypatch):
    set_env(monkeypatch, "2026-10-18T17:50:00+00:00")
    out = A.not_after_status(now=NOW)
    assert out["state"] == "EXPIRED"
    assert out["expires_in_s"] == -600.0


def test_an_unreadable_deadline_reports_no_remainder_rather_than_zero(
        monkeypatch):
    """Zero would read as "expiring right now", which is a different fact."""
    set_env(monkeypatch, "garbage")
    assert A.not_after_status(now=NOW)["expires_in_s"] is None


def test_it_gates_nothing_and_decides_nothing(monkeypatch):
    """Diagnostic only: step 8 still refuses on its own reading."""
    set_env(monkeypatch, "2026-10-18T17:00:00+00:00")
    out = A.not_after_status(now=NOW)
    assert out["state"] == "EXPIRED"
    assert "authorize_arming step 8 still decides" in out["labels"]


def test_the_boot_line_carries_the_status(monkeypatch):
    """It has to be in L5_LIVE_START or it answers nobody."""
    import inspect

    from src import l5_pmus_live_runner as R
    src = inspect.getsource(R.main)
    assert '"auth_not_after": AUTH.not_after_status()' in src
