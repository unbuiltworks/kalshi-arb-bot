"""Is the settlement-average forecast's SPREAD right, not just its side?

WHY THIS FILE EXISTS. `test_it_is_calibrated_on_simulated_windows` already
simulates the venue's rule, but it can only detect a forecast that points
the wrong WAY: it buckets at p<0.3 / p>0.7 and asks that the outcome rate
land outside 0.25 / 0.75. An over-dispersed forecast pushes p toward 0.5,
lands in the middle bucket, and passes -- and it samples the last minute
barely at all (`off` tops out at 850 of a 900-second window, so the
smallest tau it ever sees is about 50).

WHAT THESE TESTS ESTABLISH, by simulating the raw index and its own
60-second trailing average:

  1. the MEAN is unbiased at every tau, so
     `A + dA * tc * (119 - tc) / 120` is the right centre;
  2. both VARIANCE branches are analytically exact, which is checkable in
     closed form:
       tau >= 60   Sum_{u,v in [tau-59, tau]} min(u,v) / 3600
                   = (tau - 60) + 73810/3600 = tau - 39.497
       tau <  60   Sum_{i=1..tau} (tau-i+1)^2 / 3600
                   = tau(tau+1)(2tau+1)/21600
     and the code uses exactly those, plus a flat `+5` (scaled by tau/60
     below a minute);
  3. the `+5` is NOT decoration. It stands in for the one thing the model
     cannot observe -- where the RAW index sits inside its own average,
     known only through dA -- and with it the forecast sd matches the
     simulation to better than 1% for tau >= 45;
  4. BUT below about 30 seconds the same term is far too large, and the
     forecast is materially OVER-DISPERSED: about 3.8x too wide at tau=5.

(4) IS THE REASON THIS FILE IS NOT JUST A PASSING SUITE. Over-dispersion
is the CONSERVATIVE direction -- it pulls p toward 0.5, so the engine
declines rather than over-commits -- but it is also the regime where the
index has most nearly decided the outcome, i.e. where our own information
is worth most. And it matters MORE for BTC5m than for the 15-minute lane
it was tuned on: tau < 60 is 20% of a 300-second window against 6.7% of a
900-second one, so BTC5m spends about three times as much of its life in
the mis-calibrated regime.

NOTHING IS FITTED HERE. These tests record what the model does against a
simulated truth; they do not adjust it. Replacing the `+5` with a fitted
constant would be manufacturing a probability from my own simulation,
which is not evidence about BRTI.
"""
from __future__ import annotations

import math
import random

import pytest

from src import l5_btc_reference as BR

SIGMA = 3.0          # per-second vol of the RAW index, in index dollars
TRIALS = 6000
AV = BR.AVERAGING_S


def _simulate(tau: int, trials: int = TRIALS, seed: int = 20261118):
    """(bias, empirical_sd, forecast_sd) for a tau, from the venue's rule.

    The raw index is a Gaussian walk; the published series is its
    60-second trailing average, which is what the venue actually serves.
    The forecast sees only A(t) and A(t-1) -- never the raw index -- which
    is the whole reason an unobservable term is needed at all.
    """
    rng = random.Random(seed)
    errs = []
    sd_forecast = None
    for _ in range(trials):
        raw = [0.0]
        for _ in range(AV + 1 + tau + AV):
            raw.append(raw[-1] + rng.gauss(0.0, SIGMA))
        avg = [sum(raw[i - AV + 1:i + 1]) / AV
               for i in range(AV - 1, len(raw))]
        t = AV
        dist = BR.settlement_distribution(a_now=avg[t], a_prev=avg[t - 1],
                                          sigma=SIGMA, seconds_left=tau)
        errs.append(avg[t + tau] - dist["mean"])
        sd_forecast = dist["sd"]
    mean = sum(errs) / len(errs)
    var = sum((e - mean) ** 2 for e in errs) / (len(errs) - 1)
    return mean, math.sqrt(var), sd_forecast


# ------------------------------------------------- 1. the centre is right

@pytest.mark.parametrize("tau", [5, 15, 30, 45, 59, 60, 90, 150, 240])
def test_the_forecast_mean_is_unbiased_at_every_tau(tau):
    """`A + dA * tc * (119 - tc) / 120` is the right centre. The bound is
    in units of the empirical sd, because a bias worth caring about is one
    that is large RELATIVE to the spread."""
    bias, emp_sd, _ = _simulate(tau)
    assert abs(bias) < 0.12 * emp_sd, (tau, bias, emp_sd)


