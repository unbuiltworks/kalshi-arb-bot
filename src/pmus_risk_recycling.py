"""Is the engine recycling risk faster than it manufactures residuals?

OWNER 2026-09-10: "add one metric I'd make prominent: risk-recycling ratio =
completed acquisition risk returned / new residual worst-case risk created...
If that ratio is poor, more breadth simply gets us to -$15 faster. If it's
strong, quarantine can turn breadth into dramatically more repetitions
without increasing the risk envelope."

WHY THIS IS THE RIGHT METRIC NOW. The quarantine replay narrowed the
acceleration question to one number. With the county-cricket residual
quarantined, the same stall window fitted 9 more acquisitions on the
EMPIRICAL arm and 1 on the PESSIMISTIC arm -- and the entire gap was
completion: 6 of the 9 consumed ZERO budget, because a completed pair below
par has floor min(up,dn) - spent >= 0. Completion is not merely profitable,
it is what RETURNS headroom. So the throughput of the whole engine is set by
how fast acquisitions become completed pairs, not by how many slots exist.

HOW IT IS COMPUTED, and why it needs no event classification.

Each market's charge against the portfolio budget is exactly

    contribution = min(0, guaranteed_settlement_floor(inv))
                 = min(0, min(up, dn) - spent)

which is the same quantity unsettled_floor_total already sums. Watch that
number per market across an interval:

    contribution RISES toward 0  ->  risk RETURNED   (a completion)
    contribution FALLS           ->  risk CREATED    (an acquisition/extend)

    ratio = sum(returned) / |sum(created)|

No event needs to be labelled a "completion" or an "acquisition". The budget
itself says which happened, so a mislabelled action cannot flatter the ratio.

READING IT:
    > 1.0   recycling faster than manufacturing -- breadth buys repetitions
    = 1.0   treading water
    < 1.0   breadth gets to the loss stop faster, and more slots make it worse

A ratio computed over an interval in which NOTHING was created is undefined,
not infinite, and is reported as None. Dividing by zero here would print a
spectacular number for a period in which the engine did nothing.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Mapping, Optional, Sequence

ZERO = D("0")


def budget_contribution(floor) -> D:
    """What this market charges the portfolio budget: min(0, floor).

    A market whose floor is POSITIVE (a completed pair below par) charges
    nothing. It does not subsidise another market's loss -- the same rule
    unsettled_floor_total already enforces by taking only negatives.
    """
    return min(ZERO, D(str(floor)))


def floor_of(up, dn, spent) -> D:
    return min(D(str(up)), D(str(dn))) - D(str(spent))


def delta(before, after) -> dict:
    """One market's change in budget charge between two observations."""
    b, a = budget_contribution(before), budget_contribution(after)
    d = a - b
    return {
        "contribution_before_usd": str(b),
        "contribution_after_usd": str(a),
        "risk_returned_usd": str(d if d > 0 else ZERO),
        "risk_created_usd": str(-d if d < 0 else ZERO),
    }


def recycling(intervals: Sequence[Mapping[str, Any]]) -> dict:
    """The ratio over a set of per-market before/after observations.

    Each interval: {"slug", "floor_before", "floor_after"}.
    """
    returned = created = ZERO
    per_market = []
    for iv in intervals:
        d = delta(iv.get("floor_before", 0), iv.get("floor_after", 0))
        returned += D(d["risk_returned_usd"])
        created += D(d["risk_created_usd"])
        per_market.append({"slug": iv.get("slug"), **d})
    ratio = (returned / created) if created > 0 else None
    return {
        "risk_returned_usd": str(returned),
        "risk_created_usd": str(created),
        "risk_recycling_ratio": (str(ratio.quantize(D("0.0001")))
                                 if ratio is not None else None),
        "verdict": _verdict(ratio, created),
        "markets": len(per_market),
        "per_market": per_market,
        "labels": (
            "returned/created from min(0, min(up,dn)-spent) per market: the "
            "budget itself says which happened, so no action needs labelling. "
            ">1 recycles faster than it manufactures; <1 means more breadth "
            "reaches the loss stop sooner. Undefined (None) when nothing was "
            "created -- never infinite."),
    }


def _verdict(ratio: Optional[D], created: D) -> str:
    if created <= 0:
        return ("undefined: no new residual worst-case risk was created in "
                "this interval")
    if ratio > 1:
        return "recycling faster than manufacturing: breadth buys repetitions"
    if ratio == 1:
        return "treading water"
    return ("manufacturing faster than recycling: more breadth reaches the "
            "loss stop sooner")


# ------------------------------------------------------------- the funnel
FUNNEL_STAGES = (
    "first_leg_fills",
    "completions_le_1",
    "residuals_opened",
    "completed_pair_headroom_returned_usd",
    "quarantine_entries",
    "additional_acquisitions_admitted",
    "portfolio_gate_refusals",
    "settlements",
    "trading_pnl_usd",
    "credited_incentives_usd",
)


def funnel_report(period: str, stages: Mapping[str, Any],
                  intervals: Sequence[Mapping[str, Any]] = ()) -> dict:
    """The owner's per-hour / per-day report, with the ratio made prominent.

    OWNER: "Don't judge the new system primarily on number of orders." Order
    and attempt counts are deliberately absent from FUNNEL_STAGES; every
    stage is an economic outcome or a scheduler event.

    A stage that was not measured is None, never 0. An unmeasured stage and a
    stage that genuinely did not happen are different facts, and collapsing
    them is how a quiet engine comes to look like a working one.
    """
    rec = recycling(intervals)
    return {
        "kind": "risk_recycling_report",
        "period": period,
        "risk_recycling_ratio": rec["risk_recycling_ratio"],
        "verdict": rec["verdict"],
        "risk_returned_usd": rec["risk_returned_usd"],
        "risk_created_usd": rec["risk_created_usd"],
        "funnel": {k: stages.get(k) for k in FUNNEL_STAGES},
        "unmeasured_stages": sorted(k for k in FUNNEL_STAGES
                                    if stages.get(k) is None),
        "labels": rec["labels"],
    }
