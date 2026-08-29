"""Small, dependency-free statistics used by the L5 analysis.

Only what the analysis actually needs: rank-based two-sample comparison,
rank correlation, a two-proportion test, and bootstrap intervals. Everything
is deterministic -- the bootstrap is seeded -- so a rerun reproduces the
numbers in the findings document exactly.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Sequence

# --- descriptive -------------------------------------------------------------


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def quantile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolation quantile (the `numpy.percentile` default)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[int(position)]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def median(values: Sequence[float]) -> float | None:
    return quantile(values, 0.5)


def stdev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mu = sum(values) / len(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (len(values) - 1))


def describe(values: Sequence[float]) -> dict:
    return {
        "n": len(values),
        "mean": mean(values),
        "median": median(values),
        "p25": quantile(values, 0.25),
        "p75": quantile(values, 0.75),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "stdev": stdev(values),
    }


# --- distributions -----------------------------------------------------------


def normal_sf(z: float) -> float:
    """P(Z > z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)
    ) * _betacf(b, a, 1.0 - x) / b


def student_t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with `df` degrees of freedom."""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    tail = 0.5 * betainc(df / 2.0, 0.5, x)
    return tail if t > 0 else 1.0 - tail


# --- ranks -------------------------------------------------------------------


def rank_average(values: Sequence[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and values[order[end + 1]] == values[order[index]]:
            end += 1
        shared = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = shared
        index = end + 1
    return ranks


def _tie_correction(values: Sequence[float]) -> float:
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sum(t**3 - t for t in counts.values())


# --- tests -------------------------------------------------------------------


def mann_whitney_u(x: Sequence[float], y: Sequence[float]) -> dict:
    """Two-sided Mann-Whitney U with tie correction and continuity correction.

    Normal approximation -- appropriate for the sample sizes this analysis
    reports on, and explicitly marked insufficient below them.
    """
    n1, n2 = len(x), len(y)
    result = {
        "n1": n1,
        "n2": n2,
        "u": None,
        "z": None,
        "p": None,
        "median_x": median(x),
        "median_y": median(y),
        "rank_biserial": None,
    }
    if n1 == 0 or n2 == 0:
        return result

    combined = list(x) + list(y)
    ranks = rank_average(combined)
    r1 = sum(ranks[:n1])
    u1 = r1 - n1 * (n1 + 1) / 2.0
    result["u"] = u1
    # Rank-biserial correlation: effect size in [-1, 1].
    result["rank_biserial"] = 2.0 * u1 / (n1 * n2) - 1.0

    mu = n1 * n2 / 2.0
    total = n1 + n2
    tie_term = _tie_correction(combined)
    variance = (n1 * n2 / 12.0) * ((total + 1) - tie_term / (total * (total - 1)))
    if variance <= 0:
        return result
    sigma = math.sqrt(variance)
    correction = 0.5 if abs(u1 - mu) > 0.5 else 0.0
    z = (u1 - mu - math.copysign(correction, u1 - mu)) / sigma
    result["z"] = z
    result["p"] = 2.0 * normal_sf(abs(z))
    return result


def spearman(x: Sequence[float], y: Sequence[float]) -> dict:
    """Spearman rank correlation with a t-approximation p-value."""
    n = len(x)
    result = {"n": n, "rho": None, "p": None}
    if n != len(y) or n < 3:
        return result
    rx, ry = rank_average(x), rank_average(y)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denom = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if denom == 0:
        return result
    rho = num / denom
    result["rho"] = rho
    if abs(rho) >= 1.0:
        result["p"] = 0.0
        return result
    t = rho * math.sqrt((n - 2) / (1 - rho * rho))
    result["p"] = 2.0 * student_t_sf(abs(t), n - 2)
    return result


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> dict:
    """Two-sided pooled two-proportion z-test."""
    result = {
        "k1": k1, "n1": n1, "k2": k2, "n2": n2,
        "p1": (k1 / n1) if n1 else None,
        "p2": (k2 / n2) if n2 else None,
        "diff": None, "z": None, "p": None,
    }
    if not n1 or not n2:
        return result
    p1, p2 = k1 / n1, k2 / n2
    result["diff"] = p1 - p2
    pooled = (k1 + k2) / (n1 + n2)
    denom = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if denom == 0:
        return result
    z = (p1 - p2) / denom
    result["z"] = z
    result["p"] = 2.0 * normal_sf(abs(z))
    return result


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float | None],
    n_resamples: int,
    seed: int,
    alpha: float = 0.05,
) -> dict:
    """Percentile bootstrap interval. Seeded, so reruns match exactly."""
    result = {"n": len(values), "point": None, "lo": None, "hi": None}
    if not values:
        return result
    result["point"] = statistic(values)
    if len(values) < 3:
        return result
    rng = random.Random(seed)
    size = len(values)
    draws: list[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(size)] for _ in range(size)]
        value = statistic(sample)
        if value is not None:
            draws.append(value)
    if not draws:
        return result
    result["lo"] = quantile(draws, alpha / 2.0)
    result["hi"] = quantile(draws, 1.0 - alpha / 2.0)
    return result


def binomial_sign_test(successes: int, trials: int, p_null: float = 0.5) -> dict:
    """Two-sided test that a rate differs from `p_null` (normal approximation)."""
    result = {
        "successes": successes,
        "trials": trials,
        "rate": (successes / trials) if trials else None,
        "p_null": p_null,
        "z": None,
        "p": None,
    }
    if trials <= 0:
        return result
    sigma = math.sqrt(trials * p_null * (1 - p_null))
    if sigma == 0:
        return result
    diff = successes - trials * p_null
    correction = math.copysign(0.5, diff) if abs(diff) > 0.5 else 0.0
    z = (diff - correction) / sigma
    result["z"] = z
    result["p"] = 2.0 * normal_sf(abs(z))
    return result


def verdict(p: float | None, n: int, min_n: int) -> str:
    """Deliberately conservative labelling used throughout the findings.

    These are observational comparisons on one trader's fills, with no
    correction for the number of hypotheses tested. Nothing here is described
    as more than 'evidence'.
    """
    if n < min_n:
        return "insufficient data"
    if p is None:
        return "not computable"
    if p < 0.001:
        return "strong evidence"
    if p < 0.01:
        return "moderate evidence"
    if p < 0.05:
        return "weak evidence"
    return "no evidence"
