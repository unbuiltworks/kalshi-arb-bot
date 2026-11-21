"""The runtime-config verifier grades against the CURRENT approval.

WHAT WAS WRONG. `APPROVED` was campaign 1's profile, frozen in code. By
2026-10-28 eight of its rows disagreed with what the owner had since
approved -- backstop 40 against an expected 56, exposure $20 against $5,
four windows against eight -- so `L5_EFFECTIVE_RUNTIME_CONFIG` read
`PASS: false` on a correctly configured service, and it was about to be
used as the pre-arm check for a live campaign. A gate that always fails is
a gate nobody reads.

THE FIX IS NOT "MAKE IT PASS". These tests exist to prove that: the
per-campaign figures are derived from the document that approved them,
intersected with code ceilings they can never widen, and every absent or
unreadable input FAILS. The verifier must stay capable of saying no.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_runtime_config_verify as V
from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_two_sided_probe as TS

DEPLOYED = {
    "L5_LIVE_AUTH_MAX_OPEN": "40", "L5_LIVE_AUTH_MAX_COMPLETE": "40",
    "L5_LIVE_AUTH_MAX_REPLACE": "64", "L5_LIVE_AUTH_MAX_SUBMISSIONS": "40",
    "L5_MAX_TOTAL_EXPOSURE_USD": "20", "L5_MAX_CASH_DEPLOYED_USD": "12",
    "L5_MAX_L5_DAILY_LOSS_USD": "5", "L5_MAX_CUMULATIVE_LOSS_USD": "20",
    # RAISED WITH THE OWNER-AUTHORIZED LIMIT 2026-11-02. This is the
    # SECOND test snapshot carrying the figure, and it failed exactly as
    # this file says a verifier should: "the verifier must stay capable
    # of saying no." It said no to a deployed snapshot that no longer
    # matched the approval, which is the behaviour, not a defect.
    "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10",
    "L5_TS_OPENING_NOTIONAL_BUDGET_USD": "3",
    "L5_LIVE_AUTH_MAX_WINDOWS": "4", "L5_WINDOW_STRATEGY": "TWO_SIDED",
    "L5_MAX_LIVE_ORDERS": "4", "L5_TS_CANCEL_MARGIN_S": "300",
}


def doc(windows=4, exposure="20", daily="5", approved=True, hours=2.0):
    now = dt.datetime.now(dt.timezone.utc)
    return json.dumps({
        "approved": approved, "nonce": "n" * 64,
        "series": "cpc-btc-updown-15m-", "issued_utc": now.isoformat(),
        "expires_utc": (now + dt.timedelta(hours=hours)).isoformat(),
        "max_windows": windows, "max_total_exposure_usd": exposure,
        "max_daily_loss_usd": daily})


@pytest.fixture()
def deployed(monkeypatch):
    for k, v in DEPLOYED.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("L5_CAMPAIGN_APPROVAL", doc())
    return monkeypatch


def row(result, name):
    return next(r for r in result["rows"] if r["name"] == name)


# ================================ 1. the configuration that was failing
def test_the_deployed_configuration_now_passes(deployed):
    assert V.verify()["PASS"] is True


def test_the_eight_previously_stale_rows_pass(deployed):
    r = V.verify()
    for name in ("submission_budget_open", "submission_budget_complete",
                 "submission_budget_replace", "submission_backstop",
                 "aggregate_exposure_usd", "aggregate_exposure_in_force_usd",
                 "opening_notional_budget_usd",
                 "max_windows_authorization"):
        assert row(r, name)["ok"] is True, name


# ================================ 2. it is NOT an unconditional pass
def test_no_approval_fails_the_document_derived_rows(deployed):
    deployed.delenv("L5_CAMPAIGN_APPROVAL", raising=False)
    r = V.verify()
    assert r["PASS"] is False
    for name in ("aggregate_exposure_usd", "aggregate_exposure_in_force_usd",
                 "max_windows_authorization"):
        assert row(r, name)["ok"] is False, name
        assert row(r, name)["required"] == V.NO_APPROVAL


@pytest.mark.parametrize("bad", ["", "   ", "{not json", "[1,2]"])
def test_an_unusable_document_fails_rather_than_defaults(deployed, bad):
    deployed.setenv("L5_CAMPAIGN_APPROVAL", bad)
    r = V.verify()
    assert r["PASS"] is False
    assert row(r, "aggregate_exposure_usd")["required"] == V.NO_APPROVAL


def test_an_unapproved_document_fails(deployed):
    deployed.setenv("L5_CAMPAIGN_APPROVAL", doc(approved=False))
    assert V.verify()["PASS"] is False


def test_an_expired_document_fails(deployed):
    deployed.setenv("L5_CAMPAIGN_APPROVAL", json.dumps(json.loads(
        doc()) | {"expires_utc": (dt.datetime.now(dt.timezone.utc)
                                  - dt.timedelta(hours=1)).isoformat()}))
    assert V.verify()["PASS"] is False


@pytest.mark.parametrize("env", [
    "L5_LIVE_AUTH_MAX_OPEN", "L5_LIVE_AUTH_MAX_COMPLETE",
    "L5_LIVE_AUTH_MAX_REPLACE", "L5_LIVE_AUTH_MAX_SUBMISSIONS"])
def test_an_unset_submission_budget_fails(deployed, env):
    deployed.delenv(env, raising=False)
    assert V.verify()["PASS"] is False


@pytest.mark.parametrize("env,name", [
    ("L5_LIVE_AUTH_MAX_OPEN", "submission_budget_open"),
    ("L5_LIVE_AUTH_MAX_REPLACE", "submission_budget_replace")])
def test_a_budget_over_the_ticket_ceiling_fails(deployed, env, name):
    deployed.setenv(env, str(AUTH._CLASS_TICKET_CEILING + 1))
    r = V.verify()
    assert row(r, name)["ok"] is False
    assert r["PASS"] is False


def test_a_zero_budget_fails(deployed):
    deployed.setenv("L5_LIVE_AUTH_MAX_OPEN", "0")
    assert V.verify()["PASS"] is False


def test_an_opening_notional_over_its_code_ceiling_is_clamped_not_passed_up(
        deployed):
    """The accessor itself clamps, which is where the protection lives, so
    the row reports the CEILING as effective rather than the owner's larger
    number. Grading the clamped value is the point: the verifier reports
    what the money path will actually use."""
    deployed.setenv("L5_TS_OPENING_NOTIONAL_BUDGET_USD",
                    str(D(str(TS.OPENING_NOTIONAL_MAX_USD)) + D("1")))
    r = V.verify()
    eff = D(str(row(r, "opening_notional_budget_usd")["effective"]))
    assert eff <= D(str(TS.OPENING_NOTIONAL_MAX_USD))
    assert row(r, "opening_notional_budget_usd")["ok"] is True


# ================================ 3. an approval can never WIDEN a ceiling
def test_a_document_asking_more_exposure_is_capped_by_the_code_ceiling(
        deployed):
    deployed.setenv("L5_CAMPAIGN_APPROVAL", doc(exposure="500"))
    r = V.verify()
    assert D(str(row(r, "aggregate_exposure_usd")["required"])) == \
        D(str(CA.MAX_TOTAL_EXPOSURE_USD))


def test_a_document_asking_more_windows_is_capped_by_the_code_ceiling(
        deployed):
    deployed.setenv("L5_CAMPAIGN_APPROVAL", doc(windows=99))
    r = V.verify()
    assert int(row(r, "max_windows_authorization")["required"]) == \
        int(CA.MAX_WINDOWS)


def test_a_deployed_exposure_above_the_approved_one_fails(deployed):
    """The document approved $20; deploying $25 must not pass."""
    deployed.setenv("L5_MAX_TOTAL_EXPOSURE_USD", "25")
    r = V.verify()
    assert row(r, "aggregate_exposure_usd")["ok"] is False


def test_a_tighter_deployed_window_budget_than_approved_fails_loudly(
        deployed):
    """A stale `L5_LIVE_AUTH_MAX_WINDOWS` silently caps a campaign, which is
    exactly the trap the row's own note warns about, so it must not pass."""
    deployed.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "1")
    r = V.verify()
    assert row(r, "max_windows_authorization")["ok"] is False


