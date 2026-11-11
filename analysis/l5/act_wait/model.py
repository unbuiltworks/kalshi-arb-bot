"""Stage 1 (ACT/WAIT) and Stage 3 (TIMING), scored separately and never merged.

WHY A SHRUNK FREQUENCY TABLE AND NOT SOMETHING FANCIER. numpy, scipy, sklearn
and pandas are all absent from this environment, so anything gradient-based
would arrive as a new dependency and a new failure surface. It would also be
the wrong shape: the deployed v2 artifact is already a shrunk conditional
frequency table (`full -> base -> marginal`, alpha=10), and building Stage 1 in
the same family means the two stages compose without one of them being a black
box next to the other. Every probability here can be traced to a count.

THE THREE STAGES ARE KEPT APART ON PURPOSE:

  Stage 1  ACT/WAIT      P(L5 acts within H | state knowable at T)
  Stage 2  ACTION | ACT  the EXISTING transition machinery, unchanged
  Stage 3  TIMING        P(gap bucket | state), from the same evidence

Collapsing them into one probability would hide exactly the failure this work
exists to fix -- a model that is confident about WHAT L5 does being read as
though it were confident about WHETHER L5 does anything.

THE FEATURE THAT CARRIES THE SIGNAL is `since_last_s`, bucketed on the same
boundaries the artifact's own `cadence_modal` uses. L5's selectivity is a timing
behaviour, so "how long since it last acted" is the natural predictor and it is
knowable at T.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.polymarket_l5 import action_model as am
from analysis.l5.act_wait.dataset import Opportunity

#: Same boundaries as `cadence_modal` in the deployed artifact.
GAP_BUCKETS = (("gap<2s", 2.0), ("gap2-10", 10.0), ("gap10-30", 30.0),
               ("gap>30", float("inf")))

ALPHA = 10.0          # the v2 artifact's own shrinkage strength


def gap_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "gap>30"
    for name, hi in GAP_BUCKETS:
        if seconds < hi:
            return name
    return "gap>30"


def since_bucket(seconds: float) -> str:
    return gap_bucket(seconds)


def state_of(o: Opportunity) -> str:
    """The deployed state key, on features knowable at T.

    Uses `action_model.state_key` rather than a second implementation, so a
    Stage-1 state and a Stage-2 state are the same string and the lookup tiers
    are comparable across the two.
    """
    return am.state_key(o.last_price, o.tte_s, o.residual)


def full_key(o: Opportunity) -> str:
    return f"{state_of(o)}|{o.last_rel}|{o.last_size}|{since_bucket(o.since_last_s)}"


def base_key(o: Opportunity) -> str:
    return f"{state_of(o)}|{since_bucket(o.since_last_s)}"


class ActModel:
    """P(act within H). full -> base -> marginal, shrunk, counts all the way down."""

    def __init__(self, horizon: int):
        self.h = horizon
        self.full: Dict[str, List[int]] = {}
        self.base: Dict[str, List[int]] = {}
        self.marg: List[int] = [0, 0]

    def fit(self, rows: Iterable[Opportunity]) -> "ActModel":
        for o in rows:
            y = o.acted_within[self.h]
            for table, key in ((self.full, full_key(o)), (self.base, base_key(o))):
                c = table.setdefault(key, [0, 0])
                c[y] += 1
            self.marg[y] += 1
        return self

    def _rate(self, c: Optional[Sequence[int]], prior: float) -> float:
        if not c:
            return prior
        n = c[0] + c[1]
        return (c[1] + ALPHA * prior) / (n + ALPHA)

    def predict(self, o: Opportunity) -> Tuple[float, str]:
        """(probability, tier). The tier is reported so coverage can be read
        off the predictions instead of assumed."""
        m = self.marg[0] + self.marg[1]
        prior = (self.marg[1] / m) if m else 0.0
        b = self.base.get(base_key(o))
        p_base = self._rate(b, prior)
        f = self.full.get(full_key(o))
        if f:
            return self._rate(f, p_base), "full"
        if b:
            return p_base, "base"
        return prior, "marginal"


class TimingModel:
    """Stage 3: P(next gap bucket | state), scored on its own terms."""

    def __init__(self):
        self.full: Dict[str, Dict[str, int]] = {}
        self.base: Dict[str, Dict[str, int]] = {}
        self.marg: Dict[str, int] = {}

    def fit(self, rows: Iterable[Opportunity]) -> "TimingModel":
        for o in rows:
            g = gap_bucket(o.next_gap_s)
            self.marg[g] = self.marg.get(g, 0) + 1
            for table, key in ((self.full, full_key(o)), (self.base, base_key(o))):
                d = table.setdefault(key, {})
                d[g] = d.get(g, 0) + 1
        return self

    def predict(self, o: Opportunity) -> Tuple[str, Dict[str, float], str]:
        def norm(d: Mapping[str, int]) -> Dict[str, float]:
            t = sum(d.values()) or 1
            return {k: v / t for k, v in d.items()}
        prior = norm(self.marg)
        f, b = self.full.get(full_key(o)), self.base.get(base_key(o))
        if f and sum(f.values()) >= 5:
            dist, tier = _shrink(norm(f), prior, sum(f.values())), "full"
        elif b:
            dist, tier = _shrink(norm(b), prior, sum(b.values())), "base"
        else:
            dist, tier = prior, "marginal"
        return max(dist, key=dist.get), dist, tier


def _shrink(obs: Mapping[str, float], prior: Mapping[str, float], n: int
            ) -> Dict[str, float]:
    w = n / (n + ALPHA)
    keys = set(obs) | set(prior)
    return {k: w * obs.get(k, 0.0) + (1 - w) * prior.get(k, 0.0) for k in keys}


# --------------------------------------------------------------------------
# baselines, so the model has to beat something

def baseline_always(_o: Opportunity) -> float:
    return 1.0


def make_baseline_base_rate(train: Sequence[Opportunity], h: int):
    r = sum(o.acted_within[h] for o in train) / max(1, len(train))
    return lambda _o: r


def make_baseline_cadence(train: Sequence[Opportunity], h: int):
    """Cadence ONLY: elapsed-since-last, nothing about price, tte or inventory.
    If this matches the full model, the state features are not earning their
    place."""
    tab: Dict[str, List[int]] = {}
    for o in train:
        c = tab.setdefault(since_bucket(o.since_last_s), [0, 0])
        c[o.acted_within[h]] += 1
    prior = sum(o.acted_within[h] for o in train) / max(1, len(train))

    def f(o: Opportunity) -> float:
        c = tab.get(since_bucket(o.since_last_s))
        if not c:
            return prior
        return (c[1] + ALPHA * prior) / (c[0] + c[1] + ALPHA)
    return f


def make_baseline_exact_state(train: Sequence[Opportunity], h: int):
    """Exact/full-state only: answer where we have that exact key, otherwise
    predict WAIT. This is the counterfactual the owner asked about, as a
    baseline rather than as a controller change."""
    tab: Dict[str, List[int]] = {}
    for o in train:
        c = tab.setdefault(full_key(o), [0, 0])
        c[o.acted_within[h]] += 1

    def f(o: Opportunity) -> float:
        c = tab.get(full_key(o))
        if not c:
            return 0.0
        return c[1] / (c[0] + c[1])
    return f
