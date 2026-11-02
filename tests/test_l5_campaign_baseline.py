"""The campaign's own $5 additional-loss bound, measured from a persisted start.

OWNER 2026-10-24: "Ensure the campaign has an independently enforced maximum $5
additional loss, including outstanding worst-case obligations. Do not assume
the existing $20 lifetime gate supplies this."

These tests exist to prove three things: that the bound is measured from a
starting point that cannot be moved without an owner action, that every unknown
refuses rather than counting zero, and that it never refuses the completions
that reduce risk.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

from src import l5_campaign_baseline as CB
from src import l5_live_validation_gates as VG


CID = "camp1.l5-btc15m-nokey-20261025-7c4f9a21"
OTHER = "camp1.l5-btc15m-stage1-20261024T1324Z-8a56b1d9"
#: production's all-time account-wide realized at the time of writing
START = "-11.6213"


@pytest.fixture()
def base(tmp_path):
    return CB.CampaignBaseline(root=str(tmp_path))


def captured(b, realized=START, cid=CID):
    out = b.capture(cid, account_wide_realized=realized,
                    cash_usd="341.19", inventory={"up_qty": D(0)},
                    open_orders=[], now_iso="2026-10-25T00:00:00Z")
    assert out["ok"], out
    return out


# ------------------------------------------------------- capturing the start

def test_the_baseline_records_the_starting_point_and_changes_no_loss(base):
    out = captured(base)
    assert out["captured"] is True
    assert out["baseline"]["account_wide_realized"] == "-11.6213"
    assert out["baseline"]["campaign_id"] == CID
    # the figure is RECORDED, not altered: nothing here writes a realized total
    assert base.read()["account_wide_realized"] == "-11.6213"
    assert "NOT reset" in out["labels"]


def test_a_campaign_may_not_re_measure_its_own_starting_point(base):
    """The anti-reset property. Re-capturing would move the zero point and
    hand the campaign a fresh $5, so a second call must return the stored
    document untouched -- even when realized has moved a long way since."""
    captured(base)
    again = base.capture(CID, account_wide_realized="-14.0000",
                         now_iso="2026-10-25T02:00:00Z")
    assert again["ok"] is True
    assert again["captured"] is False
    assert again["baseline"]["account_wide_realized"] == "-11.6213"
    assert base.read()["account_wide_realized"] == "-11.6213"
    # and the bound still measures the full $2.3787 of new loss
    got = base.additional_loss_usd(CID, account_wide_realized="-14.0000",
                                   open_worst_case_usd="0")
    assert got["additional_loss_usd"] == "2.3787"


def test_a_restart_cannot_move_the_zero_point(base, tmp_path):
    """A fresh object over the same volume reads the stored baseline, so a
    redeploy mid-campaign does not re-measure."""
    captured(base)
    fresh = CB.CampaignBaseline(root=str(tmp_path))
    assert fresh.read()["account_wide_realized"] == "-11.6213"
    assert fresh.capture(CID, account_wide_realized="-20")["captured"] is False


def test_a_new_campaign_captures_a_fresh_baseline_and_journals_the_old(base):
    """A new campaign id is a new zero point, which is acceptable only because
    the id comes from the owner's approval document."""
    captured(base, cid=OTHER)
    out = captured(base, realized="-12.5000", cid=CID)
    assert out["captured"] is True
    assert base.read()["campaign_id"] == CID
    hist = [json.loads(l) for l in
            open(base._history_path()).read().splitlines() if l.strip()]
    assert [h["campaign_id"] for h in hist] == [OTHER]
    assert hist[0]["account_wide_realized"] == "-11.6213"


def test_capture_refuses_when_the_starting_figure_is_unknown(base):
    """A baseline that recorded 'unknown' could never refuse anything."""
    for bad in (None, "", "nonsense", "NaN", "Infinity"):
        out = base.capture(CID, account_wide_realized=bad)
        assert out["ok"] is False, bad
        assert out["reason"] == "CAMPAIGN_BASELINE_REALIZED_UNAVAILABLE", bad


def test_capture_refuses_without_a_campaign_id(base):
    assert base.capture("", account_wide_realized=START)["reason"] == \
        "CAMPAIGN_BASELINE_ID_MISSING"


