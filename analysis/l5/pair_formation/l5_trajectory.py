"""What L5's OWN inventory trajectory looks like, in the same source markets.

The clone's episodes are only interpretable against the thing they are meant to
reproduce. This measures, over L5's full canonical history:

  * how often a window ends TWO-SIDED at all -- the behaviour we are failing to
    reproduce, measured at the source rather than assumed;
  * how long he takes to get there, first fill to first opposite-side fill;
  * what pair cost he actually achieves.

THE PAIR COST IS THE ONE THAT DECIDES AN ARGUMENT. Our own spring discipline
refuses any pair above 0.96, and a first live sample priced Polymarket's two
touches at 0.9930. If L5's achieved pair cost sits near the touch sum, the
target is simply unattainable as a touch-joining maker and the discipline is
refusing the trade he actually makes. If it sits well below, he is not joining
the touch at all -- he is resting deeper and waiting, which would explain both
the cheap pairs AND why he must be two-sided early.

Read-only over `l5_canonical_sequence.csv.gz`. Nothing here is a fill of ours,
a shadow result, or realized P&L.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

SEQ = "analysis/l5/l5_canonical_sequence.csv.gz"
PAIR_TARGET = D("0.96")          # our spring discipline, for comparison only


def windows(path: str = SEQ, *, limit: Optional[int] = None
            ) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = defaultdict(list)
    with gzip.open(path, "rt") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if limit and i >= limit:
                break
            if (row.get("side") or "").upper() != "BUY":
                continue
            out[row["window_id"]].append(row)
    for v in out.values():
        v.sort(key=lambda r: float(r["timestamp"]))
    return out


def trajectory(fills: Sequence[dict]) -> Dict[str, Any]:
    """One window: when each side first arrived, and what the pair cost."""
    up_q = up_c = dn_q = dn_c = D("0")
    t_first = float(fills[0]["timestamp"])
    first_side = fills[0]["outcome"]
    t_opp: Optional[float] = None
    for f in fills:
        q, p = D(f["quantity"]), D(f["price"])
        if f["outcome"] == "Up":
            up_q += q
            up_c += q * p
        else:
            dn_q += q
            dn_c += q * p
        if t_opp is None and up_q > 0 and dn_q > 0:
            t_opp = float(f["timestamp"])
    matched = min(up_q, dn_q)
    cost = None
    if matched > 0 and up_q > 0 and dn_q > 0:
        cost = (up_c / up_q) + (dn_c / dn_q)
    return {
        "n_fills": len(fills), "cohort": f"{fills[0]['asset']}"
                                         f"{fills[0]['duration_minutes']}m",
        "market_slug": fills[0]["market_slug"],
        "first_side": first_side, "t_first": t_first,
        "two_sided": matched > 0,
        "t_to_two_sided_s": (round(t_opp - t_first, 1)
                             if t_opp is not None else None),
        "fills_before_two_sided": _before(fills, t_opp),
        "up_qty": str(up_q), "dn_qty": str(dn_q),
        "matched_qty": str(matched),
        "residual_qty": str(abs(up_q - dn_q)),
        "pair_cost": (str(cost.quantize(D("0.000001")))
                      if cost is not None else None),
        "pair_cost_within_our_target": (bool(cost <= PAIR_TARGET)
                                        if cost is not None else None),
    }


def _before(fills: Sequence[dict], t_opp: Optional[float]) -> Optional[int]:
    if t_opp is None:
        return None
    return sum(1 for f in fills if float(f["timestamp"]) < t_opp)


def report(path: str = SEQ, *, limit: Optional[int] = None) -> Dict[str, Any]:
    ws = windows(path, limit=limit)
    rows = [trajectory(v) for v in ws.values() if v]
    two = [r for r in rows if r["two_sided"]]
    costs = sorted(D(r["pair_cost"]) for r in two if r["pair_cost"])
    lat = sorted(r["t_to_two_sided_s"] for r in two
                 if r["t_to_two_sided_s"] is not None)
    nb = sorted(r["fills_before_two_sided"] for r in two
                if r["fills_before_two_sided"] is not None)
    by: Dict[str, Any] = {}
    for c in sorted({r["cohort"] for r in rows}):
        sub = [r for r in rows if r["cohort"] == c]
        st = [r for r in sub if r["two_sided"]]
        sc = sorted(D(r["pair_cost"]) for r in st if r["pair_cost"])
        by[c] = {"windows": len(sub), "two_sided": len(st),
                 "two_sided_rate": round(len(st) / len(sub), 4) if sub else None,
                 "median_pair_cost": (str(sc[len(sc) // 2]) if sc else None),
                 "within_our_0_96_target": sum(
                     1 for x in sc if x <= PAIR_TARGET)}
    return {
        "source": path, "windows": len(rows),
        "two_sided_windows": len(two),
        "two_sided_rate": (round(len(two) / len(rows), 4) if rows else None),
        "t_to_two_sided_s": _dist(lat),
        "fills_before_two_sided": _dist(nb),
        "pair_cost": _dist_d(costs),
        "within_our_0_96_target": sum(1 for x in costs if x <= PAIR_TARGET),
        "within_our_target_rate": (
            round(sum(1 for x in costs if x <= PAIR_TARGET) / len(costs), 4)
            if costs else None),
        "our_pair_target": str(PAIR_TARGET),
        "by_cohort": by,
        "labels": (
            "L5's OWN trajectory from public history. Not a fill of ours, not "
            "a shadow result, not realized P&L. `within_our_0_96_target` "
            "compares his achieved pair cost against OUR spring discipline -- "
            "a low rate means the discipline refuses the trade he actually "
            "makes, which is a fact about our policy, not about him."),
    }


def _dist(v: Sequence) -> Optional[Dict[str, Any]]:
    if not v:
        return None
    s = sorted(v)
    return {"n": len(s), "p10": s[int(len(s) * 0.1)], "median": s[len(s) // 2],
            "p90": s[int(len(s) * 0.9)], "max": s[-1]}


def _dist_d(v: Sequence[D]) -> Optional[Dict[str, Any]]:
    d = _dist(v)
    return ({k: (str(x) if isinstance(x, D) else x) for k, x in d.items()}
            if d else None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", default=SEQ)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    print(json.dumps(report(a.seq, limit=a.limit or None), indent=1,
                     default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
