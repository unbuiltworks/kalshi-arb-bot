"""Statistics helpers, checked against values with known closed forms."""

import math

import pytest

from src.l5 import stats


class TestDescriptive:
    def test_quantile_interpolates_linearly(self):
        values = [1, 2, 3, 4]
        assert stats.quantile(values, 0.0) == 1
        assert stats.quantile(values, 0.25) == pytest.approx(1.75)
        assert stats.quantile(values, 0.5) == pytest.approx(2.5)
        assert stats.quantile(values, 1.0) == 4

    def test_median_of_odd_and_even_lengths(self):
        assert stats.median([3, 1, 2]) == 2
        assert stats.median([4, 1, 3, 2]) == pytest.approx(2.5)

    def test_empty_inputs_return_none_not_zero(self):
        assert stats.mean([]) is None
        assert stats.median([]) is None
        assert stats.stdev([1]) is None
        assert stats.describe([])["n"] == 0

    def test_sample_stdev(self):
        assert stats.stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13808993, rel=1e-6)


class TestRanks:
    def test_ties_share_their_average_rank(self):
        assert stats.rank_average([1, 2, 2, 3]) == [1.0, 2.5, 2.5, 4.0]

    def test_all_tied_values_share_the_midpoint(self):
        assert stats.rank_average([5, 5, 5]) == [2.0, 2.0, 2.0]

    def test_unsorted_input_keeps_positional_alignment(self):
        assert stats.rank_average([30, 10, 20]) == [3.0, 1.0, 2.0]


class TestDistributions:
    def test_normal_survival(self):
        assert stats.normal_sf(0.0) == pytest.approx(0.5)
        assert stats.normal_sf(1.959963985) == pytest.approx(0.025, abs=1e-6)

    def test_incomplete_beta_against_a_closed_form(self):
        # I_0.5(2,3) = 11/16
        assert stats.betainc(2, 3, 0.5) == pytest.approx(0.6875, abs=1e-9)
        assert stats.betainc(2, 3, 0.0) == 0.0
        assert stats.betainc(2, 3, 1.0) == 1.0

    def test_t_survival_matches_published_critical_values(self):
        assert stats.student_t_sf(2.228, 10) == pytest.approx(0.025, abs=1e-4)
        assert stats.student_t_sf(0.0, 10) == pytest.approx(0.5)
        assert stats.student_t_sf(-2.228, 10) == pytest.approx(0.975, abs=1e-4)


class TestMannWhitney:
    def test_completely_separated_samples(self):
        result = stats.mann_whitney_u([1, 2, 3, 4, 5], [6, 7, 8, 9, 10])
        assert result["u"] == 0.0
        assert result["rank_biserial"] == pytest.approx(-1.0)
        assert result["p"] == pytest.approx(0.0121858, rel=1e-4)

    def test_identical_samples_show_no_effect(self):
        result = stats.mann_whitney_u([1, 2, 3, 4], [1, 2, 3, 4])
        assert result["rank_biserial"] == pytest.approx(0.0)
        assert result["p"] == pytest.approx(1.0)

    def test_ties_do_not_break_the_variance(self):
        result = stats.mann_whitney_u([1, 1, 1, 2], [1, 1, 2, 2])
        assert result["p"] is not None and 0.0 <= result["p"] <= 1.0

    def test_empty_group_is_reported_not_raised(self):
        result = stats.mann_whitney_u([], [1, 2, 3])
        assert result["u"] is None and result["p"] is None


class TestSpearman:
    def test_perfect_monotonic_relationship(self):
        result = stats.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
        assert result["rho"] == pytest.approx(1.0)
        assert result["p"] == 0.0

    def test_known_partial_correlation(self):
        result = stats.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5])
        assert result["rho"] == pytest.approx(0.8)
        assert result["p"] == pytest.approx(0.10408, rel=1e-3)

    def test_inverse_relationship(self):
        assert stats.spearman([1, 2, 3], [3, 2, 1])["rho"] == pytest.approx(-1.0)

    def test_constant_input_has_no_correlation(self):
        assert stats.spearman([1, 1, 1, 1], [1, 2, 3, 4])["rho"] is None

    def test_too_few_points(self):
        assert stats.spearman([1, 2], [1, 2])["rho"] is None


class TestProportionTests:
    def test_two_proportion_z(self):
        result = stats.two_proportion_z(30, 100, 15, 100)
        assert result["diff"] == pytest.approx(0.15)
        assert result["z"] == pytest.approx(2.54000254, rel=1e-6)
        assert result["p"] == pytest.approx(0.011085, rel=1e-4)

    def test_identical_proportions(self):
        result = stats.two_proportion_z(20, 100, 20, 100)
        assert result["z"] == 0.0 and result["p"] == pytest.approx(1.0)

    def test_sign_test_against_a_fair_coin(self):
        result = stats.binomial_sign_test(60, 100)
        assert result["rate"] == pytest.approx(0.6)
        assert result["z"] == pytest.approx(1.9)
        assert result["p"] == pytest.approx(0.05743, rel=1e-3)

    def test_sign_test_with_no_trials(self):
        assert stats.binomial_sign_test(0, 0)["p"] is None


class TestBootstrap:
    def test_is_deterministic_for_a_fixed_seed(self):
        values = [float(v) for v in range(50)]
        a = stats.bootstrap_ci(values, stats.median, 500, seed=7)
        b = stats.bootstrap_ci(values, stats.median, 500, seed=7)
        assert a == b

    def test_interval_brackets_the_point_estimate(self):
        values = [float(v) for v in range(100)]
        result = stats.bootstrap_ci(values, stats.median, 800, seed=3)
        assert result["lo"] <= result["point"] <= result["hi"]

    def test_degenerate_inputs(self):
        assert stats.bootstrap_ci([], stats.median, 100, seed=1)["point"] is None
        single = stats.bootstrap_ci([5.0], stats.median, 100, seed=1)
        assert single["point"] == 5.0 and single["lo"] is None


class TestVerdict:
    @pytest.mark.parametrize(
        "p,n,expected",
        [
            (0.0001, 100, "strong evidence"),
            (0.005, 100, "moderate evidence"),
            (0.03, 100, "weak evidence"),
            (0.4, 100, "no evidence"),
            (0.0001, 5, "insufficient data"),
            (None, 100, "not computable"),
        ],
    )
    def test_labels_are_conservative(self, p, n, expected):
        assert stats.verdict(p, n, 20) == expected
