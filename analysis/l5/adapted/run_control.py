"""ADAPTED lineage — HISTORICAL_ACTION_FILTER_COUNTERFACTUAL, not executable replay.

WHAT THIS IS AND IS NOT. It takes L5's REAL fills and asks what would have
remained had a run-control rule declined some of them. A historical fill
surviving that filter is NOT evidence that OUR maker order would have been
filled at that moment: we had no order there, no queue position, and removing
his fills does not replace the flow that produced them. This can justify a
prospective shadow candidate and nothing more. It is never a backtest.

SEPARATE FROM FIDELITY. Nothing here is evidence about what L5 does or why.
The fidelity lineage keeps NOT_DETERMINED; this lineage asks only whether a
restraint rule would have kept the matched edge while cutting the residual
loss.

THE PROTOCOL IS FIXED BEFORE THE ANSWER IS SEEN:
  * split windows CHRONOLOGICALLY, 70% train / 30% out of sample;
  * the family is preregistered -- L5 control plus run caps 2, 3, 5, 8, 13, 21;
  * selection uses TRAIN ONLY, by a rule written down before looking at OOS;
  * the selected rule is applied ONCE to untouched OOS windows;
  * EVERY cap's OOS result is reported, so a winner cannot hide instability.

NO INCENTIVES. Rebates and rewards are excluded from qualification economics
entirely; they are reconciled separately and never netted into this.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.acceptance import ablate as A
from analysis.l5.acceptance.events import events
from analysis.l5.inventory_trajectory.trajectory import load

CAPS = (2, 3, 5, 8, 13, 21)
LABEL = "HISTORICAL_ACTION_FILTER_COUNTERFACTUAL"
TRAIN_FRAC = 0.70


def max_drawdown(pnls: Sequence[float]) -> float:
    """Worst peak-to-trough of the cumulative window-by-window P&L."""
    peak = cum = 0.0
    worst = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return worst


def panel(rows: Sequence[dict], ctrl: Sequence[dict],
          months: Sequence[str]) -> Dict[str, Any]:
    econ = [r for r in rows if r["total_economics"] is not None]
    spend = sum(r["spend"] for r in econ)
    tot = sum(r["total_economics"] for r in econ)
    me = sum(r["matched_economics"] for r in econ
             if r["matched_economics"] is not None)
    re_ = sum(r["residual_economics"] for r in econ
              if r["residual_economics"] is not None)
    pairs = sorted(r["final_pair_cost"] for r in rows
                   if r["final_pair_cost"] is not None)
    cm = sum(r["matched_qty"] for r in ctrl)
    by_month: Dict[str, float] = {}
    for r, m in zip(rows, months):
        if r["total_economics"] is not None:
            by_month[m] = by_month.get(m, 0.0) + r["total_economics"]
    return {
        "windows": len(rows), "windows_with_settlement": len(econ),
        "events_retained": sum(r["events_retained"] for r in rows),
        "spend": round(spend, 2),
        "matched_pnl": round(me, 2), "residual_pnl": round(re_, 2),
        "total_pnl": round(tot, 2),
        "return_per_dollar": (round(tot / spend, 6) if spend > 0 else None),
        "matched_qty": round(sum(r["matched_qty"] for r in rows), 2),
        "pct_l5_matched_retained": (round(sum(r["matched_qty"] for r in rows) / cm, 4)
                                    if cm > 0 else None),
        "median_pair_cost": (round(pairs[len(pairs) // 2], 6) if pairs else None),
        "residual_qty": round(sum(r["residual_qty"] for r in rows), 2),
        "max_drawdown": round(max_drawdown(
            [r["total_economics"] for r in econ]), 2),
        "pnl_by_month": {k: round(v, 2) for k, v in sorted(by_month.items())},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/adapted/run_control.json")
    a = ap.parse_args(argv)

    wins = load(a.activity)
    win = A.winners(a.activity)
    allev = [(s, e) for s, e in ((s, events(s, f))
                                 for s, f in sorted(wins.items())) if e]
    allev.sort(key=lambda se: se[1][0]["t"] or 0)
    cut = int(len(allev) * TRAIN_FRAC)
    parts = {"TRAIN": allev[:cut], "OOS": allev[cut:]}
    # A SECOND, REGIME split. 92% of windows fall in August, so the 70/30 count
    # split leaves an OOS of barely two days -- enough to score a rule, useless
    # for the regime-stability question. Spring -> August is a real transfer.
    def _m(se):
        t = se[1][0]["t"]
        return dt.datetime.utcfromtimestamp(t).strftime("%Y-%m") if t else "?"
    spring = [se for se in allev if _m(se) in ("2026-03", "2026-04")]
    august = [se for se in allev if _m(se) not in ("2026-03", "2026-04")]
    parts["TRAIN_SPRING"] = spring
    parts["OOS_AUGUST"] = august

    def month(s_evs):
        t = s_evs[1][0]["t"]
        return dt.datetime.utcfromtimestamp(t).strftime("%Y-%m") if t else "?"

    def run(part, keepfn):
        return [A.replay(e, keepfn, winner=win.get(s)) for s, e in part]

    rep: Dict[str, Any] = {
        "label": LABEL,
        "not_executable_replay": (
            "a historical fill surviving the filter is NOT proof our order "
            "would have filled; we held no queue position and no flow is "
            "replaced. Justifies a prospective shadow candidate only."),
        "incentives_excluded": True,
        "train_windows": len(parts["TRAIN"]), "oos_windows": len(parts["OOS"]),
        "train_span": [dt.datetime.utcfromtimestamp(
            parts["TRAIN"][0][1][0]["t"]).strftime("%Y-%m-%d"),
            dt.datetime.utcfromtimestamp(
                parts["TRAIN"][-1][1][0]["t"]).strftime("%Y-%m-%d")],
        "oos_span": [dt.datetime.utcfromtimestamp(
            parts["OOS"][0][1][0]["t"]).strftime("%Y-%m-%d"),
            dt.datetime.utcfromtimestamp(
                parts["OOS"][-1][1][0]["t"]).strftime("%Y-%m-%d")],
        "preregistered_family": ["L5_CONTROL"] + [f"cap_{c}" for c in CAPS],
        "selection_rule": ("highest total_pnl on TRAIN among the preregistered "
                           "family, required also to retain >=80% of L5's "
                           "TRAIN matched quantity. Written before OOS was "
                           "computed; applied once."),
    }
    for name, part in parts.items():
        ctrl = run(part, A.keep_all)
        ms = [month(se) for se in part]
        rep[name] = {"L5_CONTROL": panel(ctrl, ctrl, ms)}
        for c in CAPS:
            rep[name][f"cap_{c}"] = panel(run(part, A.causal_run_cap(c)),
                                          ctrl, ms)

    elig2 = [(v["total_pnl"], k) for k, v in rep["TRAIN_SPRING"].items()
             if k != "L5_CONTROL"
             and (v["pct_l5_matched_retained"] or 0) >= 0.80]
    elig2.sort(reverse=True)
    rep["chosen_on_train_spring"] = elig2[0][1] if elig2 else None
    elig = [(v["total_pnl"], k) for k, v in rep["TRAIN"].items()
            if k != "L5_CONTROL"
            and (v["pct_l5_matched_retained"] or 0) >= 0.80]
    elig.sort(reverse=True)
    chosen = elig[0][1] if elig else None
    rep["chosen_on_train"] = chosen
    rep["train_eligible"] = [k for _, k in elig]
    rep["OOS_RESULT_OF_CHOSEN"] = rep["OOS"].get(chosen) if chosen else None
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)

    print(f"{LABEL}")
    print(f"TRAIN {rep['train_windows']}w {rep['train_span']}   "
          f"OOS {rep['oos_windows']}w {rep['oos_span']}")
    print(f"selection: {rep['selection_rule']}")
    print(f"eligible on TRAIN (>=80% matched): {rep['train_eligible']}")
    print(f"CHOSEN: {chosen}\n")
    hdr = (f"{'rule':<13} {'totalPnL':>10} {'matchPnL':>10} {'residPnL':>10} "
           f"{'ret/$':>10} {'matched%':>9} {'pair':>9} {'maxDD':>9}")
    for name in ("TRAIN", "OOS", "TRAIN_SPRING", "OOS_AUGUST"):
        print(f"--- {name} ---")
        print(hdr)
        for k in ["L5_CONTROL"] + [f"cap_{c}" for c in CAPS]:
            v = rep[name][k]
            mark = ("  <-- chosen" if (name in ("OOS", "OOS_AUGUST")
                                       and k == chosen) else "")
            print(f"{k:<13} {v['total_pnl']:>10.2f} {v['matched_pnl']:>10.2f} "
                  f"{v['residual_pnl']:>10.2f} {str(v['return_per_dollar']):>10} "
                  f"{v['pct_l5_matched_retained']*100:>8.1f}% "
                  f"{str(v['median_pair_cost']):>9} {v['max_drawdown']:>9.2f}{mark}")
        print()
    print("OOS P&L by month:")
    for k in ["L5_CONTROL"] + [f"cap_{c}" for c in CAPS]:
        print(f"  {k:<13} {rep['OOS'][k]['pnl_by_month']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
