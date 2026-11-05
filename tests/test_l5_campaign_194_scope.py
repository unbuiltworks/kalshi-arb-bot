"""The issue-194 two-hour campaign: every number at or inside a code ceiling.

WHAT THIS GUARDS. The owner's agreed scope (issue #194 comment
2026-11-05T20:20:35Z) is 500 total submissions, 2 concurrent resting orders,
$20 exposure, $12 deployed cash, $5 daily realized-loss halt, over two hours.
This file asserts that the minting script asks for nothing a code constant
does not already permit -- so "prepare the campaign" can never quietly become
"widen a limit", and so a future change to either side is caught here rather
than at arming.

IT ALSO PINS THE RESERVE ARITHMETIC. `completion_capacity_refusal` refuses to
OPEN exposure the COMPLETE budget could no longer close, at
`COMPLETION_SUBMISSIONS_PER_OBLIGATION` per obligation. A class split that
looks generous but starves that reserve produces a campaign that opens a few
positions and then refuses every further open -- which is exactly the
arithmetic failure that capped Campaign E at OPEN 3/4.

NOTHING HERE INSTALLS, ARMS OR TRADES.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH


def _load():
    p = pathlib.Path("scripts/l5_mint_campaign_194.py")
    spec = importlib.util.spec_from_file_location("mint194", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _load()


# ------------------------------------------------- scope within ceilings
def test_windows_are_exactly_the_code_ceiling_and_two_hours():
    assert M.WINDOWS == CA.MAX_WINDOWS == 8
    assert M.WINDOWS * 15 * 60 == M.DURATION_S == 7200, "8 x 15m == 2h"


def test_duration_is_within_the_campaign_staleness_bound():
    """The gate judges an env-set document stale at MAX_CAMPAIGN_S from
    issue, so a campaign longer than that could expire mid-run."""
    assert M.DURATION_S <= CA.MAX_CAMPAIGN_S


def test_exposure_and_loss_are_at_or_inside_their_ceilings():
    from decimal import Decimal as D
    assert D(M.EXPOSURE_USD) <= CA.MAX_TOTAL_EXPOSURE_USD
    assert D(M.DAILY_LOSS_USD) <= CA.MAX_DAILY_LOSS_USD
    assert D(M.EXPOSURE_USD) == D("20")
    assert D(M.DAILY_LOSS_USD) == D("5")
    assert D(M.CASH_USD) == D("12")


def test_the_series_is_one_the_code_allows():
    assert M.mint()["series"] in CA.ALLOWED_SERIES


# ------------------------------------------------- the completion reserve
def test_the_class_budgets_sum_within_the_flat_total():
    assert sum(M.CLASS_BUDGETS.values()) <= M.MAX_SUBMISSIONS


def test_the_completion_reserve_holds_for_every_permitted_open():
    """The whole point of the uneven split."""
    worst, ok, total_ok, per = M.reserve_holds()
    assert per == AUTH.COMPLETION_SUBMISSIONS_PER_OBLIGATION
    assert ok, "COMPLETE starves before OPEN is exhausted (margin %d)" % worst
    assert total_ok
    assert worst >= 0


def test_complete_is_at_least_two_per_open():
    """Stated directly, so the intent survives a change to the split."""
    per = AUTH.COMPLETION_SUBMISSIONS_PER_OBLIGATION
    assert M.CLASS_BUDGETS["COMPLETE"] >= per * M.CLASS_BUDGETS["OPEN"]


def test_a_starved_split_is_detected_rather_than_passing(monkeypatch):
    """The guard has to be able to FAIL, or it guards nothing."""
    monkeypatch.setattr(M, "CLASS_BUDGETS",
                        {"OPEN": 200, "COMPLETE": 10, "REPLACE": 10})
    worst, ok, _total, _per = M.reserve_holds()
    assert not ok and worst < 0


def test_every_submission_class_has_a_budget():
    assert set(M.CLASS_BUDGETS) == set(AUTH.SUBMISSION_CLASSES)


# ------------------------------------------------------- the document
def test_the_minted_document_carries_every_required_field():
    doc = M.mint()
    missing = [f for f in CA.REQUIRED_FIELDS if f not in doc]
    assert missing == [], missing


def test_the_minted_document_is_approved_by_the_real_gate():
    st, raw = M.validate(M.mint())
    assert st.get("approved") is True, st
    assert raw, "the raw bytes are what the digest binds"


def test_the_nonce_is_long_enough_for_the_gate_and_fresh_each_mint():
    a, b = M.mint(), M.mint()
    assert len(a["nonce"]) >= 8, "the gate refuses a nonce under 8 chars"
    assert a["nonce"] != b["nonce"], "a new campaign is a new nonce"


def test_expiry_is_exactly_the_agreed_two_hours_after_issue():
    import datetime as dt
    doc = M.mint()
    i = dt.datetime.fromisoformat(doc["issued_utc"])
    e = dt.datetime.fromisoformat(doc["expires_utc"])
    assert (e - i).total_seconds() == M.DURATION_S


def test_a_future_dated_document_is_refused_by_the_gate():
    """`approval_issued_in_the_future` -- pinned because minting AT INSTALL
    TIME is the whole design, and a clock-skewed stamp must fail closed."""
    import datetime as dt
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    st, _ = M.validate(M.mint(now=future))
    assert st.get("approved") is not True
    # the gate names its refusal under `why`; `reason` is absent here
    assert st.get("why") == "approval_issued_in_the_future", st
    assert st.get("age_s", 0) < 0


def test_a_stale_document_is_refused_by_the_gate():
    """What the production heartbeat was reporting: the installed document
    was issued 2026-11-05T11:47Z and read at 20:19Z, far past the bound."""
    import datetime as dt
    old = (dt.datetime.now(dt.timezone.utc)
           - dt.timedelta(seconds=CA.MAX_CAMPAIGN_S + 600))
    st, _ = M.validate(M.mint(now=old))
    assert st.get("approved") is not True
    assert st.get("why") in ("approval_stale", "approval_expired"), st
    assert st.get("age_s", 0) > CA.MAX_CAMPAIGN_S


def test_the_script_validates_read_only_and_never_reserves():
    """A verification that consumed the window it was verifying has already
    happened once in this program. `authorize_window` RESERVES; `state`
    does not, and the script must only ever call the latter."""
    src = pathlib.Path("scripts/l5_mint_campaign_194.py").read_text()
    # THE CALL, not the word: the script's own prose names the reserving
    # helper precisely in order to explain why it does not call it, and a
    # comment must not be mistaken for a call site. (Fourth time a
    # source-text guard in this repository has caught explanatory prose --
    # the guards are right to be strict.)
    assert "authorize_window(" not in src
    assert ".state()" in src


def test_the_script_cannot_clear_halt_or_arm():
    src = pathlib.Path("scripts/l5_mint_campaign_194.py").read_text()
    for forbidden in ("set-variables", "set_variables", "try_arm",
                      "authorize_arming", "disarm("):
        assert forbidden not in src, forbidden
    assert "L5_LIVE_HALT must still" in src, "it must say so explicitly"
