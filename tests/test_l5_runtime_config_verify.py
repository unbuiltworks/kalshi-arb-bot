"""The effective runtime configuration, and the drift it must catch.

The owner's instruction was to verify the EFFECTIVE configuration and not
just the intended variable names. These tests exist to prove the verifier
does that -- that it reads what the money path reads, that a wrong value
fails even when the variable is present, and that it cannot be satisfied by
a variable the accessor ignores.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_runtime_config_verify as V


APPROVED_ENV = {
    "L5_LIVE_AUTH_MAX_OPEN": "16",
    "L5_LIVE_AUTH_MAX_COMPLETE": "24",
    "L5_LIVE_AUTH_MAX_REPLACE": "32",
    "L5_LIVE_AUTH_MAX_SUBMISSIONS": "56",
    "L5_LIVE_AUTH_MAX_WINDOWS": "8",
    "L5_TS_CANCEL_MARGIN_S": "300",
    "L5_WINDOW_STRATEGY": "TWO_SIDED",
    "L5_MAX_LIVE_ORDERS": "1",
}


@pytest.fixture()
def approved(monkeypatch):
    for k, v in APPROVED_ENV.items():
        monkeypatch.setenv(k, v)
    # the defensive flag is OFF at deploy, which the verifier requires
    monkeypatch.delenv("L5_DEFENSIVE_COMPLETION", raising=False)
    monkeypatch.delenv("L5_DAILY_REALIZED_LOSS_HALT_USD", raising=False)
    return monkeypatch


def row(out, name):
    return next(r for r in out["rows"] if r["name"] == name)


def test_the_approved_configuration_passes_every_row(approved):
    out = V.verify()
    assert out["PASS"] is True, out["failed"]
    assert out["checked"] == 22
    assert out["failed"] == []


def test_every_row_names_the_accessor_it_read(approved):
    """A row that cannot say where its number came from is not evidence."""
    for r in V.verify()["rows"]:
        assert r["source"], r["name"]
        assert r["effective"] is not None, r["name"]


# ------------------------------------------- the owner's margin condition

def test_a_cancel_margin_that_is_not_300_fails_and_takes_the_guard_with_it(
        approved):
    """The owner approved T+540-585 BECAUSE the margin is 300. The guard is
    derived from it, so a different margin silently moves the approved
    interval -- which is exactly why the margin and all three derived rows
    are checked rather than the margin alone."""
    approved.setenv("L5_TS_CANCEL_MARGIN_S", "180")
    out = V.verify()
    assert out["PASS"] is False
    for name, expect in (("cancel_margin_s", "300.0"),
                         ("defensive_terminal_s", "600.0"),
                         ("defensive_guard_opens_s", "540.0"),
                         ("defensive_last_actionable_s", "585.0")):
        r = row(out, name)
        assert r["ok"] is False, name
        assert str(r["required"]) == expect
    # and the guard really has moved, which is the harm
    assert row(out, "defensive_terminal_s")["effective"] == 720.0


def test_the_derived_guard_rows_track_the_margin_rather_than_being_fixtures(
        approved):
    """If these were hard-coded they would pass at any margin. At 300 the
    terminal is 600; the derivation is what is being checked."""
    approved.setenv("L5_TS_CANCEL_MARGIN_S", "240")
    out = V.verify()
    assert row(out, "defensive_terminal_s")["effective"] == 660.0
    assert row(out, "defensive_guard_opens_s")["effective"] == 600.0
    assert row(out, "defensive_last_actionable_s")["effective"] == 645.0


# ----------------------------------- a present variable with a wrong value

@pytest.mark.parametrize("var,bad,name", [
    ("L5_LIVE_AUTH_MAX_OPEN", "8", "submission_budget_open"),
    ("L5_LIVE_AUTH_MAX_COMPLETE", "12", "submission_budget_complete"),
    ("L5_LIVE_AUTH_MAX_REPLACE", "16", "submission_budget_replace"),
    ("L5_LIVE_AUTH_MAX_SUBMISSIONS", "28", "submission_backstop"),
    ("L5_LIVE_AUTH_MAX_WINDOWS", "4", "max_windows_authorization"),
    ("L5_WINDOW_STRATEGY", "L5_FOLLOWING", "window_strategy"),
])
def test_the_previous_campaign_s_value_fails_each_row(approved, var, bad,
                                                      name):
    """Every `bad` here is the FOUR-window campaign's own approved value, so
    these are the exact drifts that would occur by simply not changing a
    variable."""
    approved.setenv(var, bad)
    out = V.verify()
    assert out["PASS"] is False
    assert row(out, name)["ok"] is False
    assert name in out["failed"]


def test_an_unreadable_budget_fails_rather_than_reading_as_unlimited(
        approved):
    """`max_of_class` returns 0 for an unreadable value -- never unlimited.
    The verifier must surface that as a failure, not as a pass."""
    approved.setenv("L5_LIVE_AUTH_MAX_OPEN", "sixteen")
    out = V.verify()
    assert out["PASS"] is False
    assert row(out, "submission_budget_open")["effective"] == 0


def test_an_absent_budget_fails(approved):
    approved.delenv("L5_LIVE_AUTH_MAX_COMPLETE", raising=False)
    out = V.verify()
    assert out["PASS"] is False
    assert row(out, "submission_budget_complete")["effective"] is None \
        or row(out, "submission_budget_complete")["ok"] is False


# ------------------------------ limits with no variable at all to set wrong

def test_the_code_constant_limits_are_checked_not_just_the_variables(
        approved):
    """The $2 budget, the 1.20 ceiling, the four-hour campaign ceiling and
    the approval ceilings have NO environment variable. An
    environment-only check cannot see them and an environment change
    cannot move them -- so they are checked as constants."""
    out = V.verify()
    for name, value in (("defensive_loss_budget_usd", "2.00"),
                        ("defensive_pair_ceiling", "1.20"),
                        ("campaign_duration_ceiling_s", 14400.0),
                        ("approval_exposure_ceiling_usd", "5"),
                        ("approval_daily_loss_ceiling_usd", "5"),
                        ("max_windows_code_ceiling", 8)):
        r = row(out, name)
        assert r["ok"] is True, name
        assert r["effective"] == value, name
        assert "code constant" in r["source"], name


def test_the_daily_halt_is_read_through_its_accessor_not_its_variable(
        approved):
    """`daily_realized_loss_halt()` FLOORS at $5 and CAPS at $50 and falls
    back to $5 on anything unreadable, so the variable's text is not the
    limit. A variable trying to loosen it below $5 must still read $5."""
    approved.setenv("L5_DAILY_REALIZED_LOSS_HALT_USD", "1")
    out = V.verify()
    assert row(out, "daily_realized_loss_halt_usd")["effective"] == "5"
    assert row(out, "daily_realized_loss_halt_usd")["ok"] is True
    # and a value ABOVE the owner's approved figure fails the row
    approved.setenv("L5_DAILY_REALIZED_LOSS_HALT_USD", "25")
    out = V.verify()
    assert row(out, "daily_realized_loss_halt_usd")["effective"] == "25"
    assert row(out, "daily_realized_loss_halt_usd")["ok"] is False


# -------------------------------------- the deploy state, and activation

def test_the_build_must_deploy_with_defensive_execution_disabled(approved):
    out = V.verify()
    assert row(out, "defensive_execution_enabled")["effective"] is False
    assert row(out, "defensive_execution_enabled")["ok"] is True
    approved.setenv("L5_DEFENSIVE_COMPLETION", "1")
    out = V.verify()
    assert out["PASS"] is False
    assert "defensive_execution_enabled" in out["failed"]


def test_the_activation_switches_are_reported_and_never_graded(approved):
    """Grading these would make the verifier fail at exactly the moment
    activation succeeds, which would teach the owner to ignore it."""
    out = V.verify()
    names = {r["name"] for r in out["observed_not_graded"]}
    assert "shadow_only" in names
    graded = {r["name"] for r in out["rows"]}
    assert "shadow_only" not in graded
    # flipping it changes the report and not the verdict
    approved.setenv("L5_SHADOW_ONLY", "0")
    out = V.verify()
    assert out["PASS"] is True
    assert next(r for r in out["observed_not_graded"]
                if r["name"] == "shadow_only")["effective"] is False


def test_nothing_in_the_verifier_can_submit_arm_or_reach_a_venue():
    """It is meant to be run inside the DEPLOYED container, beside real
    credentials, so it must not be able to act. Checked on precise call
    shapes rather than on substrings: `cancel_cutoff` and `cancel_margin_s`
    are legitimate reads of a CLOCK and an earlier version of this test
    rejected them, which is a test that would have forced the verifier to
    stop reading the very value the owner's rule is about."""
    import inspect
    src = inspect.getsource(V)
    for forbidden in (".submit(", ".cancel(", "transport",
                      "claim_submission", "claim_accumulation",
                      "arm_token", "PMUS_API", "requests", "httpx",
                      "urllib", "socket"):
        assert forbidden not in src, forbidden
    # and it imports nothing that can place an order
    for order_capable in ("pmus_q1_executor", "pmus_micro_executor",
                          "l5_pmus_transport"):
        assert order_capable not in src, order_capable


def test_the_approved_set_is_code_so_drift_shows_as_a_failing_row():
    assert V.APPROVED["submission_backstop"] == 56
    assert V.APPROVED["submission_budget_open"] == 16
    assert V.APPROVED["submission_budget_complete"] == 24
    assert V.APPROVED["submission_budget_replace"] == 32
    assert V.APPROVED["defensive_loss_budget_usd"] == D("2")
    assert V.APPROVED["aggregate_exposure_usd"] == D("5")
    assert V.APPROVED["daily_realized_loss_halt_usd"] == D("5")
    assert V.APPROVED["maker_pair_ceiling"] == D("0.96")
    assert V.APPROVED["defensive_pair_ceiling"] == D("1.20")
    assert V.APPROVED["cancel_margin_s"] == 300.0
    assert V.APPROVED["defensive_guard_opens_s"] == 540.0
    assert V.APPROVED["defensive_last_actionable_s"] == 585.0
    assert V.APPROVED["max_windows"] == 8
    assert V.APPROVED["max_live_orders"] == 1
    assert V.APPROVED["defensive_execution_enabled"] is False
