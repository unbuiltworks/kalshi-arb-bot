"""L5_OBSERVATION: recent population vs canonical history, identical definitions.

SOURCE-SUPPORTED FACTS ONLY. Fills, timestamps, outcome, price, size, cohort,
reconstructed inventory, the observed cash wedge, settlement. No inferred
maker/taker, no hypothetical quote, no assumed decision rule enters anything
here -- `fee_bearing` is the OBSERVED cash wedge and is never relabelled.

THE POINT. An independent review of ~4,000 recent trades reported much sparser
behaviour than our canonical reconstruction. That is not accepted as canonical.
This module rebuilds BOTH populations under ONE set of definitions so the
comparison cannot be an artifact of how each was counted, and classifies every
material gap as REGIME_CHANGE / SAMPLE_DEFINITION / DATA_COVERAGE / UNRESOLVED.

COHORTS STAY SEPARATE. Pooling would make a discrepancy disappear rather than
explain it.
"""
from __future__ import annotations

import collections
import statistics as st
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.redemption import audit as A

COHORTS = ("BTC5m", "ETH5m", "BTC15m", "ETH15m")
FEE_RATE = 0.07
BEARING_CUT = FEE_RATE * 0.5
RECENT_N = 4000


def fills(cohorts: Sequence[str] = COHORTS) -> List[dict]:
    """Every source-supported BUY fill, chronological."""
    out: List[dict] = []
    for r in A.iter_activity():
        if str(r.get("type") or "").upper() != "TRADE":
            continue
        if str(r.get("side") or "").upper() != "BUY":
            continue
        w = A.window_of(r)
        if not w:
            continue
        cohort = f"{w[0].upper()}{w[1]}"
        if cohort not in cohorts:
            continue
        px = float(r.get("price") or 0)
        sz = float(r.get("size") or 0)
        usd = float(r.get("usdcSize") or 0)
        if not (px > 0 and sz > 0 and 0 < px < 1):
            continue
        den = px * (1.0 - px)
        out.append({
            "cid": str(r.get("conditionId") or ""),
            "t": int(r.get("timestamp") or 0),
            "cohort": cohort, "window_end": w[2], "horizon": w[1],
            "price": px, "size": sz, "usdc": usd,
            "outcome": str(r.get("outcome")),
            "up_price": px if str(r.get("outcome")) == "Up" else round(1.0 - px, 6),
            "fee_usd": usd - px * sz,
            # OBSERVED cash wedge. NOT a maker/taker label.
            "fee_bearing": (((usd - px * sz) / sz) / den) > BEARING_CUT if den > 0 else False,
        })
    out.sort(key=lambda x: x["t"])
    return out


def windows_from(fs: Sequence[dict]) -> List[dict]:
    """Group fills into windows and derive the per-window observation set."""
    g: Dict[str, List[dict]] = collections.defaultdict(list)
    for f in fs:
        g[f["cid"]].append(f)
    out: List[dict] = []
    for cid, s in g.items():
        s.sort(key=lambda x: x["t"])
        up = sum(x["size"] for x in s if x["outcome"] == "Up")
        dn = sum(x["size"] for x in s if x["outcome"] != "Up")
        cu = sum(x["usdc"] for x in s if x["outcome"] == "Up")
        cd = sum(x["usdc"] for x in s if x["outcome"] != "Up")
        tot = up + dn
        switches = sum(1 for i in range(1, len(s)) if s[i]["outcome"] != s[i - 1]["outcome"])
        runs, cur = [], 1
        for i in range(1, len(s)):
            if s[i]["outcome"] == s[i - 1]["outcome"]:
                cur += 1
            else:
                runs.append(cur); cur = 1
        runs.append(cur)
        gaps = [s[i]["t"] - s[i - 1]["t"] for i in range(1, len(s))]
        out.append({
            "cid": cid, "cohort": s[0]["cohort"], "t": s[0]["t"],
            "window_end": s[0]["window_end"],
            "n_fills": len(s), "switches": switches,
            "one_sided": (up == 0 or dn == 0),
            "runs": runs, "gaps": gaps,
            "first_price": s[0]["price"],
            "first_is_dearer": s[0]["price"] > 0.5,
            "matched_fraction": (2 * min(up, dn) / tot) if tot else None,
            "residual_abs": abs(up - dn),
            "residual_rel": ((up - dn) / tot) if tot else None,
            "effective_pair_cost": ((cu / up) + (cd / dn)) if (up > 0 and dn > 0) else None,
            "fee_bearing_frac": sum(1 for x in s if x["fee_bearing"]) / len(s),
            "clips": [x["size"] for x in s],
            "prices": [x["price"] for x in s],
        })
    out.sort(key=lambda r: r["t"])
    return out


