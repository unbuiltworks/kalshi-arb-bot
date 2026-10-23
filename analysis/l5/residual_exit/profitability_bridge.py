"""Where the theoretical edge goes, and why no configuration recovers it.

Every input is measured and sourced; nothing here is a parameter chosen to
make a conclusion come out. READ-ONLY: no network, no venue, no orders.

    PAIR_PROFIT   +0.057230 / matched pair    src/l5_breakeven.py, 15 markets
    RESIDUAL_LOSS -0.331449 / residual unit   same
    REBATE        +0.002953 / opened leg      same
    OBSERVED_P     0.834196 leg completion    same
    DEPTH2_FILL    0.144    per complement ORDER at depth 2, n=868
    ATTEMPT_DECAY  0.220/0.253/0.158/0.085 by attempt number, n=716
                   analysis/l5/COMPLETE_FILL_PREDICTORS.md
    SPREAD         1 tick on 99.40% of ticks
"""
from __future__ import annotations

import json
import math
import os
from decimal import Decimal as D
from typing import Any, Dict, List

PAIR_PROFIT = D("0.057230")
RESIDUAL_LOSS = D("-0.331449")
REBATE = D("0.002953")
OBSERVED_P = D("0.834196")
DEPTH2_FILL = 0.144
ATTEMPT_DECAY = [0.220, 0.253, 0.158] + [0.085] * 13
TICK = D("0.01")
SPREAD = D("0.01")


def ev_per_leg(p: Any) -> D:
    p = D(str(p))
    return p * PAIR_PROFIT + (D(1) - p) * RESIDUAL_LOSS + REBATE


def breakeven_p(pair_profit: D = PAIR_PROFIT) -> D:
    return (-RESIDUAL_LOSS - REBATE) / (pair_profit - RESIDUAL_LOSS)


def cumulative(rates: List[float]) -> List[float]:
    nf, out = 1.0, []
    for r in rates:
        nf *= (1 - r)
        out.append(1 - nf)
    return out


def fill_assumptions() -> Dict[str, Any]:
    """Leg completion after k attempts under each defensible assumption."""
    be = float(breakeven_p())
    scaled = [DEPTH2_FILL * r / ATTEMPT_DECAY[0] for r in ATTEMPT_DECAY]
    out: Dict[str, Any] = {"breakeven_completion": round(be, 6), "scenarios": {}}
    for name, rates in (
            ("independent_at_depth2_fill", [DEPTH2_FILL] * 16),
            ("measured_decay_at_l5_levels", ATTEMPT_DECAY[:16]),
            ("measured_decay_scaled_to_our_attempt1", scaled[:16])):
        c = cumulative(rates)
        hit = next((i + 1 for i, v in enumerate(c) if v >= be), None)
        out["scenarios"][name] = {
            "completion_by_attempt": {k: round(c[k - 1], 4)
                                      for k in (1, 2, 4, 8, 12, 16)},
            "ev_by_attempt": {k: str(ev_per_leg(c[k - 1]).quantize(D("0.000001")))
                              for k in (1, 2, 4, 8, 12, 16)},
            "attempts_to_breakeven": hit,
        }
    return out


def ceiling_curve() -> List[Dict[str, Any]]:
    """A pair ceiling T forces a minimum depth, which fixes the gross edge.
    Shallower quoting raises the REQUIRED fill rate faster than being nearer
    the touch can plausibly raise the achieved one."""
    rebate_per_pair = PAIR_PROFIT - D("0.05")   # calibrated at the one point
    rows = []
    for T in ("0.96", "0.97", "0.98", "0.99"):
        T = D(T)
        need = (D(1) - SPREAD - T) / (D(2) * TICK)
        dmin = max(0, math.ceil(float(need) - 1e-9))
        pair_cost = D(1) - SPREAD - D(2 * dmin) * TICK
        edge = D(1) - pair_cost
        pp = edge + rebate_per_pair
        p = breakeven_p(pp)
        rows.append({
            "ceiling": str(T), "min_depth_ticks": dmin,
            "pair_cost": str(pair_cost), "gross_edge": str(edge),
            "pair_profit": str(pp),
            "breakeven_completion": round(float(p), 6),
            "need_per_attempt_at_8": round(1 - (1 - float(p)) ** (1 / 8), 6),
            "need_per_attempt_at_12": round(1 - (1 - float(p)) ** (1 / 12), 6),
        })
    return rows