# -------------------------------------- 2. both branches are exact

def test_the_long_branch_is_the_closed_form():
    """tau - 39.497, from Sum min(u,v) / 3600 over the 60 settlement
    buckets. The code writes -39.5, which agrees to 3e-3."""
    exact = sum(min(240 - i, 240 - j) for i in range(60)
                for j in range(60)) / 3600.0
    assert exact == pytest.approx(240 - 39.497, abs=1e-3)
    d = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                   seconds_left=240)
    assert d["sd"] ** 2 == pytest.approx(exact + 5.0, abs=5e-3)


@pytest.mark.parametrize("tau", [1, 5, 17, 30, 59])
def test_the_short_branch_is_the_closed_form(tau):
    """Sum_{i=1..tau} (tau-i+1)^2 / 3600 -- only the UNOBSERVED buckets
    carry variance, each weighted 1/60."""
    exact = sum((tau - i + 1) ** 2 for i in range(1, tau + 1)) / 3600.0
    d = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                   seconds_left=tau)
    assert d["sd"] ** 2 == pytest.approx(exact + 5.0 * tau / AV, abs=1e-9)


def test_the_two_branches_agree_at_a_minute():
    lo = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                    seconds_left=59.999)["sd"] ** 2
    hi = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                    seconds_left=60.0)["sd"] ** 2
    assert lo == pytest.approx(hi, abs=5e-3)


# ------------------ 3. the unobservable term earns its place, tau >= 45

@pytest.mark.parametrize("tau", [45, 59, 60, 90, 150, 240])
def test_the_forecast_spread_matches_the_truth_above_45s(tau):
    """Within 4%. THE DERIVED VARIANCE ALONE IS NOT ENOUGH -- the model
    never sees the raw index, so it must carry the uncertainty about where
    the raw index sits inside its own average. That is what `+5` is."""
    _, emp_sd, fc_sd = _simulate(tau)
    assert fc_sd == pytest.approx(emp_sd, rel=0.04), (tau, emp_sd, fc_sd)


@pytest.mark.parametrize("tau", [60, 150, 240])
def test_dropping_the_unobservable_term_understates_the_spread(tau):
    """Pinning WHY it is there: without it the forecast is too confident,
    which is the dangerous direction."""
    _, emp_sd, fc_sd = _simulate(tau)
    derived_only = SIGMA * math.sqrt(fc_sd ** 2 / SIGMA ** 2 - 5.0)
    assert derived_only < emp_sd * 0.99, (tau, derived_only, emp_sd)


# --------------- 4. the known over-dispersion in the last 30 seconds

@pytest.mark.parametrize("tau,at_least", [(5, 2.5), (10, 2.0), (15, 1.4)])
def test_the_forecast_is_OVER_dispersed_in_the_last_30_seconds(
        tau, at_least):
    """CHARACTERISATION, NOT APPROVAL. The forecast sd is at least this
    multiple of the truth, so a future change that quietly widens it
    further fails here, and one that corrects it fails here too and must
    update the numbers deliberately.

    Measured: about 3.8x at tau=5, 1.6x at tau=15, 1.10x at tau=30.
    Conservative in direction -- p is pulled toward 0.5 -- but it is the
    regime where the index has most nearly decided the outcome, and
    BTC5m spends 3x more of its window here than BTC15m does.
    """
    _, emp_sd, fc_sd = _simulate(tau)
    assert fc_sd / emp_sd > at_least, (tau, emp_sd, fc_sd, fc_sd / emp_sd)


def test_over_dispersion_pulls_the_probability_toward_a_half():
    """The economic consequence, stated as a test: at 5 seconds left and
    the average a clear 6 dollars above the strike, the forecast is far
    less certain than the simulated truth warrants."""
    tau = 5
    _, emp_sd, fc_sd = _simulate(tau)
    #: ONE MODEL SD above the strike. Picking a distance far outside both
    #: spreads (6 dollars, which was my first attempt) makes BOTH answers
    #: ~1.0 and hides the gap; the divergence is where the model is
    #: merely unsure and the truth is not.
    dist_above = fc_sd
    p_model = 0.5 * math.erfc(-(dist_above / fc_sd) / math.sqrt(2.0))
    p_truth = 0.5 * math.erfc(-(dist_above / emp_sd) / math.sqrt(2.0))
    assert p_model == pytest.approx(0.8413, abs=1e-3), p_model
    assert p_truth > 0.999, (p_truth, emp_sd)
    assert p_truth - p_model > 0.15, (p_truth, p_model)
