"""Tests for the censored fill model.

Three silent measurement failures were shipped before this module, and each
produced convincing output rather than a crash: observation that stopped
after five rows and made a 4-interval window look like a result; a field
(`lastTradePx`) that was never parsed, so a classification was permanently
false; and a metric that was arithmetically `1 - spread` and therefore
measured nothing it claimed to. The last three sections here exist
specifically to make those shapes fail loudly in this module.
"""

from decimal import Decimal as D

import pytest

from src.pmus_fill_model import (L5_MEDIAN_COMPLEMENT_GAP_S, build,
                                 estimable_horizon, expected_economics,
                                 kaplan_meier, leg_curves, observation,
                                 survival_at)


def order(rel, rest, filled_qty="0"):
    return {"relation": rel, "rest_s": rest, "filled_qty": filled_qty}


# ------------------------------ censoring ------------------------------

def test_a_cancelled_order_is_censored_not_a_permanent_no_fill():
    """The whole estimator. A naive fills/orders ratio counts an order
    cancelled at 2s as evidence that 30s would also have failed; it is not.
    """
    obs = [(2.0, False)] * 90 + [(10.0, True)] * 10
    km = kaplan_meier(obs)
    naive = 10 / 100
    assert km[-1]["p_fill_by_t"] > naive * 2, (km[-1], naive)
    assert km[-1]["at_risk"] == 10       # the 90 early cancels have left


def test_risk_set_shrinks_as_orders_leave():
    km = kaplan_meier([(1.0, True), (5.0, True), (9.0, True), (9.0, False)])
    assert [r["at_risk"] for r in km] == [4, 3, 2]


def test_survival_is_monotone_non_decreasing_in_t():
    km = kaplan_meier([(t, True) for t in (1, 2, 3, 4, 5)] + [(6.0, False)])
    ps = [r["p_fill_by_t"] for r in km]
    assert all(b >= a for a, b in zip(ps, ps[1:]))


def test_no_fills_at_all_gives_an_empty_curve_not_a_zero_claim():
    assert kaplan_meier([(5.0, False), (9.0, False)]) == []


# --- GUARD 1: a truncated observation window must not read as a result ---

def test_beyond_the_observed_horizon_returns_none_not_the_last_value():
    """Failure mode 1 reproduced: a 30s curve flat-extrapolated to 120s looks
    exactly like a measurement. It must be None."""
    km = kaplan_meier([(10.0, True), (30.0, True)])
    hz = estimable_horizon([(10.0, True), (30.0, True)])
    assert survival_at(km, 30.0, hz) is not None
    assert survival_at(km, 60.0, hz) is None
    assert survival_at(km, 120.0, hz) is None


def test_horizon_is_derived_from_data_not_hardcoded():
    assert estimable_horizon([(1.0, True), (7.5, False)]) == 7.5
    assert estimable_horizon([]) == 0.0


def test_the_real_campaign_cannot_estimate_60s_or_120s():
    """Pinned against the actual shape of our data: nothing rested past ~32s."""
    orders = [order("open", 5.0, "1"), order("open", 30.5, "0"),
              order("complete", 30.0, "0"), order("complete", 12.0, "2")]
    rep = build(orders)
    assert rep["unestimable_horizons_s"] == [60.0, 120.0]
    for leg in rep["legs"].values():
        assert leg["p_fill"]["60"] is None and leg["p_fill"]["120"] is None


def test_l5s_median_gap_is_flagged_as_beyond_our_evidence():
    rep = build([order("complete", 30.0, "1")])
    assert rep["l5_gap_is_beyond_our_evidence"] is True
    assert L5_MEDIAN_COMPLEMENT_GAP_S > rep["estimable_horizon_s"]


# --- GUARD 2: the labels must actually reach the computation ---

def test_flipping_the_fill_labels_changes_the_answer():
    """Failure mode 2 reproduced: a field that never arrives yields a constant.
    If the outcome labels were dropped, both of these would agree."""
    filled = [order("complete", 10.0, "5") for _ in range(10)] + \
        [order("complete", 30.0, "0")]
    unfilled = [order("complete", 10.0, "0") for _ in range(10)] + \
        [order("complete", 30.0, "0")]
    a = leg_curves(filled)["complement"]["p_fill"]["30"]
    b = leg_curves(unfilled)["complement"]["p_fill"]["30"]
    assert a is not None and a > 0.8, a
    # zero fills among orders that were at risk is a real Kaplan-Meier
    # estimate of 0.0 INSIDE the horizon -- distinct from None, which means
    # "outside the evidence". The two must never be conflated.
    assert b == 0.0, b


