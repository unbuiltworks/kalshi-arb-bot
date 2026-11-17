"""Opening policy for a flat market: quote both legs, or only one leg first.

OWNER 2026-11-17 "Cheap-leg-first accumulation": a candidate policy the
controller may SELECT when justified, never a mandate.

EVIDENCE (live, venue-confirmed FILL events, 2026-11-15 18:21Z .. 11-17
03:29Z, every one-sided episode since the recovery controller went on: 81
episodes, 77 paired). Split by which leg filled FIRST under simultaneous
two-sided quoting:

    first leg cheaper (< 0.50): 46 episodes, pair cost 1.0001 (margin -0.0c)
                                 median +1.5c, median 84s to pair
    first leg dearer (>= 0.50): 35 episodes, pair cost 1.0177 (margin -1.8c)
                                 median -1.0c, median 111s to pair
    difference +1.8c/pair, bootstrap 90% CI (-0.5c, +4.1c), P(<=0) ~0.10;
    same sign before the intl guard (-0.4c vs -2.1c) and with it (+0.5c vs
    -1.3c). Capital at risk while unmatched ~half (86 vs 157 $*s).

So the measured gain is from NOT letting the dearer leg fill first. That is
suggestive, not proof, and cheap-first alone is break-even in this sample:
it is the gate below, not the low price, that has to justify an opening.

THE GATE (all must hold, else the market is quoted as before or not at
all -- this module only ever NARROWS what is placed):
  * the cheaper leg is chosen from EXECUTABLE prices (YES ask vs NO ask =
    1 - YES bid), never a midpoint; a tie means no cheap leg;
  * the cheap leg's price is inside [CHEAP_MIN, CHEAP_MAX] -- a long shot is
    not attractive just because it is cheap;
  * the completion it needs is economically possible NOW: the maximum
    acceptable completion price, 1 - cheap price - maker fee (0) -
    PROFIT_BUFFER, is at or above the complement price the pair plan would
    rest at (so the planned pair settles at a positive net margin);
  * in "auto", the bot's OWN resolved episodes (shrunk toward the prior
    above) must show cheap-first ahead of the average two-sided episode by
    at least AUTO_MIN_EDGE.
Nothing here places, cancels or sizes an order, and every existing gate,
cap, guard and halt in the engine still applies to what it returns.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, Iterable, Mapping, Optional

POLICIES = ("two_sided", "cheap_first", "expensive_first", "auto")

CHEAP_MIN = D("0.25")
CHEAP_MAX = D("0.49")
PROFIT_BUFFER = D("0.02")
AUTO_MIN_EDGE = D("0.005")
# prior from the live episodes above: (mean pair margin, pseudo-count)
PRIOR = {"cheap": (D("-0.0001"), D("20")), "dear": (D("-0.0177"), D("20"))}


def executable_prices(yes_bid: D, yes_ask: D) -> Dict[str, D]:
    """What each leg costs to BUY right now (its ask)."""
    return {"YES": D(yes_ask), "NO": D(1) - D(yes_bid)}


def cheap_side(yes_bid: D, yes_ask: D) -> Optional[str]:
    p = executable_prices(yes_bid, yes_ask)
    if p["YES"] == p["NO"]:
        return None
    return "YES" if p["YES"] < p["NO"] else "NO"


def max_completion_px(cost_basis: D, buffer: D = PROFIT_BUFFER,
                      fee_per_contract: D = D(0)) -> D:
    """Highest price at which the opposite leg still settles a pair at a
    positive net margin: payout 1 - filled cost - completion fee - buffer.
    Recomputed from the ACTUAL average cost of the filled leg."""
    return D(1) - D(cost_basis) - D(fee_per_contract) - D(buffer)


def evidence(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Mean pair margin of the bot's own resolved episodes, by whether the
    first leg was the cheaper one, shrunk toward PRIOR."""
    acc = {"cheap": [D(0), 0], "dear": [D(0), 0]}
    for r in records:
        if r.get("outcome") != "paired" or r.get("pair_cost") is None or \
                r.get("lead_px") is None:
            continue
        k = "cheap" if D(str(r["lead_px"])) < D("0.5") else "dear"
        acc[k][0] += D(1) - D(str(r["pair_cost"]))
        acc[k][1] += 1
    out: Dict[str, Any] = {}
    for k, (s, n) in acc.items():
        pm, pn = PRIOR[k]
        out[k] = {"n": n, "margin": (s + pm * pn) / (D(n) + pn)}
    return out


def choose(policy: str, want: Optional[Mapping[str, D]], yes_bid: D,
           yes_ask: D, *, records: Iterable[Mapping[str, Any]] = (),
           cheap_min: D = CHEAP_MIN, cheap_max: D = CHEAP_MAX,
           buffer: D = PROFIT_BUFFER, auto_min_edge: D = AUTO_MIN_EDGE
           ) -> Dict[str, Any]:
    """Which legs to quote now. Returns {"sides", "policy", "why", ...};
    sides == () means place nothing."""
    out: Dict[str, Any] = {"policy": "two_sided", "sides": ("YES", "NO"),
                           "why": "default"}
    if want is None:
        return {**out, "sides": (), "why": "no_pair_plan"}
    policy = policy if policy in POLICIES else "two_sided"
    if policy == "two_sided":
        return out
    cs = cheap_side(yes_bid, yes_ask)
    if cs is None:
        return {**out, "why": "no_cheaper_leg"}
    ds = "NO" if cs == "YES" else "YES"
    px = executable_prices(yes_bid, yes_ask)
    info = {"cheap": cs, "cheap_ask": str(px[cs]), "dear_ask": str(px[ds])}
    if policy == "expensive_first":
        # comparison arm only: quote the dearer leg alone, same pair plan
        return {**out, **info, "policy": "expensive_first", "sides": (ds,),
                "why": "comparison_arm"}
    if not (cheap_min <= px[cs] <= cheap_max):
        return {**out, **info, "why": "cheap_leg_outside_band"}
    cap = max_completion_px(want[cs], buffer)
    info["max_completion_px"] = str(cap)
    if D(want[ds]) > cap:
        return {**out, **info, "why": "completion_not_profitable"}
    if policy == "auto":
        ev = evidence(records)
        info["evidence"] = {k: {"n": v["n"], "margin": str(v["margin"])}
                            for k, v in ev.items()}
        # the two-sided baseline mixes both first-leg cases about evenly
        base = (ev["cheap"]["margin"] + ev["dear"]["margin"]) / 2
        if ev["cheap"]["margin"] - base < auto_min_edge:
            return {**out, **info, "why": "evidence_not_ahead"}
    return {**out, **info, "policy": "cheap_first", "sides": (cs,),
            "why": "gate_passed"}
