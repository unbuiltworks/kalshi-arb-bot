"""L5_FIDELITY_v0 -- a falsifiable model of L5's NEXT action from public state.

FIDELITY IS JUDGED BY BEHAVIOURAL PREDICTION, NOT PROFITABILITY. Nothing here is
tuned on P&L and no adapted rule (residual saturation lives in L5_ADAPTED) is
allowed in.

CHRONOLOGICAL SEPARATION IS STRUCTURAL, NOT PROMISED. v0 is fitted only on the
canonical population, which ENDS 2026-08-31. Every prospective prediction is
scored on live data from 2026-09-15 onward. The two cannot overlap, so leakage
is impossible by construction rather than by discipline.

PREREGISTERED HORIZONS. BTC15m's median inter-fill is 2 s, so "did L5 act?" means
something completely different at 1 s and at 30 s. The horizons are fixed HERE,
before any score is computed, and are not to be re-chosen after seeing which one
flatters the model.

COHORTS ARE NOT POOLED. BTC15m is the primary adequately powered cohort; the
other three are fitted and reported separately and are never merged into it for
sample size.
"""
from __future__ import annotations

import bisect
import collections
import hashlib
import json
import statistics as st
from typing import Any, Dict, List, Optional, Sequence

# PREREGISTERED -- fixed before scoring, never re-chosen afterwards.
HORIZONS_S = (1.0, 5.0, 15.0, 30.0)
COHORTS = ("BTC15m", "BTC5m", "ETH5m", "ETH15m")
MODEL_KIND = "l5_fidelity_v0/conditional_empirical"


def tte_bucket(tte: float) -> str:
    if tte <= 30: return "tte<=30"
    if tte <= 60: return "tte30-60"
    if tte <= 180: return "tte60-180"
    if tte <= 420: return "tte180-420"
    return "tte>420"


def gap_bucket(since: Optional[float]) -> str:
    if since is None: return "gap_none"
    if since <= 1: return "gap<=1"
    if since <= 3: return "gap1-3"
    if since <= 10: return "gap3-10"
    if since <= 30: return "gap10-30"
    return "gap>30"


def resid_bucket(rel: Optional[float]) -> str:
    if rel is None: return "resid_none"
    if abs(rel) <= 0.02: return "flat"
    return "long_up" if rel > 0 else "long_dn"


def price_bucket(p: Optional[float]) -> str:
    if p is None: return "px_none"
    if p < 0.2: return "px<20"
    if p < 0.4: return "px20-40"
    if p < 0.6: return "px40-60"
    if p < 0.8: return "px60-80"
    return "px>=80"


def state_key(*, tte: float, since: Optional[float], resid_rel: Optional[float],
              last_outcome: Optional[str], price: Optional[float]) -> str:
    return "|".join((tte_bucket(tte), gap_bucket(since), resid_bucket(resid_rel),
                     f"last={last_outcome or 'none'}", price_bucket(price)))


class FidelityV0:
    """Conditional empirical tables. Deliberately simple: v0 exists to START
    prospective scoring, not to be the final model."""

    def __init__(self, cohort: str, tables: Dict[str, Any], version: str):
        self.cohort = cohort
        self.tables = tables
        self.version = version

    # ---------------------------------------------------------------- fit
    @classmethod
    def fit(cls, fills: Sequence[dict], cohort: str) -> "FidelityV0":
        """`fills` are canonical BUY fills for ONE cohort, chronological."""
        by_window: Dict[str, List[dict]] = collections.defaultdict(list)
        for f in fills:
            by_window[f["cid"]].append(f)
        gaps: Dict[str, List[float]] = collections.defaultdict(list)
        side: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        size: Dict[str, List[float]] = collections.defaultdict(list)
        base_gaps: List[float] = []
        base_side: collections.Counter = collections.Counter()
        base_size: List[float] = []
        for cid, s in by_window.items():
            s.sort(key=lambda x: x["t"])
            up = dn = 0.0
            for i, f in enumerate(s):
                since = (f["t"] - s[i - 1]["t"]) if i else None
                tot = up + dn
                rel = ((up - dn) / tot) if tot else None
                k = state_key(tte=f["window_end"] - f["t"], since=since,
                              resid_rel=rel,
                              last_outcome=(s[i - 1]["outcome"] if i else None),
                              price=f["price"])
                if i + 1 < len(s):
                    g = float(s[i + 1]["t"] - f["t"])
                    gaps[k].append(g); base_gaps.append(g)
                    nxt = s[i + 1]["outcome"]
                    side[k][nxt] += 1; base_side[nxt] += 1
                    size[k].append(s[i + 1]["size"]); base_size.append(s[i + 1]["size"])
                if f["outcome"] == "Up": up += f["size"]
                else: dn += f["size"]
        tables = {
            "gaps": {k: sorted(v) for k, v in gaps.items() if len(v) >= 20},
            "side": {k: dict(v) for k, v in side.items() if sum(v.values()) >= 20},
            "size": {k: sorted(v) for k, v in size.items() if len(v) >= 20},
            "base_gaps": sorted(base_gaps),
            "base_side": dict(base_side),
            "base_size": sorted(base_size),
            "n_fills": len(fills),
            "n_windows": len(by_window),
        }
        blob = json.dumps({"cohort": cohort, "kind": MODEL_KIND,
                           "n": tables["n_fills"], "w": tables["n_windows"],
                           "states": sorted(tables["gaps"])[:2000]},
                          sort_keys=True).encode()
        return cls(cohort, tables, f"v0-{hashlib.sha256(blob).hexdigest()[:12]}")

    # ------------------------------------------------------------- predict
    @staticmethod
    def _p_within(sorted_gaps: Sequence[float], h: float) -> float:
        if not sorted_gaps:
            return 0.0
        return bisect.bisect_right(sorted_gaps, h) / len(sorted_gaps)

    def predict(self, *, tte: float, since: Optional[float],
                resid_rel: Optional[float], last_outcome: Optional[str],
                price: Optional[float]) -> Dict[str, Any]:
        k = state_key(tte=tte, since=since, resid_rel=resid_rel,
                      last_outcome=last_outcome, price=price)
        g = self.tables["gaps"].get(k)
        tier = "state" if g else "cohort_base"
        g = g or self.tables["base_gaps"]
        sd = self.tables["side"].get(k) or self.tables["base_side"]
        sz = self.tables["size"].get(k) or self.tables["base_size"]
        tot = sum(sd.values()) or 1
        p_up = sd.get("Up", 0) / tot
        def q(v, p): return v[min(len(v) - 1, int(p * len(v)))] if v else None
        return {
            "model_version": self.version,
            "model_kind": MODEL_KIND,
            "cohort": self.cohort,
            "state_key": k,
            "lookup_tier": tier,
            "state_n": len(g),
            "p_act_within": {str(h): round(self._p_within(g, h), 6) for h in HORIZONS_S},
            "predicted_outcome": ("Up" if p_up >= 0.5 else "Down"),
            "p_outcome_up": round(p_up, 6),
            "predicted_size_p50": q(sz, .5),
            "predicted_size_range": [q(sz, .25), q(sz, .75)],
            "predicted_next_gap_s_p50": q(g, .5),
            "predicted_next_gap_s_range": [q(g, .25), q(g, .75)],
        }


def fit_all(fills_by_cohort: Dict[str, List[dict]]) -> Dict[str, FidelityV0]:
    return {c: FidelityV0.fit(f, c) for c, f in fills_by_cohort.items() if f}
