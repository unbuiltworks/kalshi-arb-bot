#!/usr/bin/env python3
"""CONTROL vs corrected timing on the running BTC15m lane. Offline, read-only."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from src.gemini_execution import arm_compare as AC
from src.gemini_execution import lanes as LA


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence",
                    default="/state/gemini-btc15m/continuous_v1/*.jsonl")
    ap.add_argument("--out", default="analysis/gemini/arm_compare.json")
    a = ap.parse_args(argv)
    windows = LA.lane_windows(a.evidence)
    rep = {"utc": datetime.now(timezone.utc).isoformat(),
           "evidence": a.evidence, "windows_read": len(windows),
           "comparison": AC.compare(windows)}
    rep["sequences"] = {lbl: AC.the_sequence(windows, lbl)
                        for lbl in rep["comparison"]["arms"]}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)

    print("ARM_COMPARE windows_read=%s" % len(windows))
    print("%-34s %6s %7s %6s %6s %6s %9s %8s" % (
        "arm", "wins", "rested", "first", "COMP", "RECYC", "matched",
        "basis"))
    for lbl, m in rep["comparison"]["arms"].items():
        print("%-34s %6d %7d %6d %6d %6d %9s %8s   band=%s" % (
            lbl[:34], m["windows"], m["rungs_rested"], m["first_fills"],
            m["complete_fills"], m["recycled_windows"], m["matched_qty"],
            m["median_pair_basis"], m["quote_between_tte_s"]))
    for lbl, hits in rep["sequences"].items():
        for h in hits:
            print("SEQUENCE %s %s first=%s COMPLETE=%s matched=%s basis=%s"
                  % (lbl, h["symbol"], h["first_fills"], h["complete_fills"],
                     h["matched_qty"], h["effective_matched_pair_cost"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
