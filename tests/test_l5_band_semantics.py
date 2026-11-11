"""Regressions for the entry-band / held-inventory-band split.

The first version of this counterfactual let 67 refused extends "fill" and
reported +$62 of settlement upside on a market with a $30 cap. It was
arithmetically correct and completely wrong, because it dropped every other
gate the owner said to keep in force. Most of these tests exist to keep
that from happening again.
"""
from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_band_semantics as bs

ROOT = Path(__file__).resolve().parents[1]


# -------------------------------------------- the constants are production's
def test_the_legal_price_band_is_the_executors_own():
    from src import pmus_q1_executor as q1
    assert (bs.LEGAL_LO, bs.LEGAL_HI) == (q1.MIN_PRICE, q1.MAX_PRICE)


def test_the_observation_band_is_the_sessions_own():
    from src import pmus_spring_l5_session as sess
    assert (bs.OBS_LO, bs.OBS_HI) == (sess.SPRING_BAND_LO, sess.SPRING_BAND_HI)


def test_the_risk_caps_are_the_active_rungs_own():
    """R5FT carries residual_units 6 and a $30 per-market cap. An earlier
    draft used the module default of 3 and would have under-counted the
    counterfactual; a stale copy in the other direction would over-count
    it. Either way the number is wrong, so it is read from the rung."""
    from src import pmus_l5_ramp_ladder as ladder
    r5ft = ladder.rung("R5FT")
    assert bs.MAX_RESIDUAL_UNITS == int(r5ft["residual_units"])
    assert bs.PER_MARKET_CAP_USD == D(str(r5ft["per_market_cap_usd"]))


# ------------------------------------------------ the two bands are two rules
def test_held_inventory_is_gated_only_by_the_legal_band():
    """A bid of 0.78 is outside the OBSERVATION band but inside the legal
    one. Flat, that refuses; holding, it does not. That asymmetry is the
    whole subject of this module and it is production's behaviour, not a
    proposal."""
    assert bs.band_fired(D("0.78"), held=D("0")) is True
    assert bs.band_fired(D("0.78"), held=D("8.79")) is False


def test_a_bid_outside_the_legal_band_refuses_either_way():
    for held in (D("0"), D("8.79")):
        assert bs.band_fired(D("0.87"), held) is True
        assert bs.band_fired(D("0.19"), held) is True


def test_distance_outside_is_zero_inside_and_signed_the_right_way():
    assert bs.distance_outside(D("0.50")) == D("0")
    assert bs.distance_outside(D("0.87")) == D("0.07")
    assert bs.distance_outside(D("0.05")) == D("0.15")


# ------------------------------------------------------------------- the arms
def test_no_arm_widens_the_entry_band():
    """Every shadow arm leaves ENTRY exactly as production has it. Widening
    admission for a market we do not yet hold is a different and riskier
    experiment than the one being run."""
    for arm in bs.ARMS:
        assert bs.admits(arm, bid=D("0.87"), held=D("0"),
                         sampled="open") is False


def test_entry_only_admits_a_held_extend_that_control_refuses():
    assert bs.admits("CONTROL", bid=D("0.87"), held=D("8.79"),
                     sampled="extend") is False
    assert bs.admits("SHADOW_1_ENTRY_ONLY", bid=D("0.87"), held=D("8.79"),
                     sampled="extend") is True


@pytest.mark.parametrize("bid,buf5,buf10,buf15", [
    (D("0.85"), True, True, True),
    (D("0.87"), False, True, True),
    (D("0.94"), False, False, True),
    (D("0.98"), False, False, False),
    (D("0.19"), True, True, True),
    (D("0.015"), False, False, False),
])
def test_the_buffer_widths_are_fixed_arithmetic_not_fitted(bid, buf5, buf10,
                                                           buf15):
    kw = dict(held=D("8.79"), sampled="extend")
    assert bs.admits("SHADOW_2_BUF_5c", bid=bid, **kw) is buf5
    assert bs.admits("SHADOW_2_BUF_10c", bid=bid, **kw) is buf10
    assert bs.admits("SHADOW_2_BUF_15c", bid=bid, **kw) is buf15


def test_a_sampled_complete_is_admitted_identically_by_every_arm():
    """This gate never refused a COMPLETE, so the arms must not differ on
    one -- otherwise the comparison picks up a difference the band never
    made."""
    for arm in bs.ARMS:
        assert bs.admits(arm, bid=D("0.98"), held=D("8.79"),
                         sampled="complete") is True


# ------------------------------------- the caps stay in force (THE REGRESSION)
def test_the_residual_cap_is_rechecked_against_the_shadow_inventory():
    """THE REGRESSION. Dropping this let one market's arm buy 78 more
    contracts and book $62 of 'upside' inside a $30 cap. With the rung's
    own bound in force the same market admits 3 fills and blocks 64."""
    unit = D("1.92")
    assert bs.residual_cap_allows(D("-8.79"), unit) is True      # 4.58 units
    assert bs.residual_cap_allows(D("-11.52"), unit) is False    # 6.00 units
    assert bs.residual_cap_allows(D("-20.00"), unit) is False


def test_the_residual_cap_is_inert_before_a_behavioural_unit_exists():
    assert bs.residual_cap_allows(D("-8.79"), None) is True
    assert bs.residual_cap_allows(D("-8.79"), D("0")) is True


def test_the_spend_cap_is_rechecked_too():
    assert bs.spend_cap_allows(D("4.13"), D("15.32")) is True
    assert bs.spend_cap_allows(D("4.13"), D("26.00")) is False


# --------------------------------------------------- settlement is venue-tied
def test_the_winner_is_derived_from_our_confirmed_realized_number():
    """Both settled markets reproduce exactly. If a third ever fails to,
    that is a reconciliation failure and must read None."""
    assert bs.winning_side(D("6.0034"), D("6.08"), D("10.13"),
                           D("4.1266")) == "dn"
    assert bs.winning_side(D("-1.0779"), D("2.79"), D("6.28"),
                           D("3.8679")) == "up"
    assert bs.winning_side(D("-2.5896"), D("0"), D("6.61"),
                           D("2.58965")) == "up"


def test_an_unexplained_realized_number_is_not_a_coin_flip():
    assert bs.winning_side(D("99.0"), D("6.08"), D("10.13"),
                           D("4.1266")) is None


def test_an_unsettled_market_contributes_none_and_never_zero():
    """Zero would quietly average into the totals as a neutral result. It
    is not neutral; it is absent."""
    assert bs.settlement_value(D("3.81"), D("0.15"), "dn", None) is None
    assert bs.settlement_value(D("1"), D("0.15"), "dn", "dn") == D("0.85")
    assert bs.settlement_value(D("1"), D("0.15"), "dn", "up") == D("-0.15")


# ------------------------------------------------------------ tape support
def test_support_needs_a_real_fill_nearby_not_merely_in_the_market():
    fills = {"m": [1000.0]}
    assert bs.tape_support(1300.0, "m", fills) is True
    assert bs.tape_support(2000.0, "m", fills) is False
    assert bs.tape_support(1000.0, "other", fills) is False


# ------------------------------------------------------------ shadow only
def test_nothing_armed_imports_this():
    for rel in ("src/pmus_spring_l5_session.py",
                "src/pmus_l5_continuous_session.py",
                "src/pmus_sports_l5_decision.py",
                "src/pmus_q1_executor.py"):
        p = ROOT / rel
        if p.exists():
            assert "l5_band_semantics" not in p.read_text(), rel
