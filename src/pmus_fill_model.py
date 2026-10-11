"""P(fill) for a resting maker order, calibrated on our own real outcomes.

OWNER GO (#167): "expected L5 economics = probability of completing both legs
x pair economics - probability of getting stranded x residual loss +
incentives - fees... calibrate the model against those actual outcomes rather
than inventing fills from displayed quotes... separate estimates for
first-leg and complement fills over 30/60/120 seconds."

WHY THIS EXISTS
---------------
The completion shadow priced pairs at the touch and assumed every tranche
filled. That made its headline metric degenerate: `marginal_pair_cost` came
out EXACTLY equal to `1 - spread` on 400 of 501 live rows (79.8%), because
buying one side at the bid and the other at 1-ask is arithmetically 1 minus
the spread. Under that metric a market with a 0.69-wide book scored a 0.31
"good completion" -- an unfillable quote dressed as an edge -- while tight,
actively traded markets scored near par and looked worse.

Pair cost at the touch is spread. The quantity that separates a real
opportunity from a mirage is the PROBABILITY the two legs actually fill.

CALIBRATED ON REAL LABELS, NOT ON QUOTES
-----------------------------------------
Every estimate here comes from orders we actually submitted: 965 of them
across the R5F campaign, each with a relation, a rest time and a terminal
outcome. A fill is an event; an order cancelled without filling is CENSORED
at its rest time, not counted as a failure to fill for all time. That
distinction is the whole estimator, because the campaign cancelled most
orders within a few seconds: a naive "fills / orders" ratio at t=10s
compares 59 fills against a risk set that has already collapsed from 380 to
100 and reports P=0.59 where the true figure is far lower.

THE HORIZON WE CANNOT ESTIMATE, STATED BEFORE ANY NUMBER
---------------------------------------------------------
The owner asked for 30 / 60 / 120 second estimates. The campaign cannot
support the last two:

    longest rest observed, over all 965 orders : 32.2 s
    orders that rested >= 60 s                 : 0
    orders that rested >= 120 s                : 0

Beyond ~32 s there is no data at all -- not sparse data, none. Any 60 s or
120 s figure would be an extrapolation of a 30 s curve, and this module
REFUSES to emit one rather than let an invented number travel downstream
with a real one. `estimable_horizon_s` is derived from the data and
`survival_at` returns None past it.

That limit is itself a finding. L5's median complement gap is ~47 s -- 15
seconds beyond the longest we have EVER rested an order. We have never once
operated at the horizon his behaviour lives at, so "L5 is patient and we are
not" is not a hypothesis this campaign can test. It needs a forward run that
rests longer, which is a decision for the owner, not an assumption for a
model.

WHAT THE MODEL DOES AND DOES NOT CLAIM
---------------------------------------
It estimates P(fill by t) for a first leg and for a complement, from our
outcomes, inside the observed horizon. It does NOT contain a flow term:
the campaign never recorded contemporaneous flow, so a flow coefficient
cannot be calibrated from it. Flow enters FORWARD, from the completion
shadow, and its effect on fill probability is a hypothesis this module makes
testable -- not one it assumes.
"""

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

OUT_DIR = os.path.join("analysis", "l5")
FIRST_LEG_RELATIONS = ("open", "extend")
COMPLEMENT_RELATIONS = ("complete",)
REQUESTED_HORIZONS_S = (30.0, 60.0, 120.0)
L5_MEDIAN_COMPLEMENT_GAP_S = 47.0

LABELS = {
    "event": "the order filled; time-to-event is its rest time",
    "censored": "the order was cancelled or hit its deadline unfilled; it is "
                "censored at its rest time, NOT counted as a permanent no-fill",
    "unestimable": "beyond the longest rest ever observed there is no data; "
                   "the estimator returns None instead of extrapolating",
}


def _d(x: Any) -> Optional[D]:
    if x is None:
        return None
    try:
        return D(str(x))
    except ArithmeticError:
        return None


def observation(order: Mapping[str, Any]) -> Optional[Tuple[float, bool]]:
    """(rest_time, filled) for one order, or None if it carries no rest time."""
    t = order.get("rest_s")
    if t is None:
        return None
    return float(t), (_d(order.get("filled_qty")) or D(0)) > 0


def kaplan_meier(obs: Sequence[Tuple[float, bool]]) -> List[Dict[str, float]]:
    """Right-censored survival curve for "still unfilled".

    At each distinct time with at least one fill, the risk set is every order
    still resting at that instant. Orders cancelled earlier have already left
    it -- they are not evidence that a longer rest would have failed, which is
    exactly the error a fills/orders ratio makes.
    """
    if not obs:
        return []
    times = sorted({t for t, filled in obs if filled})
    surv, out = 1.0, []
    for t in times:
        at_risk = sum(1 for tt, _ in obs if tt >= t)
        events = sum(1 for tt, f in obs if f and tt == t)
        if at_risk <= 0:
            continue
        surv *= (1.0 - events / at_risk)
        out.append({"t": t, "at_risk": at_risk, "events": events,
                    "survival": surv, "p_fill_by_t": 1.0 - surv})
    return out


def estimable_horizon(obs: Sequence[Tuple[float, bool]]) -> float:
    """The longest rest actually observed. Nothing past it is estimable."""
    return max((t for t, _ in obs), default=0.0)


def survival_at(curve: Sequence[Mapping[str, float]], t: float,
                horizon: float) -> Optional[float]:
    """P(fill by t), or None when t is beyond the observed horizon.

    Returning None rather than the curve's last value is deliberate: a flat
    extrapolation of a 30 s curve to 120 s is an assumption, and an assumption
    that travels in the same field as a measurement will be read as one.
    """
    if t > horizon:
        return None
    p = 0.0
    for row in curve:
        if row["t"] <= t:
            p = row["p_fill_by_t"]
        else:
            break
    return p


