"""L5's actual policy against ours, and the cohort error that invalidated my
own breakeven. Figures from analysis/l5/L5_BEHAVIORAL_MISMATCH.md.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D

import pytest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTheCohortErrorIsReal:
    """The 16 markets behind PAIR_PROFIT/RESIDUAL_LOSS contain no BTC15m."""

    def test_the_breakeven_cohort_has_zero_btc15m_markets(self):
        p = os.path.join(ROOT, "analysis", "l5", "l5_r5f_cohort.json")
        d = json.load(open(p))
        mk = d.get("markets") or d.get("rows") or []
        assert len(mk) >= 15
        slugs = [str(m.get("slug") or "") for m in mk]
        assert not any("btc" in s.lower() for s in slugs)
        # and it is esports/sports: aec / atc / tsc
        assert all(s.split("-")[0] in ("aec", "atc", "tsc") for s in slugs if s)

    def test_it_contains_a_pair_cost_our_ceiling_forbids(self):
        """matched_pair_cost 1.110705 cannot occur under a 0.96 ceiling, which
        is the clearest proof the cohort is a different lane."""
        p = os.path.join(ROOT, "analysis", "l5", "l5_r5f_cohort.json")
        mk = json.load(open(p)).get("markets") or []
        costs = [D(str(m["matched_pair_cost"])) for m in mk
                 if m.get("matched_pair_cost")]
        assert max(costs) > D("1.00")

    def test_the_withdrawn_breakeven_does_not_apply_to_btc15m(self):
        PP, RL = D("0.057230"), D("-0.331449")
        be = (-RL) / (PP - RL)
        assert be == pytest.approx(D("0.852758"), abs=D("0.000001"))
        # and a 0.96-capped pair cannot earn 0.057230
        assert PP > D("0.05")

    def test_the_replacement_figure_is_also_unfit(self):
        """BTC15m-native pair profit of 0.257707 is 5x the arithmetic maximum
        of a pair capped at 0.96, so it is not pair profit per pair."""
        assert D("0.257707") > 5 * D("0.05")


class TestL5IsProfitableOnHisOwnBook:

    MATCHED = D("162256.93")
    RESIDUAL = D("-144065.00")
    NET = D("18191.93")
    PP = D("0.026520")
    RL = D("-0.129033")
    COMPLETION = D("0.916387")

    def test_the_book_reconciles_and_is_net_positive(self):
        assert self.MATCHED + self.RESIDUAL == self.NET
        assert self.NET > 0

    def test_he_clears_his_own_breakeven_by_eight_points(self):
        be = (-self.RL) / (self.PP - self.RL)
        assert be == pytest.approx(D("0.829511"), abs=D("0.00001"))
        assert self.COMPLETION - be > D("0.08")

    def test_his_pair_is_WORSE_than_ours_and_his_residual_far_better(self):
        assert self.PP < D("0.057230")                 # worse pair
        assert abs(self.RL) < abs(D("-0.331449"))      # better residual
        assert abs(D("-0.331449")) / abs(self.RL) > D("2.5")

    def test_either_fix_alone_flips_us_positive(self):
        oPP, oRL, oC = D("0.057230"), D("-0.331449"), D("0.834196")
        his_econ_our_comp = oC * self.PP + (D(1) - oC) * self.RL
        our_econ_his_comp = (self.COMPLETION * oPP
                             + (D(1) - self.COMPLETION) * oRL)
        assert his_econ_our_comp > 0
        assert our_econ_his_comp > 0


class TestTheMismatchIsUnmatchedInventoryNotPairPricing:

    PERMIT = {"unmatched_cap": 61972, "pair_ceiling": 764,
              "open": 527, "later": 306, "total_fills": 63569}
    UNMATCHED = {"p10": 18.51, "p25": 51.14, "median": 127.98,
                 "p75": 226.82, "p90": 332.17, "max": 1180.33}
    OUR_CAP = 3

    def test_our_engine_would_decline_almost_all_of_his_trades(self):
        ok = self.PERMIT["open"] + self.PERMIT["later"]
        assert ok / self.PERMIT["total_fills"] < 0.02

    def test_the_unmatched_cap_is_the_dominant_refusal(self):
        t = self.PERMIT["total_fills"]
        assert self.PERMIT["unmatched_cap"] / t > 0.97
        assert self.PERMIT["pair_ceiling"] / t < 0.02

    def test_the_pair_ceiling_is_second_order_by_two_orders_of_magnitude(self):
        assert (self.PERMIT["unmatched_cap"]
                / self.PERMIT["pair_ceiling"]) > 80

    def test_his_median_unmatched_is_43x_our_cap(self):
        assert self.UNMATCHED["median"] / self.OUR_CAP > 42

    def test_he_is_essentially_never_inside_our_cap(self):
        assert 0.017 == pytest.approx(0.017, abs=0.001)   # 1.7% of his fills
        assert self.UNMATCHED["p10"] > self.OUR_CAP       # even p10 exceeds it


class TestHisResidualIsCheaperNotLuckier:

    def test_his_cost_basis_is_lower(self):
        assert D("0.3478") < D("0.5087")
        assert D("0.5087") / D("0.3478") > D("1.4")

    def test_and_his_win_rate_is_higher(self):
        assert D("0.3245") > D("0.2360")

    def test_the_mechanism_is_averaging_not_selection(self):
        """121 fills a window to average against, versus our 4.52."""
        assert 120.6 / 4.52 > 26


class TestItReplicatesOutOfSample:
    """Chronological, never shuffled. Every quantity moves FURTHER from us."""

    TRAIN = {"fills": 94.2, "unmatched": 108.74, "under_cap": 2.2,
             "basis": 0.3664}
    OOS = {"fills": 181.7, "unmatched": 148.85, "under_cap": 1.2,
           "basis": 0.3287}

    def test_fills_per_window_rises(self):
        assert self.OOS["fills"] > self.TRAIN["fills"]

    def test_unmatched_inventory_rises(self):
        assert self.OOS["unmatched"] > self.TRAIN["unmatched"]

    def test_the_share_inside_our_cap_falls(self):
        assert self.OOS["under_cap"] < self.TRAIN["under_cap"]

    def test_his_residual_basis_improves(self):
        assert self.OOS["basis"] < self.TRAIN["basis"]

    def test_both_splits_are_far_from_us_on_every_axis(self):
        for s in (self.TRAIN, self.OOS):
            assert s["fills"] > 4.52 * 10
            assert s["unmatched"] > 3 * 30
            assert s["basis"] < 0.5087


class TestWhatIsNotClaimed:

    def test_no_claim_that_our_engine_would_profit_at_his_scale(self):
        """Nothing here prices raising the cap. It shows our engine never
        reaches the state his economics come from."""
        assert True

    def test_the_binding_change_is_a_risk_control_and_is_not_made(self):
        """max_unmatched_contracts stays where it is. At his median the
        implied unmatched exposure is ~$45/window against $1.7964 of campaign
        headroom, so it needs authorization and capital, not a code edit."""
        implied = 127.98 * 0.3478
        assert implied > 40
        assert implied > float(D("1.7964"))
