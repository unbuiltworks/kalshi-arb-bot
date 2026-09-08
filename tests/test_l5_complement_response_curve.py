"""The complement response curve, the sequential-pair test, and the A/B/C/D
adjudication. Outside claims are hypotheses; this pins what our tape said."""
from decimal import Decimal as D

from src import l5_complement_response_curve as R


# --- provenance discipline --------------------------------------------------

def test_the_outside_source_is_never_treated_as_evidence():
    assert "never evidence" in R.PROVENANCE
    assert R.IMPLEMENTS_OUTSIDE_STRATEGY is False
    assert R.AUTHORISES_MODEL_CHANGE == ()
    assert R.AUTHORISES_RESTART == ()


# --- the sequential-pair finding --------------------------------------------

def test_pairs_are_predominantly_sequential_not_simultaneous():
    assert R.pairs_are_predominantly_sequential() is True
    assert R.MOVEMENT_DOMINATED_SHARE == D("0.780")
    assert dict(R.GAP_SHARES)["same second"] == D("0.013")


def test_legs_are_a_median_47_seconds_apart():
    assert R.LEG_GAP_MEDIAN_S > D("30")


def test_profit_rises_with_the_wait():
    """An arbitrage wants both legs at once; this one pays for patience."""
    assert R.profit_rises_with_the_wait() is True
    assert R.PROFIT_BY_GAP[0][1] < R.PROFIT_BY_GAP[-1][1]


def test_favourable_movement_exceeds_the_captured_profit():
    assert R.MEDIAN_FAVOURABLE_MOVE > R.MEDIAN_PAIR_PROFIT


def test_the_leg_pattern_matches_a_cheap_then_dear_sequence():
    for leg1, leg2, cost in R.LEG_PATTERN:
        assert cost < D("1.0")
        assert leg1 + leg2 == cost or abs((leg1 + leg2) - cost) < D("0.06")


def test_simultaneous_executability_is_labelled_unverifiable():
    assert R.SIMULTANEOUS_EXECUTABILITY == "UNVERIFIABLE"
    assert R.SPREAD_BUCKET_IS_AN_UPPER_BOUND is True


# --- the response curve -----------------------------------------------------

def test_cheaper_own_side_means_buy_more_dearer_means_complement():
    curve = {lab: same for lab, _, same, _ in R.RESPONSE_CURVE}
    assert curve["-5..-2c"] > D("0.6")      # got cheaper -> buy more
    assert curve["+2..+5c"] < D("0.4")      # got dearer  -> complement


def test_both_tails_soften_so_it_is_not_naive_momentum():
    curve = {lab: same for lab, _, same, _ in R.RESPONSE_CURVE}
    assert curve["<=-10c"] < curve["-5..-2c"]
    assert curve[">=+10c"] > curve["+2..+5c"]


# --- A/B/C ------------------------------------------------------------------

def test_movement_is_the_dominant_explanation():
    assert R.WINNER == "C"
    assert R.movement_beats_pair_cost_and_inventory() is True
    assert R.gain("C") > D("0.10")


def test_inventory_rebalancing_adds_nothing_and_slightly_hurts():
    assert R.inventory_adds_nothing() is True
    assert R.gain("B") < D("0.01")


def test_the_joint_model_is_not_better_than_A_plus_C():
    assert R.OOS_LOGLOSS["AC"] <= R.OOS_LOGLOSS["ABC"]


# --- price bands ------------------------------------------------------------

def test_cheap_residuals_lose_and_dear_residuals_win():
    d = dict(R.RESIDUAL_PNL_PER_UNIT)
    assert d["25-45c"] < D("-0.2")
    assert d["75-90c"] > D("0.1")


# --- queue ------------------------------------------------------------------

def test_maker_fills_are_adversely_selected_not_pre_positioned():
    assert R.maker_fills_are_adversely_selected() is True


def test_the_missing_evidence_for_the_queue_claim_is_named():
    assert len(R.QUEUE_EVIDENCE_REQUIRED) >= 3
    assert any("book snapshot" in e for e in R.QUEUE_EVIDENCE_REQUIRED)


# --- untested stays untested ------------------------------------------------

def test_incentives_are_untested_not_weakly_supported():
    assert R.D_INCENTIVE_STATUS == "UNTESTED_NOT_SOURCEABLE"
    assert len(R.D_REQUIRED) >= 2


def test_external_signal_is_untested():
    assert R.E_EXTERNAL_STATUS == "UNTESTED_NO_ALIGNED_DATA"


def test_every_outside_hypothesis_has_an_explicit_verdict():
    assert len(R.HYPOTHESIS_VERDICTS) == 6
    assert R.HYPOTHESIS_VERDICTS["2_sequential_pairs"] == "STRONGLY SUPPORTED"
    assert R.HYPOTHESIS_VERDICTS["1_pre_positioning"] == "NOT ESTABLISHED"
    assert R.HYPOTHESIS_VERDICTS["5_external_signal"] == "UNTESTED"
