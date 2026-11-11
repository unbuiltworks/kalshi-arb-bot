"""Small dependency-free statistics kit.

Deliberately stdlib-only: this study must run anywhere, and the tests it needs
(rank-sum, proportion, correlation, bootstrap) are short enough to implement
transparently. All p-values are two-sided normal/t approximations and are
reported alongside sample sizes -- with a few dozen windows, effect sizes and
n matter far more than the exact p.
"""

from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Optional, Sequence, Tuple


def clean(values: Sequence[Optional[float]]) -> List[float]:
    out = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(number) or math.isinf(number):
            continue
        out.append(number)
    return out


def mean(values: Sequence[float]) -> Optional[float]:
    data = clean(values)
    return sum(data) / len(data) if data else None


def median(values: Sequence[float]) -> Optional[float]:
    data = sorted(clean(values))
    if not data:
        return None
    mid = len(data) // 2
    if len(data) % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    data = sorted(clean(values))
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    position = q * (len(data) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (position - low)


def describe(values: Sequence[Optional[float]]) -> Dict[str, Optional[float]]:
    data = clean(values)
    return {
        "n": len(data),
        "mean": mean(data),
        "median": median(data),
        "p25": quantile(data, 0.25),
        "p75": quantile(data, 0.75),
        "min": min(data) if data else None,
        "max": max(data) if data else None,
        "stdev": _stdev(data),
    }


def _stdev(data: Sequence[float]) -> Optional[float]:
    if len(data) < 2:
        return None
    mu = sum(data) / len(data)
    return math.sqrt(sum((value - mu) ** 2 for value in data) / (len(data) - 1))


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_sided_p_from_z(z: float) -> float:
    return max(0.0, min(1.0, 2.0 * (1.0 - normal_cdf(abs(z)))))


def _rank(data: Sequence[float]) -> List[float]:
    order = sorted(range(len(data)), key=lambda index: data[index])
    ranks = [0.0] * len(data)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and data[order[end + 1]] == data[order[position]]:
            end += 1
        average_rank = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = average_rank
        position = end + 1
    return ranks


def mann_whitney(a: Sequence[float], b: Sequence[float]) -> Dict[str, Optional[float]]:
    """Two-sided rank-sum test with tie correction (normal approximation)."""
    x, y = clean(a), clean(b)
    n1, n2 = len(x), len(y)
    result: Dict[str, Optional[float]] = {
        "n1": n1,
        "n2": n2,
        "median1": median(x),
        "median2": median(y),
        "u": None,
        "z": None,
        "p_value": None,
        "rank_biserial": None,
    }
    if n1 == 0 or n2 == 0:
        return result
    combined = x + y
    ranks = _rank(combined)
    rank_sum_x = sum(ranks[:n1])
    u1 = rank_sum_x - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u = min(u1, u2)
    mu = n1 * n2 / 2.0
    tie_term = 0.0
    counts: Dict[float, int] = {}
    for value in combined:
        counts[value] = counts.get(value, 0) + 1
    total = n1 + n2
    for count in counts.values():
        if count > 1:
            tie_term += count ** 3 - count
    variance = (n1 * n2 / 12.0) * ((total + 1) - tie_term / (total * (total - 1))) if total > 1 else 0.0
    if variance <= 0:
        result.update({"u": u, "z": None, "p_value": None})
        return result
    z = (u1 - mu) / math.sqrt(variance)
    result.update(
        {
            "u": u,
            "u1": u1,
            "u2": u2,
            "z": z,
            "p_value": two_sided_p_from_z(z),
            # Rank-biserial: +1 means group 1 dominates group 2.
            "rank_biserial": 2.0 * u1 / (n1 * n2) - 1.0,
        }
    )
    return result


def two_proportion_test(x1: int, n1: int, x2: int, n2: int) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "x1": x1,
        "n1": n1,
        "p1": (x1 / n1) if n1 else None,
        "x2": x2,
        "n2": n2,
        "p2": (x2 / n2) if n2 else None,
        "diff": None,
        "z": None,
        "p_value": None,
    }
    if not n1 or not n2:
        return result
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    denominator = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    result["diff"] = p1 - p2
    if denominator == 0:
        return result
    z = (p1 - p2) / denominator
    result.update({"z": z, "p_value": two_sided_p_from_z(z)})
    return result