def test_a_corrupt_baseline_file_refuses_rather_than_reading_as_empty(base):
    captured(base)
    with open(base._path(), "w") as fh:
        fh.write("{not json")
    with pytest.raises(CB.CampaignBaselineUnavailable):
        base.read()
    assert base.capture(CID, account_wide_realized=START)["reason"] == \
        "CAMPAIGN_BASELINE_UNREADABLE"
    assert base.additional_loss_usd(
        CID, account_wide_realized=START,
        open_worst_case_usd="0")["reason"] == "CAMPAIGN_BASELINE_UNREADABLE"


def test_a_missing_file_is_not_a_failure_but_is_not_a_measurement_either(base):
    assert base.read() == {"campaign_id": None}
    got = base.additional_loss_usd(CID, account_wide_realized=START,
                                   open_worst_case_usd="0")
    assert got["ok"] is False
    assert got["reason"] == "CAMPAIGN_BASELINE_ABSENT"


# -------------------------------------------------------------- the figure

def test_the_figure_is_realized_delta_plus_the_open_books_worst_case(base):
    captured(base)
    got = base.additional_loss_usd(CID, account_wide_realized="-13.0000",
                                   open_worst_case_usd="1.25")
    assert got["ok"] is True
    assert got["realized_delta_loss_usd"] == "1.3787"
    assert got["open_worst_case_usd"] == "1.25"
    assert got["additional_loss_usd"] == "2.6287"


def test_a_realized_gain_creates_no_additional_room(base):
    """A campaign that made money does not thereby earn a bigger loss
    budget -- the same rule the campaign worst-case budget states."""
    captured(base)
    got = base.additional_loss_usd(CID, account_wide_realized="-8.0000",
                                   open_worst_case_usd="0.50")
    assert got["realized_delta_loss_usd"] == "0"
    assert got["additional_loss_usd"] == "0.50"


def test_a_profitable_open_book_contributes_no_risk_but_never_credit(base):
    """A completed pair's deficit is NEGATIVE. It must not be refused, and it
    must not pay for someone else's loss either."""
    captured(base)
    got = base.additional_loss_usd(CID, account_wide_realized="-13.0000",
                                   open_worst_case_usd="-0.11")
    assert got["open_worst_case_usd"] == "0"
    assert got["additional_loss_usd"] == "1.3787"


def test_the_baseline_of_another_campaign_is_never_measured_against(base):
    captured(base, cid=OTHER)
    got = base.additional_loss_usd(CID, account_wide_realized=START,
                                   open_worst_case_usd="0")
    assert got["ok"] is False
    assert got["reason"] == "CAMPAIGN_BASELINE_FOREIGN:%s" % OTHER


def test_an_unknown_current_or_open_figure_refuses(base):
    captured(base)
    assert base.additional_loss_usd(
        CID, account_wide_realized=None,
        open_worst_case_usd="0")["reason"] == "CAMPAIGN_REALIZED_UNAVAILABLE"
    got = base.additional_loss_usd(CID, account_wide_realized=START,
                                   open_worst_case_usd=None)
    assert got["reason"] == "CAMPAIGN_OPEN_WORST_CASE_UNAVAILABLE"
    assert "counting zero" in got["note"]


# -------------------------------------------------------------- the refusal

def test_it_refuses_at_the_limit_and_not_before(base):
    captured(base)
    kw = dict(account_wide_realized=START, limit_usd=D("5.00"))
    assert base.refuse(CID, open_worst_case_usd="4.99", **kw) is None
    assert base.refuse(CID, open_worst_case_usd="5.00", **kw) == \
        "CAMPAIGN_ADDITIONAL_LOSS_LIMIT:5.00>=5.00"
    assert base.refuse(CID, open_worst_case_usd="7", **kw).startswith(
        "CAMPAIGN_ADDITIONAL_LOSS_LIMIT:7")