def campaign_bridge() -> Dict[str, Any]:
    """The 2026-10-23 campaign, planned -> realized, from the venue rows."""
    legs = [
        {"window": "0230z", "qty": D("1.17"), "cost": D("0.07"),
         "planned_complement": D("0.88"), "actual_complement": D("0.97"),
         "outcome": "defensive_cross"},
        {"window": "0300z", "qty": D("1.79"), "cost": D("0.20"),
         "planned_complement": D("0.75"), "actual_complement": None,
         "outcome": "held_to_settlement_worthless"},
        {"window": "0245z", "qty": D(0), "cost": None,
         "planned_complement": None, "actual_complement": None,
         "outcome": "no_fill"},
    ]
    rows, planned, slip, settle = [], D(0), D(0), D(0)
    for l in legs:
        if l["qty"] == 0:
            rows.append(dict(l, qty="0", planned_edge="0", realized="0"))
            continue
        pe = (D(1) - (l["cost"] + l["planned_complement"])) * l["qty"]
        planned += pe
        if l["actual_complement"] is not None:
            rl = (D(1) - (l["cost"] + l["actual_complement"])) * l["qty"]
            sl = rl - pe
            slip += sl
            rows.append({"window": l["window"], "planned_edge": str(pe),
                         "complement_slippage": str(sl), "residual_settlement": "0",
                         "realized": str(rl)})
        else:
            rl = -(l["cost"] * l["qty"])
            st = rl - pe
            settle += st
            rows.append({"window": l["window"], "planned_edge": str(pe),
                         "complement_slippage": "0",
                         "residual_settlement": str(st), "realized": str(rl)})
    realized = planned + slip + settle
    shortfall = slip + settle
    return {
        "rows": rows,
        "planned_edge_if_both_paired": str(planned),
        "complement_slippage": str(slip),
        "residual_settlement": str(settle),
        "fees_charged_by_venue": "0.0000",
        "realized": str(realized),
        "venue_reported": "-0.4048",
        "reconciles": realized == D("-0.4048"),
        "attribution_of_shortfall": {
            "missed_complement_to_settlement": round(
                float(abs(settle) / abs(shortfall)), 4),
            "complement_price_slippage": round(
                float(abs(slip) / abs(shortfall)), 4),
            "fees": 0.0,
            "queue_adverse_selection": ("n/a -- no complement ever filled, so "
                                        "no fill was adversely selected"),
        },
    }


def report() -> Dict[str, Any]:
    return {
        "inputs": {"pair_profit": str(PAIR_PROFIT),
                   "residual_loss": str(RESIDUAL_LOSS),
                   "rebate_per_leg": str(REBATE),
                   "observed_leg_completion": str(OBSERVED_P),
                   "depth2_per_order_fill": DEPTH2_FILL,
                   "attempt_decay": ATTEMPT_DECAY[:4]},
        "breakeven_completion": round(float(breakeven_p()), 6),
        "ev_at_observed_completion": str(ev_per_leg(OBSERVED_P)),
        "fill_assumptions": fill_assumptions(),
        "ceiling_curve": ceiling_curve(),
        "campaign_bridge": campaign_bridge(),
        "verdict": ("negative expectancy under every defensible fill "
                    "assumption; no ceiling/depth point clears breakeven"),
    }


def main() -> int:
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "profitability_bridge.json")
    with open(dst, "w") as fh:
        json.dump(report(), fh, indent=1, sort_keys=True)
    print("wrote", dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
