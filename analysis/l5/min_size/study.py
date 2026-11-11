"""Can our engine even place what L5 does? P3: the minimum-size constraint.

THE QUESTION. Polymarket declares `orderMinSize: 5` (confirmed live on both
crypto cohorts, alongside tick 0.01 and rewardsMinSize 50). L5's actions are
not all that big. Every action below the minimum is one our engine cannot place
AT ALL on Polymarket -- not "places worse", cannot place -- so the fraction
below it bounds what any executable-economics result can possibly cover.

A FILL IS NOT AN ORDER, and the difference decides the answer. The REPLAY
intent sets `incremental_qty` from L5's own fill (`service.py`), so per-fill
size is what our engine would try to place today. But a large order filling in
pieces produces several small fills, and counting those as several small
INTENTS would invent a constraint that is an artifact of our intent
construction rather than a fact about L5. Both are therefore reported, and
where they disagree the order-level figure is the one about L5.

WAITING IS NOT FREE, AND IT IS NOT FIDELITY. Aggregating sub-minimum actions
until they reach 5 is an adaptation: it changes when we act, what price is
still available, and -- if L5 switched sides while we waited -- what we would
end up holding. Every aggregation measured here is labelled L5_ADAPTED. None of
it is FIDELITY, and no aggregate result in this module may be reported as
replication.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, Iterable, List, Optional, Sequence

SEQ = "analysis/l5/l5_canonical_sequence.csv.gz"
# venue-declared, read live 2026-09-15 from both crypto cohorts, not remembered
PM_MIN_SIZE = D("5")
PM_TICK = D("0.01")
# the engine's own intent lifetime: an aggregation that takes longer than this
# is not a delayed version of the same intent, it is a different one
INTENT_VALID_S = 30.0
# fills of one order arrive together; beyond this they are separate decisions
ORDER_GAP_S = 2.0


def load(path: str = SEQ, *, limit: Optional[int] = None) -> List[dict]:
    with gzip.open(path, "rt") as fh:
        r = csv.DictReader(fh)
        rows = []
        for i, row in enumerate(r):
            if limit and i >= limit:
                break
            rows.append(row)
    return rows


def buys(rows: Sequence[dict]) -> List[dict]:
    """BUY fills only. A SELL is an exit, and the minimum-size question is
    about acquiring inventory, which is what the intent expresses."""
    return [r for r in rows if (r.get("side") or "").upper() == "BUY"]


def orders(fills: Sequence[dict]) -> List[dict]:
    """Group consecutive fills that plausibly came from ONE order.

    Same window, same outcome, same price, within ORDER_GAP_S. Deliberately
    conservative: merging too eagerly would understate the constraint, which
    is the direction that flatters us.
    """
    out: List[dict] = []
    cur: Optional[dict] = None
    for f in fills:
        key = (f["window_id"], f["outcome"], f["price"])
        t = float(f["timestamp"])
        if (cur and cur["key"] == key and t - cur["t_last"] <= ORDER_GAP_S):
            cur["qty"] += D(f["quantity"])
            cur["dollars"] += D(f["dollars"] or "0")
            cur["n_fills"] += 1
            cur["t_last"] = t
            continue
        if cur:
            out.append(cur)
        cur = {"key": key, "window_id": f["window_id"],
               "market_slug": f["market_slug"],
               "cohort": f"{f['asset']}{f['duration_minutes']}m",
               "outcome": f["outcome"], "price": D(f["price"]),
               "qty": D(f["quantity"]), "dollars": D(f["dollars"] or "0"),
               "n_fills": 1, "t_first": t, "t_last": t,
               "tte_s": f.get("seconds_to_settlement")}
    if cur:
        out.append(cur)
    return out


def distribution(sizes: Sequence[D], *, minimum: D = PM_MIN_SIZE
                 ) -> Dict[str, Any]:
    n = len(sizes)
    if not n:
        return {"n": 0}
    tot = sum(sizes, D("0"))
    below = [s for s in sizes if s < minimum]
    srt = sorted(sizes)
    return {
        "n": n,
        "below_min": len(below),
        "below_min_frac": round(len(below) / n, 4),
        "qty_total": str(tot),
        "qty_below_min": str(sum(below, D("0"))),
        "volume_frac_below_min": (round(float(sum(below, D("0")) / tot), 4)
                                  if tot > 0 else None),
        "median": str(srt[n // 2]),
        "p10": str(srt[int(n * 0.10)]), "p90": str(srt[int(n * 0.90)]),
        "min": str(srt[0]), "max": str(srt[-1]),
        "histogram": _hist(sizes, minimum),
    }


def _hist(sizes: Sequence[D], minimum: D) -> Dict[str, int]:
    c: Counter = Counter()
    for s in sizes:
        if s < 1:
            c["<1"] += 1
        elif s < minimum:
            c[f"1-{minimum}"] += 1
        elif s < 20:
            c[f"{minimum}-20"] += 1
        elif s < 100:
            c["20-100"] += 1
        else:
            c["100+"] += 1
    return dict(c)


def economics(units: Sequence[dict], *, minimum: D = PM_MIN_SIZE
              ) -> Dict[str, Any]:
    """What the sub-minimum actions are worth, so "only 12% of actions" is
    never mistaken for "only 12% of the money"."""
    below = [u for u in units if u["qty"] < minimum]
    dol = sum((u["dollars"] for u in units), D("0"))
    dol_b = sum((u["dollars"] for u in below), D("0"))
    return {
        "actions": len(units), "actions_below_min": len(below),
        "notional_total": str(dol.quantize(D("0.01"))),
        "notional_below_min": str(dol_b.quantize(D("0.01"))),
        "notional_frac_below_min": (round(float(dol_b / dol), 4)
                                    if dol > 0 else None),
        "mean_price_below_min": (
            str((sum((u["price"] for u in below), D("0")) / len(below))
                .quantize(D("0.0001"))) if below else None),
        "mean_price_at_or_above": (
            str((sum((u["price"] for u in units if u["qty"] >= minimum),
                     D("0")) / max(1, len(units) - len(below)))
                .quantize(D("0.0001")))),
    }


def aggregation(units: Sequence[dict], *, minimum: D = PM_MIN_SIZE,
                valid_s: float = INTENT_VALID_S) -> Dict[str, Any]:
    """L5_ADAPTED. What waiting to reach the minimum would actually cost.

    For each sub-minimum action, walk forward through L5's own subsequent
    actions in the SAME market until the cumulative quantity reaches the
    minimum, and record what changed on the way: the side, the price we would
    have had to pay, and whether the original intent was still alive.
    """
    by_market: Dict[str, List[dict]] = {}
    for u in units:
        by_market.setdefault(u["window_id"], []).append(u)
    res = {"reached": 0, "never_reached": 0, "side_changed": 0,
           "price_exceeded_first": 0, "outlived_intent": 0,
           "wait_s": [], "extra_cost_per_share": [],
           "_recovered": D("0"), "_extra_cost": D("0"),
           "_extra_cost_whole_order": D("0")}
    for rows in by_market.values():
        rows.sort(key=lambda r: r["t_first"])
        for i, u in enumerate(rows):
            if u["qty"] >= minimum:
                continue
            acc, worst, switched = u["qty"], u["price"], False
            t_end = None
            for v in rows[i + 1:]:
                if v["outcome"] != u["outcome"]:
                    switched = True
                    break
                acc += v["qty"]
                worst = max(worst, v["price"])
                if acc >= minimum:
                    t_end = v["t_first"]
                    break
            if switched or t_end is None:
                res["never_reached"] += 1
                if switched:
                    res["side_changed"] += 1
                continue
            res["reached"] += 1
            wait = t_end - u["t_first"]
            res["wait_s"].append(wait)
            if wait > valid_s:
                res["outlived_intent"] += 1
            # MARGINAL attribution on both sides, so the ratio is defensible.
            # What waiting recovers is this sub-minimum action's own notional:
            # the later actions that carry the total to the minimum are mostly
            # placeable in their own right and would happen regardless. What it
            # costs is the concession on THOSE SAME shares -- charging it on
            # the whole 5-share order would credit one quantity and debit
            # another, inflating the ratio in our favour when we then reject
            # aggregation on it.
            res["_recovered"] += u["dollars"]
            if worst > u["price"]:
                res["price_exceeded_first"] += 1
                res["extra_cost_per_share"].append(float(worst - u["price"]))
                res["_extra_cost"] += (worst - u["price"]) * u["qty"]
                res["_extra_cost_whole_order"] += (worst - u["price"]) * minimum
    w = sorted(res.pop("wait_s"))
    e = sorted(res.pop("extra_cost_per_share"))
    rec, cost = res.pop("_recovered"), res.pop("_extra_cost")
    cost_wo = res.pop("_extra_cost_whole_order")
    return {
        **res,
        "label": "L5_ADAPTED",
        # THE COMPARISON THAT DECIDES IT. Aggregating buys back the notional of
        # the actions we could otherwise not place -- at whatever the price had
        # moved to by the time the minimum was reached. L5's measured trading
        # drag is 0.037% per dollar, so a cent of concession is ~63x the edge
        # being chased; this states both sides in dollars rather than leaving
        # "median +1c" to be read as small.
        "notional_recovered": str(rec.quantize(D("0.01"))),
        "extra_cost_paid": str(cost.quantize(D("0.01"))),
        "cost_per_dollar_recovered": (round(float(cost / rec), 4)
                                      if rec > 0 else None),
        "l5_drag_per_dollar": 0.00037,
        "cost_vs_l5_edge_x": (round(float(cost / rec) / 0.00037, 1)
                              if rec > 0 else None),
        "extra_cost_if_charged_on_whole_order": str(
            cost_wo.quantize(D("0.01"))),
        "median_wait_s": (round(w[len(w) // 2], 1) if w else None),
        "p90_wait_s": (round(w[int(len(w) * 0.9)], 1) if w else None),
        "median_extra_cost_per_share": (round(e[len(e) // 2], 4) if e else None),
        "labels": ("aggregating to the venue minimum is an ADAPTATION, never "
                   "fidelity: it changes when we act, what price is left, and "
                   "-- where the side changed first -- what we would hold. "
                   "`never_reached` actions cannot be placed at all."),
    }


def report(path: str = SEQ, *, limit: Optional[int] = None) -> Dict[str, Any]:
    rows = load(path, limit=limit)
    fl = buys(rows)
    od = orders(fl)
    fill_sizes = [D(f["quantity"]) for f in fl]
    fill_units = [{"qty": D(f["quantity"]), "price": D(f["price"]),
                   "dollars": D(f["dollars"] or "0"),
                   "outcome": f["outcome"], "window_id": f["window_id"],
                   "t_first": float(f["timestamp"])} for f in fl]
    by_cohort: Dict[str, Any] = {}
    for c in sorted({u["cohort"] for u in od}):
        sub = [u for u in od if u["cohort"] == c]
        by_cohort[c] = {"orders": distribution([u["qty"] for u in sub]),
                        "economics": economics(sub)}
    return {
        "source": path, "rows": len(rows), "buy_fills": len(fl),
        "reconstructed_orders": len(od),
        "venue": {"order_min_size": str(PM_MIN_SIZE), "tick": str(PM_TICK),
                  "read": "live from both crypto cohorts 2026-09-15"},
        "per_fill": distribution(fill_sizes),
        "per_fill_economics": economics(fill_units),
        "per_order": distribution([u["qty"] for u in od]),
        "per_order_economics": economics(od),
        "aggregation_L5_ADAPTED": aggregation(od),
        "by_cohort": by_cohort,
        "model_intent_note": (
            "the MODEL (FIDELITY v0.2) intent hardcodes incremental_qty=1 in "
            "service.py, which is below the venue minimum unconditionally: "
            "every MODEL intent is unplaceable on Polymarket as it stands, "
            "independently of anything in L5's distribution"),
        "labels": ("per_fill is what the REPLAY intent uses today; per_order "
                   "is the statement about L5. Where they disagree, the "
                   "constraint is partly an artifact of our own intent "
                   "construction. Nothing here is a fill, a shadow result or "
                   "realized P&L -- it is a placeability census over history."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", default=SEQ)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    rep = report(a.seq, limit=a.limit or None)
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
