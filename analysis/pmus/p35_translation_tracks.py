"""P35 -- the four execution translations, over the preserved evidence.

Read-only. Runs T1..T4 over the five authenticated windows and compares each
against L5_NATIVE. The frozen production/shadow baseline is not touched: this
reads `analysis/pmus/evidence/` and the frozen mirror-lifecycle classification
and writes only its own report.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from decimal import Decimal as D
from typing import Any, Dict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from analysis.pmus import p34_amplitude_report as P34          # noqa: E402
from src import l5_execution_translations as X                 # noqa: E402
from src.l5_replication_fidelity import Path                   # noqa: E402

EVID = "analysis/pmus/evidence/observer_rows_settled_windows.jsonl"

#: settlement of each preserved window, read from the frozen sequence report
SEQ = "analysis/pmus/p32_sequence_report.json"


def settlements() -> Dict[str, D]:
    out = {}
    try:
        r = json.load(open(SEQ))
    except Exception:                                          # noqa: BLE001
        return out
    for w, d in (r.get("windows") or {}).items():
        v = (d.get("settlement") or {}).get("yes_value")
        if v is not None:
            out[w] = D(str(v))
    return out


def native_summary(rows) -> Dict[str, Any]:
    """L5_NATIVE: what he did, at his own relative sizes. The baseline."""
    p = Path()
    acts = sorted(rows, key=lambda r: r["l5_event_utc"])
    sides = []
    for i, r in enumerate(acts):
        side = (r.get("l5") or {}).get("pmus_side")
        if side is None:
            continue
        sides.append(side)
        p.add(i, side)
    s = p.summary()
    sizes = [float((r.get("l5") or {}).get("l5_size") or 0) for r in acts]
    up = sum(z for z, sd in zip(sizes, sides) if sd == "UP")
    dn = sum(z for z, sd in zip(sizes, sides) if sd == "DOWN")
    return {
        "actions": s["actions"], "up": s["up"], "dn": s["dn"],
        "longest_same_side_run": s["longest_same_side_run"],
        "opposite_acquired": s["opposite_acquired"],
        "inventory_crossed_zero": s["inventory_crossed_zero"],
        "overshoot_past_zero": s["overshoot_past_zero"],
        "max_abs_imbalance": s["max_abs_imbalance"],
        "terminal_matched": s["terminal_matched"],
        "terminal_residual": s["terminal_residual"],
        "l5_shares_up": round(up, 2), "l5_shares_dn": round(dn, 2),
        "l5_shares_residual": round(up - dn, 2),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    rows = [json.loads(l) for l in open(EVID) if l.strip()]
    rows = [r for r in rows if r.get("kind") == "l5_action" and not r.get("backlog")]
    auth = P34.authenticated_map(rows)
    lam = X.lambda_ref(rows)
    sett = settlements()

    by_w = collections.defaultdict(list)
    for r in rows:
        by_w[r["window_start_utc"][11:16]].append(r)

    report: Dict[str, Any] = {
        "label": "L5_EXECUTION_TRANSLATION_TRACKS_V1",
        "phase": "PHASE_1_SHADOW_COUNTERFACTUAL_ONLY",
        "lambda_ref": str(lam),
        "lambda_definition": ("1 / median(l5_size) over the preserved corpus, "
                              "so the median L5 action maps to 1 share. "
                              "Computed from L5 sizes ONLY -- no price, fill, "
                              "outcome or P&L enters it."),
        "crossing_rule": ("the PMUS touch observed AT OUR RECEIPT timestamp "
                          "only; UP crosses at ask, DOWN at 1-bid; capped at "
                          "the displayed size; taker fee charged; used ONLY "
                          "where maker reproduction is impossible"),
        "actions_with_authenticated_class": len(auth),
        "windows": {},
    }

    agg = {t: collections.Counter() for t in X.TRACKS}
    for w, rs in sorted(by_w.items()):
        nat = native_summary(rs)
        entry = {"L5_NATIVE": nat, "settlement_yes_value": str(sett.get(w)),
                 "tracks": {}}
        print("\n================ WINDOW %s   L5 actions=%d  "
              "native: run %d xzero %s overshoot %s residual_shares %s"
              % (w, nat["actions"], nat["longest_same_side_run"],
                 nat["inventory_crossed_zero"], nat["overshoot_past_zero"],
                 nat["l5_shares_residual"]))
        for name, prop, cross in (("T1_q1_maker", False, False),
                                  ("T2_q1_cross", False, True),
                                  ("T3_prop_maker", True, False),
                                  ("T4_prop_cross", True, True)):
            t = X.run_track(rs, auth, proportional=prop, allow_cross=cross,
                            lam=lam)
            s = X.summarize(t, sett.get(w))
            entry["tracks"][name] = s
            for k in ("reproduced", "l5_actions"):
                agg[name][k] += s[k]
            agg[name]["total_usd_x10000"] += int(
                round(s["total_settlement_implied_shadow_usd"] * 10000))
            for k, v in (s["habitat_failures"] or {}).items():
                agg[name]["hab_" + k] += v
            print("  %-14s repro %3d/%3d (%5s%%)  xzero %-5s over %8.2f  "
                  "matched %8.2f resid %9.2f  pair %s  TOTAL %+9.4f"
                  % (name, s["reproduced"], s["l5_actions"],
                     s["reproduction_rate_pct"], s["inventory_crossed_zero"],
                     s["overshoot_past_zero"], s["matched_final"],
                     s["residual_final"], s["pair_cost_final"],
                     s["total_settlement_implied_shadow_usd"]))
        report["windows"][w] = entry

    print("\n================ AGGREGATE over %d windows" % len(by_w))
    report["aggregate"] = {}
    for name in X.TRACKS:
        c = agg[name]
        tot = c["total_usd_x10000"] / 10000.0
        rate = (100.0 * c["reproduced"] / c["l5_actions"]) if c["l5_actions"] else None
        hab = {k[4:]: v for k, v in c.items() if k.startswith("hab_")}
        report["aggregate"][name] = {
            "reproduced": c["reproduced"], "l5_actions": c["l5_actions"],
            "reproduction_rate_pct": round(rate, 1) if rate is not None else None,
            "total_settlement_implied_shadow_usd": round(tot, 4),
            "habitat_failures": hab,
        }
        print("  %-14s repro %3d/%3d (%5.1f%%)  TOTAL %+9.4f  habitat %s"
              % (name, c["reproduced"], c["l5_actions"], rate or 0.0, tot, hab))

    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(report, indent=1, default=str))
        print("\nwrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