def one_proportion_test(successes: int, n: int, p0: float = 0.5) -> Dict[str, Optional[float]]:
    result: Dict[str, Optional[float]] = {
        "successes": successes,
        "n": n,
        "p_hat": (successes / n) if n else None,
        "p0": p0,
        "z": None,
        "p_value": None,
        "ci95": None,
    }
    if not n:
        return result
    p_hat = successes / n
    denominator = math.sqrt(p0 * (1 - p0) / n)
    if denominator > 0:
        z = (p_hat - p0) / denominator
        result.update({"z": z, "p_value": two_sided_p_from_z(z)})
    # Wilson score interval -- behaves sensibly at small n and extreme rates.
    z95 = 1.959963985
    centre = (p_hat + z95 * z95 / (2 * n)) / (1 + z95 * z95 / n)
    half = (
        z95
        * math.sqrt(p_hat * (1 - p_hat) / n + z95 * z95 / (4 * n * n))
        / (1 + z95 * z95 / n)
    )
    result["ci95"] = [max(0.0, centre - half), min(1.0, centre + half)]
    return result


def spearman(x: Sequence[float], y: Sequence[float]) -> Dict[str, Optional[float]]:
    pairs = [
        (float(a), float(b))
        for a, b in zip(x, y)
        if a is not None and b is not None and not _bad(a) and not _bad(b)
    ]
    n = len(pairs)
    result: Dict[str, Optional[float]] = {"n": n, "rho": None, "p_value": None}
    if n < 3:
        return result
    rank_x = _rank([pair[0] for pair in pairs])
    rank_y = _rank([pair[1] for pair in pairs])
    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n
    numerator = sum((rank_x[i] - mean_x) * (rank_y[i] - mean_y) for i in range(n))
    denominator = math.sqrt(
        sum((value - mean_x) ** 2 for value in rank_x)
        * sum((value - mean_y) ** 2 for value in rank_y)
    )
    if denominator == 0:
        return result
    rho = numerator / denominator
    result["rho"] = rho
    if abs(rho) < 1.0:
        t = rho * math.sqrt((n - 2) / (1 - rho * rho))
        # Normal approximation to the t distribution; fine for n >= 20, rough below.
        result["p_value"] = two_sided_p_from_z(t)
    else:
        result["p_value"] = 0.0
    return result


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], Optional[float]] = median,
    iterations: int = 2000,
    seed: int = 20260829,
    alpha: float = 0.05,
) -> Optional[Tuple[float, float]]:
    data = clean(values)
    if len(data) < 3:
        return None
    rng = random.Random(seed)
    estimates = []
    size = len(data)
    for _ in range(iterations):
        sample = [data[rng.randrange(size)] for _ in range(size)]
        estimate = statistic(sample)
        if estimate is not None:
            estimates.append(estimate)
    if not estimates:
        return None
    estimates.sort()
    low = estimates[int((alpha / 2) * (len(estimates) - 1))]
    high = estimates[int((1 - alpha / 2) * (len(estimates) - 1))]
    return (low, high)


def _bad(value: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return True
    return math.isnan(number) or math.isinf(number)


def confidence_label(n: int, p_value: Optional[float], effect_present: bool) -> str:
    """Conservative verbal confidence, driven mostly by sample size."""
    if n < 8:
        return "insufficient-data"
    if not effect_present:
        return "no-evidence"
    if p_value is None:
        return "descriptive-only"
    if n < 20:
        return "suggestive-small-n" if p_value < 0.05 else "no-evidence"
    if p_value < 0.01:
        return "moderate" if n < 100 else "strong"
    if p_value < 0.05:
        return "weak" if n < 100 else "moderate"
    return "no-evidence"
