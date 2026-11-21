"""The BTC15m-native baseline, and the cohort error it replaces.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

import json
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = json.load(open(os.path.join(ROOT, "analysis", "l5", "btc15m",
                                   "native_baseline.json")))


class TestItReconcilesToCash:
    """The only metric that reconciles: redemption minus acquisition."""

    def test_every_split_reconciles_to_the_cashflow_identity(self):
        for k, v in BASE["splits"].items():
            assert v["reconciles"] is True, k

    def test_the_realized_net_is_16436_not_18192(self):
        a = BASE["splits"]["all"]
        assert a["net_cash_usd"] == pytest.approx(16436.09, abs=0.01)
        assert a["partition_sum"] == pytest.approx(18191.93, abs=0.01)
        assert a["partition_overstates_cash_by"] == pytest.approx(1755.84,
                                                                  abs=0.01)

    def test_so_the_18192_figure_was_an_artifact(self):
        a = BASE["splits"]["all"]
        assert a["partition_sum"] > a["net_cash_usd"]


class TestTheCohortErrorIsNamedAndGuarded:

    def test_the_contaminated_cohort_is_named_in_the_baseline(self):
        assert "l5_r5f_cohort.json" in BASE["contaminated_cohort_not_used"]
        assert "zero BTC15m" in BASE["contaminated_reason"]

    def test_the_capacity_curve_declares_it_is_not_btc15m(self):
        from src import l5_capacity_curve as CC
        assert CC.INSTRUMENT_CLASS != "BTC15m"
        assert "btc15m/native_baseline.py" in CC.NATIVE_BASELINE_FOR_BTC15M

    def test_the_baseline_declares_its_own_instrument_class(self):
        assert BASE["instrument_class"] == "BTC15m"


class TestL5sEdgeIsAlmostEntirelyInSample:

    def test_out_of_sample_is_one_basis_point(self):
        o = BASE["splits"]["oos"]
        assert o["bp_per_dollar"] < 2.0
        assert o["net_cash_usd"] < 400

    def test_train_is_fifty_times_better_than_oos(self):
        t, o = BASE["splits"]["train"], BASE["splits"]["oos"]
        assert t["bp_per_dollar"] / o["bp_per_dollar"] > 40

    def test_half_the_spend_is_out_of_sample_and_returns_nothing(self):
        t, o = BASE["splits"]["train"], BASE["splits"]["oos"]
        assert o["gross_acquisition_usd"] > t["gross_acquisition_usd"]
        assert o["net_cash_usd"] < t["net_cash_usd"] / 40


class TestTheFeeRegimeDecidesTheTarget:
    """Our own Oct 23 fills carry commission 0.0000 -- no fee and no credit --
    so L5's FEE-FREE figure is the right comparison, not his as-observed one
    and not a maker-credit scenario this venue has never paid us."""

    def test_most_of_his_fills_are_fee_free(self):
        assert BASE["splits"]["all"]["fee_bearing_fill_frac"] < 0.16

    def test_and_even_more_so_out_of_sample(self):
        assert BASE["splits"]["oos"]["fee_bearing_fill_frac"] < 0.05

    def test_the_fee_free_oos_target_is_about_seventeen_bp(self):
        o = BASE["splits"]["oos"]
        assert 15.0 < o["fee_free_bp_per_dollar"] < 20.0

    def test_more_fee_free_fills_did_NOT_mean_better_returns(self):
        """OOS has 4.9% bearing against TRAIN's 24.7% and earns 1 bp against
        52. So the fee explains the gap BETWEEN cohorts, not within BTC15m."""
        t, o = BASE["splits"]["train"], BASE["splits"]["oos"]
        assert o["fee_bearing_fill_frac"] < t["fee_bearing_fill_frac"]
        assert o["bp_per_dollar"] < t["bp_per_dollar"]


class TestTheEdgeIsTooConcentratedToBeABusiness:

    def test_the_top_one_percent_of_windows_exceeds_the_whole_oos_net(self):
        assert BASE["splits"]["oos"]["top_1pct_share_of_fee_free_net"] > 1.0

    def test_the_top_ten_percent_is_several_times_the_net(self):
        assert BASE["splits"]["oos"]["top_10pct_share_of_fee_free_net"] > 5.0

    def test_win_rate_is_near_a_coin_flip(self):
        for k in ("all", "oos"):
            assert 0.49 < BASE["splits"][k]["win_rate"] < 0.56
