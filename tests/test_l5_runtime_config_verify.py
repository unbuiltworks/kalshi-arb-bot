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


# ============ THE PRE-DEPLOY SNAPSHOT MODE, AND WHY IT IS ONLY A PREDICTION
#
# The in-container run is the real check, but it needs this module to BE in
# the running image -- and the build live in production (962e7a5d) predates
# it, so there is nothing to run there. The snapshot mode fills exactly that
# gap: before changing a variable, ask what the accessors would make of the
# values the platform currently holds.

APPROVED_SNAPSHOT = {
    "L5_LIVE_AUTH_MAX_OPEN": "16",
    "L5_LIVE_AUTH_MAX_COMPLETE": "24",
    "L5_LIVE_AUTH_MAX_REPLACE": "32",
    "L5_LIVE_AUTH_MAX_SUBMISSIONS": "56",
    "L5_LIVE_AUTH_MAX_WINDOWS": "8",
    "L5_TS_CANCEL_MARGIN_S": "300",
    "L5_WINDOW_STRATEGY": "TWO_SIDED",
    "L5_MAX_LIVE_ORDERS": "1",
    "L5_SHADOW_ONLY": "1",
}

#: what production is believed to hold TODAY: the four-window campaign's
#: values, which is the drift the owner must not change blind.
CURRENT_SNAPSHOT = dict(APPROVED_SNAPSHOT,
                        **{"L5_LIVE_AUTH_MAX_OPEN": "8",
                           "L5_LIVE_AUTH_MAX_COMPLETE": "12",
                           "L5_LIVE_AUTH_MAX_REPLACE": "16",
                           "L5_LIVE_AUTH_MAX_SUBMISSIONS": "28",
                           "L5_LIVE_AUTH_MAX_WINDOWS": "4"})


def test_the_approved_snapshot_predicts_a_pass():
    out = V.verify_snapshot(APPROVED_SNAPSHOT)
    assert out["PASS"] is True, out["failed"]
    assert out["basis"] == "SNAPSHOT_PREDICTION"


def test_todays_production_values_predict_exactly_the_rows_that_must_change():
    """The useful answer: not "something is wrong" but WHICH five."""
    out = V.verify_snapshot(CURRENT_SNAPSHOT)
    assert out["PASS"] is False
    assert set(out["failed"]) == {
        "submission_budget_open", "submission_budget_complete",
        "submission_budget_replace", "submission_backstop",
        "max_windows_authorization"}
    # and the margin and the whole derived guard already hold at 300
    for name in ("cancel_margin_s", "defensive_terminal_s",
                 "defensive_guard_opens_s", "defensive_last_actionable_s"):
        assert row(out, name)["ok"] is True, name


def test_a_prediction_never_claims_to_be_a_verification():
    """It cannot see a variable the snapshot omitted, a value the platform
    renders differently at runtime, or code in the deployed image that
    differs from this tree."""
    out = V.verify_snapshot(APPROVED_SNAPSHOT)
    assert out["basis"] == "SNAPSHOT_PREDICTION"
    assert out["in_container_check_required"] is True
    assert "PREDICTION, not a verification" in out["labels"]
    # even a passing prediction keeps the requirement
    assert V.verify_snapshot(CURRENT_SNAPSHOT)["in_container_check_required"]


def test_a_snapshot_carrying_a_secret_is_REFUSED_not_filtered(tmp_path):
    """A careless paste must fail loudly, so the file gets deleted and the
    paste gets redone -- not be quietly half-accepted while a credential
    sits on disk and in this process's memory."""
    for bad in ("PMUS_API_SECRET_KEY", "PMUS_API_KEY_ID", "L5_ARM_TOKEN",
                "SOME_PASSWORD", "L5_LIVE_AUTH_LEDGER"):
        with pytest.raises(V.SnapshotRefused) as e:
            V.verify_snapshot({"L5_LIVE_AUTH_MAX_OPEN": "16", bad: "x"})
        assert bad in str(e.value)
        # and it names the remedy rather than just complaining
        assert "Remove them" in str(e.value)


