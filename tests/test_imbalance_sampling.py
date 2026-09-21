"""Locks the canonical sampling rule in place permanently.

The rule cost us a false hypothesis once (graveyard #9 in the external-review
handoff). These tests make it mechanical so it cannot be lost again.
"""
import sys

import pytest

sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/pmus")

import imbalance_sampling as S


def test_floor_matches_the_published_table():
    """The numbers quoted in the handoff and the reports must stay reproducible."""
    for n, want in ((10, 0.246), (18, 0.185), (30, 0.144),
                    (50, 0.112), (100, 0.079), (1000, 0.025)):
        assert S.noise_floor(n) == pytest.approx(want, abs=0.002), n


def test_floor_falls_with_sample_size():
    vals = [S.noise_floor(n) for n in (10, 18, 30, 50, 100, 500, 1000)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_the_l5_reading_and_a_pmus_reading_are_not_the_same_claim():
    """0.083 across 58,312 fills is nearly all real. The same 0.083 across 18
    prints is BELOW what a fair coin shows and is evidence of nothing."""
    assert S.excess(0.083, 58312) > 0.07
    assert S.excess(0.083, 18) < 0.0


def test_raw_comparison_across_unequal_n_is_refused():
    with pytest.raises(ValueError):
        S.compare_raw(0.083, 18, 0.083, 58312)
    assert S.compare_raw(0.40, 18, 0.10, 18) == pytest.approx(0.30)


def test_imbalance_returns_none_for_no_flow_never_zero():
    assert S.imbalance(0.0, 0.0) is None
    assert S.imbalance(3.0, 1.0) == pytest.approx(0.5)


def test_excess_propagates_missing_rather_than_inventing_a_value():
    assert S.excess(None, 18) is None


def test_matched_sample_of_a_balanced_series_lands_on_the_floor():
    balanced = [0, 1] * 500
    d = S.matched_sample(balanced, 18, trials=3000)
    assert sum(d) / len(d) == pytest.approx(S.noise_floor(18), abs=0.02)


def test_matched_sample_of_a_skewed_series_sits_well_above_the_floor():
    skewed = [1] * 850 + [0] * 150
    d = S.matched_sample(skewed, 18, trials=3000)
    assert sum(d) / len(d) > S.noise_floor(18) + 0.3


def test_cluster_bootstrap_resamples_markets_not_rows():
    rows = ([{"slug": "a", "v": 1.0}] * 50) + ([{"slug": "b", "v": 0.0}] * 50)
    mean = lambda rs: (sum(r["v"] for r in rs) / len(rs)) if rs else None
    ci = S.cluster_bootstrap_ci(rows, mean, trials=800)
    # two markets with opposite constant values: resampling MARKETS must be able
    # to reach 0.0 and 1.0. Resampling rows never would.
    assert ci["lo"] == pytest.approx(0.0, abs=1e-9)
    assert ci["hi"] == pytest.approx(1.0, abs=1e-9)
    assert ci["n_clusters"] == 2


def test_cluster_bootstrap_refuses_a_single_cluster():
    assert S.cluster_bootstrap_ci([{"slug": "a", "v": 1.0}], lambda rs: 1.0) is None


def test_exact_branch_survives_large_n():
    """REGRESSION. The exact branch used 2.0 ** n, a float, which overflows
    above n = 1023 -- so every n from 1024 to 4000 raised OverflowError instead
    of returning a floor. Found while measuring a 693,112-print Kalshi tape."""
    for n in (1023, 1024, 2048, 4001, 20000):
        v = S.noise_floor(n)
        assert 0.0 < v < 1.0, (n, v)


def test_approximation_agrees_with_exact_at_the_boundary():
    """The normal approximation takes over at EXACT_MAX_N; it must not introduce
    a step change there."""
    n = S.EXACT_MAX_N
    exact = S.noise_floor(n)
    approx = (2.0 / (3.141592653589793 * n)) ** 0.5
    assert abs(exact - approx) / exact < 0.001


def test_very_large_n_floor_is_tiny_but_positive():
    """L5's 58,312-fill reading has an essentially zero floor -- which is why
    its 0.083 is real and an 18-print 0.083 is not."""
    assert S.noise_floor(58312) < 0.005
    assert S.noise_floor(58312) > 0.0


def test_aggregate_imbalance_does_not_net_across_markets():
    """REGRESSION. Two markets, each 100% one-sided in OPPOSITE directions, is
    total imbalance -- not zero. Pooling first gives 0.0 and reverses conclusions."""
    per_market = [(100.0, 0.0), (0.0, 100.0)]
    assert S.aggregate_imbalance(per_market) == pytest.approx(1.0)
    pooled = S.imbalance(100.0, 100.0)          # the WRONG form, for contrast
    assert pooled == pytest.approx(0.0)


def test_aggregate_imbalance_matches_pooled_when_markets_agree():
    """When every market leans the same way the two forms coincide, which is
    exactly why the bug survives casual checking."""
    per_market = [(80.0, 20.0), (160.0, 40.0)]
    assert S.aggregate_imbalance(per_market) == pytest.approx(0.6)
    assert S.imbalance(240.0, 60.0) == pytest.approx(0.6)


def test_aggregate_imbalance_reproduces_the_measured_kalshi_correction():
    """The real numbers that reversed a venue conclusion."""
    assert S.aggregate_imbalance([(24451.0, 24295.0)]) == pytest.approx(0.0032, abs=1e-4)
    assert S.rm_floor(0.4144) == pytest.approx(1.415, abs=0.002)
    assert S.rm_floor(0.0032) == pytest.approx(0.0064, abs=1e-4)


def test_aggregate_imbalance_returns_none_on_no_flow():
    assert S.aggregate_imbalance([(0.0, 0.0)]) is None


def test_rm_floor_matches_the_identity_and_rejects_bad_input():
    for I in (0.083, 0.315, 0.1054, 0.4144):
        assert S.rm_floor(I) == pytest.approx(2 * I / (1 - I))
    with pytest.raises(ValueError):
        S.rm_floor(1.0)
