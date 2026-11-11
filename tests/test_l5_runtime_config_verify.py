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
    "L5_MAX_LIVE_ORDERS": "4",
    # THE APPROVED AGGREGATE CEILING, and it has to be HERE rather than
    # left to the code default. `R.PROBE_LIMITS` defaults to the $20 of the
    # earlier eight-window mandate, so omitting this variable tested the
    # default instead of the configuration the owner approved -- and
    # production, which sets it to 5, was graded against a number nobody
    # had approved.
    "L5_MAX_TOTAL_EXPOSURE_USD": "5",
    # THE CAMPAIGN'S OWN ADDITIONAL-LOSS BOUND. Part of the approved
    # configuration, so the fixture carries it: unset leaves the gate inert,
    # which is not the configuration that was approved.
    # RAISED WITH THE OWNER-AUTHORIZED LIMIT 2026-11-02: the snapshot has to
    # carry the figure the verifier now requires, or the approved
    # configuration fails its own prediction.
    "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10.00",
    # THE WORST-CASE OPENING NOTIONAL. Same reasoning: the owner approved
    # this campaign AS the notional-cap experiment, so unset -- which leaves
    # the cap inert -- is not the approved configuration.
    "L5_TS_OPENING_NOTIONAL_BUDGET_USD": "0.80",
}


@pytest.fixture(autouse=True)
def _pin_the_frozen_ceiling(monkeypatch):
    """Pin `PROBE_LIMITS` to the approved $5 for every test in this module.

    `R.PROBE_LIMITS` is built ONCE at import, so its value depends on what
    the environment held when the runner module was FIRST imported -- which
    in a full-suite run is decided by whichever test module imported it
    first. That made these tests pass alone and fail in the suite, and it
    also means `verify_snapshot` cannot express this row through a snapshot.
    Pinning it here is what a correctly restarted service looks like; the
    restart-pending state gets its own test below.
    """
    from src import l5_pmus_live_runner as _R
    monkeypatch.setitem(_R.PROBE_LIMITS, "max_total_exposure_usd", D("5"))


def _approval_document():
    """The document that APPROVES the figures `APPROVED_ENV` deploys.

    The verifier now grades the per-campaign figures against the owner's
    document rather than against a dict frozen in code -- which is what let
    eight of its rows drift out of agreement with the configuration the
    owner had actually approved. So the fixture has to carry the document
    too: without one the document-derived rows read NO_APPROVAL_IN_FORCE and
    fail, which is correct behaviour and not a bug in the verifier.
    """
    import datetime as _dt
    import json as _json
    now = _dt.datetime.now(_dt.timezone.utc)
    return _json.dumps({
        "approved": True, "nonce": "a" * 64,
        "series": "cpc-btc-updown-15m-",
        "issued_utc": now.isoformat(),
        "expires_utc": (now + _dt.timedelta(hours=2)).isoformat(),
        "max_windows": int(APPROVED_ENV["L5_LIVE_AUTH_MAX_WINDOWS"]),
        "max_total_exposure_usd": APPROVED_ENV["L5_MAX_TOTAL_EXPOSURE_USD"],
        "max_daily_loss_usd": "5",
        "max_open_submissions": int(APPROVED_ENV["L5_LIVE_AUTH_MAX_OPEN"]),
        "max_complete_submissions":
            int(APPROVED_ENV["L5_LIVE_AUTH_MAX_COMPLETE"]),
        "max_replace_submissions":
            int(APPROVED_ENV["L5_LIVE_AUTH_MAX_REPLACE"]),
        "max_total_submissions":
            int(APPROVED_ENV["L5_LIVE_AUTH_MAX_SUBMISSIONS"]),
    })


@pytest.fixture()
def approved(monkeypatch):
    for k, v in APPROVED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", _approval_document())
    # the defensive flag is OFF at deploy, which the verifier requires
    monkeypatch.delenv("L5_DEFENSIVE_COMPLETION", raising=False)
    monkeypatch.delenv("L5_DAILY_REALIZED_LOSS_HALT_USD", raising=False)
    return monkeypatch


