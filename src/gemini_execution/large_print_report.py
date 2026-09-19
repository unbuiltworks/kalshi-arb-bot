#!/usr/bin/env python3
"""Run the large-print forensic across every captured BTC5m window. Read-only.

Assembles each contract's complete public tape, our own recorded cycles, and
the venue's settlement, then replays every hypothetical fill forward through
the FROZEN L5 policy. Sends nothing; Model A is unchanged.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import large_prints as LP
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV
from src.gemini_execution.volume_report import _expiry_ms

LABEL = "GEMINI_L5_LARGE_PRINT_FORENSIC_V1"


def _ms(utc: Any) -> Any:
    try:
        return int(datetime.fromisoformat(
            str(utc).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def cycles_by_symbol(jsonl: str, runs: str) -> Dict[str, List[Dict[str, Any]]]:
    by: Dict[str, List[Dict[str, Any]]] = {}
    try:
        fh = open(jsonl, encoding="utf-8")
    except OSError:
        fh = None
    if fh:
        with fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "cycle" or not r.get("symbol"):
                    continue
                by.setdefault(r["symbol"], []).append({
                    "utc": r.get("utc"), "utc_ms": _ms(r.get("utc")),
                    "tte_s": r.get("tte_s"), "book": r.get("book"),
                    "depth": r.get("depth"),
                    "actions": r.get("l5_action") or []})
    for p in sorted(glob.glob(runs)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sym = (d.get("after") or {}).get("symbol")
        for dec in (d.get("decisions") or []):
            bs = dec.get("book_state") or {}
            if not bs or not sym:
                continue
            by.setdefault(sym, []).append({
                "utc": dec.get("utc"), "utc_ms": _ms(dec.get("utc")),
                "tte_s": dec.get("tte_s"), "book": bs.get("presented"),
                "depth": bs.get("depth"), "actions": dec.get("actions") or []})
    for v in by.values():
        v.sort(key=lambda x: (x.get("utc_ms") or 0))
    return by


def build(*, jsonl: str, runs: str, soaks: str,
          replay_top: int = 6) -> Dict[str, Any]:
    from src.gemini_execution.volume_report import captured_windows
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)

    rows: List[Dict[str, Any]] = []
    resolutions: Dict[str, Any] = {}
    tapes: Dict[str, Any] = {}
    for sym in sorted(meta):
        tape = WV.fetch_tape(sym)
        if not tape.get("readable"):
            continue
        exp = _expiry_ms(sym)
        for p in tape["prints"]:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        tapes[sym] = tape
        try:
            resolutions[sym] = MD.resolution_for(sym, reads=1)
        except Exception as exc:                                # noqa: BLE001
            resolutions[sym] = {"verdict": "UNREADABLE",
                                "why": "%s: %s" % (type(exc).__name__, exc)}
        rows.extend(LP.forensic(symbol=sym, prints=tape["prints"],
                                cycles=cyc.get(sym) or [],
                                resolution=resolutions[sym]))

    # THE INVENTORY PATH is replayed for the largest prints that a quote of
    # ours would actually have reached -- the ones where the question "what
    # happens after the fill" is not hypothetical twice over.
    reachable = [r for r in rows if r.get("evaluable") and any(
        (r["quote_tests"].get(k) or {}).get("would_reach_us")
        for k in ("NATIVE", "IMPROVE_1_TICK"))]
    reachable.sort(key=lambda r: float(r["print"]["amount"]), reverse=True)
    paths = []
    for r in reachable[:replay_top]:
        sym = r["symbol"]
        t = r["quote_tests"]
        which = ("NATIVE" if (t.get("NATIVE") or {}).get("would_reach_us")
                 else "IMPROVE_1_TICK")
        px = t[which]["price"]
        ab = (t[which].get("absorbed") or {}).get("0.01") or {}
        qty = ab.get("hypothetically_filled") or "0.01"
        path = LP.inventory_path(
            symbol=sym, leg=r["reachable_leg"], qty=qty, price=px,
            from_ms=r["print"]["utc_ms"], cycles=cyc.get(sym) or [],
            prints=tapes[sym]["prints"], resolution=resolutions.get(sym) or {})
        path.update({"seeded_from_tid": r["tid"], "overlay": which,
                     "print_amount": r["print"]["amount"],
                     "print_tte_s": r["print"]["tte_s"]})
        paths.append(path)

    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "census": LP.census(rows),
        "attribution": LP.attribution(
            [p for t in tapes.values() for p in (t.get("prints") or [])],
            rows),
        "rows": rows,
        "inventory_paths": paths,
        "labels": ("SHADOW forensic. Every decision replayed comes from the "
                   "FROZEN policy; nothing here chooses a price, a size or "
                   "an action, no order was sent, and every fill is "
                   "THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak_*.json")
    ap.add_argument("--out", default="analysis/gemini/large_prints.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print("LARGE_PRINTS " + json.dumps(rep["census"]["buckets"], default=str),
          flush=True)
    print("PATHS " + json.dumps(
        [{"tid": p.get("seeded_from_tid"), "amt": p.get("print_amount"),
          "overlay": p.get("overlay"), "fills_after": p.get(
              "theoretical_fills_after_seed"),
          "actions": p.get("actions_seen")} for p in rep["inventory_paths"]],
        default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
