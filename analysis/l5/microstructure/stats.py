"""Nonparametric statistics, with effect sizes and per-window reproducibility.

WHY NONPARAMETRIC. Quantities, depths and tick movements here are skewed,
bounded and spiky; a mean and a t-test would describe none of them honestly.
Everything below is medians, quantiles, rank correlation and Cliff's delta.

WHY REPRODUCIBILITY IS A FIRST-CLASS OUTPUT. A relationship measured on the
pooled sample can be produced entirely by one window. `reproduces` recomputes
the relationship INSIDE each confirmation window and reports how many show the
same direction, so a pooled effect that rests on one window is visible as
such.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

import math


def clean(v: Sequence[Any]) -> List[float]:
    return [float(x) for x in v if x is not None and not (
        isinstance(x, float) and math.isnan(x))]


def qs(v: Sequence[Any]) -> Dict[str, Any]:
    c = sorted(clean(v))
    if not c:
        return {"n": 0}
    def at(p):
        return round(c[int(p * (len(c) - 1))], 6)
    return {"n": len(c), "min": at(0.0), "p10": at(0.10), "p25": at(0.25),
            "p50": at(0.50), "p75": at(0.75), "p90": at(0.90),
            "max": at(1.0),
            "mean": round(sum(c) / len(c), 6),
            "share_negative": round(sum(1 for x in c if x < 0) / len(c), 4),
            "share_zero": round(sum(1 for x in c if x == 0) / len(c), 4),
            "share_positive": round(sum(1 for x in c if x > 0) / len(c), 4)}


def _ranks(v: List[float]) -> List[float]:
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(x: Sequence[Any], y: Sequence[Any]) -> Dict[str, Any]:
    """Rank correlation on the pairs where BOTH values exist."""
    px, py = [], []
    for a, b in zip(x, y):
        if a is None or b is None:
            continue
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if math.isnan(fa) or math.isnan(fb):
            continue
        px.append(fa)
        py.append(fb)
    n = len(px)
    if n < 10:
        return {"n": n, "rho": None, "note": "too few pairs"}
    rx, ry = _ranks(px), _ranks(py)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return {"n": n, "rho": None, "note": "no variance"}
    rho = num / (dx * dy)
    # Fisher z approximation for a rough two-sided p
    z = 0.5 * math.log((1 + rho) / (1 - rho)) * math.sqrt(n - 3) \
        if abs(rho) < 1 else float("inf")
    p = math.erfc(abs(z) / math.sqrt(2)) if math.isfinite(z) else 0.0
    return {"n": n, "rho": round(rho, 4), "approx_p": round(p, 6),
             "direction": ("+" if rho > 0 else "-" if rho < 0 else "0")}


def cliffs_delta(a: Sequence[Any], b: Sequence[Any],
                 cap: int = 4000) -> Dict[str, Any]:
    """P(a>b) - P(a<b). -1..+1, 0 = no separation. Subsampled above `cap`."""
    ca, cb = clean(a), clean(b)
    if len(ca) < 5 or len(cb) < 5:
        return {"n_a": len(ca), "n_b": len(cb), "delta": None,
                "note": "too few"}
    if len(ca) > cap:
        step = len(ca) // cap
        ca = ca[::step]
    if len(cb) > cap:
        step = len(cb) // cap
        cb = cb[::step]
    sb = sorted(cb)
    import bisect
    gt = lt = 0
    for x in ca:
        lo = bisect.bisect_left(sb, x)
        hi = bisect.bisect_right(sb, x)
        gt += lo
        lt += len(sb) - hi
    tot = len(ca) * len(sb)
    d = (gt - lt) / tot
    mag = ("negligible" if abs(d) < 0.147 else
           "small" if abs(d) < 0.33 else
           "medium" if abs(d) < 0.474 else "large")
    return {"n_a": len(ca), "n_b": len(sb), "delta": round(d, 4),
            "magnitude": mag,
            "median_a": round(sorted(ca)[len(ca) // 2], 6),
            "median_b": round(sb[len(sb) // 2], 6)}


def by_bucket(rows: List[dict], key: str, target: str,
              edges: Sequence[float]) -> List[Dict[str, Any]]:
    """Median `target` within quantile-free fixed buckets of `key`."""
    out = []
    for lo, hi in zip((None,) + tuple(edges), tuple(edges) + (None,)):
        sel = []
        for r in rows:
            v = r.get(key)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if lo is not None and v < lo:
                continue
            if hi is not None and v >= hi:
                continue
            t = r.get(target)
            if t is not None:
                sel.append(float(t))
        out.append({"bucket": "%s..%s" % (lo, hi), "n": len(sel),
                    "median_target": (round(sorted(sel)[len(sel) // 2], 6)
                                      if sel else None)})
    return out


def reproduces(rows: List[dict], window_key: str,
               fn: Callable[[List[dict]], Optional[float]],
               min_n: int = 20) -> Dict[str, Any]:
    """Recompute `fn` inside each window and report direction agreement."""
    per: Dict[str, Optional[float]] = {}
    groups: Dict[str, List[dict]] = {}
    for r in rows:
        groups.setdefault(r[window_key], []).append(r)
    for w, g in sorted(groups.items()):
        per[w] = fn(g) if len(g) >= min_n else None
    vals = [v for v in per.values() if v is not None]
    pos = sum(1 for v in vals if v > 0)
    neg = sum(1 for v in vals if v < 0)
    return {"windows_measured": len(vals), "windows_total": len(per),
            "positive": pos, "negative": neg,
            "direction_agreement": (round(max(pos, neg) / len(vals), 4)
                                    if vals else None),
            "dominant_direction": ("+" if pos > neg else
                                   "-" if neg > pos else "tie"),
            "per_window": {k: (round(v, 4) if v is not None else None)
                           for k, v in per.items()}}
