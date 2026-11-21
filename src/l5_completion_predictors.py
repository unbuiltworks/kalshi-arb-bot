"""Which PRE-ORDER observable actually predicts an acceptable completion?

OWNER (#167): "No hindsight filters. We need something observable BEFORE
placing the first order that raises the probability that a first fill becomes
an economically acceptable completion." And: finish by selecting the single
highest-leverage falsifiable change, not another sprawling experiment.

TWO RESULTS, ONE NEGATIVE AND ONE STRONG
-----------------------------------------
Measured on 83 resolvable persistence windows from the flow-ranked shadow
(deployment 35050df5, 2026-09-09T15:07-15:53Z), plus the R5F settled record.

1. WITHIN the flow-ranked roster, the flow score does NOT discriminate.

       low  pre-window score 0.252-0.448   984 obs   23 opps   2.3374%
       high pre-window score 0.527-0.750  1008 obs   26 opps   2.5794%
       ratio 1.10x

   This does not disprove the selector -- the roster only pins the top 12, so
   the comparison is high against very-high with the dead markets already
   excluded, and the between-population gain is real and large (0.19% ->
   ~2.2%). It says the selector works as a FLOOR, not as a ranking: crossing
   the threshold is what pays, and pushing flow higher buys nothing. So there
   is no reason to invest further in ranking sophistication.

2. ADVERSE COMPLEMENT MOVEMENT collapses completion opportunity ~4x.

       complement moved against us    18 win   432 obs    3 opps   0.6944%
       complement stayed or improved  65 win  1560 obs   46 opps   2.9487%
       ratio 4.25x

   This is the strongest discriminator found anywhere in the record, and it
   corroborates the settled evidence independently: of the 33 entry_band_left
   decisions in R5F, 27 (82%) occurred in the three markets that lost money,
   and `r5f_day1_sequence.json` already attributes the band's protective
   effect to price movement rather than price level -- "the price ran through
   the range in minutes... which is a selection fact, not an L5 fidelity
   gain."

WHY THIS IS A MECHANISM AND NOT YET A LEVER
--------------------------------------------
Deterioration is measured DURING the window, so result 2 is not itself a
pre-order signal. It establishes that price movement destroys completion; it
does NOT establish that movement is predictable from before entry. The lever
requires one further link -- that a market drifting before entry keeps
drifting after it -- and that link is UNTESTED. Reporting the 4.25x as a
pre-order result would be exactly the hindsight the owner ruled out.
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence

ADVERSE = "complement_moved_against_us"
STABLE = "complement_stable_or_improved"

# measured 2026-09-09, carried so a rerun that disagrees is visible
OBSERVED = {
    "within_roster_flow_score": {"low_rate": 0.023374, "high_rate": 0.025794,
                                 "ratio": 1.10, "verdict": "does not rank"},
    "adverse_complement_movement": {"adverse_rate": 0.006944,
                                    "stable_rate": 0.029487, "ratio": 4.25,
                                    "verdict": "strongest discriminator found"},
    "entry_band_concentration": {"firings": 33, "in_losing_markets": 27,
                                 "share": 0.82},
}

LABELS = {
    "mechanism_not_lever": "adverse movement is measured DURING the window; "
                           "it shows movement destroys completion, not that "
                           "movement is predictable before entry",
    "no_invented_fills": "an opportunity is the touch reaching our price with "
                         "a print in the same bucket; never a fill",
}


def classify_window(win: Mapping[str, Any]) -> Optional[str]:
    """Did the complement run away from us during this window?

    Positive deterioration means the complement got MORE expensive than where
    we would have entered -- it moved against the completion.
    """
    if not isinstance(win, Mapping) or "buckets" not in win:
        return None
    if win.get("unresolvable_buckets"):
        return None
    worst = 0.0
    for b in win["buckets"].values():
        dv = b.get("deterioration_vs_entry")
        if dv is not None:
            worst = max(worst, float(dv))
    return ADVERSE if worst > 0.0 else STABLE


def opportunity_rate(wins: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    obs = opp = 0
    for w in wins:
        for b in w["buckets"].values():
            obs += b["observations"]
            opp += b["opportunities"]
    return {"windows": len(wins), "observations": obs, "opportunities": opp,
            "rate": round(opp / obs, 6) if obs else None}


def split_by_movement(wins: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The 4.25x split, recomputed rather than remembered."""
    groups: Dict[str, List[Mapping[str, Any]]] = {ADVERSE: [], STABLE: []}
    for w in wins:
        k = classify_window(w)
        if k:
            groups[k].append(w)
    out = {k: opportunity_rate(v) for k, v in groups.items()}
    a, s = out[ADVERSE]["rate"], out[STABLE]["rate"]
    out["ratio_stable_over_adverse"] = (round(s / a, 4) if (a and s) else None)
    out["labels"] = LABELS
    return out
