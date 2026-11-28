"""The approved-transition stop, pinned to the owner's six requirements.

OWNER 2026-09-11: "effective stop = the stop belonging to the currently
authorized campaign/rung. At boot, if there is an exact, unconsumed,
signed/recorded approval for transition to R5FT, the re-arm preflight may
evaluate the approved R5FT stop of -$30 before consuming the approval.
Otherwise it must continue using the active rung's existing stop. Do not make
-$30 override R1/R2/R4 or any historical/micro-rung stop."
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_ramp_ladder as ladder
from src import pmus_transition_stop as ts
from src import pmus_l5_continuous_session as cont


def _approval(**over):
    base = next(a for a in cont.load_campaign_approvals()
                if a["campaign_id"] == ts.TRANSITION_APPROVAL_ID)
    return {**base, **over}


def _deploy():
    return {"commit": "bd589676", "deployment_id": "6ab7b8b6"}


# ---------------------------------------------------------------- (1)
def test_no_pending_approval_leaves_r5f_binding_at_minus_fifteen():
    got = ts.transition_stop("R5F", None, deployment=_deploy())
    assert got["stop"] == "-15.00"
    assert got["source"] == "active_rung"
    assert "no_pending_approval" in got["refused"]


# ---------------------------------------------------------------- (2)
def test_the_exact_pending_r5ft_approval_lets_the_preflight_use_minus_thirty():
    got = ts.transition_stop("R5F", _approval(), deployment=_deploy())
    assert got["stop"] == "-30.00"
    assert got["source"] == "approved_transition"
    assert got["approval"] == "R5FT-CANARY-2026-09-11"
    assert got["refused"] == []
    # and the ACTIVE rung's own number is still reported beside it
    assert got["active_rung_stop"] == "-15.00"


# ---------------------------------------------------------------- (3)
@pytest.mark.parametrize("kw,expect", [
    ({"approval": {"campaign_id": "SOMETHING-ELSE", "rung": "R5FT",
                   "source": "x", "approved_by": "owner"}},
     "approval_id_mismatch"),
    ({"approval": None}, "no_pending_approval"),
])
def test_a_wrong_approval_fails_closed(kw, expect):
    got = ts.transition_stop("R5F", kw["approval"], deployment=_deploy())
    assert got["stop"] == "-15.00" and got["source"] == "active_rung"
    assert expect in got["refused"]


def test_a_wrong_rung_fails_closed():
    """The approval names R5FT; one naming anything else must not widen."""
    got = ts.transition_stop("R5F", _approval(rung="R4"), deployment=_deploy())
    assert got["stop"] == "-15.00" and got["source"] == "active_rung"
    assert "approval_rung_mismatch" in got["refused"]


def test_transitioning_from_the_wrong_active_rung_fails_closed():
    got = ts.transition_stop("R4", _approval(), deployment=_deploy())
    assert got["stop"] == "-15.00" or got["source"] == "active_rung"
    assert "active_rung_is_not_the_transition_source" in got["refused"]


def test_an_unknown_deployment_fails_closed():
    for bad in ({}, {"commit": "abc"}, {"deployment_id": "xyz"}):
        got = ts.transition_stop("R5F", _approval(), deployment=bad)
        assert got["source"] == "active_rung"
        assert "deployment_identity_unknown" in got["refused"]


def test_an_already_consumed_approval_fails_closed():
    got = ts.transition_stop("R5F", _approval(), deployment=_deploy(),
                             consumed_ids=[ts.TRANSITION_APPROVAL_ID])
    assert got["source"] == "active_rung"
    assert "approval_already_consumed" in got["refused"]


def test_an_unrecorded_or_non_owner_approval_fails_closed():
    assert "approval_not_recorded" in ts.transition_stop(
        "R5F", _approval(source=""), deployment=_deploy())["refused"]
    assert "approval_not_owner" in ts.transition_stop(
        "R5F", _approval(approved_by="claude"), deployment=_deploy())["refused"]


# ---------------------------------------------------------------- (4)
def test_the_micro_rungs_keep_their_own_dollar_stop():
    """THE THING THE REVERTED ACCOUNT-WIDE OVERRIDE BROKE. R1/R2C carry the
    absolute -$1.00 and must keep it, with or without a pending approval."""
    for rid in ("R1", "R2C"):
        assert ladder.daily_loss_stop(ladder.rung(rid)) == D("-1.00"), rid
        got = ts.transition_stop(rid, _approval(), deployment=_deploy())
        assert got["stop"] == "-1.00", rid
        assert got["source"] == "active_rung", rid


def test_no_other_rung_stop_moved():
    assert ladder.daily_loss_stop(ladder.rung("R4")) == D("-15.00")
    assert ladder.daily_loss_stop(ladder.rung("R5F")) == D("-15.00")
    assert ladder.daily_loss_stop(ladder.rung("R5FT")) == D("-30.00")
    assert not hasattr(ladder, "ACCOUNT_DAILY_LOSS_STOP"), (
        "the account-wide override was reverted; it must not come back")


def test_worst_case_reports_each_rungs_own_stop_again():
    assert ladder.worst_case(ladder.rung("R4"))["daily_loss_stop_usd"] == "-15.00"
    assert ladder.worst_case(ladder.rung("R5FT"))["daily_loss_stop_usd"] == "-30.00"


# ---------------------------------------------------------------- (5)
def test_consumption_cannot_widen_beyond_the_approved_thirty(monkeypatch):
    """An approval, or a later ladder edit, asking for more than the -$30
    the owner named reads back -$30."""
    wide = dict(ladder.rung("R5FT"))
    wide["daily_loss_stop_usd"] = "-500.00"
    monkeypatch.setattr(ladder, "rung",
                        lambda rid=None: dict(wide) if str(rid).upper() == "R5FT"
                        else ladder.RUNGS[0] if rid is None else _orig_rung(rid))
    got = ts.transition_stop("R5F", _approval(), deployment=_deploy())
    assert got["stop"] == "-30.00"
    assert D(got["transition_limit"]) == D("-30.00")


_orig_rung = ladder.rung


def test_the_limit_is_the_number_the_owner_named():
    assert ts.APPROVED_TRANSITION_STOP_LIMIT == D("-30.00")
    assert ts.TRANSITION_FROM == "R5F" and ts.TRANSITION_TO == "R5FT"
    assert ts.TRANSITION_APPROVAL_ID == "R5FT-CANARY-2026-09-11"


# ---------------------------------------------------------------- (6)
def test_unresolved_venue_inventory_counts_as_risk_even_when_not_held():
    """aec-county-not-ham-2026-09-08 carried 45.01 units at the venue while
    blocking_held() reported [] -- so `held` alone is not the risk set. Risk
    is priced over the LEDGER's unsettled set, which is a different view and
    the one every call site uses."""
    import inspect
    src = inspect.getsource(cont._combined_now)
    assert "unsettled_slugs()" in src
    assert "held" not in src.split("combined_loss")[0].replace(
        "held_inventory_from_ledger", ""), (
        "the budget must not be priced over the scheduler's held set")
    # and the risk-set audit exists precisely to make the divergence visible
    from src import pmus_risk_set_audit as rsa
    snap = rsa.snapshot(["aec-county-not-ham-2026-09-08"], [],
                        combined_loss_usd="-15.1516",
                        daily_loss_stop_usd="-15.00")
    assert "aec-county-not-ham-2026-09-08" in str(snap)
