"""The depth frontier: is there a sweet spot, or does deeper just mean cheaper?

    resting depth -> first-fill probability -> two-sided probability
                  -> time-to-two-sided -> pair cost -> residual
                  -> settled economics

THE QUESTION IS NOT WHETHER THE DEEPEST ORDER LOOKS CHEAPEST. It always will.
A quote four ticks behind the touch has a wonderful theoretical pair cost and
may never trade, and an earlier version of the policy proved the danger by
selecting exactly that. So every depth is scored on BOTH halves and sorted
into three verdicts:

    TOO_SHALLOW   fills readily, pair economics poor
    VIABLE        enough two-sided fills AND positive economics
    TOO_DEEP      attractive theoretical pair cost, insufficient flow

THE 0.96 CEILING DOES NOT CENSOR THIS. Combinations whose pair cost exceeds it
are labelled RESEARCH_INELIGIBLE_FOR_LIVE and measured anyway -- whether they
would have filled, and what they would have settled at. A production constraint
that silently deletes observations makes itself unfalsifiable.

AND QUEUE REALISM IS NOT OPTIONAL. Every rate is reported at all four fill
criteria (TOUCH / TRADE_AT_PRICE / TRADE_THROUGH / VOLUME_AHEAD_CLEARED).
Live-readiness reads the strictest. A depth that looks viable only under TOUCH
is a depth that looks viable on queue priority we did not possess.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

from src.l5_shadow import cohorts as CO
from src.l5_shadow import two_sided as TS

CRITERIA = ("TOUCH", "TRADE_AT_PRICE", "TRADE_THROUGH", "VOLUME_AHEAD_CLEARED")
STRICTEST = "VOLUME_AHEAD_CLEARED"
RESEARCH_LABEL = "RESEARCH_INELIGIBLE_FOR_LIVE"
# a depth needs this many decided quotes before its rate means anything
MIN_QUOTES_PER_DEPTH = 10


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


def _met(row: dict, criterion: str) -> Optional[bool]:
    """Did this quote satisfy `criterion`? None when the verdict is unknown."""
    if row.get("evidenced_fill") is None and not row.get("fill_criteria"):
        return None
    fc = row.get("fill_criteria") or {}
    if criterion not in fc:
        return None
    return bool(fc[criterion])


def depth_rows(outcomes: Sequence[dict], *, venue: str, arm: str
               ) -> Dict[int, List[dict]]:
    """One arm's decided quotes on one venue, grouped by resting depth."""
    by: Dict[int, List[dict]] = defaultdict(list)
    for o in outcomes:
        if o.get("arm") != arm or o.get("venue") != venue:
            continue
        if not CO.is_maker_cohort(o.get("cohort")):
            continue
        d = o.get("ticks_below_touch")
        if d is None:
            continue
        by[int(d)].append(o)
    return dict(sorted(by.items()))


def two_sided_by_depth(outcomes: Sequence[dict], *, venue: str, arm: str,
                       criterion: str) -> Dict[int, Dict[str, Any]]:
    """For each depth: did a window reach two-sided using a leg at that depth?

    A pair needs both sides. The depth credited is the one the window's
    shallowest filled leg rested at on each side, and the pair cost is what
    those legs actually cost -- not the cheapest combination that was quoted.
    """
    per_market: Dict[Any, Dict[str, Any]] = {}
    for o in outcomes:
        if (o.get("arm") != arm or o.get("venue") != venue
                or not CO.is_maker_cohort(o.get("cohort"))):
            continue
        if _met(o, criterion) is not True:
            continue
        m = per_market.setdefault(o.get("market_key"),
                                  {"Up": [], "Down": []})
        side = str(o.get("desired_outcome"))
        if side in m:
            m[side].append(o)
    out: Dict[int, Dict[str, Any]] = defaultdict(
        lambda: {"two_sided_windows": 0, "pair_costs": [], "times": [],
                 "residual": D("0")})
    for mk, m in per_market.items():
        if not m["Up"] or not m["Down"]:
            continue
        u = min(m["Up"], key=lambda o: int(o.get("ticks_below_touch") or 0))
        d = min(m["Down"], key=lambda o: int(o.get("ticks_below_touch") or 0))
        depth = max(int(u.get("ticks_below_touch") or 0),
                    int(d.get("ticks_below_touch") or 0))
        cost = D(str(u["quote_price"])) + D(str(d["quote_price"]))
        g = out[depth]
        g["two_sided_windows"] += 1
        g["pair_costs"].append(cost)
        tu, td = float(u.get("placed_at") or 0), float(d.get("placed_at") or 0)
        g["times"].append(abs(td - tu))
        g["residual"] += abs(D(str(u["quote_qty"])) - D(str(d["quote_qty"])))
    return dict(sorted(out.items()))


