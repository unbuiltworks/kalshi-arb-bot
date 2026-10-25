"""The ACTUAL PMUS/Kalshi BTC15m settlement statistic, and what is knowable when.

PROVENANCE, AND A CORRECTION TO THIS REPO'S OWN EARLIER CLAIM
--------------------------------------------------------------
`GRR-KAIKO_RFR_<ASSET>USD_60S` is **GEMINI's** benchmark. It appears in this
repo only inside Gemini market descriptions captured under `analysis/gemini/`
(each citing `assets.gemini.com/predictions/terms_and_conditions/...`) and in
`data/ref_lag/`, whose `window` and `settlement` rows are **100% `GEMI-*`
markets** -- 34,944 of them, zero from any other venue.

PMUS and Kalshi BTC15m settle off **CF Benchmarks BRTI**, per
`analysis/pmus/P24_PMUS_BTC15M.md`, which quotes the market description:

  > settles to Up if the Bitcoin price at `T_end` is greater than or equal to
  > the price at `T_start`. ... in the last minute before each of those two
  > times, 60 BRTI prices are collected. The official value for that time is
  > the simple average of these prices, rounded to the nearest 2 decimal places.

So an earlier recommendation in `L5_INVENTORY_ENGINE_VALIDATION.md` -- build the
estimator on "strike vs `GRR-KAIKO_RFR_*_60S`" -- named the WRONG BENCHMARK for
PMUS. It carried Gemini's index across venues. That is corrected here.

TWO STRUCTURAL FACTS THE EARLIER FRAMING GOT WRONG
---------------------------------------------------
1. **It is endpoint-vs-endpoint, not price-vs-fixed-strike.** BOTH ends are
   60-observation averages. The "strike" is `BRTI(T_start)`, itself the average
   over the minute BEFORE the window opens -- so it is fully determined at (in
   fact just before) window open, and is not a round number chosen in advance.
   That is FAVOURABLE: the strike is knowable early, and the only unknown is
   the closing average.
2. **"83% of settlement value is fixed at 10 s remaining" is a statement about
   WEIGHT, not about DETERMINACY**, and conflating the two is the trap. See
   `determined_fraction` and `is_mathematically_determined` below: the first is
   the arithmetic 83.3%, the second is almost never true.

WHAT WE DO NOT HAVE
-------------------
**Zero BRTI observations.** `src/btc_ref_recorder.py` records Coinbase, and
says so itself: "Coinbase is a PROXY, not the settlement source... nothing
produced from this file is a settlement replay." Every function here that needs
real observations therefore takes them as ARGUMENTS and none of them invents a
series. A proxy may be used to study the STRUCTURE of the statistic -- how
determined a 60-point average is with tau left -- because that is a property of
averaging and of volatility rather than of which index is averaged. It may not
be used to settle a window, and `SOURCE_PROXY` exists so a result computed that
way cannot be reported as if it were benchmark-derived.

TIMESTAMP BOUNDARY, STATED AS UNRESOLVED
-----------------------------------------
"the last minute before" T is consistent with `[T-60s, T)` and, on a reading
where the observation stamped exactly T belongs to the window, with
`(T-60s, T]`. The two differ by ONE observation of sixty. That is 1.67% of the
weight -- small, but it is exactly the kind of off-by-one that decides a
near-the-money window, so `AveragingConvention` carries both and no code here
silently picks one. Resolving it needs the BRTI feed itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_HALF_UP
from typing import List, Optional, Sequence, Tuple

LABEL = "BRTI_SETTLEMENT_V1"

#: the authoritative index for PMUS and Kalshi BTC15m
BENCHMARK = "CF Benchmarks BRTI"
#: Gemini's index, which is NOT this one. Kept named so a mix-up is loud.
GEMINI_BENCHMARK = "GRR-KAIKO_RFR_BTCUSD_60S"

#: observations per endpoint average, and the published rounding
N_OBS = 60
ROUND_DP = D("0.01")

SOURCE_BENCHMARK = "BRTI"
SOURCE_PROXY = "PROXY_NOT_SETTLEMENT_GRADE"


class AveragingConvention:
    """The two readings of "the last minute before T". UNRESOLVED without the
    feed; both are carried so neither is assumed."""

    LEFT_CLOSED = "[T-60,T)"      # observation at exactly T excluded
    RIGHT_CLOSED = "(T-60,T]"     # observation at exactly T included
    BOTH = (LEFT_CLOSED, RIGHT_CLOSED)

    @staticmethod
    def select(obs: Sequence[Tuple[float, float]], t_end: float,
               convention: str) -> List[float]:
        """Pick the 60 observations for the endpoint at `t_end`.

        `obs` is (timestamp, value), ascending. Returns the values in the
        minute before `t_end` under the stated convention. It does NOT pad to
        60: a short list means observations are missing, and the caller must
        decide what to do about that rather than have a mean quietly computed
        over fewer points.
        """
        lo = t_end - float(N_OBS)
        if convention == AveragingConvention.LEFT_CLOSED:
            return [v for t, v in obs if lo <= t < t_end]
        if convention == AveragingConvention.RIGHT_CLOSED:
            return [v for t, v in obs if lo < t <= t_end]
        raise ValueError(f"unknown convention {convention!r}")


def endpoint_value(values: Sequence[float], *, require_full: bool = True
                   ) -> Optional[D]:
    """The published endpoint statistic: simple average, rounded to 2 dp.

    Returns None when fewer than 60 observations are present and
    `require_full`, because an average over 58 points is a different statistic
    from the one that settles the contract and must not be passed off as it.
    """
    if not values:
        return None
    if require_full and len(values) != N_OBS:
        return None
    tot = sum(D(str(v)) for v in values)
    return (tot / D(len(values))).quantize(ROUND_DP, rounding=ROUND_HALF_UP)


def resolves_up(close_value: Optional[D], open_value: Optional[D]
                ) -> Optional[bool]:
    """UP iff BRTI(T_end) >= BRTI(T_start). Note the tie goes to UP.

    None in means None out: an unresolvable endpoint is not a NO.
    """
    if close_value is None or open_value is None:
        return None
    return close_value >= open_value


# ------------------------------------------------- what is knowable at tau
@dataclass(frozen=True)
class PartialState:
    """The closing average, split into what is settled and what is not.

    k         observations of the closing average already elapsed
    tau       observations still to come (k + tau == 60)
    partial   SUM of the elapsed observations (not their mean)
    strike    BRTI(T_start), the already-determined open endpoint
    source    SOURCE_BENCHMARK or SOURCE_PROXY -- carried so a proxy result
              can never be reported as benchmark-derived
    """

    k: int
    tau: int
    partial: float
    strike: float
    source: str = SOURCE_PROXY

    @property
    def elapsed_mean(self) -> Optional[float]:
        return (self.partial / self.k) if self.k > 0 else None

    @property
    def determined_weight(self) -> float:
        """k/60 -- THE 83% NUMBER. At tau=10 this is 0.8333.

        It is the share of the closing average's WEIGHT that is already fixed.
        It is NOT the probability the outcome is decided, and it is not a
        measure of how much information we have about the outcome.
        """
        return self.k / N_OBS

    def required_remaining_mean(self) -> Optional[float]:
        """The mean the remaining `tau` observations must reach for UP.

        From (k*Abar_k + tau*M_tau)/60 >= S:
            M_tau >= (60*S - partial) / tau

        The `tau` in the denominator is the whole story: as the window runs
        down, the required move in the remaining observations is AMPLIFIED by
        60/tau. At tau=10 a $1 shortfall in the partial sum's contribution
        demands a $6 move in the last ten observations. So a late window is
        more decided when it is already away from the strike, and more
        violently sensitive when it is not.
        """
        if self.tau <= 0:
            return None
        return (N_OBS * self.strike - self.partial) / self.tau

    def is_mathematically_determined(self) -> Optional[bool]:
        """True/False only when NO possible remaining observations can change
        the outcome; None when the outcome is still open.

        The only hard bound on a price is that it is positive, so UP is
        guaranteed iff the elapsed contribution ALONE already clears the
        strike. DOWN can never be strictly guaranteed, because an unbounded
        upward move in the remaining observations can always rescue it.

        In practice this is almost never True, which is the point: the 83%
        weight figure says nothing about determinacy. Determinacy at realistic
        volatilities is what `p_up` estimates, with an uncertainty attached.
        """
        if self.tau <= 0:
            return bool(self.partial / N_OBS >= self.strike)
        if self.partial / N_OBS >= self.strike:
            return True                      # already clears on elapsed alone
        return None                          # still open

    def p_up(self, sigma_per_obs: float, *, drift: float = 0.0
             ) -> Tuple[float, float]:
        """P(UP) under a normal approximation, with its own 1-sd uncertainty.

        The remaining mean M_tau is modelled as the mean of `tau` correlated
        observations of a price that random-walks at `sigma_per_obs` per step.
        For a walk, the variance of the mean of the next tau observations is
        sigma^2 * (tau+1)(2tau+1)/(6*tau) -- NOT sigma^2/tau, which would
        assume independence and badly understate the risk. That distinction
        matters more than the choice of index: independence would make a late
        window look far more decided than it is.

        Returns (p_up, sigma_p) where sigma_p is a crude sensitivity of p to a
        25% misestimate of volatility -- carried so the engine's PROB_UNCERTAIN
        gate sees honest uncertainty rather than a point estimate.
        """
        if self.tau <= 0:
            det = self.is_mathematically_determined()
            return (1.0 if det else 0.0), 0.0
        need = self.required_remaining_mean()
        if need is None:
            return 0.5, 0.5
        m0 = self.elapsed_mean
        if m0 is None:
            return 0.5, 0.5
        t = float(self.tau)
        var_mean = (sigma_per_obs ** 2) * (t + 1.0) * (2.0 * t + 1.0) / (6.0 * t)
        sd = math.sqrt(max(var_mean, 1e-18))
        mu = m0 + drift
        z = (mu - need) / sd
        p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

        sd_hi = sd * 1.25
        z_hi = (mu - need) / sd_hi
        p_hi = 0.5 * (1.0 + math.erf(z_hi / math.sqrt(2.0)))
        return p, abs(p - p_hi)


def determined_fraction(tau: int) -> float:
    """k/60 for tau remaining observations. tau=10 -> 0.8333.

    Exposed as its own function because the 83% claim is most often quoted
    without its denominator, and this is the whole of it.
    """
    return max(0, N_OBS - tau) / N_OBS