def test_a_refused_snapshot_loads_nothing_into_the_environment(monkeypatch):
    monkeypatch.delenv("L5_LIVE_AUTH_MAX_OPEN", raising=False)
    with pytest.raises(V.SnapshotRefused):
        V.verify_snapshot({"L5_LIVE_AUTH_MAX_OPEN": "16",
                           "PMUS_API_SECRET_KEY": "xxx"})
    import os
    assert os.environ.get("L5_LIVE_AUTH_MAX_OPEN") is None
    assert os.environ.get("PMUS_API_SECRET_KEY") is None


def test_a_non_allowlisted_variable_is_ignored_by_NAME_and_never_valued():
    """Names are not secrets and the owner needs to see what was skipped.
    A VALUE outside the allowlist has no business being echoed by a tool
    whose output gets pasted into a chat."""
    out = V.verify_snapshot(dict(APPROVED_SNAPSHOT,
                                 RAILWAY_PROJECT_NAME="whatever",
                                 PYTHONUNBUFFERED="1"))
    assert out["snapshot_ignored_names_only"] == ["PYTHONUNBUFFERED",
                                                  "RAILWAY_PROJECT_NAME"]
    blob = str(out)
    assert "whatever" not in blob
    assert set(out["snapshot_applied"]) <= set(V.SNAPSHOT_ALLOWED)


def test_a_variable_the_snapshot_omits_is_predicted_as_ABSENT(monkeypatch):
    """Not as this shell's value. A snapshot that forgets a variable must
    predict what production would do WITHOUT it -- otherwise the prediction
    silently inherits the machine it ran on."""
    monkeypatch.setenv("L5_DEFENSIVE_COMPLETION", "1")
    out = V.verify_snapshot(APPROVED_SNAPSHOT)
    # the flag is absent from the snapshot, so it is predicted OFF even
    # though this process has it ON
    assert row(out, "defensive_execution_enabled")["effective"] is False
    assert "L5_DEFENSIVE_COMPLETION" in out["snapshot_absent_from_allowlist"]


def test_the_environment_is_restored_afterwards_including_to_absent(
        monkeypatch):
    import os
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_OPEN", "99")
    monkeypatch.delenv("L5_TS_CANCEL_MARGIN_S", raising=False)
    V.verify_snapshot(APPROVED_SNAPSHOT)
    assert os.environ["L5_LIVE_AUTH_MAX_OPEN"] == "99"
    assert "L5_TS_CANCEL_MARGIN_S" not in os.environ


def test_a_snapshot_that_is_not_an_object_is_refused():
    for bad in ([], "x", 3, None):
        with pytest.raises(V.SnapshotRefused):
            V.verify_snapshot(bad)


def test_the_cli_exits_nonzero_on_a_failing_prediction(capsys, tmp_path):
    import json as _json
    f = tmp_path / "snap.json"
    f.write_text(_json.dumps(CURRENT_SNAPSHOT))
    assert V.main(["--from-snapshot", str(f)]) == 1
    out = _json.loads(capsys.readouterr().out)
    assert out["basis"] == "SNAPSHOT_PREDICTION"
    assert out["PASS"] is False

    f.write_text(_json.dumps(APPROVED_SNAPSHOT))
    assert V.main(["--from-snapshot", str(f)]) == 0


def test_the_cli_exits_2_and_prints_the_remedy_on_a_secret(capsys, tmp_path):
    import json as _json
    f = tmp_path / "snap.json"
    f.write_text(_json.dumps({"PMUS_API_SECRET_KEY": "xxx"}))
    assert V.main(["--from-snapshot", str(f)]) == 2
    out = _json.loads(capsys.readouterr().out)
    assert out["basis"] == "SNAPSHOT_REFUSED"
    assert "xxx" not in capsys.readouterr().out
