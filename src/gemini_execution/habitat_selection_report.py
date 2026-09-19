#!/usr/bin/env python3
"""One pre-entry row per window, joined to its outcome. Read-only.

Outcomes come from the GEMINI_RECYCLE sequential replay -- the adapted
execution control the owner kept, because EXTEND suppression independently cut
residual without losing matched inventory. Nothing is trained; nothing is sent.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.gemini_execution import habitat_selection as H
from src.gemini_execution import metadata as MD
from src.gemini_execution import tape_replay as TR
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol
from src.gemini_execution.volume_report import _expiry_ms, captured_windows

LABEL = "GEMINI_PRE_ENTRY_HABITAT_V1"


def predecessor(symbol: str) -> Optional[str]:
    tk = MD.event_ticker(symbol)
    if not tk:
        return None
    sib = MD.sibling_ticker(tk, 5)
    return ("GEMI-%s-UP" % sib) if sib else None


def live_snapshots(soaks: str) -> Dict[str, Dict[str, Any]]:
    """The pre-entry snapshots collected live, keyed by symbol."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(glob.glob(soaks)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for w in (d.get("windows") or []):
            pe = w.get("pre_entry")
            if pe and pe.get("symbol"):
                out[pe["symbol"]] = pe
    return out


def build(*, jsonl: str, runs: str, soaks: str,
          exclude_bursts: bool = False) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)
    snaps = live_snapshots(soaks)

    rows: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for sym in sorted(meta):
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            skipped.append({"symbol": sym, "why": "tape unreadable"})
            continue
        exp = _expiry_ms(sym)
        for p in t["prints"]:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        try:
            res = MD.resolution_for(sym, reads=1)
        except Exception as exc:                                # noqa: BLE001
            res = {"verdict": "UNREADABLE",
                   "why": "%s: %s" % (type(exc).__name__, exc)}
        w = TR.replay_window(symbol=sym, policy=TR.POLICY_B,
                             cycles=cyc.get(sym) or [], prints=t["prints"],
                             resolution=res, expiry_ms=exp,
                             exclude_bursts=exclude_bursts)
        if not w.get("sufficient"):
            skipped.append({"symbol": sym, "why": w.get("why")})
            continue
        prev = predecessor(sym)
        prev_tape = WV.fetch_tape(prev) if prev else {"prints": []}
        row = H.pre_entry_row(
            symbol=sym, cycles=cyc.get(sym) or [], prints=t["prints"],
            prior_symbol=prev,
            prior_prints=(prev_tape.get("prints") or []),
            live_snapshot=snaps.get(sym))
        if not row.get("admissible"):
            skipped.append({"symbol": sym, "why": row.get("why")})
            continue
        row["_outcome"] = H.outcomes_from(w)
        rows.append(row)

    dirty = [r for r in rows if not (r.get("leakage_check") or {}).get("clean")]
    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "excluded_bursts": exclude_bursts,
        "independent_windows": len(rows),
        "windows_skipped": len(skipped), "skipped": skipped,
        "rows_failing_the_leakage_check": len(dirty),
        "leakage_failures": [r["symbol"] for r in dirty],
        "outcome_base_rates": {
            k: sum(1 for r in rows if r["_outcome"].get(k))
            for k in ("initial_maker_fill",
                      "opposite_reachable_print_after_first_fill",
                      "complete_fill", "matched_pair", H.PRIMARY)},
        "distributions": H.distributions(rows),
        "correlations": H.correlations(rows),
        "leave_one_out": H.leave_one_out(rows),
        "oracle_bound": H.oracle_bound(rows),
        "rows": rows,
        "labels": ("PRE-ENTRY ONLY. Every feature is sourced strictly before "
                   "the first hypothetical order; outcomes are computed "
                   "separately from the GEMINI_RECYCLE replay. Nothing is "
                   "trained, no threshold is fitted, no admission rule "
                   "exists, and every execution behind the outcomes is "
                   "SHADOW/THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak*.json")
    ap.add_argument("--out", default="analysis/gemini/habitat_selection.json")
    a = ap.parse_args(argv)
    both = {}
    for excl in (False, True):
        rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks,
                    exclude_bursts=excl)
        both["NO_BURSTS" if excl else "FULL_TAPE"] = rep
        print("%-10s windows=%-3s leakage_failures=%-2s base=%s oracle_avoided=%s"
              % ("NO_BURSTS" if excl else "FULL", rep["independent_windows"],
                 rep["rows_failing_the_leakage_check"],
                 json.dumps(rep["outcome_base_rates"]),
                 rep["oracle_bound"]["gross_exposure_avoided_share"]),
              flush=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(both, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