def leg_curves(orders: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, rels in (("first_leg", FIRST_LEG_RELATIONS),
                       ("complement", COMPLEMENT_RELATIONS)):
        obs = [o for o in (observation(r) for r in orders
                           if r.get("relation") in rels) if o]
        curve = kaplan_meier(obs)
        hz = estimable_horizon(obs)
        out[name] = {
            "n_orders": len(obs),
            "n_filled": sum(1 for _, f in obs if f),
            "estimable_horizon_s": hz,
            "curve": curve,
            "p_fill": {str(int(h)): survival_at(curve, h, hz)
                       for h in REQUESTED_HORIZONS_S},
        }
    return out


def expected_economics(p_first: Optional[float], p_complement: Optional[float],
                       pair_cost: D, residual_loss_per_unit: D,
                       incentives: D = D(0), fees: D = D(0)) -> Dict[str, Any]:
    """OWNER's expression, with unknowns propagated as unknown.

        E = P(both legs) x (1 - pair_cost) - P(stranded) x residual_loss
            + incentives - fees

    A None probability makes the expectation None. It is never silently
    treated as zero or one: an unestimable horizon must not become a
    confident number by passing through arithmetic.
    """
    if p_first is None or p_complement is None:
        return {"expected_usd": None,
                "why": "a leg probability is outside the estimable horizon",
                "p_both": None, "p_stranded": None}
    p_both = p_first * p_complement
    p_stranded = p_first * (1.0 - p_complement)
    pair_edge = (D(1) - pair_cost)
    e = (D(str(p_both)) * pair_edge
         - D(str(p_stranded)) * residual_loss_per_unit
         + incentives - fees)
    return {"expected_usd": e, "p_both": p_both, "p_stranded": p_stranded,
            "pair_edge_if_completed": pair_edge,
            "labels": "probabilities from our own censored outcomes; "
                      "pair_edge is 1 - pair_cost and is NOT an edge unless "
                      "both legs actually fill"}


def build(orders: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    legs = leg_curves(orders)
    hz = min(legs[k]["estimable_horizon_s"] for k in legs)
    return {
        "orders_used": sum(legs[k]["n_orders"] for k in legs),
        "legs": legs,
        "estimable_horizon_s": hz,
        "requested_horizons_s": list(REQUESTED_HORIZONS_S),
        "unestimable_horizons_s": [h for h in REQUESTED_HORIZONS_S if h > hz],
        "l5_median_complement_gap_s": L5_MEDIAN_COMPLEMENT_GAP_S,
        "l5_gap_is_beyond_our_evidence": L5_MEDIAN_COMPLEMENT_GAP_S > hz,
        "no_flow_term": (
            "the campaign recorded no contemporaneous flow, so no flow "
            "coefficient is calibrated here; flow enters forward from the "
            "completion shadow and its effect is a hypothesis to test, not "
            "an assumption baked in"),
        "labels": LABELS,
    }


def render_md(rep: Mapping[str, Any]) -> str:
    L = ["# P(fill) for a resting maker order, from our own outcomes", "",
         "_Calibrated on %d real submitted orders. A fill is an event; a "
         "cancelled order is CENSORED, not a permanent no-fill._"
         % rep["orders_used"], "",
         "## The horizon we cannot estimate", "",
         "Longest rest ever observed: **%.1f s**. Orders resting past 60 s: "
         "**0**. Past 120 s: **0**." % rep["estimable_horizon_s"], "",
         "Requested horizons that are NOT estimable: %s. The estimator "
         "returns None for these rather than extrapolating."
         % (rep["unestimable_horizons_s"] or "none"), "",
         "**L5's median complement gap is %.0f s — beyond our entire "
         "observed range.** We have never operated at the horizon his "
         "behaviour lives at." % rep["l5_median_complement_gap_s"], "",
         "## P(fill by t)", "",
         "| leg | orders | filled | horizon | P(30s) | P(60s) | P(120s) |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for name in ("first_leg", "complement"):
        g = rep["legs"][name]
        p = g["p_fill"]
        L.append("| %s | %d | %d | %.1fs | %s | %s | %s |" % (
            name, g["n_orders"], g["n_filled"], g["estimable_horizon_s"],
            ("%.3f" % p["30"]) if p["30"] is not None else "—",
            ("%.3f" % p["60"]) if p["60"] is not None else "**not estimable**",
            ("%.3f" % p["120"]) if p["120"] is not None else "**not estimable**"))
    L += ["", "## No flow term", "", rep["no_flow_term"], "",
          "Labels: " + "; ".join(f"{k} = {v}" for k, v in rep["labels"].items()),
          ""]
    return "\n".join(L)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    from src.l5_r4_action_comparison import harvest
    from src.l5_r4_fidelity_decomposition import expand_logs
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args(argv)
    h = harvest(expand_logs(a.logs), "R5F/")
    rep = build(h["orders"])
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "pmus_fill_model.json"), "w",
              encoding="utf-8") as f:
        json.dump(rep, f, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "PMUS_FILL_MODEL.md"), "w",
              encoding="utf-8") as f:
        f.write(render_md(rep))
    print(json.dumps({"orders": rep["orders_used"],
                      "horizon_s": rep["estimable_horizon_s"],
                      "unestimable": rep["unestimable_horizons_s"],
                      "p_fill": {k: rep["legs"][k]["p_fill"] for k in rep["legs"]}},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