# ================================ 4. the code ceilings stay code ceilings
def test_code_constant_rows_do_not_consult_the_document(deployed):
    """A document must not be able to move a code constant."""
    before = {r["name"]: r["required"] for r in V.verify()["rows"]}
    deployed.setenv("L5_CAMPAIGN_APPROVAL",
                    doc(windows=8, exposure="1", daily="1"))
    after = {r["name"]: r["required"] for r in V.verify()["rows"]}
    for name in ("defensive_loss_budget_usd", "defensive_pair_ceiling",
                 "maker_pair_ceiling", "campaign_duration_ceiling_s",
                 "approval_exposure_ceiling_usd",
                 "approval_daily_loss_ceiling_usd", "max_windows_code_ceiling"):
        assert before[name] == after[name], name


def test_every_row_states_its_source_and_a_verdict(deployed):
    """Unchanged property, re-asserted: a row that cannot be read is a
    FAILURE and never a default, and every row says where its value came
    from so an operator can check the claim."""
    for r in V.verify()["rows"]:
        assert r["source"], r["name"]
        assert isinstance(r["ok"], bool), r["name"]
        assert "required" in r and "effective" in r, r["name"]


def test_a_raising_accessor_fails_the_row_rather_than_the_call(deployed,
                                                               monkeypatch):
    from src import l5_pmus_live_runner as R

    def boom():
        raise RuntimeError("accessor exploded")

    monkeypatch.setattr(R, "max_live_orders", boom)
    r = V.verify()
    assert r["PASS"] is False
    assert row(r, "max_live_orders")["ok"] is False
