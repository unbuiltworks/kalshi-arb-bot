"""Why the October 23 campaign lost money, and why no exit policy fixes it.

Every number is measured and sourced in
`analysis/l5/residual_exit/profitability_bridge.py`. No order is submitted, no
credential is used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.residual_exit import profitability_bridge as PB


class TestTheUnitEconomicsSetTheBar:
    """A residual costs 5.79x what a pair earns, which is the whole problem."""

    def test_the_loss_to_gain_ratio(self):
        r = abs(PB.RESIDUAL_LOSS) / PB.PAIR_PROFIT
        assert D("5.7") < r < D("5.9")

    def test_breakeven_completion_is_845_percent(self):
        assert PB.breakeven_p() == pytest.approx(D("0.845160"), abs=D("0.000001"))

    def test_the_observed_completion_is_BELOW_breakeven(self):
        assert PB.OBSERVED_P < PB.breakeven_p()
        assert PB.ev_per_leg(PB.OBSERVED_P) < 0

    def test_so_the_policy_was_negative_before_any_execution_detail(self):
        assert PB.ev_per_leg(PB.OBSERVED_P) == pytest.approx(
            D("-0.004262"), abs=D("0.000001"))


class TestTheCompletionAssumptionsReconcile:
    """0.144 and 0.72 are not in conflict -- they are different denominators.
    0.144 is per complement ORDER at depth 2; 0.72 is per LEG compounded over
    the permitted accumulation rounds. 1-(1-0.144)^8 = 0.712."""

    def test_the_72_percent_figure_is_the_depth2_rate_over_eight_rounds(self):
        assert 1 - (1 - PB.DEPTH2_FILL) ** 8 == pytest.approx(0.7117, abs=0.001)

    def test_a_single_attempt_is_catastrophically_negative(self):
        assert PB.ev_per_leg(PB.DEPTH2_FILL) < D("-0.27")

    def test_the_campaign_placed_one_complement_per_window_not_eight(self):
        """0230z and 0300z each placed exactly one complement, which was
        cancelled unfilled. So the realized assumption was 0.144, not 0.72."""
        assert PB.ev_per_leg(PB.DEPTH2_FILL) < PB.ev_per_leg(0.72)


class TestAttemptsAreNotIndependentAndThatKillsTheLever:
    """The only lever with a positive story is more complement attempts. It
    works under independence and fails under the measured decay."""

    def test_the_measured_rate_decays_by_attempt_number(self):
        assert PB.ATTEMPT_DECAY[0] == 0.220
        assert PB.ATTEMPT_DECAY[3] == 0.085
        assert PB.ATTEMPT_DECAY[3] < PB.ATTEMPT_DECAY[0] / 2

    def test_independence_says_twelve_attempts_breaks_even(self):
        s = PB.fill_assumptions()["scenarios"]["independent_at_depth2_fill"]
        assert s["attempts_to_breakeven"] == 12

    def test_the_measured_decay_at_our_level_NEVER_breaks_even(self):
        s = PB.fill_assumptions()["scenarios"][
            "measured_decay_scaled_to_our_attempt1"]
        assert s["attempts_to_breakeven"] is None
        assert s["completion_by_attempt"][16] < PB.breakeven_p()

    def test_so_raising_the_attempt_budget_is_not_supported(self):
        a = PB.fill_assumptions()["scenarios"][
            "measured_decay_scaled_to_our_attempt1"]
        assert D(a["ev_by_attempt"][16]) < 0


class TestTheCeilingCurveClosesFromBothEnds:
    """Tighter ceiling -> deeper quote -> fill rate too low.
    Looser ceiling -> shallower quote -> gross edge too small.
    No point on it has a measured fill rate above its own requirement."""

    def test_the_live_ceiling_forces_depth_two(self):
        r = {x["ceiling"]: x for x in PB.ceiling_curve()}
        assert r["0.96"]["min_depth_ticks"] == 2
        assert r["0.96"]["gross_edge"] == "0.05"

    def test_loosening_the_ceiling_raises_the_requirement(self):
        r = {x["ceiling"]: x for x in PB.ceiling_curve()}
        assert (r["0.99"]["breakeven_completion"]
                > r["0.96"]["breakeven_completion"])
        assert (r["0.99"]["need_per_attempt_at_8"]
                > r["0.96"]["need_per_attempt_at_8"])

    def test_every_ceiling_needs_more_than_the_measured_fill_rate(self):
        for x in PB.ceiling_curve():
            assert x["need_per_attempt_at_8"] > PB.DEPTH2_FILL, x["ceiling"]

    def test_twelve_rounds_at_the_live_ceiling_is_exactly_the_measured_rate(self):
        """The one coincidence worth naming: at 12 rounds the requirement is
        14.40%, which IS the measured depth-2 rate. It is why independence
        looked survivable, and the decay is why it is not."""
        r = {x["ceiling"]: x for x in PB.ceiling_curve()}
        assert r["0.96"]["need_per_attempt_at_12"] == pytest.approx(
            PB.DEPTH2_FILL, abs=0.0005)


class TestTheCampaignBridgeReconcilesToTheVenue:

    def test_it_reconciles_exactly(self):
        b = PB.campaign_bridge()
        assert b["reconciles"] is True
        assert D(b["realized"]) == D("-0.4048")

    def test_planned_edge_was_positive(self):
        assert D(PB.campaign_bridge()["planned_edge_if_both_paired"]) > 0

    def test_four_fifths_of_the_shortfall_is_the_missed_complement(self):
        a = PB.campaign_bridge()["attribution_of_shortfall"]
        assert a["missed_complement_to_settlement"] > 0.80

    def test_fees_contributed_nothing(self):
        b = PB.campaign_bridge()
        assert b["fees_charged_by_venue"] == "0.0000"
        assert b["attribution_of_shortfall"]["fees"] == 0.0

    def test_queue_adverse_selection_is_not_claimed(self):
        """No complement ever filled, so no fill was adversely selected.
        Naming it as a loss bucket would be inventing a mechanism."""
        a = PB.campaign_bridge()["attribution_of_shortfall"]
        assert "no complement ever filled" in a["queue_adverse_selection"]

    def test_the_defensive_cross_REDUCED_the_0230z_loss(self):
        """It is a tail control that worked, not a cause of the loss:
        -0.0468 crossed against -0.0819 held."""
        held = -(D("0.07") * D("1.17"))
        crossed = (D(1) - (D("0.07") + D("0.97"))) * D("1.17")
        assert crossed > held


class TestNoCandidateIsPromoted:
    """Every lever examined is negative or inconclusive, so the engine's
    economics are left alone. The only change shipped is a TIGHTENING of a
    control, which has no economic effect."""

    def test_the_verdict_is_negative(self):
        assert "negative expectancy" in PB.report()["verdict"]

    def test_no_ceiling_change_is_supported(self):
        assert all(x["need_per_attempt_at_8"] > PB.DEPTH2_FILL
                   for x in PB.ceiling_curve())

    def test_no_attempt_budget_change_is_supported(self):
        assert PB.fill_assumptions()["scenarios"][
            "measured_decay_scaled_to_our_attempt1"][
            "attempts_to_breakeven"] is None
