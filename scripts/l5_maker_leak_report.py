"""Quantify the maker-completion leak on both cohorts, then suppress it.

Diagnosis only. theta is read from the frozen engine and never written; the
only other bar is $1.00, which is arithmetic, not a fitted parameter.
"""
from __future__ import annotations

import json
import statistics
import sys
from decimal import Decimal as D
from typing import Any, Dict, List

import scripts.pmus_completeness_rule as CR
from scripts.audit_economic_complete import USABLE, TAPE as FROZEN_TAPE
from src import l5_maker_leak as ML
from src import pmus_l5_native_engine as E

OOS_TAPE = "data/pmus_btc15m_tape_oos/oos_tape_ordered.jsonl"
SETTLE = "analysis/l5/r7_oos_settlement_authoritative.json"
OUT = "analysis/l5/r7_maker_leak.json"


def frozen_by():
    from scripts.audit_economic_complete import load
    return load()


def cohorts():
    oos_by = CR.load_tape(OOS_TAPE)
    win = json.load(open(SETTLE))["winners"]
    oos = {s: win[s] for s in sorted(oos_by)
           if CR.evaluate(s, oos_by[s]).get("tape_test_passes")
           and s not in USABLE and s in win}
    fz_by = frozen_by()
    return ({"name": "frozen25", "by": fz_by, "win": dict(USABLE)},
            {"name": "oos49", "by": oos_by, "win": oos})


def measure(coh, clip: int) -> Dict[str, Any]:
    buckets: Dict[str, Dict[str, D]] = {}
    for label, _lo, _hi in ML.BUCKETS:
        buckets[label] = {"events": D(0), "qty": D(0), "cash": D(0),
                          "pnl": D(0), "maker_qty": D(0), "taker_qty": D(0)}
    per_window: List[Dict[str, Any]] = []
    residue = D(0)
    base_net = D(0)
    for slug, winner in coh["win"].items():
        r = ML.instrument(coh["by"][slug], winner=winner, clip=D(str(clip)))
        net = ML.net_of(r)
        base_net += net
        attributed = D(0)
        wb: Dict[str, D] = {}
        for a in r["acquisitions"]:
            lab = ML.bucket_of(a["pair_cost"])
            b = buckets[lab]
            b["events"] += 1
            b["qty"] += a["matched_delta"]
            b["cash"] += a["matched_delta"] * a["pair_cost"]
            contrib = a["matched_delta"] * (D(1) - a["pair_cost"])
            b["pnl"] += contrib
            b["maker_qty" if a["kind"].startswith("maker") else
              "taker_qty"] += a["matched_delta"]
            attributed += contrib
            wb[lab] = wb.get(lab, D(0)) + contrib
        # average-cost bookkeeping means a later cheap fill on the other side
        # improves the basis of inventory that was ALREADY matched. That is
        # real money and it is not attributable to any single acquisition, so
        # it is reported as a reconciliation line rather than smeared.
        residue += D(str(r["matched_pnl"])) - attributed
        per_window.append({
            "window": slug[-5:], "net": str(net),
            "matched_pnl": str(r["matched_pnl"]),
            "resid_pnl": str(r["resid_pnl"]), "fees": str(r["fees"]),
            "attributed": str(attributed),
            "buckets": {k: str(v) for k, v in wb.items()},
            "acqs": len(r["acquisitions"])})
    n = len(coh["win"])
    return {"cohort": coh["name"], "clip": clip, "n": n,
            "mean_net": str(base_net / n),
            "buckets": {k: {kk: str(vv) for kk, vv in v.items()}
                        for k, v in buckets.items()},
            "basis_improvement_residue": str(residue),
            "per_window": per_window}


def suppress(coh, clip: int, ceiling: D) -> Dict[str, Any]:
    tot = D(0)
    rows = []
    sq, se = D(0), 0
    resid_tot, fee_tot, matched_tot = D(0), D(0), D(0)
    losing, ge5 = 0, 0
    for slug, winner in coh["win"].items():
        r = ML.replay_suppressed(coh["by"][slug], winner=winner,
                                 clip=D(str(clip)), ceiling=ceiling)
        net = ML.net_of(r)
        tot += net
        sq += r["suppressed_qty"]
        se += r["suppressed_events"]
        resid_tot += D(str(r["resid_pnl"]))
        fee_tot += D(str(r["fees"]))
        matched_tot += D(str(r["matched_pnl"]))
        if net < 0:
            losing += 1
        if net >= 5:
            ge5 += 1
        rows.append({"window": slug[-5:], "net": str(net),
                     "matched_pnl": str(r["matched_pnl"]),
                     "resid_pnl": str(r["resid_pnl"]),
                     "resid_qty": str(r["resid_qty"]),
                     "matched_qty": str(r["matched_qty"]),
                     "fees": str(r["fees"]),
                     "suppressed_qty": str(r["suppressed_qty"]),
                     "suppressed_events": r["suppressed_events"]})
    n = len(coh["win"])
    nets = sorted(D(x["net"]) for x in rows)
    return {"cohort": coh["name"], "clip": clip, "ceiling": str(ceiling),
            "n": n, "mean_net": str(tot / n),
            "median_net": str(nets[n // 2] if n % 2 else
                              (nets[n // 2 - 1] + nets[n // 2]) / 2),
            "losing": losing, "ge5": ge5,
            "worst": str(nets[0]), "best": str(nets[-1]),
            "mean_matched_pnl": str(matched_tot / n),
            "mean_resid_pnl": str(resid_tot / n),
            "mean_fees": str(fee_tot / n),
            "suppressed_qty": str(sq), "suppressed_events": se,
            "per_window": rows}


if __name__ == "__main__":
    clips = [int(x) for x in (sys.argv[1:] or ["10"])]
    doc: Dict[str, Any] = {"LABEL": "MAKER_LEAK_DIAGNOSIS",
                           "theta": str(E.THETA),
                           "labels": ("SHADOW / HISTORICAL REPLAY. No "
                                      "parameter fitted; theta read, never "
                                      "written. No live order."),
                           "measure": [], "suppressed": []}
    for coh in cohorts():
        for clip in clips:
            m = measure(coh, clip)
            doc["measure"].append(m)
            print("MEASURED %s q%d n=%d mean=%s" % (
                coh["name"], clip, m["n"], m["mean_net"]), flush=True)
            for ceiling in (ML.DOLLAR, E.THETA):
                s = suppress(coh, clip, ceiling)
                doc["suppressed"].append(s)
                print("SUPPRESSED %s q%d ceiling=%s mean=%s median=%s "
                      "losing=%d suppressed_qty=%s" % (
                          coh["name"], clip, ceiling, s["mean_net"],
                          s["median_net"], s["losing"], s["suppressed_qty"]),
                      flush=True)
    json.dump(doc, open(OUT, "w"), indent=1)
    print("WROTE " + OUT, flush=True)
