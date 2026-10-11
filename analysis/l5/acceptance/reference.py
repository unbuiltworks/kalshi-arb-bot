"""FIDELITY: is there a reference price against which L5 keeps accumulating?

"He continues while the price is good" needs a definition of good. Current
same-side VWAP is the obvious one and also the useless one: Δpair > 0 IS price
> VWAP, so scoring that reference against a worsening/improving label is
circular. Every alternative below is a DIFFERENT quantity, each knowable at the
instant of the event.

WHAT IS SCORED IS BEHAVIOUR, NOT MONEY. The target is L5's own next action --
does he CONTINUE on this side -- and the baseline is the persistence rate. A
reference earns its place only by predicting his continuation better than
"he just did", chronologically out of sample. No reference is chosen on the
test windows.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.acceptance.dependency import annotate
from analysis.l5.inventory_trajectory.trajectory import load

REFS = ("same_side_vwap", "opposite_cost_basis", "probability_mid",
        "recent_fill_vwap", "run_start_price", "rolling_min", "rolling_max",
        "pair_budget")


def rows_with_target(wins) -> List[Dict[str, Any]]:
    out = []
    for s, f in sorted(wins.items()):
        ev = annotate(s, f)
        for i, r in enumerate(ev[:-1]):
            r = dict(r)
            r["continues"] = 1.0 if ev[i + 1]["side"] == r["side"] else 0.0
            out.append(r)
    out.sort(key=lambda r: (r["t"] or 0, r["slug"], r["i"]))
    return out


def split(rows, frac=0.70):
    first: Dict[str, float] = {}
    for r in rows:
        t = r["t"] or 0
        if r["slug"] not in first or t < first[r["slug"]]:
            first[r["slug"]] = t
    order = sorted(first, key=lambda s: first[s])
    tr = set(order[:int(len(order) * frac)])
    return ([r for r in rows if r["slug"] in tr],
            [r for r in rows if r["slug"] not in tr])


def score(rows, ref: str) -> Dict[str, Any]:
    """Accuracy of 'he continues iff the price is favourable vs this reference'."""
    usable = [r for r in rows if (r["refs"] or {}).get(ref) is not None]
    if not usable:
        return {"n": 0}
    hit = fav = 0
    fav_cont = unfav_cont = fav_n = unfav_n = 0
    for r in usable:
        favourable = r["px"] <= r["refs"][ref]
        fav += 1 if favourable else 0
        pred = 1.0 if favourable else 0.0
        hit += 1 if pred == r["continues"] else 0
        if favourable:
            fav_n += 1
            fav_cont += r["continues"]
        else:
            unfav_n += 1
            unfav_cont += r["continues"]
    base = sum(r["continues"] for r in usable) / len(usable)
    return {
        "n": len(usable),
        "favourable_share": round(fav / len(usable), 4),
        "accuracy": round(hit / len(usable), 4),
        "P_continue_when_favourable": (round(fav_cont / fav_n, 4)
                                       if fav_n else None),
        "P_continue_when_unfavourable": (round(unfav_cont / unfav_n, 4)
                                         if unfav_n else None),
        "separation": (round(fav_cont / fav_n - unfav_cont / unfav_n, 4)
                       if fav_n and unfav_n else None),
        "always_continue_baseline": round(base, 4),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/acceptance/reference.json")
    a = ap.parse_args(argv)
    rows = rows_with_target(load(a.activity))
    tr, te = split(rows)
    rep: Dict[str, Any] = {
        "rows": len(rows), "train": len(tr), "oos": len(te),
        "train_windows": len({r["slug"] for r in tr}),
        "oos_windows": len({r["slug"] for r in te}),
        "TRAIN": {r_: score(tr, r_) for r_ in REFS},
    }
    # freeze the choice on TRAIN: biggest separation, ties broken by accuracy
    cand = [(v.get("separation") or -9, v.get("accuracy") or 0, k)
            for k, v in rep["TRAIN"].items() if v.get("n")]
    cand.sort(reverse=True)
    chosen = cand[0][2]
    rep["chosen_on_train"] = chosen
    rep["selection_rule"] = ("largest |separation| in P(continue) on TRAIN "
                             "windows only; ties broken by accuracy")
    rep["OOS_all_references"] = {r_: score(te, r_) for r_ in REFS}
    rep["OOS_chosen"] = rep["OOS_all_references"][chosen]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(f"rows={rep['rows']}  train={rep['train']} ({rep['train_windows']}w)"
          f"  OOS={rep['oos']} ({rep['oos_windows']}w)")
    print(f"\nchosen on TRAIN: {chosen}\n")
    hdr = (f"{'reference':<22} {'favShare':>9} {'acc':>7} {'P(cont|fav)':>12} "
           f"{'P(cont|unfav)':>14} {'separation':>11}")
    for name, tab in (("TRAIN", rep["TRAIN"]), ("OOS", rep["OOS_all_references"])):
        print(f"--- {name} (always-continue baseline "
              f"{list(tab.values())[0].get('always_continue_baseline')}) ---")
        print(hdr)
        for k, v in tab.items():
            if not v.get("n"):
                continue
            mark = "  <-- chosen" if k == chosen else ""
            print(f"{k:<22} {v['favourable_share']:>9} {v['accuracy']:>7} "
                  f"{str(v['P_continue_when_favourable']):>12} "
                  f"{str(v['P_continue_when_unfavourable']):>14} "
                  f"{str(v['separation']):>11}{mark}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
