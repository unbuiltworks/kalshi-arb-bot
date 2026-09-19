#!/usr/bin/env python3
"""CONTROL vs corrected timing on the running BTC15m lane. Offline, read-only.

Reads only what the lane persisted. Places nothing, cancels nothing, and
constructs no session.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from src.gemini_execution import arm_bands as AB
from src.gemini_execution import arm_compare as AC
from src.gemini_execution import lanes as LA

COLS = ("readable_cycles", "rungs_rested", "first_fills", "extend_fills",
        "complete_fills", "matched_pairs", "actual_pair_basis_median",
        "fee_positive_pairs", "residual_generated", "matched_gross")


def overlap_divergence(per_arm) -> dict:
    """THE VALIDITY CHECK. Below tte 300 both arms can act, and the frozen
    band is identical there, so their decisions should match. If they do not,
    something other than timing moved and the comparison is void."""
    arms = [a for a in per_arm if a != AC.CONTROL]
    if not arms:
        return {"checked": False, "why": "no corrected arm in this evidence"}
    ctl = per_arm[AC.CONTROL]["bands"]["<=300"]
    out = {}
    for a in arms:
        arm = per_arm[a]["bands"]["<=300"]
        out[a] = {k: [ctl.get(k), arm.get(k)]
                  for k in ("first_fills", "open_fills", "extend_fills",
                            "complete_fills", "rungs_rested")
                  if ctl.get(k) != arm.get(k)}
    return {"checked": True, "differences_below_tte_300": out,
            "expected": "EMPTY. The arms share the frozen band below 300; a "
                        "difference here means the change was not confined "
                        "to timing, and the comparison cannot be read."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence",
                    default="/state/gemini-btc15m/continuous_v1/*.jsonl")
    ap.add_argument("--out", default="analysis/gemini/arm_compare.json")
    a = ap.parse_args(argv)
    windows = LA.lane_windows(a.evidence)
    comparison = AC.compare(windows)
    labels = list(comparison["arms"])
    per_arm = {lbl: AB.bands(windows, lbl) for lbl in labels}

    rep = {"utc": datetime.now(timezone.utc).isoformat(),
           "evidence": a.evidence, "windows_read": len(windows),
           "comparison": comparison, "bands": per_arm,
           "chain": {lbl: AC.chain_census(windows, lbl) for lbl in labels},
           "chains_by_window": {lbl: [AC.the_chain(w, lbl) for w in windows]
                                for lbl in labels},
           "overlap_divergence": overlap_divergence(per_arm)}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)

    print("ARM_COMPARE windows_read=%s" % len(windows))
    for lbl in labels:
        b = per_arm[lbl]
        print("\n%s  band=%s  windows=%s" % (
            lbl, comparison["arms"][lbl]["quote_between_tte_s"], b["windows"]))
        print("  %-9s %6s %7s %6s %6s %6s %6s %9s %6s %9s %7s" % (
            "tte", "readC", "rested", "first", "EXT", "COMP", "pairs",
            "basis", "fee+", "resGen", "m/g"))
        for name, _, _ in AB.BANDS:
            r = b["bands"][name]
            print("  %-9s %6d %7d %6d %6d %6d %6d %9s %6d %9s %7s" % (
                name, r["readable_cycles"], r["rungs_rested"],
                r["first_fills"], r["extend_fills"], r["complete_fills"],
                r["matched_pairs"], r["actual_pair_basis_median"],
                r["fee_positive_pairs"], r["residual_generated"],
                r["matched_gross"]))
        c = rep["chain"][lbl]["broke_at"]
        print("  CHAIN broke_at: " + "  ".join(
            "%s=%d" % (k, v) for k, v in c.items() if v))

    d = rep["overlap_divergence"]
    if d.get("checked"):
        bad = {k: v for k, v in d["differences_below_tte_300"].items() if v}
        print("\nOVERLAP <=300 divergence: %s" % (bad or "NONE (as required)"))
    for h in [x for lbl in labels for x in rep["chains_by_window"][lbl]
              if x.get("complete")]:
        print("SEQUENCE COMPLETE %s %s" % (h["arm"], h["symbol"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
