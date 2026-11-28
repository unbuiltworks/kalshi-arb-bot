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
MODEL_KIND = "l5_fidelity_v0_2/conditional_empirical"

# PREREGISTERED SIZE BINS -- fixed HERE, before any size score exists, and not
# to be re-cut after seeing which edges flatter the model.
#
# WHY BINS AND NOT A MEDIAN. v0/v0.1 predicted `predicted_size_p50` and scored
# `next_size - p50`. On a distribution running p10=2, p50=14, p90=49, p99=80,
# that point estimate cannot be right and the error is uninformative: the very
# first scored row predicted 15.1 against an actual 80.0 for a "+64.9 miss"
# that says nothing about whether the model understood the state. A size
# prediction is a DISTRIBUTION; it is scored as one, with the probability mass
# the model put on the bin L5 actually landed in.
#
# The edges are round decade-ish numbers chosen on the CANONICAL population
# only (which ends 2026-08-31, so live scoring cannot have informed them), and
# they spread that population 3.7 / 10.6 / 25.3 / 34.1 / 21.6 / 4.6 per cent --
# no bin starved, none swallowing the mass.
SIZE_BIN_EDGES = (1.0, 3.0, 10.0, 30.0, 60.0)
SIZE_BIN_LABELS = ("<1", "1-3", "3-10", "10-30", "30-60", ">=60")

# TWO ESTIMANDS, NEVER MIXED. Scoring one model against the other's sampling is
# what produced a predicted 0.93 against an actual 0.46 ACT-within-30s.
#
#   A  FILL_ANCHORED   P(next action within h | state immediately after a fill)
#      This is the fresh-gap CDF and it is directly comparable to the canonical
#      inter-fill training distribution.
#
#   B  ARBITRARY_STATE P(acts within h | no action for elapsed t, state)
#      A RESIDUAL waiting time, not a fresh gap. It is the conditional survival
#      1 - S(t+h)/S(t), never the unconditioned CDF. Freezing at ordinary
#      recorder timestamps and scoring with A's distribution is exactly the
#      length-biased error being corrected here.
#
# WAIT is one of the main reverse-engineering questions, so B is the one we
# ultimately need; A is the clean anchor and is operational first.
ESTIMAND_A = "fill_anchored"
ESTIMAND_B = "arbitrary_state"

# v0 -> v0.1: A TRAIN/SERVE SKEW THAT DESTROYED THE STRONGEST FEATURE.
# v0 fitted the key with last_outcome = s[i-1] (the fill BEFORE the current
# one) while the runner serves last_outcome = the MOST RECENT fill, s[i]. The
# model was therefore trained on one meaning of "last outcome" and queried with
# another. It matters enormously: on 206,512 OOS BTC15m pairs,
# P(next=Up | last=Up) = 0.733 and P(next=Up | last=Down) = 0.261 -- AUC 0.736 --
# and v0 was emitting p_up near 0.50 in exactly those states.
# `since` is also dropped from the key: at fit time only the COMPLETED gap is
# known, while at serve time it is the elapsed-so-far. Conditioning on it fit a
# quantity that cannot be observed at prediction time. It is retained for the
# TIMING table, where the completed gap is the correct target.


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
    """The key must mean the SAME THING at fit and at serve. `since` is not in
    it: only the completed gap is known when fitting, and only the elapsed-so-
    far when serving, so keying on it trains against an unobservable."""
    return "|".join((tte_bucket(tte), resid_bucket(resid_rel),
                     f"last={last_outcome or 'none'}", price_bucket(price)))


def size_bin(v: float) -> str:
    """Which preregistered bin a size falls in. Edges are right-open."""
    return SIZE_BIN_LABELS[bisect.bisect_right(SIZE_BIN_EDGES, v)]


def size_hist(values: Sequence[float]) -> Dict[str, float]:
    """Empirical probability over the preregistered bins.

    Laplace-smoothed by one pseudo-count per bin so a bin unseen in a state is
    improbable rather than IMPOSSIBLE. Without it a single surprise observation
    scores as log(0) and one live row could dominate the whole scoreboard.
    """
    c = collections.Counter(size_bin(float(v)) for v in values)
    tot = len(values) + len(SIZE_BIN_LABELS)
    return {b: (c[b] + 1) / tot for b in SIZE_BIN_LABELS}


