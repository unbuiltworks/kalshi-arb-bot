"""Uncertainty and clustering for the adverse-selection estimate."""
import math

import pytest

from analysis.l5.adverse_selection_stats import (
    wilson, cluster_ratio, z_test_against, analyse, power_table,
    REPLAY_ESTIMATE, L5_EARLY_RATE)


def test_the_reference_rates_are_the_measured_ones():
    assert REPLAY_ESTIMATE == 0.81
    assert L5_EARLY_RATE == 0.57


# ------------------------------------------------------------- wilson

def test_wilson_brackets_the_point_estimate():
    lo, hi = wilson(8, 10)
    assert lo < 0.8 < hi


def test_wilson_stays_inside_zero_one_at_the_extremes():
    for k, n in ((0, 5), (5, 5), (0, 1), (1, 1)):
        lo, hi = wilson(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_narrows_with_n():
    w20 = wilson(16, 20); w200 = wilson(160, 200)
    assert (w200[1] - w200[0]) < (w20[1] - w20[0])


def test_wilson_of_nothing_is_not_a_number():
    lo, hi = wilson(0, 0)
    assert math.isnan(lo) and math.isnan(hi)


# ------------------------------------------------------- clustering

def test_one_trial_per_cluster_reduces_to_the_binomial_se():
    """The two estimators must agree in the window-level case."""
    cl = [(1, 1)] * 8 + [(0, 1)] * 2
    r = cluster_ratio(cl)
    assert r["p"] == pytest.approx(0.8)
    binom = math.sqrt(0.8 * 0.2 / 10)
    assert r["se"] == pytest.approx(binom, rel=0.12)
    assert r["design_effect"] == pytest.approx(1.0, rel=0.25)


def test_correlated_clusters_inflate_the_se_above_naive():
    """Perfectly correlated windows: 10 fills each, all-or-nothing."""
    cl = [(10, 10)] * 8 + [(0, 10)] * 2
    r = cluster_ratio(cl)
    assert r["p"] == pytest.approx(0.8)
    naive = math.sqrt(0.8 * 0.2 / 100)
    assert r["se"] > naive * 2, (r["se"], naive)
    assert r["design_effect"] > 4


def test_a_single_cluster_yields_no_se_rather_than_zero():
    r = cluster_ratio([(3, 4)])
    assert r["p"] == pytest.approx(0.75)
    assert r["se"] is None, "one window cannot estimate between-window spread"


def test_empty_clusters_are_none_not_zero():
    r = cluster_ratio([])
    assert r["p"] is None and r["se"] is None


def test_zero_trial_clusters_are_dropped_from_m():
    r = cluster_ratio([(0, 0), (1, 1), (0, 1)])
    assert r["m_clusters"] == 2 and r["n"] == 2


# ----------------------------------------------------------- testing

def test_a_rate_at_the_reference_has_z_zero():
    t = z_test_against(0.81, 0.05, 0.81)
    assert t["z"] == pytest.approx(0.0)
    assert t["p_value"] == pytest.approx(1.0, abs=1e-9)


def test_a_two_sigma_gap_is_about_five_percent():
    t = z_test_against(0.57, (0.81 - 0.57) / 1.959963985, 0.81)
    assert t["p_value"] == pytest.approx(0.05, abs=0.005)


def test_no_se_gives_no_verdict_rather_than_a_false_one():
    t = z_test_against(0.6, None, 0.81)
    assert t["z"] is None and t["p_value"] is None


# ----------------------------------------------------------- analyse

def W(lost, uncomp=0, uncomp_lost=0):
    return {"residual_side_lost": lost, "uncomplemented_fills": uncomp,
            "uncomplemented_lost": uncomp_lost}


def test_analyse_counts_windows_not_fills_for_the_headline():
    rows = [W(True, 5, 5)] * 8 + [W(False, 5, 0)] * 2
    a = analyse(rows)
    assert a["window_level"]["windows_with_residual"] == 10
    assert a["window_level"]["residual_lost"] == 8
    assert a["window_level"]["p_hat"] == pytest.approx(0.8)


def test_unresolved_windows_are_excluded_and_counted():
    rows = [W(True), W(None), W(False), W(None)]
    a = analyse(rows)
    assert a["window_level"]["windows_with_residual"] == 2
    assert a["excluded_unresolved_windows"] == 2


def test_analyse_tests_against_both_reference_rates():
    rows = [W(True, 1, 1)] * 17 + [W(False, 1, 0)] * 13   # 57%
    a = analyse(rows)["window_level"]
    assert a["vs_l5_early_57"]["p0"] == 0.57
    assert a["vs_replay_81"]["p0"] == 0.81
    # at 57/100 of 30 windows the replay's 81% should look unlikely
    assert a["vs_replay_81"]["p_value"] < 0.05
    assert a["vs_l5_early_57"]["p_value"] > 0.5


def test_a_flat_programme_yields_no_headline_estimate():
    a = analyse([W(None), W(None)])
    assert a["window_level"]["p_hat"] is None
    assert a["window_level"]["vs_replay_81"] is None


def test_the_fill_level_estimator_is_labelled_not_comparable():
    a = analyse([W(True, 3, 2), W(False, 4, 1)])
    assert "not comparable" in a["fill_level_clustered"]["labels"].lower()


def test_the_window_estimator_is_labelled_comparable():
    a = analyse([W(True, 1, 1)])
    assert "comparable" in a["window_level"]["labels"].lower()


# ------------------------------------------------------------- power

def test_power_is_honest_about_forty_to_sixty_windows():
    """40-60 AUTHORIZED windows yield ~58% of that in residual observations.

    At 23-35 observations the separation is 1.8-2.3 sigma, NOT the ~2.9 an
    earlier note claimed by confusing windows with observations.
    """
    t = {r["windows_with_residual"]: r for r in power_table()}
    assert t[20]["sigma_separating_57_from_81"] < 2.0
    assert t[30]["sigma_separating_57_from_81"] < 2.2
    assert t[60]["sigma_separating_57_from_81"] > 2.8
    for r in power_table():
        assert r["half_width_95_at_57pct"] > r["half_width_95_at_81pct"]