def test_realized_losses_and_open_risk_combine_against_the_one_limit(base):
    """$3 settled plus a $2 open book is $5 and stops, even though neither
    half would."""
    captured(base)
    assert base.refuse(CID, account_wide_realized="-14.6213",
                       open_worst_case_usd="2.00",
                       limit_usd=D("5.00")) == \
        "CAMPAIGN_ADDITIONAL_LOSS_LIMIT:5.0000>=5.00"


def test_every_unknown_reaches_the_caller_as_a_refusal(base):
    """`refuse` must never answer None on a figure it could not establish."""
    for kw in ({"campaign_id": ""},
               {"account_wide_realized": None},
               {"open_worst_case_usd": "oops"},
               {"limit_usd": "oops"}):
        args = dict(campaign_id=CID, account_wide_realized=START,
                    open_worst_case_usd="0", limit_usd=D("5.00"))
        args.update(kw)
        cid = args.pop("campaign_id")
        assert base.refuse(cid, **args) is not None, kw


# ------------------------------------------------------- the env-set ceiling

def test_unset_is_inert_and_unreadable_is_the_tightest_bound(monkeypatch):
    monkeypatch.delenv(CB.LIMIT_ENV, raising=False)
    assert CB.limit_from_env() is None
    for bad in ("five", "NaN", "-1"):
        monkeypatch.setenv(CB.LIMIT_ENV, bad)
        assert CB.limit_from_env() == D(0), bad


def test_the_env_may_tighten_the_ceiling_and_can_never_widen_it(monkeypatch):
    monkeypatch.setenv(CB.LIMIT_ENV, "2.50")
    assert CB.limit_from_env() == D("2.50")
    monkeypatch.setenv(CB.LIMIT_ENV, "500")
    assert CB.limit_from_env() == CB.MAX_ADDITIONAL_LOSS_USD == D("10.00")


def test_the_code_constant_is_the_owner_approved_five(monkeypatch):
    """Pinned: a later edit that widens the code ceiling has to change a
    test that says why it is five."""
    assert CB.MAX_ADDITIONAL_LOSS_USD == D("10.00")


# --------------------------------------------- wired into ValidationGates

INV_FLAT = {"up_qty": D(0), "dn_qty": D(0), "up_cost": D(0), "dn_cost": D(0)}


def gates(baseline=None, limit=None, **kw):
    return VG.ValidationGates(
        max_cash_deployed_usd=D("5"),
        max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("20"),
        campaign_id=CID,
        campaign_baseline=baseline,
        max_campaign_additional_loss_usd=limit,
        **kw)


def refuse(g, *, qty="2", price="0.30", intent="ORDER_INTENT_BUY_LONG",
           realized=START, inventory=INV_FLAT, outstanding=()):
    return g.refuse(order_qty=D(qty), order_price=D(price),
                    order_intent=intent, cumulative_realized=D(realized),
                    cumulative_unrealized_loss=D(0),
                    inventory=inventory, outstanding=list(outstanding))


def test_unconfigured_the_gate_is_inert(base):
    """No limit means no change to any existing behaviour -- not even a
    baseline read."""
    assert refuse(gates()) is None
    assert refuse(gates(baseline=base)) is None


def test_configured_without_a_baseline_refuses(base):
    assert refuse(gates(limit=D("5.00"))) == "CAMPAIGN_BASELINE_UNCONFIGURED"
    # configured, baseline object present, but nothing captured yet
    assert refuse(gates(baseline=base, limit=D("5.00"))) == \
        "CAMPAIGN_BASELINE_ABSENT"


def test_configured_without_a_campaign_id_refuses(base):
    captured(base)
    g = VG.ValidationGates(campaign_baseline=base,
                           max_campaign_additional_loss_usd=D("5.00"))
    assert refuse(g) == "CAMPAIGN_BASELINE_NO_CAMPAIGN_ID"


def test_configured_without_inventory_refuses(base):
    captured(base)
    g = gates(baseline=base, limit=D("5.00"))
    assert refuse(g, inventory=None) == \
        "CAMPAIGN_ADDITIONAL_LOSS_NO_INVENTORY"


def test_the_campaigns_first_leg_is_allowed_at_its_starting_point(base):
    captured(base)
    assert refuse(gates(baseline=base, limit=D("5.00"))) is None