def eligible_windows(cohort: str, t0: int, t1: int) -> int:
    """The DENOMINATOR, where observable: windows sit on a fixed clock, so the
    count of windows that EXISTED in a span is derivable without a book feed.

    This is the difference between "L5 chose 20 of 25" and "20 of 500".
    """
    dur = A.DURATION_S[cohort[3:].lower() if cohort[:3] in ("BTC", "ETH") else "5m"]
    return max(0, int((t1 - t0) // dur))


def q(xs: Sequence[float], p: float) -> Optional[float]:
    v = sorted(x for x in xs if x is not None)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def describe(ws: List[dict], fs: List[dict], label: str) -> Dict[str, Any]:
    if not ws:
        return {"label": label, "windows": 0}
    t0, t1 = min(w["t"] for w in ws), max(w["window_end"] for w in ws)
    cohort = ws[0]["cohort"]
    elig = eligible_windows(cohort, t0, t1)
    allgaps = [g for w in ws for g in w["gaps"]]
    allruns = [r for w in ws for r in w["runs"]]
    clips = [c for w in ws for c in w["clips"]]
    prices = [p for w in ws for p in w["prices"]]
    two = [w for w in ws if not w["one_sided"]]
    return {
        "label": label, "cohort": cohort,
        "span_days": round((t1 - t0) / 86400, 1),
        "fills": len(fs),
        "windows_touched": len(ws),
        "windows_eligible_observable": elig,
        "touch_rate": (len(ws) / elig) if elig else None,
        "fills_per_window_p50": q([w["n_fills"] for w in ws], .5),
        "fills_per_window_mean": round(st.mean([w["n_fills"] for w in ws]), 2),
        "one_sided_pct": round(100.0 * sum(1 for w in ws if w["one_sided"]) / len(ws), 1),
        "two_sided_pct": round(100.0 * len(two) / len(ws), 1),
        "switches_per_window_p50_two_sided": q([w["switches"] for w in two], .5),
        "fills_per_run_p50": q(allruns, .5),
        "inter_fill_s_p50": q(allgaps, .5),
        "inter_fill_s_p90": q(allgaps, .9),
        "price_p25": q(prices, .25), "price_p50": q(prices, .5), "price_p75": q(prices, .75),
        "first_fill_dearer_pct": round(100.0 * sum(1 for w in ws if w["first_is_dearer"]) / len(ws), 1),
        "clip_p25": q(clips, .25), "clip_p50": q(clips, .5), "clip_p75": q(clips, .75),
        "clip_top5": [c for c, _ in collections.Counter(clips).most_common(5)],
        "matched_fraction_p50": q([w["matched_fraction"] for w in ws], .5),
        "residual_abs_p50": q([w["residual_abs"] for w in ws], .5),
        "residual_rel_p50": q([abs(w["residual_rel"]) for w in ws if w["residual_rel"] is not None], .5),
        "effective_pair_p50": q([w["effective_pair_cost"] for w in ws], .5),
        "fee_bearing_pct": round(100.0 * sum(1 for f in fs if f["fee_bearing"]) / len(fs), 1),
    }


CLASSES = ("REGIME_CHANGE", "SAMPLE_DEFINITION", "DATA_COVERAGE", "UNRESOLVED")
