"""Run every ablation and report the panel. Control must reproduce L5 exactly."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Sequence

from analysis.l5.acceptance import ablate as A
from analysis.l5.acceptance.events import events
from analysis.l5.inventory_trajectory.trajectory import load

FIELDS = ("events_retained", "spend", "up_qty", "dn_qty", "matched_qty",
          "residual_qty", "normalized_imbalance", "final_pair_cost",
          "settlement_cashflow", "matched_economics", "residual_economics",
          "total_economics")


def aggregate(rows: Sequence[dict], ctrl: Sequence[dict]) -> Dict[str, Any]:
    """Sum across windows. Only windows with an established winner contribute
    to settlement economics; the rest are counted and excluded."""
    out: Dict[str, Any] = {}
    for f in ("events_retained", "spend", "up_qty", "dn_qty", "matched_qty",
              "residual_qty"):
        out[f] = round(sum(r[f] for r in rows), 4)
    econ = [r for r in rows if r["total_economics"] is not None]
    out["windows_with_settlement"] = len(econ)
    # SAME DENOMINATOR. The economics below sum only over windows with an
    # established winner, so the spend they are measured against must too --
    # otherwise settlement_cashflow - spend does not equal total_economics and
    # the row silently mixes two populations.
    out["spend_settled"] = round(sum(r["spend"] for r in econ), 4)
    for f in ("settlement_cashflow", "matched_economics", "residual_economics",
              "total_economics"):
        out[f] = round(sum(r[f] for r in econ if r[f] is not None), 4)
    sp = sum(r["spend"] for r in econ)
    out["return_per_dollar"] = (round(out["total_economics"] / sp, 6)
                                if sp > 0 else None)
    pairs = [r["final_pair_cost"] for r in rows
             if r["final_pair_cost"] is not None]
    pairs.sort()
    out["median_final_pair_cost"] = (round(pairs[len(pairs) // 2], 6)
                                     if pairs else None)
    out["windows_two_sided"] = len(pairs)
    imb = [r["normalized_imbalance"] for r in rows
           if r["normalized_imbalance"] is not None]
    imb.sort()
    out["median_normalized_imbalance"] = (round(imb[len(imb) // 2], 4)
                                          if imb else None)
    cm = sum(r["matched_qty"] for r in ctrl)
    out["pct_l5_matched_retained"] = (round(out["matched_qty"] / cm, 4)
                                      if cm > 0 else None)
    cs = sum(r["spend"] for r in ctrl)
    out["pct_l5_spend_retained"] = (round(out["spend"] / cs, 4) if cs > 0
                                    else None)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity", default="data/l5/raw/activity.jsonl")
    ap.add_argument("--out", default="analysis/l5/acceptance/ablation.json")
    a = ap.parse_args(argv)

    wins = load(a.activity)
    win = A.winners(a.activity)
    allev = [(s, events(s, f)) for s, f in sorted(wins.items())]
    allev = [(s, e) for s, e in allev if e]

    def run(keepfn, per_window=None) -> List[dict]:
        rows = []
        for s, evs in allev:
            k = per_window(s, evs) if per_window else keepfn
            rows.append(A.replay(evs, k, winner=win.get(s)))
        return rows

    ctrl = run(A.keep_all)
    rep: Dict[str, Any] = {"windows": len(allev),
                           "winner_unknown": sum(1 for s, _ in allev
                                                 if not win.get(s))}
    rep["CONTROL"] = aggregate(ctrl, ctrl)

    # EXACT CONTROL CHECK, against the untouched events.
    # Compared UNROUNDED: the aggregate rounds to 4dp, so checking the rounded
    # figure against the source can only ever fail by the rounding itself --
    # which it did, and briefly looked like a replay discrepancy. Event count
    # must match exactly; the money is float summation over 60k terms, so it
    # is checked on relative error rather than bit equality.
    tot_ev = sum(len(e) for _, e in allev)
    tot_sp = sum(x["usdc"] for _, e in allev for x in e)
    tot_q = sum(x["qty"] for _, e in allev for x in e)
    raw_sp = sum(r["spend"] for r in ctrl)
    raw_q = sum(r["up_qty"] + r["dn_qty"] for r in ctrl)
    rep["control_check"] = {
        "events_source": tot_ev,
        "events_replay": sum(r["events_retained"] for r in ctrl),
        "spend_rel_err": abs(raw_sp - tot_sp) / max(tot_sp, 1e-9),
        "qty_rel_err": abs(raw_q - tot_q) / max(tot_q, 1e-9),
    }
    rep["control_is_exact"] = (
        rep["control_check"]["events_replay"] == tot_ev
        and rep["control_check"]["spend_rel_err"] < 1e-9
        and rep["control_check"]["qty_rel_err"] < 1e-9)

    causal = {
        "REMOVE_WORSENING_RESIDUAL": A.causal_not_worsening_residual,
        "REMOVE_IMPROVING_RESIDUAL": A.causal_not_improving_residual,
        "REMOVE_ALL_RESIDUAL": A.causal_no_residual,
        "REMOVE_WORSENING_MATCH_EXPANSION": A.causal_not_worsening_expansion,
        "REMOVE_ALL_WORSENING_EVENTS": A.causal_not_worsening_any,
    }
    rep["CAUSAL_REPLAY"] = {k: aggregate(run(f), ctrl)
                            for k, f in causal.items()}

    # STATIC ORACLE: decide from the ORIGINAL path's realized deltas
    static: Dict[str, Any] = {}
    for name, pred in (
            ("REMOVE_WORSENING_RESIDUAL",
             lambda e: e["class"] == "RESIDUAL_EXTENSION"
             and e["d_pair"] is not None and e["d_pair"] > 0),
            ("REMOVE_IMPROVING_RESIDUAL",
             lambda e: e["class"] == "RESIDUAL_EXTENSION"
             and e["d_pair"] is not None and e["d_pair"] < 0),
            ("REMOVE_ALL_RESIDUAL",
             lambda e: e["class"] == "RESIDUAL_EXTENSION"),
            ("REMOVE_WORSENING_MATCH_EXPANSION",
             lambda e: e["class"] == "MATCH_EXPANSION_WORSENING"),
            ("REMOVE_ALL_WORSENING_EVENTS",
             lambda e: e["d_pair"] is not None and e["d_pair"] > 0)):
        static[name] = aggregate(
            run(None, per_window=lambda s, evs, p=pred: A.static_drop(
                {i for i, e in enumerate(evs) if p(e)})), ctrl)
    rep["STATIC_ORACLE"] = static

    rep["RUN_CAPS"] = {f"cap_{n}": aggregate(run(A.causal_run_cap(n)), ctrl)
                       for n in (2, 3, 5, 8, 13, 21)}
    rep["TTE_STOPS"] = {f"stop_at_tte_{t}": aggregate(
        run(A.causal_tte_stop(t)), ctrl) for t in (600, 300, 120, 60)}
    rep["PROGRESS_STOPS_HINDSIGHT"] = {
        f"first_{int(p*100)}pct": aggregate(
            run(None, per_window=lambda s, evs, pp=p:
                A.hindsight_progress_stop(len(evs), pp)), ctrl)
        for p in (0.25, 0.50, 0.70, 0.80, 0.90)}
    rep["labels"] = (
        "real fills only; no event is ever substituted. Every ablation is a "
        "strict subsequence. State, class and pair deltas are RECOMPUTED from "
        "the retained sequence at every step. PROGRESS_STOPS_HINDSIGHT is NOT "
        "executable: a window's eventual event count is unknowable mid-window.")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({"windows": rep["windows"],
                      "winner_unknown": rep["winner_unknown"],
                      "control_is_exact": rep["control_is_exact"],
                      "CONTROL": rep["CONTROL"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