def row(out, name):
    return next(r for r in out["rows"] if r["name"] == name)


def obs(out, name):
    """A REPORTED, ungraded switch. Separate helper from `row` on purpose:
    `row` would raise StopIteration for one of these, and the distinction
    between graded and reported is the thing these tests are about."""
    return next(r for r in out["observed_not_graded"] if r["name"] == name)


def test_the_approved_configuration_passes_every_row(approved):
    out = V.verify()
    assert out["PASS"] is True, out["failed"]
    # 21 -> 22 when defensive_execution_enabled moved to
    # observed_not_graded; 22 -> 23 with the phase-1 invariant that the
    # mandate signing key is never reachable from the runtime.
    assert out["checked"] == 26
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
                        ("approval_exposure_ceiling_usd", "20"),
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

def test_defensive_execution_is_reported_and_rolls_back_without_failing(
        approved):
    """BOTH postures must be deployable, and the value must stay visible.

    THE DEFECT THIS REPLACES. This test used to assert the verifier FAILS
    when L5_DEFENSIVE_COMPLETION=1, because the row was graded against
    APPROVED["defensive_execution_enabled"] = False. That made the
    pre-deploy gate refuse the owner-authorized live window at exactly the
    moment it was switched on -- and inverting the APPROVED value instead
    would have made it refuse a deliberate safety ROLLBACK to defensive-off,
    which is worse: it obstructs standing the window down in a hurry.

    An activation switch cannot be graded in either direction without
    breaking at one of the two moments that matter, so it is now REPORTED.
    Visibility is preserved -- the effective value still appears in the
    verdict -- while neither posture can fail the verifier.
    """
    out = V.verify()
    assert obs(out, "defensive_execution_enabled")["effective"] is False
    assert out["PASS"] is True, out["failed"]

    # ON while the approved execution_mode is MAKER_ONLY: still REPORTED, and
    # now it FAILS -- on the consistency row, not on a constant.
    #
    # THIS ASSERTION USED TO READ "the authorized live posture ... still PASS",
    # and that was the assumption which let 2026-10-23 02:39:04 cross at a pair
    # cost of 1.04 under a mandate the module issued itself, while
    # `AUTH.EXECUTION_MODE` -- a field of the digest the OWNER approved -- said
    # MAKER_ONLY. Arming a crosser is not the authorized posture under a
    # maker-only mandate, and the verifier now says so.
    approved.setenv("L5_DEFENSIVE_COMPLETION", "1")
    out = V.verify()
    assert obs(out, "defensive_execution_enabled")["effective"] is True
    assert out["PASS"] is False
    assert out["failed"] == ["maker_only_forbids_defensive_crossing"]
    # and it is still not graded against a constant
    assert "defensive_execution_enabled" not in out["failed"]

    # OFF again: the rollback. Must also PASS, which is the whole point.
    approved.delenv("L5_DEFENSIVE_COMPLETION", raising=False)
    out = V.verify()
    assert obs(out, "defensive_execution_enabled")["effective"] is False
    assert out["PASS"] is True, out["failed"]

    # and it is NOT silently gradeable again: absent from the graded rows.
    assert "defensive_execution_enabled" not in {
        r["name"] for r in out["rows"]}


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
    # RAISED 5 -> 20 by owner decision; the daily halt stays $5.
    assert V.APPROVED["aggregate_exposure_usd"] == D("5")
    assert V.APPROVED["daily_realized_loss_halt_usd"] == D("5")
    assert V.APPROVED["maker_pair_ceiling"] == D("0.96")
    assert V.APPROVED["defensive_pair_ceiling"] == D("1.20")
    assert V.APPROVED["cancel_margin_s"] == 300.0
    assert V.APPROVED["defensive_guard_opens_s"] == 540.0
    assert V.APPROVED["defensive_last_actionable_s"] == 585.0
    assert V.APPROVED["max_windows"] == 8
    # raised 2 -> 4 by owner decision 2026-11-10 (the four-order
    # configuration, deployed disarmed; no live trading authorized by it)
    assert V.APPROVED["max_live_orders"] == 4
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
    # RAISED 1 -> 2 WITH THE OWNER-AUTHORIZED SLOT 2026-11-04, for the same
    # reason the additional-loss line below was raised: the snapshot has to
    # carry the figure the verifier now requires, or the approved
    # configuration fails its own prediction. Production already holds 2, so
    # this is NOT one of the rows the owner must still change and
    # CURRENT_SNAPSHOT inherits it unchanged.
    "L5_MAX_LIVE_ORDERS": "4",
    "L5_SHADOW_ONLY": "1",
    "L5_MAX_TOTAL_EXPOSURE_USD": "5",
    # RAISED WITH THE OWNER-AUTHORIZED LIMIT 2026-11-02: the snapshot has to
    # carry the figure the verifier now requires, or the approved
    # configuration fails its own prediction.
    "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10.00",
    "L5_TS_OPENING_NOTIONAL_BUDGET_USD": "0.80",
}

