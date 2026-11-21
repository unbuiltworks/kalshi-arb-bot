"""Adapted V1 changes residual management and nothing else -- and it is blind.

L5's Sep 23-25 decomposition is why: the matched leg earned +520.7, +411.1
and +382.7 bp of spend while the residual leg lost -719.8, -1,175.5 and
-824.9. The remainder was the hole, so the remainder is the only thing this
policy is allowed to touch.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import src.l5_adapted_v1 as A


# ------------------------------------------------------- the hard barrier

@pytest.mark.parametrize("leak", [
    {"settlementPx": "1.0"},
    {"settlement_px": "0.0"},
    {"market_state": "MARKET_STATE_EXPIRED"},
    {"resolved": True},
    {"winner": "UP"},
    {"redeem_usdc": "40"},
    {"realized_pnl": "1.12"},
    {"expired": False},
    {"nested": {"deep": {"settlement": "1.0"}}},
    {"rows": [{"ok": 1}, {"payout": "1.0"}]},
])
def test_any_settlement_information_refuses_the_decision(leak):
    with pytest.raises(A.OutcomeLeak):
        A.assert_blind(leak)


def test_a_blind_state_is_accepted():
    A.assert_blind({"residual": "6.08", "residual_age_s": 120.0,
                    "time_remaining_s": 300.0,
                    "book": {"bid": "0.4", "ask": "0.42"},
                    "pair_cost": "0.95"})


def test_the_policy_itself_refuses_a_leaking_state():
    p = A.AdaptedV1(max_residual_shares=D("2"))
    with pytest.raises(A.OutcomeLeak):
        p.wants_repair({"residual": "6", "settlementPx": "1.0"})
    with pytest.raises(A.OutcomeLeak):
        p.prefers({"residual": "6", "winner": "UP"}, "DOWN")


# ------------------------------------------------------------- levers off

def test_with_every_lever_off_nothing_is_ever_repaired():
    p = A.AdaptedV1()
    out = p.wants_repair({"residual": "9.99", "residual_age_s": 100000.0})
    assert out["repair"] is False
    assert out["reasons"] == []


# --------------------------------------------------------- one lever each

def test_a_tighter_residual_cap_asks_for_repair():
    p = A.AdaptedV1(max_residual_shares=D("2"))
    out = p.wants_repair({"residual": "6.08", "residual_age_s": 5.0})
    assert out["repair"] is True
    assert out["reasons"] == ["RESIDUAL_ABOVE_CAP"]
    assert out["toward"] == "DOWN"          # long residual repairs with DOWN


def test_a_short_residual_repairs_the_other_way():
    p = A.AdaptedV1(max_residual_shares=D("2"))
    assert p.wants_repair({"residual": "-6.08"})["toward"] == "UP"


def test_a_residual_inside_the_cap_is_left_alone():
    p = A.AdaptedV1(max_residual_shares=D("10"))
    assert p.wants_repair({"residual": "6.08"})["repair"] is False


def test_an_aged_residual_asks_for_repair_on_age_alone():
    p = A.AdaptedV1(max_residual_age_s=60.0)
    out = p.wants_repair({"residual": "0.5", "residual_age_s": 61.0})
    assert out["reasons"] == ["RESIDUAL_TOO_OLD"]


def test_age_cannot_conjure_a_repair_out_of_a_flat_book():
    p = A.AdaptedV1(max_residual_age_s=60.0)
    assert p.wants_repair({"residual": "0",
                           "residual_age_s": 9999.0})["repair"] is False


def test_both_levers_name_both_reasons():
    p = A.AdaptedV1(max_residual_shares=D("2"), max_residual_age_s=60.0)
    out = p.wants_repair({"residual": "6.08", "residual_age_s": 61.0})
    assert out["reasons"] == ["RESIDUAL_ABOVE_CAP", "RESIDUAL_TOO_OLD"]


# ------------------------------------------------------------ repair bias

def test_repair_bias_prefers_the_side_that_shrinks_the_imbalance():
    p = A.AdaptedV1(repair_bias=True)
    st = {"residual": "6.08"}
    assert p.prefers(st, "DOWN") is True
    assert p.prefers(st, "UP") is False


def test_repair_bias_is_silent_when_flat():
    p = A.AdaptedV1(repair_bias=True)
    assert p.prefers({"residual": "0"}, "DOWN") is False


def test_bias_off_never_prefers():
    p = A.AdaptedV1()
    assert p.prefers({"residual": "6.08"}, "DOWN") is False


# ------------------------------------------------------------- comparison

def test_every_axis_is_reported_not_just_the_flattering_ones():
    out = A.compare({"matched_pnl": "7052", "residual_pnl": "-9749",
                     "total": "-2697", "max_residual": "10"},
                    {"matched_pnl": "6900", "residual_pnl": "-4000",
                     "total": "2900", "max_residual": "2"})
    assert set(out["axes"]) == set(A.COMPARISON_AXES)
    assert out["axes"]["residual_pnl"]["delta"] == "5749"
    assert out["axes"]["matched_pnl"]["delta"] == "-152"
    # an axis neither arm reported is present and empty, not omitted
    assert out["axes"]["incentives"] == {"native": None, "adapted": None}