def test_first_leg_and_complement_are_estimated_separately():
    orders = ([order("open", 5.0, "1")] * 8 + [order("open", 30.5, "0")] * 2
              + [order("complete", 5.0, "1")] * 2 + [order("complete", 30.5, "0")] * 8)
    legs = leg_curves(orders)
    assert legs["first_leg"]["p_fill"]["30"] > legs["complement"]["p_fill"]["30"]
    assert legs["first_leg"]["n_orders"] == 10
    assert legs["complement"]["n_orders"] == 10


def test_extend_counts_as_a_first_leg():
    legs = leg_curves([order("extend", 5.0, "1")])
    assert legs["first_leg"]["n_orders"] == 1
    assert legs["complement"]["n_orders"] == 0


def test_an_order_without_a_rest_time_is_dropped_not_defaulted_to_zero():
    assert observation({"relation": "open", "filled_qty": "1"}) is None


# --- GUARD 3: the output must not be degenerate with a single input ---

def test_p_fill_is_not_a_function_of_rest_time_alone():
    """Failure mode 3 reproduced: `1 - spread` was a perfect function of one
    input and therefore measured nothing. Two populations with IDENTICAL rest
    times and different outcomes must give different answers."""
    same_t_mostly_filled = [order("complete", 10.0, "1")] * 9 + \
                           [order("complete", 30.5, "0")]
    same_t_mostly_unfilled = [order("complete", 10.0, "1")] + \
                             [order("complete", 30.5, "0")] * 9
    a = leg_curves(same_t_mostly_filled)["complement"]["p_fill"]["30"]
    b = leg_curves(same_t_mostly_unfilled)["complement"]["p_fill"]["30"]
    assert a != b and a > b, (a, b)


def test_the_model_carries_no_flow_term_it_did_not_calibrate():
    """Flow was never recorded in the campaign, so a flow coefficient here
    would be invented. Its absence is asserted so it cannot appear quietly."""
    rep = build([order("complete", 10.0, "1")])
    assert "no_flow_term" in rep
    blob = str(rep["legs"]).lower()
    assert "flow" not in blob, "a flow term appeared in a leg estimate"


# ------------------------------ the expectation ------------------------------

def test_an_unestimable_probability_propagates_as_none_never_as_zero():
    """A None that silently becomes 0.0 turns 'we cannot know' into a
    confident loss, and 1.0 turns it into a confident win."""
    e = expected_economics(None, 0.5, D("0.98"), D("0.40"))
    assert e["expected_usd"] is None and e["p_both"] is None
    e2 = expected_economics(0.5, None, D("0.98"), D("0.40"))
    assert e2["expected_usd"] is None


def test_expectation_prices_stranding_against_completion():
    e = expected_economics(0.8, 0.5, D("0.96"), D("0.30"))
    assert e["p_both"] == pytest.approx(0.40)
    assert e["p_stranded"] == pytest.approx(0.40)
    # 0.40*0.04 - 0.40*0.30 = 0.016 - 0.12
    assert e["expected_usd"] == pytest.approx(D("-0.104"))


def test_filling_the_first_leg_easily_is_a_liability_without_the_complement():
    """Written expecting the opposite, and the model was right.

    A wide dead book barely fills EITHER leg, so it barely ever strands: its
    expectation is near zero. A market that fills the first leg readily but
    completes only ~55% stranded 27% of the time, and at a 0.40 residual loss
    that dominates a 4c pair edge outright. So "easy to get filled" is not the
    property to select on -- CONDITIONAL completion is. This is the stranding
    mechanism from R5F, priced.
    """
    mirage = expected_economics(0.05, 0.02, D("0.31"), D("0.40"))
    eager = expected_economics(0.60, 0.55, D("0.96"), D("0.40"))
    assert mirage["p_both"] < 0.01
    assert eager["p_stranded"] > 0.25
    assert eager["expected_usd"] < mirage["expected_usd"]


def test_conditional_completion_is_what_improves_the_expectation():
    """Holding the first leg fixed, a higher complement probability must
    improve the expectation -- that is the selector we are looking for."""
    poor = expected_economics(0.60, 0.30, D("0.96"), D("0.40"))
    good = expected_economics(0.60, 0.90, D("0.96"), D("0.40"))
    assert good["expected_usd"] > poor["expected_usd"]


def test_incentives_and_fees_enter_the_expectation():
    base = expected_economics(0.5, 0.5, D("0.98"), D("0.10"))
    with_inc = expected_economics(0.5, 0.5, D("0.98"), D("0.10"),
                                  incentives=D("0.05"), fees=D("0.01"))
    assert with_inc["expected_usd"] - base["expected_usd"] == D("0.04")
