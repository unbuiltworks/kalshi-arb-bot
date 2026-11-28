"""The exact late-window variance: derivation, and what it is worth.

THE DERIVATION, which these tests check rather than assume. With R the raw
BRTI (a random walk, per-second sd sigma) and A its published 60-second
trailing average, the settlement value is A(T). Splitting A(T) - A(t) into
what is still to come and what has already happened:

    A(T) - A(t) = [ SUM_{j=1..tau} (tau-j+1) e_j                (future)
                  + SUM_{k=0..58} min(59-k, tau) u_{t-k} ] / 60 (past)

The FUTURE term is independent of dA and contributes
tau(tau+1)(2tau+1)/21600 -- already exact in the code. The PAST term is
the piece of the settlement average that has already happened and which
the published average does NOT reveal individually: only its window sum
is observed. That is the "uncertainty in the underlying instantaneous
index". Conditioning on dA = (R(t) - R(t-60))/60, with
a_k = min(59-k, tau)/60,

    Var[past | dA] = SUM a_k^2 - (SUM a_k)^2 / 60.

TWO FINDINGS, and the first is why the fix is small.

  1. THE `+5` WAS NEVER ARBITRARY. At tau = 60 the expression above is
     4.998611 -- which is the 5.0 the code has carried for tau >= 60 all
     along. The constant is this term evaluated where the whole averaging
     window is unobserved, and it is right there.

  2. THE RAMP BELOW A MINUTE WAS. Scaling that constant linearly as
     5*tau/60 overstates the variance by up to 12.3x in sd at tau=1 and
     3.83x at tau=5, because as tau -> 0 almost all of the settlement
     average is already inside A(t) and almost nothing is unobserved.

SIMULATION IS NOT CALIBRATION, and these tests are the former. They
establish that the estimator is the correct conditional distribution FOR
A GAUSSIAN RANDOM WALK. Whether BRTI is one over five-minute horizons is
an empirical question, and no recorded BRTI series exists to answer it --
`l5_btc_reference` keeps twenty minutes in memory and writes nothing. So
nothing here claims the model is calibrated to the real index.
"""
from __future__ import annotations

import math
import random

import pytest

from src import l5_btc_reference as BR

AV = BR.AVERAGING_S


# ---------------------------------------------------------------- helpers

def _exact_by_conditioning(tau: int, lags: int = 1) -> float:
    """Var[A(T)-A(t) | {A(t-k)-A(t)}] by full Gaussian conditioning.

    Independent of the closed form: builds each quantity as a vector of
    increment coefficients and conditions directly. Pure Python so the
    suite needs no numpy.
    """
    t = 200
    n = t + tau + 1

    def vec(lo: int, hi: int):
        v = [0.0] * n
        for s in range(lo, hi + 1):
            for i in range(max(s, 0)):
                v[i] += 1.0 / AV
        return v

    def sub(a, b):
        return [x - y for x, y in zip(a, b)]

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    a_t = vec(t - AV + 1, t)
    target = sub(vec(t + tau - AV + 1, t + tau), a_t)
    obs = [sub(vec(t - k - AV + 1, t - k), a_t) for k in range(1, lags + 1)]
    var = dot(target, target)
    if not obs:
        return var
    # one observable: the scalar case is all we need (the code conditions
    # on dA alone), and it keeps this independent of a matrix solver.
    assert lags == 1
    c = dot(obs[0], target)
    return var - c * c / dot(obs[0], obs[0])


def _var_of(tau, exact_flag, monkeypatch):
    if exact_flag:
        monkeypatch.setenv(BR.EXACT_LATE_VAR_ENV, BR.EXACT_LATE_VAR)
    else:
        monkeypatch.delenv(BR.EXACT_LATE_VAR_ENV, raising=False)
    d = BR.settlement_distribution(a_now=100.0, a_prev=100.0, sigma=1.0,
                                   seconds_left=tau)
    return d["sd"] ** 2


# ------------------------------------------- 1. the closed form is exact

@pytest.mark.parametrize("tau", [1, 2, 5, 10, 15, 20, 30, 40, 45, 50, 59])
def test_the_corrected_variance_is_the_exact_conditional_variance(
        tau, monkeypatch):
    """To 8 decimal places against full Gaussian conditioning."""
    got = _var_of(tau, True, monkeypatch)
    assert got == pytest.approx(_exact_by_conditioning(tau), abs=1e-8)


def test_the_plus_five_is_this_same_term_at_a_full_minute():
    """FINDING 1: the constant was principled, not fitted."""
    assert BR.past_window_residual_var(60) == pytest.approx(4.998611,
                                                            abs=1e-6)
    assert BR.past_window_residual_var(60) == pytest.approx(5.0, abs=2e-3)


def test_the_long_branch_was_already_exact(monkeypatch):
    """tau >= 60 is unchanged by the correction, because it was right."""
    for tau in (60, 90, 150, 240):
        assert _var_of(tau, True, monkeypatch) == \
            _var_of(tau, False, monkeypatch)
        assert _var_of(tau, True, monkeypatch) == pytest.approx(
            _exact_by_conditioning(tau), abs=2e-3)


def test_the_branches_still_meet_at_one_minute(monkeypatch):
    lo = _var_of(59.999, True, monkeypatch)
    hi = _var_of(60.0, True, monkeypatch)
    assert lo == pytest.approx(hi, rel=2e-3)


def test_the_residual_vanishes_as_the_window_closes():
    """At tau = 0 the settlement average IS the observed average."""
    assert BR.past_window_residual_var(0) == pytest.approx(0.0, abs=1e-12)
    assert BR.past_window_residual_var(0.5) < 1e-3