#: what production is believed to hold TODAY: the four-window campaign's
#: values, which is the drift the owner must not change blind.
#: production carries no campaign additional-loss bound at all, which leaves
#: that gate INERT -- so it is one of the rows the owner must change, and the
#: prediction has to say so instead of inheriting the approved value.
_CUR = dict(APPROVED_SNAPSHOT)
_CUR.pop("L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD", None)
#: production has never carried the opening-notional cap either, so it is
#: the OTHER row the owner must set, and the prediction must name it rather
#: than inherit the approved value.
_CUR.pop("L5_TS_OPENING_NOTIONAL_BUDGET_USD", None)
CURRENT_SNAPSHOT = dict(_CUR,
                        **{"L5_LIVE_AUTH_MAX_OPEN": "8",
                           "L5_LIVE_AUTH_MAX_COMPLETE": "12",
                           "L5_LIVE_AUTH_MAX_REPLACE": "16",
                           "L5_LIVE_AUTH_MAX_SUBMISSIONS": "28",
                           "L5_LIVE_AUTH_MAX_WINDOWS": "4"})


def test_the_approved_snapshot_predicts_a_pass(monkeypatch):
    # THE DOCUMENT IS NOT A SNAPSHOT VARIABLE, deliberately: it carries the
    # nonce, and `SNAPSHOT_ALLOWED` exists so a file that gets pasted into a
    # chat cannot contain a credential. So a prediction reads the document
    # from the ambient environment, and a test that predicts has to set one.
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", _approval_document())
    out = V.verify_snapshot(APPROVED_SNAPSHOT)
    assert out["PASS"] is True, out["failed"]
    assert out["basis"] == "SNAPSHOT_PREDICTION"


def test_todays_production_values_predict_exactly_the_rows_that_must_change(monkeypatch):
    """The useful answer: not "something is wrong" but WHICH rows."""
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", _approval_document())
    out = V.verify_snapshot(CURRENT_SNAPSHOT)
    assert out["PASS"] is False
    assert set(out["failed"]) == {
        "submission_budget_open", "submission_budget_complete",
        "submission_budget_replace", "submission_backstop",
        "max_windows_authorization",
        # unset in production, so the campaign bound is inert
        "campaign_additional_loss_usd",
        # likewise the opening-notional cap: unset means no cap at all
        "opening_notional_budget_usd"}
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
    assert obs(out, "defensive_execution_enabled")["effective"] is False
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


def test_the_cli_exits_nonzero_on_a_failing_prediction(capsys, tmp_path,
                                                       monkeypatch):
    import json as _json
    # as above: the document is ambient, never part of the snapshot file
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", _approval_document())
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


