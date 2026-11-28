"""Is the prospective sample mature? The criterion is in MATURITY.md and was
fixed before the data existed.

Pure Python -- no numpy, no scipy -- and no tuning: nothing here feeds back into
the controller. It decides only when to STOP measuring.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# --- the preregistered constants. Changing one is changing the criterion.
PRECISION_PP = 2.0          # 95% CI half-width target, percentage points
MATERIAL_PP = 5.0           # where the operational conclusion could change
DOUBLING_PP = 2.0           # M4
TREND_PP = 3.0              # M5
MIN_OBS = 25_000            # M1
MIN_MARKETS = 100
MIN_HOURS = 3.0
MIN_BLOCKS = 12
BLOCK_S = 15 * 60           # batch-means block
TREND_WINDOW_S = 30 * 60
TREND_WINDOWS = 4
NO_STATUS_MAX_FRAC = 0.05   # M0
V1_STEADY_MAX = 0.99        # M2
GUARD_OBS = 100_000         # stopping guard
CHECKPOINTS = (1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000)

#: two-sided 95% Student-t, df -> t. Beyond the table, 1.96 is close enough.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
        13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
        19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042, 40: 2.021, 60: 2.000}


def t95(df: int) -> float:
    if df <= 0:
        return float("inf")
    if df in _T95:
        return _T95[df]
    keys = [k for k in sorted(_T95) if k <= df]
    return _T95[keys[-1]] if df < 60 else 1.96


def batch_means_ci(blocks: Sequence[Tuple[int, int]]) -> dict:
    """95% CI on a rate from NON-OVERLAPPING batch means.

    `blocks` is [(numerator, denominator), ...]. Rows 20 s apart in the same
    market are not independent draws, so a binomial CI would be far too narrow;
    the block is the independent unit, and the spread BETWEEN blocks carries
    the autocorrelation the rows hide.
    """
    usable = [(a, b) for a, b in blocks if b > 0]
    B = len(usable)
    num = sum(a for a, _ in usable)
    den = sum(b for _, b in usable)
    if not den:
        return {"rate": None, "half_width_pp": None, "blocks": B,
                "reason": "no observations"}
    rate = num / den
    if B < 2:
        return {"rate": rate, "half_width_pp": None, "blocks": B,
                "reason": "need >= 2 complete blocks for a spread"}
    means = [a / b for a, b in usable]
    mbar = sum(means) / B
    var = sum((m - mbar) ** 2 for m in means) / (B - 1)
    se = math.sqrt(var / B)
    return {"rate": round(rate, 6), "block_mean": round(mbar, 6),
            "half_width_pp": round(100.0 * t95(B - 1) * se, 4),
            "blocks": B, "observations": den, "reason": None}


def blocks_of(rows: Sequence[Mapping[str, Any]], key: str,
              block_s: float = BLOCK_S,
              complete_only: bool = True) -> List[Tuple[int, int]]:
    """Bucket per-cycle counts into non-overlapping time blocks.

    A trailing partial block is DROPPED by default: a block that covers two
    minutes is not an equal draw beside one that covers fifteen, and letting it
    in would understate the spread exactly when the sample is youngest.
    """
    if not rows:
        return []
    t0 = float(rows[0]["t"])
    buckets: Dict[int, List[int]] = {}
    span: Dict[int, List[float]] = {}
    for r in rows:
        i = int((float(r["t"]) - t0) // block_s)
        n, d = buckets.setdefault(i, [0, 0])
        buckets[i] = [n + int(r.get(key) or 0), d + int(r.get("rows") or 0)]
        s = span.setdefault(i, [float(r["t"]), float(r["t"])])
        span[i] = [min(s[0], float(r["t"])), max(s[1], float(r["t"]))]
    out = []
    for i in sorted(buckets):
        if complete_only and i == max(buckets) and \
                (span[i][1] - span[i][0]) < 0.5 * block_s:
            continue
        out.append(tuple(buckets[i]))
    return out


def doubling(rows: Sequence[Mapping[str, Any]], key: str) -> dict:
    """|p(N) - p(N/2)|, cumulative, on the most recent doubling."""
    tot = sum(int(r.get("rows") or 0) for r in rows)
    if tot < 2:
        return {"delta_pp": None, "reason": "sample too small"}
    half, seen, num_h, den_h = tot / 2.0, 0, 0, 0
    for r in rows:
        if seen >= half:
            break
        num_h += int(r.get(key) or 0)
        den_h += int(r.get("rows") or 0)
        seen += int(r.get("rows") or 0)
    num = sum(int(r.get(key) or 0) for r in rows)
    if not den_h or not tot:
        return {"delta_pp": None, "reason": "no first half"}
    return {"rate_half": round(num_h / den_h, 6), "rate_full": round(num / tot, 6),
            "delta_pp": round(abs(100.0 * (num / tot - num_h / den_h)), 4),
            "n_half": den_h, "n_full": tot, "reason": None}


def trend(rows: Sequence[Mapping[str, Any]], key: str) -> dict:
    """The last TREND_WINDOWS non-overlapping windows vs the cumulative rate."""
    b = blocks_of(rows, key, block_s=TREND_WINDOW_S)
    if len(b) < TREND_WINDOWS:
        return {"max_abs_dev_pp": None, "windows": len(b),
                "reason": f"need {TREND_WINDOWS} complete windows"}
    den = sum(d for _, d in b)
    cum = sum(n for n, _ in b) / den if den else None
    last = b[-TREND_WINDOWS:]
    devs = [round(100.0 * (n / d - cum), 4) for n, d in last if d]
    return {"cumulative": round(cum, 6), "window_devs_pp": devs,
            "max_abs_dev_pp": round(max(abs(x) for x in devs), 4) if devs else None,
            "windows": len(b), "reason": None}


def assess(rows: Sequence[Mapping[str, Any]], *, restarts: int = 0,
           act_artifact_missing: int = 0, no_status: int = 0,
           distinct_markets: Optional[int] = None) -> dict:
    """M0-M5 against the preregistered criterion. All six must hold."""
    obs = sum(int(r.get("rows") or 0) for r in rows)
    cycles = len(rows)
    hours = ((float(rows[-1]["t"]) - float(rows[0]["t"])) / 3600.0
             if len(rows) > 1 else 0.0)
    ci11 = batch_means_ci(blocks_of(rows, "proposed"))
    ci1 = batch_means_ci(blocks_of(rows, "v1_would_have"))
    d11, d1 = doubling(rows, "proposed"), doubling(rows, "v1_would_have")
    t11, t1 = trend(rows, "proposed"), trend(rows, "v1_would_have")

    def ok(v, lim):
        return v is not None and v <= lim

    m0 = {"restarts": restarts, "act_artifact_missing": act_artifact_missing,
          "no_status_frac": round(no_status / cycles, 4) if cycles else None,
          "pass": (restarts == 0 and act_artifact_missing == 0
                   and (not cycles or no_status / cycles < NO_STATUS_MAX_FRAC))}
    m1 = {"observations": obs, "distinct_markets": distinct_markets,
          "hours": round(hours, 3), "blocks": ci11.get("blocks"),
          "pass": (obs >= MIN_OBS and (distinct_markets or 0) >= MIN_MARKETS
                   and hours >= MIN_HOURS
                   and (ci11.get("blocks") or 0) >= MIN_BLOCKS)}
    v1rate = ci1.get("rate")
    m2 = {"v1_rate": v1rate,
          "departed_one": v1rate is not None and v1rate < V1_STEADY_MAX,
          "pass": (v1rate is not None and v1rate < V1_STEADY_MAX
                   and ok(ci1.get("half_width_pp"), PRECISION_PP)
                   and ok(d1.get("delta_pp"), DOUBLING_PP)),
          "note": ("every V1 WAIT is a risk cap; while the book is near flat "
                   "V1 sits at 1.00 and any reduction is overstated")}
    m3 = {"v1_1": ci11, "v1": ci1, "target_pp": PRECISION_PP,
          "pass": (ok(ci11.get("half_width_pp"), PRECISION_PP)
                   and ok(ci1.get("half_width_pp"), PRECISION_PP))}
    m4 = {"v1_1": d11, "v1": d1, "target_pp": DOUBLING_PP,
          "pass": ok(d11.get("delta_pp"), DOUBLING_PP)
                  and ok(d1.get("delta_pp"), DOUBLING_PP)}
    m5 = {"v1_1": t11, "v1": t1, "target_pp": TREND_PP,
          "pass": ok(t11.get("max_abs_dev_pp"), TREND_PP)
                  and ok(t1.get("max_abs_dev_pp"), TREND_PP)}
    tests = {"M0_validity": m0, "M1_coverage": m1, "M2_baseline_steady": m2,
             "M3_precision": m3, "M4_doubling": m4, "M5_no_trend": m5}
    mature = all(t["pass"] for t in tests.values())
    return {
        "mature": mature,
        "failing": [k for k, t in tests.items() if not t["pass"]],
        "tests": tests,
        "next_checkpoint": next((c for c in CHECKPOINTS if c > obs), None),
        "guard_tripped": obs >= GUARD_OBS and not m3["pass"],
        "criterion": "analysis/l5/v1_1_shadow/MATURITY.md, preregistered 2026-09-16",
        "labels": ("decides when to STOP measuring, never what the controller "
                   "does; nothing here is tuned from this sample"),
    }
