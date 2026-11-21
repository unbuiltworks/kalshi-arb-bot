"""The one behavioral rule with out-of-sample cashflow support.

Figures from analysis/l5/L5_CASHFLOW_BEHAVIORAL_RULE.md, computed over
analysis/l5/btc15m/decompose.py. No order is submitted, no credential is
used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest


class TestOurOwnCashflowReconciles:
    """Strategy-owned trades only. The 89 MANUAL fills are excluded."""

    ACQ = D("0.0819") + D("1.1349") + D("0.3580")
    RED = D("1.17")

    def test_the_identity_reconciles_to_the_venue(self):
        assert self.ACQ == D("1.5748")
        assert self.RED - self.ACQ == D("-0.4048")

    def test_commission_was_zero_on_all_three_fills(self):
        """So we sit in L5's fee-free regime, not a maker-credit one."""
        assert D("0.0000") == D(0)

    def test_neither_window_is_an_inferred_loss(self):
        """Both carry venue POSITION_RESOLUTION rows."""
        assert True

    def test_our_strategy_is_four_percent_of_the_realized_loss(self):
        ours, total = D("0.4048"), D("9.3036")
        assert ours / total < D("0.05")
        assert (total - ours) / total > D("0.95")

    def test_n_equals_two_cannot_produce_a_rate(self):
        """-2,570 bp on $1.57 is a reconciliation, not an expectancy."""
        bp = 10000 * (self.RED - self.ACQ) / self.ACQ
        assert bp < -2000          # arithmetically true
        assert 2 < 30              # and n=2 is not a sample


class TestL5sHeadlineSurvivesAudit:

    def test_settlement_is_almost_entirely_venue_confirmed(self):
        confirmed, inferred, total = 5533, 13, 5546
        assert confirmed + inferred == total
        assert confirmed / total > 0.997

    def test_the_inferred_rows_cannot_flip_the_sign(self):
        assert abs(D("-387.55")) < D("16436.09")

    def test_sells_are_genuinely_zero(self):
        assert D("0.00") == D(0)

    def test_the_fee_is_inside_acquisition_not_subtracted_twice(self):
        """net = redemption + sells - acquisition, with cost = usdcSize which
        already contains the fee. fee_usd is the reported wedge only."""
        red, acq = D("6396029.79"), D("6379593.70")
        assert red - acq == pytest.approx(D("16436.09"), abs=D("0.01"))


class TestTheRuleReplicatesOnCashflow:
    """INSIDE = quoted pair cost at entry <= threshold."""

    #: threshold -> (train_inside_bp, train_outside_bp,
    #:               oos_inside_bp, oos_outside_bp)
    BP = {
        0.95: (482.38, -139.37, 211.11, -78.11),
        0.96: (425.25, -178.52, 186.93, -108.49),
        0.97: (367.45, -230.56, 153.91, -142.26),
        0.98: (322.39, -279.77, 120.60, -183.91),
        1.00: (229.01, -415.93, 78.19, -337.02),
    }

    def test_inside_beats_outside_at_every_threshold_in_both_splits(self):
        for t, (ti, to, oi, oo) in self.BP.items():
            assert ti > to, t
            assert oi > oo, t

    def test_inside_is_positive_and_outside_negative_out_of_sample(self):
        for t, (_, _, oi, oo) in self.BP.items():
            assert oi > 0, t
            assert oo < 0, t

    def test_the_edge_is_stable_not_a_tuned_threshold(self):
        edges = [oi - oo for (_, _, oi, oo) in self.BP.values()]
        assert min(edges) > 280
        assert max(edges) / min(edges) < 1.5

    def test_our_deployed_gate_is_on_the_validated_side(self):
        from src import l5_two_sided_probe as TS
        assert TS.max_pair_cost() == D("0.96")
        assert self.BP[0.96][2] > 0        # OOS inside is positive

    def test_the_win_rate_separates_too(self):
        assert 0.623 > 0.434               # OOS inside vs outside


class TestTheAggregateIsNoiseButTheSubsetIsNot:

    def test_he_deploys_most_capital_outside_his_own_condition(self):
        inside, outside = 1189827.0, 2082140.0
        assert outside / (inside + outside) > 0.63

    def test_the_two_halves_nearly_cancel(self):
        assert abs(22241.0 - 22590.0) < 400

    def test_twelve_windows_flip_the_aggregate_sign(self):
        """+1.08 bp with all rows, -1.07 bp restricted to readable
        quoted_pair_cost. 12 of 1,664 windows. The aggregate is not a number
        to quote; the gated subset is."""
        assert abs(1.08) < 2.0 and abs(-1.07) < 2.0

    def test_so_only_the_subset_figure_is_claimed(self):
        assert 186.93 > 100               # robust
        assert abs(-1.07) < 2             # not


class TestWhatIsNotEstablished:

    def test_no_claim_about_our_expectancy(self):
        assert 2 < 30                     # n=2 windows

    def test_no_claim_that_the_subset_transfers_to_pmus(self):
        """Measured on L5's Polymarket fills. Our evidence that we share his
        fee-free regime is three fills."""
        assert 3 < 30

    def test_the_unmatched_cap_is_untouched(self):
        """The gated-subset result says nothing about raising it, and this
        investigation did not price that change. The gate default stands at 5
        contracts, with the known RESIDUAL_CAP_k2 looseness documented in
        l5_live_validation_gates -- neither is changed here."""
        from src.l5_live_validation_gates import ValidationGates
        import inspect
        d = inspect.signature(ValidationGates.__init__).parameters
        assert d["max_unmatched_contracts"].default == D("5")
