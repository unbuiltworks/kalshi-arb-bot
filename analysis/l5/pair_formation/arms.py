"""Reactive clone vs state-driven inventory controller — the falsifiable test.

    R  REACTIVE       one quote, on the side L5 just filled. The complement can
                      only be quoted AFTER L5's next fill is published, by
                      which time the flow that would have filled it is gone.
    S  STATE-DRIVEN   the contemporaneous state implies desired inventory on
                      BOTH outcomes, so both quotes rest before either fills.

Same trigger, same instant, same frozen intent. The ONLY difference is whether
the complement was already resting.

THIS IS WHAT MAKES ARM S REFUTABLE. If S forms no more pairs than R, the
continuous-inventory hypothesis is wrong and this is the number that says so.
Arm S is a MODEL CANDIDATE throughout and is never reported as L5's policy.

THE ARMS ARE SCORED FROM SEPARATE STORES ON PURPOSE. Arm S's quotes are a
different policy's; letting them into `maker_outcomes.jsonl` would rewrite the
reactive clone's measured fill rate and pair economics with results it never
produced.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

PLANS = "two_sided_plans.jsonl"
S_OUT = "two_sided_outcomes.jsonl"
R_OUT = "maker_outcomes.jsonl"


def _load(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def availability(plans: Sequence[dict]) -> Dict[str, Any]:
    """Before any fill: could a fee-safe two-sided pair even be PLACED?

    A venue that cannot host both legs inside the pair target is not a venue
    where L5's complement behaviour transfers, whatever our fill rate is.
    """
    by: Dict[str, Counter] = {}
    costs: Dict[str, List[D]] = {}
    all_costs: Dict[str, List[D]] = {}
    for p in plans:
        v = str(p.get("venue"))
        c = by.setdefault(v, Counter())
        c["planned"] += 1
        # PRICED BUT REFUSED IS THE INTERESTING CASE. Whether a pair cleared
        # the target says yes or no; HOW FAR from it the venue sits says
        # whether the target is attainable here at all. A first live sample
        # priced Polymarket's two touches at 0.9930 against a 0.96 target --
        # if that is typical, the binding constraint is the venue's natural
        # maker pair cost, not our fill rate and not our reactivity.
        if p.get("pair_cost"):
            all_costs.setdefault(v, []).append(D(p["pair_cost"]))
        if p.get("admissible"):
            c["admissible"] += 1
            if p.get("pair_cost"):
                costs.setdefault(v, []).append(D(p["pair_cost"]))
        else:
            c[_bucket(p.get("reason"))] += 1
    out = {}
    for v, c in sorted(by.items()):
        cs, acs = sorted(costs.get(v, [])), sorted(all_costs.get(v, []))
        out[v] = {
            **dict(c),
            "admissible_rate": (round(c["admissible"] / c["planned"], 4)
                                if c["planned"] else None),
            "median_admissible_pair_cost": (str(cs[len(cs) // 2])
                                            if cs else None),
            "priced_pairs": len(acs),
            "pair_cost_all_priced": (
                {"p10": str(acs[int(len(acs) * 0.1)]),
                 "median": str(acs[len(acs) // 2]),
                 "p90": str(acs[int(len(acs) * 0.9)]),
                 "min": str(acs[0]), "max": str(acs[-1]),
                 "under_1_00": sum(1 for x in acs if x < D("1")),
                 "reads": ("how far the venue's own two touches sit from the "
                           "fee-safe target, whether or not they cleared it. "
                           "If the median sits above the target, the binding "
                           "constraint is the venue, not our execution")}
                if acs else None),
        }
    return out


def _bucket(reason: Any) -> str:
    r = str(reason or "").lower()
    if "above the fee-safe target" in r:
        return "refused_pair_too_expensive"
    if "no bid to join" in r or "cannot host" in r:
        return "no_complement_book"
    if "could not be read" in r or "unknown" in r:
        return "book_unknown"
    if "did not declare" in r:
        return "venue_terms_undeclared"
    return "other"


def arm_pairs(outcomes: Sequence[dict]) -> Dict[str, Any]:
    """Matched inventory formed, per (venue, market), from one arm's verdicts."""
    by: Dict[tuple, Dict[str, Any]] = {}
    for o in outcomes:
        if o.get("evidenced_fill") is not True:
            continue
        k = (o.get("venue"), o.get("market_key"))
        g = by.setdefault(k, {"Up": D("0"), "Down": D("0"), "px": {}})
        side = str(o.get("desired_outcome"))
        if side not in ("Up", "Down"):
            continue
        try:
            g[side] += D(str(o.get("quote_qty") or "0"))
            g["px"].setdefault(side, D(str(o.get("quote_price") or "0")))
        except Exception:                                     # noqa: BLE001
            continue
    paired, one_sided, costs = 0, 0, []
    for g in by.values():
        if g["Up"] > 0 and g["Down"] > 0:
            paired += 1
            if "Up" in g["px"] and "Down" in g["px"]:
                costs.append(g["px"]["Up"] + g["px"]["Down"])
        else:
            one_sided += 1
    cs = sorted(costs)
    return {"markets_with_a_fill": len(by), "paired": paired,
            "one_sided": one_sided,
            "pair_rate": (round(paired / len(by), 4) if by else None),
            "median_effective_pair_cost": (str(cs[len(cs) // 2])
                                           if cs else None)}


def report(out_dir: str) -> Dict[str, Any]:
    plans = _load(os.path.join(out_dir, PLANS))
    s_out = _load(os.path.join(out_dir, S_OUT))
    r_out = _load(os.path.join(out_dir, R_OUT))
    R, S = arm_pairs(r_out), arm_pairs(s_out)
    verdict = _verdict(R, S)
    return {
        "computed_at": time.time(), "out_dir": out_dir,
        "arm_S_label": "MODEL_CANDIDATE/state_driven_two_sided",
        "pair_availability_before_any_fill": availability(plans),
        "arm_R_reactive": R,
        "arm_S_state_driven": S,
        "verdict": verdict,
        "labels": (
            "same trigger, same instant, same frozen intent; the only "
            "difference is whether the complement was already resting. Arm S "
            "is a MODEL CANDIDATE, never a claim about L5. Its quotes are "
            "scored from a separate store so they cannot rewrite the reactive "
            "clone's measured economics."),
    }


def _verdict(R: Dict[str, Any], S: Dict[str, Any]) -> Dict[str, Any]:
    """State the refutation condition explicitly, before the data arrives."""
    rp, sp = R.get("pair_rate"), S.get("pair_rate")
    if rp is None or sp is None:
        return {"decided": False,
                "why": ("not enough evidence in one or both arms; a missing "
                        "pair rate is not a zero")}
    return {"decided": True,
            "arm_R_pair_rate": rp, "arm_S_pair_rate": sp,
            "delta": round(sp - rp, 4),
            "supports_continuous_inventory_hypothesis": sp > rp,
            "why": ("the hypothesis predicts S > R. S <= R REFUTES it, and "
                    "that outcome is reported as plainly as the other")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-research/l5-shadow-state")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.out_dir), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