def test_the_residual_is_monotone_in_tau():
    vals = [BR.past_window_residual_var(t) for t in range(0, 61)]
    assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:])), vals[:8]


# ----------------------------------- 2. how wrong the ramp was, in sd

@pytest.mark.parametrize("tau,sd_ratio", [
    (1, 12.3), (5, 3.83), (10, 2.17), (15, 1.60), (30, 1.10),
])
def test_the_old_ramp_was_this_many_times_too_wide(tau, sd_ratio,
                                                   monkeypatch):
    """FINDING 2, quantified. These are the numbers the correction buys."""
    old = _var_of(tau, False, monkeypatch)
    new = _var_of(tau, True, monkeypatch)
    assert math.sqrt(old / new) == pytest.approx(sd_ratio, rel=0.02)


def test_the_correction_narrows_below_45s_and_barely_widens_above(
        monkeypatch):
    """MY FIRST VERSION OF THIS TEST ASSERTED "only ever narrows" AND WAS
    WRONG. The ramp crosses the exact term twice: it is too WIDE below
    tau=45 (hugely so at the very end) and slightly too NARROW from 45 to
    60. So the correction is not uniformly a loosening -- between 45 and
    60 it makes the forecast marginally MORE cautious."""
    for tau in range(1, 45):
        assert _var_of(tau, True, monkeypatch) < \
            _var_of(tau, False, monkeypatch)
    for tau in range(45, 60):
        new = _var_of(tau, True, monkeypatch)
        old = _var_of(tau, False, monkeypatch)
        assert new >= old
        #: and never by more than 1% in sd, so the band is immaterial
        assert math.sqrt(new / old) < 1.01, (tau, new, old)


# ------------------------------------- 3. Monte Carlo, the venue's rule

@pytest.mark.parametrize("tau", [5, 15, 30, 59])
def test_monte_carlo_agrees_with_the_corrected_variance(tau, monkeypatch):
    """Simulate the RAW index and its published average, and compare the
    realised spread against the forecast. 3% tolerance at 8000 paths."""
    rng = random.Random(4242)
    sigma, trials = 3.0, 8000
    errs = []
    for _ in range(trials):
        raw = [0.0]
        for _ in range(AV + 1 + tau + AV):
            raw.append(raw[-1] + rng.gauss(0.0, sigma))
        avg = [sum(raw[i - AV + 1:i + 1]) / AV
               for i in range(AV - 1, len(raw))]
        t = AV
        monkeypatch.setenv(BR.EXACT_LATE_VAR_ENV, BR.EXACT_LATE_VAR)
        d = BR.settlement_distribution(a_now=avg[t], a_prev=avg[t - 1],
                                       sigma=sigma, seconds_left=tau)
        errs.append(avg[t + tau] - d["mean"])
    m = sum(errs) / len(errs)
    emp = math.sqrt(sum((e - m) ** 2 for e in errs) / (len(errs) - 1))
    fc = sigma * math.sqrt(_exact_by_conditioning(tau))
    assert emp == pytest.approx(fc, rel=0.03), (tau, emp, fc)


# --------------------------------------- 4. it arrives OFF, and is safe

def test_it_is_off_unless_the_owner_sets_it():
    assert BR.exact_late_variance() is False


@pytest.mark.parametrize("raw", ["", "exact", "Exact", "ON", "1", "YES",
                                 "TRUE", "EXACTLY"])
def test_only_the_exact_string_arms_it(monkeypatch, raw):
    monkeypatch.setenv(BR.EXACT_LATE_VAR_ENV, raw)
    assert BR.exact_late_variance() is (raw.strip() == "EXACT")


def test_off_reproduces_the_shipped_numbers_exactly(monkeypatch):
    """The deployed image must behave identically until switched on."""
    for tau in (1, 5, 15, 30, 45, 59, 60, 240):
        got = _var_of(tau, False, monkeypatch)
        want = (tau - 39.5 + 5.0 if tau >= AV else
                tau * (tau + 1.0) * (2.0 * tau + 1.0) / 21600.0
                + 5.0 * tau / AV)
        assert got == pytest.approx(want, abs=1e-12), tau


@pytest.mark.parametrize("flag", [False, True])
def test_every_fail_closed_reason_survives_the_correction(monkeypatch,
                                                          flag):
    """The correction touches ONE arithmetic line. Stale data, a strike
    for another window, a gap and too little history must still refuse."""
    if flag:
        monkeypatch.setenv(BR.EXACT_LATE_VAR_ENV, BR.EXACT_LATE_VAR)
    series = [(1000 + i, 100.0) for i in range(400)]
    now = series[-1][0] + 1.0
    end = series[-1][0] + 30.0

    def p(**kw):
        base = dict(series=series, strike=100.0, window_end=end, now=now)
        base.update(kw)
        return BR.probability_up(**base)

    assert p(now=now + BR.MAX_AGE_S + 1)["reason"] == "INDEX_STALE"
    assert p(series=[])["reason"] == "NO_INDEX"
    assert p(strike=0.0)["reason"] == "NO_STRIKE"
    #: the TAIL, not the head: truncating to the first rows makes the
    #: newest bucket old and INDEX_STALE fires first, which is correct
    #: precedence and was my test's error, not the module's.
    assert p(series=series[-3:])["reason"] in (
        "TOO_LITTLE_HISTORY_FOR_VOLATILITY", "INDEX_GAP")
    assert p(window_end=series[-1][0] - 10.0)["reason"] == "WINDOW_OVER"
    #: and a healthy call still produces a probability under both
    assert p()["ok"] is True
