"""The PMUS/Kalshi BTC15m settlement statistic, and the 83% claim.

These pin the contract arithmetic, the benchmark identity (so a Kaiko/BRTI
mix-up cannot recur silently), and the distinction between the WEIGHT of
elapsed observations and the DETERMINACY of the outcome.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import brti_settlement as B


# ------------------------------------------------------- benchmark identity
def test_the_benchmark_is_brti_and_kaiko_is_a_different_venues_index():
    assert B.BENCHMARK == "CF Benchmarks BRTI"
    assert "KAIKO" in B.GEMINI_BENCHMARK
    assert B.BENCHMARK != B.GEMINI_BENCHMARK


def test_a_proxy_result_can_never_pass_as_benchmark_derived():
    st = B.PartialState(k=50, tau=10, partial=50 * 100.0, strike=100.0,
                        source=B.SOURCE_PROXY)
    assert st.source != B.SOURCE_BENCHMARK
    assert "NOT_SETTLEMENT" in B.SOURCE_PROXY


# ------------------------------------------------------- endpoint statistic
def test_the_endpoint_is_a_60_point_simple_average_rounded_to_two_places():
    vals = [100.004] * B.N_OBS
    assert B.endpoint_value(vals) == D("100.00")
    vals = [100.005] * B.N_OBS
    assert B.endpoint_value(vals) == D("100.01")          # half up
    mixed = [100.0] * 59 + [160.0]
    # mean 101.0 exactly
    assert B.endpoint_value(mixed) == D("101.00")


def test_an_average_over_fewer_than_sixty_points_is_not_the_statistic():
    """An average of 58 observations is a DIFFERENT statistic from the one that
    settles the contract and must not be passed off as it."""
    assert B.endpoint_value([100.0] * 58) is None
    assert B.endpoint_value([]) is None
    # explicitly opting in is allowed, and then it is the caller's claim
    assert B.endpoint_value([100.0] * 58, require_full=False) == D("100.00")


def test_resolution_is_endpoint_vs_endpoint_with_ties_going_up():
    assert B.resolves_up(D("100.00"), D("99.99")) is True
    assert B.resolves_up(D("100.00"), D("100.00")) is True      # tie -> UP
    assert B.resolves_up(D("99.99"), D("100.00")) is False
    # an unresolvable endpoint is not a NO
    assert B.resolves_up(None, D("100.00")) is None
    assert B.resolves_up(D("100.00"), None) is None


# -------------------------------------------------- averaging boundary
def test_both_timestamp_conventions_are_carried_and_differ_by_one_observation():
    obs = [(float(t), 100.0 + t) for t in range(-61, 1)]   # t = -61..0
    left = B.AveragingConvention.select(obs, 0.0, B.AveragingConvention.LEFT_CLOSED)
    right = B.AveragingConvention.select(obs, 0.0, B.AveragingConvention.RIGHT_CLOSED)
    assert len(left) == 60 and len(right) == 60
    # the one observation they disagree on is the boundary itself
    assert set(right) - set(left) == {100.0}               # the t = 0 sample
    assert set(left) - set(right) == {100.0 - 60}          # the t = -60 sample
    assert B.endpoint_value(left) != B.endpoint_value(right)
    assert len(B.AveragingConvention.BOTH) == 2


def test_an_unknown_convention_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError):
        B.AveragingConvention.select([], 0.0, "whatever-seems-right")


def test_selection_does_not_pad_missing_observations():
    sparse = [(float(t), 100.0) for t in range(-60, 0, 2)]    # every other second
    got = B.AveragingConvention.select(sparse, 0.0,
                                       B.AveragingConvention.LEFT_CLOSED)
    assert len(got) == 30
    assert B.endpoint_value(got) is None      # and so it is not the statistic


# ------------------------------------------------------------ the 83% claim
def test_the_eighty_three_percent_figure_is_exactly_fifty_sixtieths():
    assert B.determined_fraction(10) == pytest.approx(50 / 60)
    assert round(B.determined_fraction(10), 4) == 0.8333
    assert B.determined_fraction(60) == 0.0
    assert B.determined_fraction(0) == 1.0
    assert B.determined_fraction(90) == 0.0          # clamped, never negative


def test_weight_fixed_is_not_the_same_quantity_as_outcome_determined():
    """The trap this whole module exists to mark. 83% of the WEIGHT is fixed at
    tau=10 while the outcome of an at-the-money window is not determined at
    all."""
    st = B.PartialState(k=50, tau=10, partial=50 * 100_000.0,
                        strike=100_000.0)
    assert st.determined_weight == pytest.approx(0.8333, abs=1e-4)
    assert st.is_mathematically_determined() is None      # NOT decided
    p, _ = st.p_up(sigma_per_obs=3.0)
    assert p == pytest.approx(0.5, abs=1e-6)              # a coin flip


def test_mathematical_determinacy_requires_the_elapsed_part_alone_to_clear():
    # elapsed contribution alone already above the strike -> guaranteed UP,
    # since the remaining observations cannot be negative
    st = B.PartialState(k=50, tau=10, partial=60 * 100_000.0, strike=100_000.0)
    assert st.is_mathematically_determined() is True
    # below it, DOWN is never strictly guaranteed: an unbounded up-move rescues
    st2 = B.PartialState(k=50, tau=10, partial=10 * 100_000.0, strike=100_000.0)
    assert st2.is_mathematically_determined() is None


def test_the_required_remaining_mean_is_amplified_by_sixty_over_tau():
    """The amplification is the real mechanism: a shortfall in the partial
    average demands a 60/tau larger move in what is left."""
    strike, m0 = 100_000.0, 99_999.0            # $1 below the strike
    for tau in (30, 10, 5):
        k = B.N_OBS - tau
        st = B.PartialState(k=k, tau=tau, partial=k * m0, strike=strike)
        need = st.required_remaining_mean()
        assert need - m0 == pytest.approx((B.N_OBS / tau) * (strike - m0),
                                          rel=1e-6)
    # and at tau = 0 there is nothing left to require
    assert B.PartialState(k=60, tau=0, partial=60 * m0,
                          strike=strike).required_remaining_mean() is None


def test_p_up_moves_the_right_way_and_carries_uncertainty():
    strike = 100_000.0
    above = B.PartialState(k=50, tau=10, partial=50 * 100_050.0, strike=strike)
    below = B.PartialState(k=50, tau=10, partial=50 * 99_950.0, strike=strike)
    p_a, u_a = above.p_up(sigma_per_obs=3.0)
    p_b, u_b = below.p_up(sigma_per_obs=3.0)
    assert p_a > 0.9 and p_b < 0.1
    assert u_a >= 0.0 and u_b >= 0.0
    # more volatility pulls both toward a coin flip
    p_a2, _ = above.p_up(sigma_per_obs=300.0)
    assert abs(p_a2 - 0.5) < abs(p_a - 0.5)


def test_variance_of_the_remaining_mean_assumes_a_walk_not_independence():
    """Independence would understate the risk badly -- 6x at tau=10 on the
    measured proxy volatility -- and would make a late window look far more
    decided than it is."""
    import math
    sigma, tau = 3.0, 10.0
    walk = sigma ** 2 * (tau + 1) * (2 * tau + 1) / (6 * tau)
    indep = sigma ** 2 / tau
    assert walk > indep * 5
    # a state close enough to the money that p is strictly interior, so the
    # variance choice actually shows up in the answer
    st = B.PartialState(k=50, tau=10, partial=50 * 100_001.0, strike=100_000.0)
    p_walk, _ = st.p_up(sigma_per_obs=sigma)
    # under independence the same state would look much more certain
    sd_walk, sd_indep = math.sqrt(walk), math.sqrt(indep)
    assert sd_walk > sd_indep
    assert 0.0 < p_walk < 1.0


def test_a_fully_elapsed_window_is_simply_decided():
    st = B.PartialState(k=60, tau=0, partial=60 * 100_001.0, strike=100_000.0)
    p, u = st.p_up(sigma_per_obs=3.0)
    assert (p, u) == (1.0, 0.0)
    st2 = B.PartialState(k=60, tau=0, partial=60 * 99_999.0, strike=100_000.0)
    assert st2.p_up(sigma_per_obs=3.0)[0] == 0.0