def test_maker_only_forbids_an_armed_crosser_but_permits_rollback(approved,
                                                                  monkeypatch):
    """The row that 0230z needed. It grades CONSISTENCY between two things the
    deployment already states, never a fixed expectation of either, so both
    postures stay deployable and a safety rollback can never be obstructed."""
    from src import l5_live_authorization as AUTH

    def row(out):
        return [r for r in out["rows"]
                if r["name"] == "maker_only_forbids_defensive_crossing"][0]

    # MAKER_ONLY + defensive OFF -> passes. This is the rollback, and it must
    # pass unconditionally.
    monkeypatch.setattr(AUTH, "EXECUTION_MODE", "MAKER_ONLY", raising=False)
    approved.delenv("L5_DEFENSIVE_COMPLETION", raising=False)
    assert row(V.verify())["ok"] is True

    # MAKER_ONLY + defensive ON -> the 0230z state, and it fails.
    approved.setenv("L5_DEFENSIVE_COMPLETION", "1")
    assert row(V.verify())["ok"] is False
    assert V.verify()["PASS"] is False

    # a declared crossing mode + defensive ON -> passes. The check does not
    # forbid crossing; it forbids crossing that the mandate did not approve.
    monkeypatch.setattr(AUTH, "EXECUTION_MODE", "TAKER_PERMITTED",
                        raising=False)
    assert row(V.verify())["ok"] is True

    # FAIL CLOSED. An absent, empty, unreadable or unrecognised mode is NOT
    # permission to cross: it is treated as MAKER_ONLY. A mode we cannot read
    # is not consent, and only an EXPLICIT crossing mode is.
    for bad in ("", "   ", None, "maker_only", "MAKERONLY", "anything"):
        monkeypatch.setattr(AUTH, "EXECUTION_MODE", bad, raising=False)
        approved.setenv("L5_DEFENSIVE_COMPLETION", "1")
        assert row(V.verify())["ok"] is False, bad
    # and with defensive off, every one of them passes
    for bad in ("", None, "anything"):
        monkeypatch.setattr(AUTH, "EXECUTION_MODE", bad, raising=False)
        approved.delenv("L5_DEFENSIVE_COMPLETION", raising=False)
        assert row(V.verify())["ok"] is True, bad


def test_the_approved_execution_mode_is_inside_the_owner_digest():
    """Why the row is justified: execution_mode is a SCOPE_FIELD, so the
    owner's approval digest covers it. Crossing under MAKER_ONLY contradicts
    the approved envelope itself, not merely a local preference."""
    from src import l5_live_authorization as AUTH
    assert "execution_mode" in AUTH.SCOPE_FIELDS
    assert AUTH.EXECUTION_MODE == "MAKER_ONLY"


# ------------------------------------------------- phase 1: signed mandates

def test_the_signing_key_invariant_is_graded(approved):
    """Requirement 1 is an invariant, not a phase, so it IS graded."""
    r = row(V.verify(), "mandate_signing_key_absent_from_runtime")
    assert r["ok"] is True


def test_a_signing_seed_in_the_environment_fails_the_row(approved,
                                                         monkeypatch):
    monkeypatch.setenv("L5_MANDATE_SIGNING_KEY", "ab" * 32)
    assert row(V.verify(), "mandate_signing_key_absent_from_runtime")["ok"] \
        is False


def test_a_pem_private_key_anywhere_fails_the_row(approved, monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VAR",
                       "-----BEGIN PRIVATE KEY-----\nabc\n")
    assert row(V.verify(), "mandate_signing_key_absent_from_runtime")["ok"] \
        is False


def test_the_public_key_in_code_does_not_trip_the_scan(approved):
    """The 64-hex PUBLIC key lives in code, not the environment."""
    assert row(V.verify(),
               "mandate_signing_key_absent_from_runtime")["ok"] is True


def test_the_mandate_verdict_is_reported_and_not_graded(approved):
    """Phase 1. Grading it would fail every deploy until a mandate exists."""
    out = V.verify()
    assert obs(out, "mandate_verdict")["effective"]
    assert obs(out, "mandate_enforcing")["effective"] in ("False", False)
    assert "mandate_verdict" not in out["failed"]


def test_the_shipped_state_has_no_trusted_keys(approved):
    assert obs(V.verify(), "mandate_trusted_key_ids")["effective"] \
        == "(none)"