def frontier(outcomes: Sequence[dict], *, venue: str, arm: str,
             criterion: str = STRICTEST) -> Dict[str, Any]:
    by_depth = depth_rows(outcomes, venue=venue, arm=arm)
    two = two_sided_by_depth(outcomes, venue=venue, arm=arm,
                             criterion=criterion)
    rows = []
    for depth, quotes in by_depth.items():
        decided = [q for q in quotes if _met(q, criterion) is not None]
        hit = [q for q in decided if _met(q, criterion) is True]
        t = two.get(depth, {})
        costs = sorted(t.get("pair_costs") or [])
        med = costs[len(costs) // 2] if costs else None
        rows.append({
            "resting_depth_ticks": depth,
            "quotes": len(quotes), "decided": len(decided),
            "first_fill_probability": (round(len(hit) / len(decided), 4)
                                       if decided else None),
            "two_sided_windows": t.get("two_sided_windows", 0),
            "median_time_to_two_sided_s": (
                round(sorted(t["times"])[len(t["times"]) // 2], 1)
                if t.get("times") else None),
            "median_pair_cost": (str(med.quantize(D("0.000001")))
                                 if med is not None else None),
            "above_par_pairs": sum(1 for c in costs if c > D("1")),
            "pairs_within_live_ceiling": sum(
                1 for c in costs if c <= TS.ADAPTED_EXECUTABLE_PAIR_CEILING),
            "research_ineligible_pairs": sum(
                1 for c in costs if c > TS.ADAPTED_EXECUTABLE_PAIR_CEILING),
            "residual_qty": str(t.get("residual", D("0"))),
            **({"pair_economics_label": RESEARCH_LABEL,
                "why_label": ("this depth's pairs price above the live 0.96 "
                              "ceiling. Measured anyway: a production "
                              "constraint that deletes observations makes "
                              "itself unfalsifiable")}
               if med is not None and med > TS.ADAPTED_EXECUTABLE_PAIR_CEILING
               else {}),
            "verdict": _verdict(len(decided), len(hit), med,
                                t.get("two_sided_windows", 0)),
        })
    return {"venue": venue, "arm": arm, "criterion": criterion,
            "depths": rows, "sweet_spot": _sweet_spot(rows)}


def _verdict(decided: int, hit: int, med_cost: Optional[D],
             two_sided: int) -> str:
    if decided < MIN_QUOTES_PER_DEPTH:
        return "INSUFFICIENT_DATA"
    p = hit / decided
    if med_cost is None:
        return ("TOO_DEEP" if p < 0.1 else "NO_PAIR_YET")
    if med_cost > D("1"):
        return "TOO_SHALLOW"          # fills, and the pair loses money
    if med_cost > TS.ADAPTED_EXECUTABLE_PAIR_CEILING:
        return "TOO_SHALLOW_FOR_LIVE"  # fills, priced outside our ceiling
    if two_sided == 0:
        return "TOO_DEEP"
    return "VIABLE"


def _sweet_spot(rows: Sequence[dict]) -> Optional[Dict[str, Any]]:
    """The shallowest VIABLE depth, if one exists. Shallowest because a
    shallower quote fills more readily and we are not hunting for the cheapest
    theoretical pair -- that is the trap this whole module exists to avoid."""
    ok = [r for r in rows if r["verdict"] == "VIABLE"]
    if not ok:
        return None
    r = min(ok, key=lambda x: x["resting_depth_ticks"])
    return {"resting_depth_ticks": r["resting_depth_ticks"],
            "median_pair_cost": r["median_pair_cost"],
            "first_fill_probability": r["first_fill_probability"],
            "two_sided_windows": r["two_sided_windows"],
            "why": ("shallowest depth that both fills and prices inside the "
                    "live ceiling; deeper is cheaper on paper and thinner in "
                    "practice")}


def report(out_dir: str) -> Dict[str, Any]:
    outs = (_load(os.path.join(out_dir, "inventory_outcomes.jsonl"))
            + _load(os.path.join(out_dir, "two_sided_outcomes.jsonl")))
    venues = sorted({o.get("venue") for o in outs if o.get("venue")})
    out: Dict[str, Any] = {"computed_at": time.time(), "cohort": "BTC15m"}
    for v in venues:
        for arm in ("S", "D"):
            for crit in CRITERIA:
                f = frontier(outs, venue=v, arm=arm, criterion=crit)
                if any(r["decided"] for r in f["depths"]):
                    out.setdefault(f"{v}|{arm}", {})[crit] = f
    out["signals"] = signals(outs, out)
    out["labels"] = (
        "every rate at all four fill criteria; live-readiness reads "
        f"{STRICTEST}. Depths whose pairs price above the live ceiling are "
        f"{RESEARCH_LABEL} and measured anyway. The sweet spot is the "
        "SHALLOWEST viable depth, never the cheapest.")
    return out


def signals(outs: Sequence[dict], rep: Dict[str, Any]) -> Dict[str, Any]:
    """Report immediately on these, rather than waiting for the >=20 gate."""
    d_fills = [o for o in outs if o.get("arm") == "D"
               and _met(o, STRICTEST) is True]
    pairs, settled = 0, 0
    for k, byc in rep.items():
        if not isinstance(byc, dict) or "|" not in str(k):
            continue
        f = byc.get(STRICTEST)
        if f:
            pairs += sum(r["two_sided_windows"] for r in f["depths"])
    dom = None
    for k, byc in rep.items():
        if not isinstance(byc, dict) or "|" not in str(k):
            continue
        f = byc.get(STRICTEST)
        if f and f.get("sweet_spot"):
            dom = {"key": k, **f["sweet_spot"]}
    return {
        "first_D_leg_filled": bool(d_fills),
        "first_D_pair_completed": pairs > 0,
        "pairs_completed": pairs,
        "a_depth_is_dominating": dom,
        "venues_diverge": _diverge(rep),
        "report_now": bool(d_fills or pairs or dom),
        "why": ("the >=20 gate is the minimum for LIVE qualification, not for "
                "telling you something changed"),
    }


def _diverge(rep: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    got = {}
    for k, byc in rep.items():
        if not isinstance(byc, dict) or "|" not in str(k):
            continue
        f = byc.get(STRICTEST)
        if f:
            got[k] = sum(r["two_sided_windows"] for r in f["depths"])
    pm = {k: v for k, v in got.items() if k.startswith("PMUS")}
    gm = {k: v for k, v in got.items() if k.startswith("GEMINI")}
    if not pm or not gm:
        return None
    a, b = max(pm.values()), max(gm.values())
    return ({"PMUS_two_sided": a, "GEMINI_two_sided": b,
             "materially_diverged": abs(a - b) >= 5}
            if (a or b) else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/state/l5-research/l5-shadow-state")
    a = ap.parse_args(argv)
    print(json.dumps(report(a.out_dir), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
