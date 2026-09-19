#!/usr/bin/env python3
"""Full sequential-tape replay over every sufficiently reconstructed window.

Two policies, four passes: A and B, each with and without the >=100-contract
prints, so no conclusion can rest on a handful of bursts. Read-only; sends
nothing; Model A unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import metadata as MD
from src.gemini_execution import tape_replay as TR
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol
from src.gemini_execution.volume_report import _expiry_ms, captured_windows

LABEL = "GEMINI_TAPE_REPLAY_V1"


def build(*, jsonl: str, runs: str, soaks: str) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)
    tapes: Dict[str, Any] = {}
    resolutions: Dict[str, Any] = {}
    for sym in sorted(meta):
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        exp = _expiry_ms(sym)
        for p in t["prints"]:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        tapes[sym] = t
        try:
            resolutions[sym] = MD.resolution_for(sym, reads=1)
        except Exception as exc:                                # noqa: BLE001
            resolutions[sym] = {"verdict": "UNREADABLE",
                                "why": "%s: %s" % (type(exc).__name__, exc)}

    passes: Dict[str, Any] = {}
    frontier_rows: List[Dict[str, Any]] = []
    windows_by_pass: Dict[str, List[Dict[str, Any]]] = {}
    for policy in TR.POLICIES:
        for excl in (False, True):
            key = "%s%s" % (policy, "_NO_BURSTS" if excl else "")
            rows = []
            for sym in sorted(tapes):
                w = TR.replay_window(
                    symbol=sym, policy=policy, cycles=cyc.get(sym) or [],
                    prints=tapes[sym]["prints"],
                    resolution=resolutions.get(sym) or {},
                    expiry_ms=_expiry_ms(sym), exclude_bursts=excl)
                if w.get("sufficient"):
                    w["economics"] = TR.economics(
                        w["fills"], None, w.get("settlement") or {})
                    # the frontier is computed once, on the full-tape A pass
                    if policy == TR.POLICY_A and not excl:
                        for ep in w["missed_complement_episodes"]:
                            f = TR.frontier_for(ep, tapes[sym]["prints"])
                            f.update({"symbol": sym,
                                      "model_a_price": ep.get("price"),
                                      "held_leg": ep.get("held_leg"),
                                      "rested_s": ep.get("rested_s")})
                            frontier_rows.append(f)
                    for ep in w["missed_complement_episodes"]:
                        ep.pop("depth", None)
                rows.append(w)
            windows_by_pass[key] = rows
            passes[key] = TR.policy_census(rows)

    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "windows_offered": len(tapes),
        "min_cycles_for_a_window": TR.MIN_CYCLES_FOR_A_WINDOW,
        "by_policy": passes,
        "frontier": TR.frontier_census(frontier_rows),
        "frontier_rows": frontier_rows,
        "windows": windows_by_pass,
        "labels": ("SHADOW. The tape is the clock: prints are walked in order "
                   "and inventory plus the Model-A action are recomputed "
                   "after every hypothetical execution. Nothing was sent and "
                   "every execution is THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak*.json")
    ap.add_argument("--out", default="analysis/gemini/tape_replay.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    for k, v in rep["by_policy"].items():
        print("%-28s windows=%-3s filled=%-3s paired=%-3s init=%-3s "
              "comp=%-3s ext=%-3s m/g=%s" % (
                  k, v.get("independent_windows"), v.get("windows_filled"),
                  v.get("windows_paired"), v.get("initial_fills"),
                  v.get("complete_fills"), v.get("extend_fills"),
                  v.get("matched_over_gross")), flush=True)
    f = rep["frontier"]
    print("FRONTIER episodes=%s reachable=%s cross=%s %s" % (
        f.get("episodes"), f.get("reachable_at_some_maker_price"),
        f.get("needing_a_cross"),
        json.dumps(f.get("by_minimum_improvement"))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
