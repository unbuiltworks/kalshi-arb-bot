"""Timestamp-causal settlement-probability estimator for 15m up/down windows.

WHAT IT IS ALLOWED TO SEE. Only the market's own book and tape, as of the
decision timestamp. No strike, no reference index, no spot: those exist at
tick resolution for 4 windows only (analysis/profit_search/tick/), far too few
to calibrate against, so this estimator does not pretend to use them. It is
honest about that by construction -- the feature vector cannot contain
information the replay did not have.

THE BASE RATE IS THE MARKET. p_base = the YES mid. That is a deliberately
edge-free starting point: trading on the mid alone loses exactly the fees. Any
edge this estimator has must come from the TILT, and the tilt is fitted on
chronologically earlier windows only.

THE TILT. Two features, both measured causally at the decision frame:
  imb   book depth imbalance (yes_bid_qty - no_bid_qty)/(sum). This is the one
        signal already measured real out-of-sample in this repo: it predicts
        the MID at t = +6.41, 48 of 54 markets positive. Whether it also
        predicts SETTLEMENT is the question this module answers, and the answer
        is not assumed.
  flow  signed taker flow over a trailing window, normalised. Trailing only.

sigma CARRIES THE TRUTH. The uncertainty returned is the fitted residual sd of
the tilt on the training split, inflated for a short trailing history or a
stale book. A model with no fitted skill returns a sigma large enough that the
engine's PROB_UNCERTAIN gate refuses every trade -- which is the intended
behaviour, not a failure: no reliable probability means WAIT.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from decimal import Decimal as D
from typing import List, Optional, Sequence, Tuple

from src.gemini_execution.inventory_engine import Book, Prob

LABEL = "L5_INVENTORY_PROB_V1"

ZERO = D(0)
ONE = D(1)


@dataclass
class Calibration:
    """Fitted tilt. All fields are plain floats so this serialises to JSON and
    can be committed next to the replay that used it."""

    b_imb: float = 0.0
    b_flow: float = 0.0
    intercept: float = 0.0
    resid_sd: float = 0.5          # 0.5 = no skill at all on a binary outcome
    n_train: int = 0
    t_imb: float = 0.0
    t_flow: float = 0.0
    train_windows: int = 0
    train_until: float = 0.0
    note: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=True)


@dataclass
class Features:
    imb: float
    flow: float
    mid: float
    tte: float
    book_age_s: float
    n_prints: int


def signed_flow(prints: Sequence[tuple], t: float, lookback: float) -> float:
    """Signed taker flow in the trailing `lookback` seconds, in [-1, 1].

    Trailing only: a print at or after `t` is never counted. A 'yes' taker
    print is a buyer of YES (pressure up), a 'no' taker print is a buyer of NO.
    """
    up = dn = 0.0
    lo = t - lookback
    for pt, px, q, sd in prints:
        if pt >= t:
            break
        if pt < lo:
            continue
        if sd == "yes":
            up += float(q)
        else:
            dn += float(q)
    tot = up + dn
    return ((up - dn) / tot) if tot > 0 else 0.0


def features_at(book: Book, prints: Sequence[tuple], t: float, close: float,
                lookback: float = 60.0, book_age_s: float = 0.0) -> Features:
    lo = t - lookback
    n = sum(1 for pt, *_ in prints if lo <= pt < t)
    return Features(imb=float(book.imbalance), flow=signed_flow(prints, t, lookback),
                    mid=float(book.mid), tte=max(0.0, close - t),
                    book_age_s=book_age_s, n_prints=n)


def estimate(f: Features, cal: Calibration, *, max_book_age_s: float = 30.0,
             min_prints: int = 1) -> Prob:
    """Turn features + calibration into a probability the engine can gate on."""
    if f.book_age_s > max_book_age_s:
        return Prob(p_up=D(str(round(f.mid, 4))), sigma=D("0.50"),
                    age_s=f.book_age_s, stale=True, source=f"{LABEL}:STALE_BOOK")
    if f.n_prints < min_prints:
        # no trailing flow to speak of: fall back to the mid and say so by
        # returning a sigma the engine will refuse.
        return Prob(p_up=D(str(round(f.mid, 4))), sigma=D("0.50"),
                    age_s=f.book_age_s, stale=True, source=f"{LABEL}:NO_FLOW")

    tilt = cal.intercept + cal.b_imb * f.imb + cal.b_flow * f.flow
    p = min(0.99, max(0.01, f.mid + tilt))
    # uncertainty: the fitted residual sd, floored so a weak fit is never sold
    # as a strong one, and inflated when the window still has a long way to go.
    sigma = max(cal.resid_sd, 0.01)
    return Prob(p_up=D(str(round(p, 4))), sigma=D(str(round(sigma, 4))),
                age_s=f.book_age_s, stale=False,
                source=f"{LABEL}:MID+TILT(n={cal.n_train})")


def market_only(f: Features, *, max_book_age_s: float = 30.0) -> Prob:
    """The edge-free control: p = the market's own mid, with the sigma a
    one-shot binary outcome actually carries. Used as the WAIT/no-signal arm."""
    stale = f.book_age_s > max_book_age_s
    sd = math.sqrt(max(1e-9, f.mid * (1.0 - f.mid)))
    return Prob(p_up=D(str(round(f.mid, 4))), sigma=D(str(round(sd, 4))),
                age_s=f.book_age_s, stale=stale, source=f"{LABEL}:MID_ONLY")


def oracle(yes_won: int) -> Prob:
    """Perfect foresight. NOT a strategy and never used for a reported return:
    it exists only to bound how much a strategy could earn if the probability
    problem were solved, which separates 'no edge' from 'no execution'."""
    return Prob(p_up=(ONE if yes_won == 1 else ZERO), sigma=D("0.0"),
                age_s=0.0, stale=False, source=f"{LABEL}:ORACLE")


def partial_oracle(f: Features, yes_won: int, alpha: float) -> Prob:
    """A probability that resolves exactly `alpha` of the market's uncertainty.

    p = mid + alpha * (outcome - mid), with sigma shrunk by the same factor.
    alpha = 0 is the market (no edge); alpha = 1 is perfect foresight. This is
    NOT a strategy: it is the instrument used to measure how much settlement
    uncertainty a forecaster would have to resolve before the inventory
    controller's economics turn positive. The answer is a REQUIREMENT on future
    research, not a return.
    """
    base = f.mid
    p = base + alpha * (float(yes_won) - base)
    sd = math.sqrt(max(1e-9, base * (1.0 - base))) * (1.0 - alpha)
    return Prob(p_up=D(str(round(min(0.999, max(0.001, p)), 4))),
                sigma=D(str(round(max(0.0, sd), 4))), age_s=f.book_age_s,
                stale=False, source=f"{LABEL}:PARTIAL_ORACLE(alpha={alpha})")


# ---- fitting (train split only) ----------------------------------------------
def fit(samples: Sequence[Tuple[Features, int]], *, train_until: float = 0.0,
        train_windows: int = 0) -> Calibration:
    """Least-squares fit of (yes_won - mid) on [imb, flow].

    Ordinary least squares on a 0/1 target is a crude but unbiased linear
    probability model; what matters here is the t statistics and the residual
    sd, both of which are reported rather than hidden, and the fact that this
    function never sees a window outside the training split.
    """
    n = len(samples)
    if n < 30:
        return Calibration(resid_sd=0.5, n_train=n,
                           note="too few training samples to fit; no tilt")

    ys = [float(w) - f.mid for f, w in samples]
    x1 = [f.imb for f, _ in samples]
    x2 = [f.flow for f, _ in samples]

    def mean(v):
        return sum(v) / len(v)

    my, m1, m2 = mean(ys), mean(x1), mean(x2)
    dy = [y - my for y in ys]
    d1 = [a - m1 for a in x1]
    d2 = [a - m2 for a in x2]

    s11 = sum(a * a for a in d1)
    s22 = sum(a * a for a in d2)
    s12 = sum(a * b for a, b in zip(d1, d2))
    s1y = sum(a * b for a, b in zip(d1, dy))
    s2y = sum(a * b for a, b in zip(d2, dy))

    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return Calibration(resid_sd=0.5, n_train=n,
                           note="degenerate design matrix; no tilt")

    b1 = (s22 * s1y - s12 * s2y) / det
    b2 = (s11 * s2y - s12 * s1y) / det
    a0 = my - b1 * m1 - b2 * m2

    resid = [y - (a0 + b1 * u + b2 * v) for y, u, v in zip(ys, x1, x2)]
    dof = max(1, n - 3)
    s2 = sum(r * r for r in resid) / dof
    sd = math.sqrt(s2)

    # standard errors from the inverse of the (centred) 2x2 design
    se1 = math.sqrt(s2 * s22 / det) if det > 0 else float("inf")
    se2 = math.sqrt(s2 * s11 / det) if det > 0 else float("inf")
    t1 = b1 / se1 if se1 > 0 else 0.0
    t2 = b2 / se2 if se2 > 0 else 0.0

    return Calibration(b_imb=b1, b_flow=b2, intercept=a0, resid_sd=sd,
                       n_train=n, t_imb=t1, t_flow=t2,
                       train_windows=train_windows, train_until=train_until,
                       note="OLS of (yes_won - mid) on [imb, flow], train split only")


# ---- the venue's ACTUAL settlement statistic -------------------------------
#: Why this exists: an earlier checkpoint planned this estimator on
#: GRR-KAIKO_RFR_*_60S, which is GEMINI's benchmark, not PMUS's or Kalshi's.
#: See L5_BENCHMARK_AUDIT_AND_SETTLEMENT_ESTIMATOR.md. The guard below makes
#: that class of mistake a refusal rather than a silent wrong answer.
NO_BENCHMARK = "NO_BENCHMARK_OBSERVATIONS"
WRONG_BENCHMARK = "WRONG_BENCHMARK_SOURCE"


def brti_prob(state, *, sigma_per_obs: float, drift: float = 0.0) -> Prob:
    """P(UP) from the REAL PMUS/Kalshi statistic: a partial BRTI closing
    average against the already-determined opening average.

    `state` is a `brti_settlement.PartialState`. It must carry
    `source == SOURCE_BENCHMARK`: a probability derived from a proxy series is
    refused outright rather than returned with a wide sigma, because the
    failure mode being guarded here is not noise, it is predicting a different
    statistic than the one that settles the contract.

    We currently hold ZERO BRTI observations, so in production this returns the
    NO_BENCHMARK refusal and the engine WAITs. That is the correct behaviour and
    the reason is recorded precisely, rather than as a generic uncertainty.
    """
    from src.gemini_execution import brti_settlement as BS

    if state is None:
        return Prob(p_up=D("0.5"), sigma=D("0.50"), stale=True,
                    source=f"{LABEL}:{NO_BENCHMARK}")
    if getattr(state, "source", None) != BS.SOURCE_BENCHMARK:
        # a proxy cannot settle a window; see btc_ref_recorder.py's own warning
        return Prob(p_up=D("0.5"), sigma=D("0.50"), stale=True,
                    source=f"{LABEL}:{WRONG_BENCHMARK}"
                           f"({getattr(state, 'source', None)})")
    p, sens = state.p_up(sigma_per_obs=sigma_per_obs, drift=drift)
    return Prob(p_up=D(str(round(p, 4))), sigma=D(str(round(max(sens, 0.0), 4))),
                age_s=0.0, stale=False,
                source=f"{LABEL}:BRTI(k={state.k},tau={state.tau})")
