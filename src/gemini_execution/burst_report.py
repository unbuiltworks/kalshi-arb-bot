#!/usr/bin/env python3
"""Run the large-flow precursor study across the complete BTC5m tape."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import bursts as BU
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol
from src.gemini_execution.volume_report import _expiry_ms, captured_windows

#: features compared burst-vs-control
KEYS = tuple(
    ["prints_%ds" % w for w in BU.LOOKBACKS_S]
    + ["contracts_%ds" % w for w in BU.LOOKBACKS_S]
    + ["buy_share_%ds" % w for w in BU.LOOKBACKS_S]
    + ["aggressor_imbalance_%ds" % w for w in BU.LOOKBACKS_S]
    + ["max_print_%ds" % w for w in BU.LOOKBACKS_S]
    + ["price_velocity_%ds" % w for w in BU.LOOKBACKS_S]
    + ["price_range_%ds" % w for w in BU.LOOKBACKS_S]
    + ["tte_s", "spread", "touch_depth", "ladder_total",
       "book_imbalance_top_over_total", "depth_change",
       "depth_depletion_share", "levels_disappeared", "levels_appeared"])


def build(*, jsonl: str, runs: str, soaks: str,
          threshold: str) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)
    burst_rows: List[Dict[str, Any]] = []
    ctrl_rows: List[Dict[str, Any]] = []
    detail: List[Dict[str, Any]] = []
    n_prints = 0
    for sym in sorted(meta):
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        exp = _expiry_ms(sym)
        prints = t["prints"]
        for p in prints:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        n_prints += len(prints)
        cs = cyc.get(sym) or []
        bs = BU.bursts_in(prints, threshold=threshold)
        cps = BU.control_points(prints, [b["utc_ms"] for b in bs])
        for b in bs:
            row = BU.features_before(prints, cs, at_ms=b["utc_ms"],
                                     tte_s=b.get("tte_s"))
            row.update({"symbol": sym, "amount": str(b.get("amount")),
                        "type": b.get("type"), "price": str(b.get("price"))})
            burst_rows.append(row)
            detail.append(row)
        for c in cps:
            row = BU.features_before(prints, cs, at_ms=c["utc_ms"],
                                     tte_s=c.get("tte_s"))
            row.update({"symbol": sym, "amount": str(c.get("amount")),
                        "type": c.get("type")})
            ctrl_rows.append(row)

    cmp_ = BU.compare(burst_rows, ctrl_rows, KEYS)
    return {
        "label": "GEMINI_BURST_PRECURSOR_V1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "threshold_contracts": threshold,
        "windows": len(meta), "prints_scanned": n_prints,
        "n_bursts": len(burst_rows), "n_controls": len(ctrl_rows),
        "control_clearance_s": BU.CONTROL_CLEARANCE_S,
        "comparison": cmp_,
        "verdict": BU.verdict(cmp_, len(burst_rows)),
        "bursts": detail,
        "labels": ("OBSERVATIONAL. Nothing is fitted, no threshold is set, "
                   "and no admission rule follows from this."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak*.json")
    ap.add_argument("--threshold", default="100")
    ap.add_argument("--out", default="analysis/gemini/burst_precursors.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks,
                threshold=a.threshold)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    print("BURSTS n=%d controls=%d separated=%s" % (
        rep["n_bursts"], rep["n_controls"],
        rep["verdict"]["features_separated_from_control"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