def test_a_variable_changed_without_a_restart_FAILS_rather_than_passing(
        approved):
    """The aggregate ceiling is the one limit where intent and enforcement
    can diverge.

    `R.PROBE_LIMITS` is built at import and `SafetyLimits` is constructed
    from it, so the ceiling in force is the one the process booted with.
    An owner who edits the Railway variable and does not restart has a
    service still enforcing the OLD number. That must be a failing row, not
    a passing one, because the alternative is discovering it through an
    order that was admitted under a ceiling nobody approved.
    """
    from src import l5_pmus_live_runner as _R
    # the variable now says 5 (the fixture) but the booted ceiling is 20
    approved.setitem(_R.PROBE_LIMITS, "max_total_exposure_usd", D("20"))
    out = V.verify()
    assert out["PASS"] is False
    assert row(out, "aggregate_exposure_usd")["ok"] is True
    assert row(out, "aggregate_exposure_usd")["effective"] == "5"
    assert row(out, "aggregate_exposure_in_force_usd")["ok"] is False
    assert row(out, "aggregate_exposure_in_force_usd")["effective"] \
        == "20"
    assert "aggregate_exposure_in_force_usd" in out["failed"]
    assert "frozen at boot" in \
        row(out, "aggregate_exposure_in_force_usd")["source"]


def test_a_tighter_ceiling_than_the_code_constant_still_passes(approved):
    """$5 deployed against CA.MAX_TOTAL_EXPOSURE_USD = $20 is correct, not a
    drift. `_looser` flags a field only when the deployed value is GREATER
    than the approved one, so a tighter deployed ceiling arms normally --
    which is why the approved row is 5 and the code-constant row is 20 at
    the same time."""
    out = V.verify()
    assert out["PASS"] is True, out["failed"]
    assert row(out, "aggregate_exposure_usd")["effective"] == "5"
    assert row(out, "approval_exposure_ceiling_usd")["effective"] == "20"


# ---------------------------------------------------------------------------
# A TIGHTER CAMPAIGN LOSS BUDGET IS INSIDE THE APPROVAL (2026-11-11)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0.30", "0.01", "5.00", "10.00"])
def test_a_campaign_loss_budget_at_or_below_the_ceiling_passes(approved,
                                                               value):
    approved.setenv("L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD", value)
    out = V.verify()
    r = row(out, "campaign_additional_loss_usd")
    assert r["ok"] is True, r
    assert D(r["effective"]) == D(value)
    assert "campaign_additional_loss_usd" not in out["failed"]


def test_an_unset_campaign_loss_budget_still_fails(approved):
    approved.delenv("L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD", raising=False)
    out = V.verify()
    assert row(out, "campaign_additional_loss_usd")["ok"] is False
    assert out["PASS"] is False


@pytest.mark.parametrize("value", ["0", "zero", "-1"])
def test_a_zero_or_unreadable_campaign_loss_budget_still_fails(approved,
                                                               value):
    """Unreadable reads as $0, the bound that refuses every order. That is
    a broken configuration, not a tight one."""
    approved.setenv("L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD", value)
    out = V.verify()
    r = row(out, "campaign_additional_loss_usd")
    assert r["ok"] is False, r
    assert D(r["effective"]) == 0


def test_a_budget_above_the_ceiling_is_clamped_to_it_not_honoured(approved):
    approved.setenv("L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD", "25")
    r = row(V.verify(), "campaign_additional_loss_usd")
    assert D(r["effective"]) == D("10.00") and r["ok"] is True


# ---------------------------------------------------------------------------
# THE ORDER LIMIT: THE APPROVED FOUR, OR THE PREPARED EIGHT (2026-11-11)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,ok", [("4", True), ("8", True), ("2", False),
                                      ("6", False), ("9", False),
                                      ("eight", False)])
def test_the_order_limit_passes_at_four_or_eight_and_nothing_else(
        approved, value, ok):
    approved.setenv("L5_MAX_LIVE_ORDERS", value)
    r = row(V.verify(), "max_live_orders")
    assert r["ok"] is ok, r


def test_the_approved_order_limit_is_still_recorded_as_four():
    assert V.APPROVED["max_live_orders"] == 4
    assert V.MAX_LIVE_ORDERS_PREPARED == 8