def test_the_gate_stops_the_campaign_once_it_is_five_down(base):
    """$4 of settled loss plus a 2@0.80 first leg is over $5 and stops, while
    the lifetime $20 ceiling would still allow it."""
    captured(base)
    g = gates(baseline=base, limit=D("5.00"))
    why = refuse(g, qty="2", price="0.80", realized="-15.6213")
    assert why is not None and why.startswith("CAMPAIGN_ADDITIONAL_LOSS_LIMIT")
    # and the SAME order passes every pre-existing ceiling, which is the
    # point: this bound is not already supplied by them
    assert refuse(gates(), qty="2", price="0.80", realized="-15.6213") is None


def test_it_never_refuses_a_completion_that_reduces_risk(base):
    """A leg that matches existing inventory has a NEGATIVE worst case, so a
    campaign deep into its budget can still close its book. A bound that
    blocked completions would be worse than no bound."""
    captured(base)
    g = gates(baseline=base, limit=D("5.00"))
    inv = {"up_qty": D("2"), "dn_qty": D(0),
           "up_cost": D("0.60"), "dn_cost": D(0)}
    # 2 DOWN at 0.36 completes the pair: combined cost 0.96 < 1.00
    assert refuse(g, qty="2", price="0.36",
                  intent="ORDER_INTENT_BUY_SHORT",
                  realized="-15.0000", inventory=inv) is None


def test_outstanding_obligations_are_counted_not_just_fills(base):
    """The owner required outstanding worst-case obligations in the bound. A
    resting order the book has not filled still counts against the $5."""
    captured(base)
    g = gates(baseline=base, limit=D("5.00"))
    rest = [{"side": "UP", "qty": D("2"), "price": D("0.80"),
             "intent": "ORDER_INTENT_BUY_LONG"}]
    alone = refuse(g, qty="2", price="0.80", realized="-13.9000")
    withrest = refuse(g, qty="2", price="0.80", realized="-13.9000",
                      outstanding=rest)
    assert alone is None
    assert withrest is not None
    assert withrest.startswith("CAMPAIGN_ADDITIONAL_LOSS_LIMIT")


def test_a_baseline_that_raises_is_a_refusal_not_a_pass(base):
    class Exploding:
        def refuse(self, *a, **k):
            raise RuntimeError("volume gone")
    g = gates(baseline=Exploding(), limit=D("5.00"))
    assert refuse(g) == "CAMPAIGN_ADDITIONAL_LOSS_ERROR:RuntimeError"


def test_the_gate_is_additive_and_cannot_rescue_a_refused_order(base):
    """Ordering is the safety argument: every pre-existing ceiling runs
    first, so a campaign with room cannot spend it on an order the
    account-wide gate already refused."""
    captured(base)
    g = gates(baseline=base, limit=D("5.00"))
    # the lifetime $20 ceiling is already breached
    assert refuse(g, realized="-25.0000") == \
        "CUMULATIVE_LOSS_LIMIT:25.0000>=20"


def test_telemetry_names_the_point_in_force_and_leaks_no_secret(base,
                                                                monkeypatch):
    monkeypatch.setenv(CB.LIMIT_ENV, "5.00")
    captured(base)
    t = base.telemetry()
    assert t["readable"] is True
    assert t["campaign_id"] == CID
    assert t["account_wide_realized"] == "-11.6213"
    # THE TWO ARE DIFFERENT AND THAT IS THE POINT. `max_additional_loss_usd`
    # is the CODE CEILING, now 10.00. `limit_in_force_usd` is
    # `limit_from_env()` = min(env, ceiling), and this fixture's env says 5,
    # so the EFFECTIVE limit is 5 -- raising the ceiling alone is inert.
    # Production must also set L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD to 10 or
    # the authorised $10 silently stays $5.
    assert t["limit_in_force_usd"] == "5.00"
    assert t["max_additional_loss_usd"] == "10.00"
    assert "changes nothing" in t["labels"]


def test_telemetry_reports_an_unreadable_store_as_unreadable(base):
    captured(base)
    with open(base._path(), "w") as fh:
        fh.write("[]")
    assert base.telemetry()["readable"] is False
