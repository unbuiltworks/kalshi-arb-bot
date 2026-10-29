"""FIDELITY: does accepting a worsening extension buy subsequent improvement?

THE QUESTION, AND IT IS NOT AN ECONOMIC ONE. A residual extension that raises
the pair cost looks like a mistake in isolation. If the same-side run it starts
goes on to lower the pair cost by more than it cost, it was not a mistake -- it
was the entry price of a run. This measures that directly, and NOTHING here is
optimised for P&L.

FOR EVERY WORSENING RESIDUAL EXTENSION:
  cost_accepted    the Δpair of the event itself (positive)
  run_improvement  the summed Δpair of every LATER event on the SAME side,
                   up to the next switch (negative when it improves)
  net_of_run       cost_accepted + run_improvement
                   < 0  PRODUCTIVE -- the run repaid the entry
                   > 0  DESTRUCTIVE -- it did not

The split is then predicted from state KNOWN AT ENTRY ONLY. If some observable
separates productive from destructive, there is an acceptance rule. If none
does, the rule is not in the state we can see, and NOT_DETERMINED stands.

REFERENCE PRICES. "Keep buying while the price is good" needs a definition of
good. Current same-side VWAP is one; it is also the one that makes the
classification circular, since Δpair > 0 IS price > VWAP. So the alternatives
are tested as separate references, each knowable at the instant of the event.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.inventory_trajectory.trajectory import load, window_end
from analysis.l5.next_side.features import aggregate_transactions

WINDOW_S = 900
ROLL = 10          # local rolling window, in same-side events


def annotate(slug: str, fills: Sequence[dict]) -> List[Dict[str, Any]]:
    """Every event with at-entry state and the forward run outcome."""
    end = window_end(slug)
    ev = aggregate_transactions(fills)
    up_q = dn_q = up_c = dn_c = 0.0
    run = 0
    prev: Optional[str] = None
    run_start_px: Optional[float] = None
    hist: Dict[str, List[float]] = {"Up": [], "Down": []}
    rows: List[Dict[str, Any]] = []
    for i, f in enumerate(ev):
        side = "Up" if str(f.get("outcome")) == "Up" else "Down"
        sz, usd = float(f.get("size") or 0.0), float(f.get("usdcSize") or 0.0)
        px = (usd / sz) if sz > 0 else 0.0
        t = f.get("timestamp")
        up_v = (up_c / up_q) if up_q > 0 else None
        dn_v = (dn_c / dn_q) if dn_q > 0 else None
        pair = (up_v + dn_v) if (up_v is not None and dn_v is not None) else None
        same_v = up_v if side == "Up" else dn_v
        opp_v = dn_v if side == "Up" else up_v
        h = hist[side]
        new_run = (prev is not None and side != prev) or prev is None
        if new_run:
            run_start_px = px
        # --- at-entry references, all knowable now
        refs = {
            "same_side_vwap": same_v,
            "opposite_cost_basis": opp_v,
            "probability_mid": px,           # the contract's own price
            "recent_fill_vwap": (statistics.fmean(h[-ROLL:]) if h else None),
            "run_start_price": run_start_px,
            "rolling_min": (min(h[-ROLL:]) if h else None),
            "rolling_max": (max(h[-ROLL:]) if h else None),
            "pair_budget": ((1.0 - opp_v) if opp_v is not None else None),
        }
        n_up = up_q + (sz if side == "Up" else 0.0)
        n_dn = dn_q + (sz if side == "Down" else 0.0)
        n_uc = up_c + (usd if side == "Up" else 0.0)
        n_dc = dn_c + (usd if side == "Down" else 0.0)
        n_pair = (((n_uc / n_up) + (n_dc / n_dn))
                  if (n_up > 0 and n_dn > 0) else None)
        d_pair = ((n_pair - pair) if (n_pair is not None and pair is not None)
                  else None)
        d_matched = min(n_up, n_dn) - min(up_q, dn_q)
        rows.append({
            "slug": slug, "i": i, "t": t, "side": side, "px": px, "size": sz,
            "tte_s": ((end - t) if (end is not None and t is not None) else None),
            "run_len": (run + 1 if side == prev else 1),
            "matched": min(up_q, dn_q), "resid": up_q - dn_q,
            "pair_before": pair, "d_pair": d_pair, "d_matched": d_matched,
            "refs": refs,
            "is_worsening_residual": bool(d_matched <= 0 and d_pair is not None
                                          and d_pair > 0),
        })
        up_q, dn_q, up_c, dn_c = n_up, n_dn, n_uc, n_dc
        run = rows[-1]["run_len"]
        prev = side
        h.append(px)
    # forward run outcome: later SAME-side events until the next switch
    for j, r in enumerate(rows):
        imp = 0.0
        n = 0
        for k in range(j + 1, len(rows)):
            if rows[k]["side"] != r["side"]:
                break
            if rows[k]["d_pair"] is not None:
                imp += rows[k]["d_pair"]
            n += 1
        r["run_improvement"] = imp
        r["run_events_after"] = n
        r["net_of_run"] = ((r["d_pair"] + imp) if r["d_pair"] is not None
                           else None)
    return rows


def _b(v, edges, labels):
    if v is None:
        return "unknown"
    for e, l in zip(edges, labels):
        if v < e:
            return l
    return labels[-1]


def buckets(r: dict) -> Dict[str, str]:
    same = (r["refs"] or {}).get("same_side_vwap")
    rel = (r["px"] - same) if same is not None else None
    return {
        "price_vs_same_side_vwap": _b(rel, [-0.05, -0.01, 0.01, 0.05],
                                      ["<-5c", "-5..-1c", "-1..+1c",
                                       "+1..+5c", ">+5c"]),
        "run_len": _b(r["run_len"], [2, 4, 8], ["1", "2-3", "4-7", "8+"]),
        "probability": _b(r["px"], [0.2, 0.4, 0.6, 0.8],
                          ["<.20", ".20-.40", ".40-.60", ".60-.80", ">=.80"]),
        "tte": _b((r["tte_s"] if r["tte_s"] is not None else None),
                  [120, 300, 600], ["<120s", "120-300s", "300-600s", "600s+"]),
        "size": _b(r["size"], [5, 20, 60], ["<5", "5-20", "20-60", "60+"]),
        "amount_of_worsening": _b(r["d_pair"], [0.001, 0.005, 0.02],
                                  ["<0.001", "0.001-0.005", "0.005-0.02",
                                   ">0.02"]),
    }


DIMS = ("price_vs_same_side_vwap", "run_len", "probability", "tte", "size",
        "amount_of_worsening")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/acceptance/dependency.json")
    a = ap.parse_args(argv)
    wins = load(a.activity)
    rows = [r for s, f in sorted(wins.items()) for r in annotate(s, f)]
    wr = [r for r in rows if r["is_worsening_residual"]
          and r["net_of_run"] is not None]
    prod = [r for r in wr if r["net_of_run"] < 0]
    rep: Dict[str, Any] = {
        "events": len(rows),
        "worsening_residual_extensions": len(wr),
        "productive": len(prod),
        "productive_share": round(len(prod) / len(wr), 4) if wr else None,
        "total_cost_accepted": round(sum(r["d_pair"] for r in wr), 4),
        "total_run_improvement": round(sum(r["run_improvement"] for r in wr), 4),
        "total_net_of_run": round(sum(r["net_of_run"] for r in wr), 4),
        "by": {},
    }
    for d in DIMS:
        agg: Dict[str, Dict[str, Any]] = {}
        for r in wr:
            k = buckets(r)[d]
            x = agg.setdefault(k, {"n": 0, "prod": 0, "cost": 0.0, "imp": 0.0})
            x["n"] += 1
            x["prod"] += 1 if r["net_of_run"] < 0 else 0
            x["cost"] += r["d_pair"]
            x["imp"] += r["run_improvement"]
        rep["by"][d] = {k: {"n": v["n"],
                            "productive_share": round(v["prod"] / v["n"], 4),
                            "cost_accepted": round(v["cost"], 4),
                            "run_improvement": round(v["imp"], 4),
                            "net": round(v["cost"] + v["imp"], 4)}
                        for k, v in sorted(agg.items())}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in rep.items() if k != "by"}, indent=1))
    for d in DIMS:
        print(f"\n{d}")
        print(f"  {'bucket':<16} {'n':>6} {'productive':>11} {'cost':>10} "
              f"{'improvement':>12} {'net':>10}")
        for k, v in rep["by"][d].items():
            print(f"  {k:<16} {v['n']:>6} {v['productive_share']:>11} "
                  f"{v['cost_accepted']:>10.3f} {v['run_improvement']:>12.3f} "
                  f"{v['net']:>10.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
