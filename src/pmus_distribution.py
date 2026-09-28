"""Distribution statistics reported ALONGSIDE every mean, from n=14 onward.

WHY THIS MODULE EXISTS. At n=13 the PROFIT_LOCK_5 mean crossed the $5 business
target at +7.1100/window -- and the mean EXCLUDING one window (2300z, which alone
returned +53.48) was +3.2010, exactly the n=12 figure. A mean that one observation
moves by $3.91 is not a result. So every mean is now published with the shape of
the distribution behind it, and no new cutoff is invented for "not dependent on one
observation": the complete distribution is reported and the reader judges.

`trimmed_mean` drops the single highest and single lowest observation (a 1-from-
each-end trim, not a percentile rule) and is undefined below n=3.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Dict, List, Optional, Sequence


def _q(x: D, places: str = "0.0001") -> str:
    return str(x.quantize(D(places)))


def median(xs: Sequence[D]) -> D:
    s = sorted(xs)
    n = len(s)
    if not n:
        return D(0)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def describe(values: Sequence[D], *, labels: Optional[Sequence[str]] = None,
             locked: Optional[Sequence[bool]] = None) -> Dict[str, object]:
    """The full shape behind a mean. `values` are per-window figures."""
    xs = list(values)
    n = len(xs)
    if not n:
        return {"n": 0}
    labels = list(labels or [str(i) for i in range(n)])
    s = sorted(xs)
    mean = sum(xs) / D(n)
    best_i = max(range(n), key=lambda i: xs[i])
    worst_i = min(range(n), key=lambda i: xs[i])
    total = sum(xs)
    ex_best = ([x for i, x in enumerate(xs) if i != best_i])
    out = {
        "n": n,
        "mean": _q(mean),
        "median": _q(median(xs)),
        "trimmed_mean_drop_1_each_end": (_q(sum(s[1:-1]) / D(n - 2))
                                         if n >= 3 else None),
        "positive_window_rate": "%d of %d" % (sum(1 for x in xs if x > 0), n),
        "worst_window": {"window": labels[worst_i], "value": _q(xs[worst_i])},
        "best_window": {"window": labels[best_i], "value": _q(xs[best_i])},
        # share of TOTAL P&L contributed by the best window. Only meaningful when
        # the total is positive; a negative or zero total makes a "share"
        # misleading, so it is reported as None rather than as a number.
        "best_window_share_of_total_pnl": (
            _q(xs[best_i] / total) if total > 0 else None),
        "mean_excluding_best_window": (_q(sum(ex_best) / D(n - 1))
                                       if n >= 2 else None),
        "all_values_sorted": [_q(x) for x in s],
    }
    if locked is not None:
        out["lock_hit_rate"] = "%d of %d" % (sum(1 for b in locked if b), n)
    return out