def version_of(cohort: str, tables: Dict[str, Any]) -> str:
    """Version string = CONTENT HASH OF THE WHOLE MODEL.

    v0.1 hashed only {cohort, kind, n_fills, n_windows, first 2000 state KEYS}.
    That covered the state-key SCHEMA and none of the distributions, so every
    gap, every size and every side count in the tables could be replaced and
    the version string would not move -- verified by doing exactly that. The
    whole v0 -> v0.1 lineage discipline rests on "a model change gets a new
    version", and under that hash the guarantee was simply false.

    Hashing the serialized tables makes it true: any change to any number in
    the model changes the version, so a rebuilt, re-subsampled or corrupted
    artifact can never be served under an identity it no longer has.
    """
    blob = json.dumps({"cohort": cohort, "kind": MODEL_KIND, "tables": tables},
                      sort_keys=True, separators=(",", ":")).encode()
    return f"v0.2-{hashlib.sha256(blob).hexdigest()[:12]}"


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
                # the state at the moment of predicting s[i+1] is the state
                # AFTER s[i]: the most recent fill is f itself, and the residual
                # already includes it.
                if f["outcome"] == "Up": up += f["size"]
                else: dn += f["size"]
                tot = up + dn
                rel = ((up - dn) / tot) if tot else None
                k = state_key(tte=f["window_end"] - f["t"], since=None,
                              resid_rel=rel, last_outcome=f["outcome"],
                              price=f["price"])
                if i + 1 < len(s):
                    g = float(s[i + 1]["t"] - f["t"])
                    gaps[k].append(g); base_gaps.append(g)
                    nxt = s[i + 1]["outcome"]
                    side[k][nxt] += 1; base_side[nxt] += 1
                    size[k].append(s[i + 1]["size"]); base_size.append(s[i + 1]["size"])
        tables = {
            "gaps": {k: sorted(v) for k, v in gaps.items() if len(v) >= 20},
            "side": {k: dict(v) for k, v in side.items() if sum(v.values()) >= 20},
            "size": {k: sorted(v) for k, v in size.items() if len(v) >= 20},
            "size_bins": {k: size_hist(v) for k, v in size.items() if len(v) >= 20},
            "base_gaps": sorted(base_gaps),
            "base_side": dict(base_side),
            "base_size": sorted(base_size),
            "base_size_bins": size_hist(base_size),
            "n_fills": len(fills),
            "n_windows": len(by_window),
        }
        return cls(cohort, tables, version_of(cohort, tables))

    # ------------------------------------------------------------- predict
    @staticmethod
    def _p_within(sorted_gaps: Sequence[float], h: float) -> float:
        """ESTIMAND A: the fresh-gap CDF, valid only from a fill instant."""
        if not sorted_gaps:
            return 0.0
        return bisect.bisect_right(sorted_gaps, h) / len(sorted_gaps)

    @staticmethod
    def _p_within_given_elapsed(sorted_gaps: Sequence[float], h: float,
                                elapsed: float) -> Optional[float]:
        """ESTIMAND B: P(T <= elapsed+h | T > elapsed) = 1 - S(elapsed+h)/S(elapsed).

        Returns None -- never a number -- when no training gap survived past
        `elapsed`, because the conditional is genuinely undefined there and a
        fabricated 1.0 would read as certainty.
        """
        n = len(sorted_gaps)
        if n == 0:
            return None
        n_gt_t = n - bisect.bisect_right(sorted_gaps, elapsed)
        if n_gt_t == 0:
            return None
        n_gt_th = n - bisect.bisect_right(sorted_gaps, elapsed + h)
        return 1.0 - (n_gt_th / n_gt_t)

    def predict(self, *, tte: float, since: Optional[float],
                resid_rel: Optional[float], last_outcome: Optional[str],
                price: Optional[float],
                estimand: str = ESTIMAND_A,
                elapsed_s: Optional[float] = None) -> Dict[str, Any]:
        """estimand A ignores `elapsed_s`; estimand B REQUIRES it."""
        k = state_key(tte=tte, since=since, resid_rel=resid_rel,
                      last_outcome=last_outcome, price=price)
        g = self.tables["gaps"].get(k)
        tier = "state" if g else "cohort_base"
        g = g or self.tables["base_gaps"]
        sd = self.tables["side"].get(k) or self.tables["base_side"]
        sz = self.tables["size"].get(k) or self.tables["base_size"]
        szb = (self.tables.get("size_bins", {}).get(k)
               or self.tables.get("base_size_bins")
               or size_hist(sz))
        # TRUE state n, not the length of a capped sample. `gaps`/`size` are
        # subsampled per state for artifact size while `side` keeps full
        # counts, so len(g) understates the evidence behind a state and must
        # not be reported as if it were the population.
        state_n_true = sum(self.tables["side"].get(k, {}).values()) or None
        tot = sum(sd.values()) or 1
        p_up = sd.get("Up", 0) / tot
        def q(v, p): return v[min(len(v) - 1, int(p * len(v)))] if v else None
        if estimand == ESTIMAND_A:
            pact = {str(h): round(self._p_within(g, h), 6) for h in HORIZONS_S}
            pact_reason = None
        elif estimand == ESTIMAND_B:
            if elapsed_s is None:
                raise ValueError("estimand B requires elapsed_s")
            raw = {str(h): self._p_within_given_elapsed(g, h, elapsed_s)
                   for h in HORIZONS_S}
            pact = {k: (round(v, 6) if v is not None else None)
                    for k, v in raw.items()}
            pact_reason = (None if all(v is not None for v in raw.values()) else
                           f"no training gap survived past elapsed={elapsed_s:.1f}s "
                           f"in this state: the conditional is undefined")
        else:
            raise ValueError(estimand)
        return {
            "estimand": estimand,
            "elapsed_s": elapsed_s,
            "p_act_within_reason": pact_reason,
            "model_version": self.version,
            "model_kind": MODEL_KIND,
            "cohort": self.cohort,
            "state_key": k,
            "lookup_tier": tier,
            "state_n_sampled": len(g),
            "state_n": state_n_true if state_n_true else len(g),
            "p_act_within": pact,
            "predicted_outcome": ("Up" if p_up >= 0.5 else "Down"),
            "p_outcome_up": round(p_up, 6),
            # THE size prediction is the distribution. p50/IQR are kept as
            # human-readable summaries and are NOT what gets scored.
            "predicted_size_bins": szb,
            "predicted_size_mode_bin": max(szb, key=szb.get),
            "size_bin_edges": list(SIZE_BIN_EDGES),
            "predicted_size_p50_summary_only": q(sz, .5),
            "predicted_size_range_summary_only": [q(sz, .25), q(sz, .75)],
            "predicted_next_gap_s_p50": q(g, .5),
            "predicted_next_gap_s_range": [q(g, .25), q(g, .75)],
        }


    # ------------------------------------------------------------ artifact
    def save(self, path: str) -> str:
        """Freeze the fitted tables. The 24/7 runner loads THIS, not the 163 MB
        raw tape: the model is frozen and versioned, so shipping the tape to
        re-derive it every boot would be both slow and a chance to drift."""
        import gzip
        with gzip.open(path, "wt") as fh:
            json.dump({"cohort": self.cohort, "version": self.version,
                       "tables": self.tables}, fh)
        return path

    @classmethod
    def load(cls, path: str) -> "FidelityV0":
        import gzip
        with gzip.open(path, "rt") as fh:
            d = json.load(fh)
        return cls(d["cohort"], d["tables"], d["version"])


def fit_all(fills_by_cohort: Dict[str, List[dict]]) -> Dict[str, FidelityV0]:
    return {c: FidelityV0.fit(f, c) for c, f in fills_by_cohort.items() if f}
