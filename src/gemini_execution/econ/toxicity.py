"""TOXICITY_V1 — can we tell which side is about to go wrong, before it fills?

THE MECHANISM, NAMED. The first leg fills BECAUSE THAT SIDE IS BECOMING WRONG,
while the profitable complement stays unfilled precisely because it is
profitable. Every one of the 22 observed toxic fills was NO, with markout
deteriorating to -52 ticks, while an acceptable NO-complement price sat
available and untaken. That is not a pricing problem; it is a SIDE problem.

So the rule is SIDE-SPECIFIC and deliberately asymmetric. Quoting both sides or
neither is a special case, not the design:

    ALLOW_BOTH     neither side looks adverse
    ALLOW_YES      NO looks adverse; rest YES only
    ALLOW_NO       YES looks adverse; rest NO only
    ALLOW_NEITHER  both look adverse

TWO SIGNALS, AND BOTH ARE THE MARKET'S OWN REPRICING RATHER THAN A FORECAST.
This does not try to predict BTC.

  own-bid drift   the leg's OWN bid falling over the lookback. If the book is
                  already marking a side down, resting a passive BUY there is
                  standing in front of that repricing. It needs no strike, no
                  spot and no model -- only the leg's own quoted history.
  spot adversity  for an UP contract, rising spot destroys NO and supports
                  YES; falling spot does the reverse. Used only as a SIGN
                  test against a threshold, never as a magnitude forecast.

Either firing blocks that side. They are ORed rather than blended because a
blend would need weights, and weights would need fitting on data we do not yet
have.

WALK-FORWARD ONLY, AND THE FINAL SLICE IS NEVER FITTED. `walk_forward` sorts
labelled fills by time, fits the threshold on everything before a fold and
scores it on that fold alone, then advances. The last fold is scored by a
threshold chosen without ever seeing it. A threshold picked on the whole set
and then "validated" on part of it would be the standard way to manufacture an
edge, so `evaluate` refuses to be called with the same rows it was fitted on.

NOTHING HERE IS AN ML MODEL. It is two thresholded comparisons and a grid over
a preregistered candidate list.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "gemini_econ_toxicity/v1"

ALLOW_BOTH = "ALLOW_BOTH"
ALLOW_YES = "ALLOW_YES"
ALLOW_NO = "ALLOW_NO"
ALLOW_NEITHER = "ALLOW_NEITHER"
VERDICTS = (ALLOW_BOTH, ALLOW_YES, ALLOW_NO, ALLOW_NEITHER)

#: PREREGISTERED candidate grids. Fixed before any fitting.
DRIFT_GRID: Tuple[float, ...] = (0.005, 0.010, 0.020, 0.040)
VELOCITY_GRID: Tuple[float, ...] = (1e-6, 5e-6, 2e-5, 1e-4)

#: a fold must hold at least this many labelled fills to be scored
MIN_FOLD = 8

LABELS = (
    "SIDE-SPECIFIC and deliberately asymmetric: blocking NO while allowing "
    "YES is the expected output, not an edge case.",
    "Signals are the MARKET'S OWN REPRICING, not a forecast of BTC. Spot is "
    "used as a SIGN test against a threshold, never as a magnitude.",
    "Walk-forward only. The final slice is never fitted; `evaluate` refuses "
    "rows it was fitted on.",
)


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ToxicityV1:
    """Two thresholded comparisons. No weights, no fitting beyond a grid."""

    drift_threshold: float = 0.010
    velocity_threshold: float = 2e-5
    use_spot: bool = True

    def _leg_adverse(self, feat: Mapping[str, Any], leg: str) -> Dict[str, Any]:
        held = str(leg).upper()
        reasons: List[str] = []

        # 1. the leg's OWN bid falling over the lookback
        drift = _f(feat.get("held_bid_drift_60s") if feat.get("leg") == held
                   else None)
        if drift is None:
            # the feature row was assembled for the other leg; derive this
            # leg's drift from the offset samples instead
            a = (feat.get("offsets") or {}).get("t-0s") or {}
            b = (feat.get("offsets") or {}).get("t-60s") or {}
            if a.get("available") and b.get("available"):
                k = "yes_bid" if held == "YES" else "no_bid"
                x, y = _f(a.get(k)), _f(b.get(k))
                drift = None if (x is None or y is None) else x - y
        if drift is not None and drift <= -abs(self.drift_threshold):
            reasons.append("own bid fell %.4f over 60s, at or beyond %.4f"
                           % (drift, -abs(self.drift_threshold)))

        # 2. spot moving against this leg. UP contract: rising spot destroys
        #    NO and supports YES.
        vel = _f(feat.get("spot_velocity_60s")) if self.use_spot else None
        if vel is not None:
            adverse = (vel >= abs(self.velocity_threshold) if held == "NO"
                       else vel <= -abs(self.velocity_threshold))
            if adverse:
                reasons.append("spot velocity %.3g is adverse to %s"
                               % (vel, held))

        return {"adverse": bool(reasons), "reasons": reasons,
                "own_bid_drift_60s": drift, "spot_velocity_60s": vel}

    def decide(self, feat: Mapping[str, Any]) -> Dict[str, Any]:
        y = self._leg_adverse(feat, "YES")
        n = self._leg_adverse(feat, "NO")
        if y["adverse"] and n["adverse"]:
            v = ALLOW_NEITHER
        elif y["adverse"]:
            v = ALLOW_NO
        elif n["adverse"]:
            v = ALLOW_YES
        else:
            v = ALLOW_BOTH
        return {"schema": SCHEMA, "verdict": v, "YES": y, "NO": n,
                "thresholds": {"drift": self.drift_threshold,
                               "velocity": self.velocity_threshold},
                "labels": list(LABELS)}

    def allows(self, feat: Mapping[str, Any], leg: str) -> bool:
        v = self.decide(feat)["verdict"]
        held = str(leg).upper()
        if v == ALLOW_BOTH:
            return True
        if v == ALLOW_NEITHER:
            return False
        return v == (ALLOW_YES if held == "YES" else ALLOW_NO)


# --------------------------------------------------------------- scoring

def score(rule: ToxicityV1, rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """What the rule would have done to a set of labelled fills."""
    kept, blocked = [], []
    for r in rows:
        feat = r.get("features") or {}
        if rule.allows(feat, r.get("leg") or "YES"):
            kept.append(r)
        else:
            blocked.append(r)

    def _tox(rs):
        return [r for r in rs if (r["outcome"] or {}).get("toxic")]

    def _mo(rs):
        xs = [_f((r["outcome"] or {}).get("markout_60s_ticks")) for r in rs]
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else None

    def _comp(rs):
        xs = [(r["outcome"] or {}).get("complement_filled") for r in rs]
        xs = [x for x in xs if x is not None]
        return (sum(1 for x in xs if x) / len(xs)) if xs else None

    def _resid(rs):
        xs = [_f((r["outcome"] or {}).get("max_adverse_ticks")) for r in rs]
        xs = [x for x in xs if x is not None]
        return statistics.median(xs) if xs else None

    n = len(rows)
    return {
        "n": n,
        "kept": len(kept), "blocked": len(blocked),
        "opportunity_retained": (len(kept) / n) if n else None,
        "toxic_total": len(_tox(rows)),
        "toxic_avoided": len(_tox(blocked)),
        "toxic_avoided_share": ((len(_tox(blocked)) / len(_tox(rows)))
                                if _tox(rows) else None),
        "good_blocked": len([r for r in blocked
                             if not (r["outcome"] or {}).get("toxic")]),
        "good_blocked_share": (
            (len([r for r in blocked if not (r["outcome"] or {}).get("toxic")])
             / len([r for r in rows if not (r["outcome"] or {}).get("toxic")]))
            if [r for r in rows if not (r["outcome"] or {}).get("toxic")]
            else None),
        "markout_p50_unfiltered": _mo(rows),
        "markout_p50_filtered": _mo(kept),
        "complement_rate_unfiltered": _comp(rows),
        "complement_rate_filtered": _comp(kept),
        "max_adverse_p50_unfiltered": _resid(rows),
        "max_adverse_p50_filtered": _resid(kept),
        "labels": list(LABELS),
    }


def _fit(rows: Sequence[Mapping[str, Any]], *, use_spot: bool = True
         ) -> ToxicityV1:
    """Pick thresholds on TRAINING rows only, over the preregistered grid.

    Objective: maximise toxic fills avoided minus good fills blocked, so a rule
    that blocks everything scores no better than one that blocks nothing.
    """
    best, best_obj = ToxicityV1(use_spot=use_spot), None
    for d in DRIFT_GRID:
        for v in VELOCITY_GRID:
            r = ToxicityV1(drift_threshold=d, velocity_threshold=v,
                           use_spot=use_spot)
            s = score(r, rows)
            obj = s["toxic_avoided"] - s["good_blocked"]
            if best_obj is None or obj > best_obj:
                best, best_obj = r, obj
    return best


def evaluate(rule: ToxicityV1, rows: Sequence[Mapping[str, Any]], *,
             fitted_on: Optional[Sequence[Mapping[str, Any]]] = None
             ) -> Dict[str, Any]:
    """Score a rule on rows it was NOT fitted on. Refuses otherwise."""
    if fitted_on is not None:
        ids = {id(r) for r in fitted_on}
        if any(id(r) in ids for r in rows):
            return {"schema": SCHEMA, "refused": True,
                    "why": ("these rows were in the fitting set; scoring a "
                            "threshold on the data that chose it is how an "
                            "edge gets manufactured")}
    return {"schema": SCHEMA, "refused": False, **score(rule, rows)}


def walk_forward(rows: Sequence[Mapping[str, Any]], *, folds: int = 4,
                 min_fold: int = MIN_FOLD,
                 use_spot: bool = True) -> Dict[str, Any]:
    """Chronological split: fit on the past, score the next fold, advance."""
    lab = sorted((r for r in rows
                  if (r.get("outcome") or {}).get("toxic") is not None),
                 key=lambda r: r.get("t") or 0.0)
    n = len(lab)
    out: Dict[str, Any] = {"schema": SCHEMA, "labelled": n, "folds": [],
                           "labels": list(LABELS)}
    if n < min_fold * 2:
        out.update(sufficient=False,
                   why=("%d labelled fills is below %d, the minimum for even "
                        "one train/test split; no thresholds were fitted"
                        % (n, min_fold * 2)))
        return out

    size = max(min_fold, n // folds)
    marks = list(range(size, n, size)) or [size]
    for cut in marks:
        train, test = lab[:cut], lab[cut:cut + size]
        if len(test) < min_fold:
            continue
        rule = _fit(train, use_spot=use_spot)
        res = evaluate(rule, test, fitted_on=train)
        out["folds"].append({
            "train_n": len(train), "test_n": len(test),
            "train_span_t": [train[0].get("t"), train[-1].get("t")],
            "test_span_t": [test[0].get("t"), test[-1].get("t")],
            "thresholds": {"drift": rule.drift_threshold,
                           "velocity": rule.velocity_threshold},
            "result": res,
        })
    out["sufficient"] = bool(out["folds"])
    if out["folds"]:
        oos = [f["result"] for f in out["folds"]]
        def _m(k):
            xs = [r.get(k) for r in oos if r.get(k) is not None]
            return statistics.median(xs) if xs else None
        out["oos_summary"] = {
            "folds_scored": len(oos),
            "toxic_avoided_share_p50": _m("toxic_avoided_share"),
            "good_blocked_share_p50": _m("good_blocked_share"),
            "opportunity_retained_p50": _m("opportunity_retained"),
            "markout_p50_unfiltered": _m("markout_p50_unfiltered"),
            "markout_p50_filtered": _m("markout_p50_filtered"),
            "complement_rate_unfiltered": _m("complement_rate_unfiltered"),
            "complement_rate_filtered": _m("complement_rate_filtered"),
            "separation": ("the filtered markout must beat the unfiltered one "
                           "OUT OF SAMPLE on most folds, or there is no rule "
                           "here worth deploying"),
        }
    return out
