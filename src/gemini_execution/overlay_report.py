#!/usr/bin/env python3
"""Run the three-overlay study over every recorded cycle. Read-only, SHADOW.

For each cycle that carries a book, it evaluates NATIVE / JOIN_BEST /
IMPROVE_1_TICK against the contract's real print tape over the interval to
the next cycle. Nothing is sent, Model A is untouched, and every fill figure
is THEORETICAL.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import continuous_probe as CP
from src.gemini_execution import overlays as OV
from src.gemini_execution import window_volume as WV
from src.gemini_execution.volume_report import _expiry_ms

LABEL = "GEMINI_L5_OVERLAY_STUDY_V1"


def _ms(utc: Any) -> Any:
    try:
        return int(datetime.fromisoformat(
            str(utc).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _cycles_from_jsonl(path: str) -> Dict[str, List[Dict[str, Any]]]:
    """Shadow cycles, grouped by symbol, oldest first."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return by
    with fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") != "cycle" or not r.get("symbol"):
                continue
            by.setdefault(r["symbol"], []).append({
                "utc": r.get("utc"), "tte_s": r.get("tte_s"),
                "book": r.get("book"), "depth": r.get("depth"),
                "actions": r.get("l5_action") or []})
    for v in by.values():
        v.sort(key=lambda x: str(x.get("utc")))
    return by


def _cycles_from_runs(pattern: str) -> Dict[str, List[Dict[str, Any]]]:
    """Live decisions that carry a book, grouped by symbol."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for p in sorted(glob.glob(pattern)):
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
                "utc": dec.get("utc"), "tte_s": dec.get("tte_s"),
                "book": bs.get("presented"), "depth": bs.get("depth"),
                "actions": dec.get("actions") or []})
    for v in by.values():
        v.sort(key=lambda x: str(x.get("utc")))
    return by


def build(*, jsonl: str, runs: str) -> Dict[str, Any]:
    by = _cycles_from_jsonl(jsonl)
    for sym, rows in _cycles_from_runs(runs).items():
        by.setdefault(sym, []).extend(rows)
        by[sym].sort(key=lambda x: str(x.get("utc")))

    out_rows: List[Dict[str, Any]] = []
    refused = 0
    per_symbol: Dict[str, Any] = {}
    for sym in sorted(by):
        tape = WV.fetch_tape(sym)
        prints = tape.get("prints") or []
        cyc = by[sym]
        sym_rows = []
        for i, c in enumerate(cyc):
            book = c.get("book") or {}
            # A REFUSED BOOK IS NOT A QUOTE OPPORTUNITY. The controller would
            # not have quoted into it and neither does this study.
            if not book.get("readable") or CP.crossed(book):
                refused += 1
                continue
            t0 = _ms(c.get("utc"))
            t1 = (_ms(cyc[i + 1].get("utc")) if i + 1 < len(cyc)
                  else (t0 + 8000 if t0 else None))
            for act in (c.get("actions") or [{"leg": "YES", "price": None}]):
                leg = str(act.get("leg") or "YES").upper()
                row = OV.overlay_cycle(
                    utc_ms=t0, next_utc_ms=t1, leg=leg,
                    native_price=act.get("price"), book=book,
                    depth=c.get("depth"), prints=prints,
                    complement_book=book)
                later = cyc[i + 1:]
                for name in OV.OVERLAYS:
                    ov = row["overlays"].get(name) or {}
                    if not ov.get("exists"):
                        continue
                    ov["survival"] = OV.survival(ov["price"], leg, later)
                    ov["adverse_after_theoretical_fill"] = OV.adverse_after(
                        ov["price"], leg, later)
                row.update({"symbol": sym, "tte_s": c.get("tte_s"),
                            "utc": c.get("utc")})
                sym_rows.append(row)
        out_rows.extend(sym_rows)
        if sym_rows:
            per_symbol[sym] = OV.overlay_census(sym_rows)

    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "cycles_refused_unreadable_book": refused,
        "census": OV.overlay_census(out_rows),
        "per_symbol": per_symbol,
        "rows": out_rows,
        "labels": ("SHADOW OVERLAY STUDY. No order was sent, Model A is "
                   "unchanged, and every fill figure is THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--out", default="analysis/gemini/overlay_study.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    c = rep["census"]
    print("OVERLAY_STUDY " + json.dumps(
        {"cycles": c["cycles"],
         **{k: {kk: c[k][kk] for kk in
                ("cycles_where_it_exists", "best_liquidity_share",
                 "cycles_with_a_reaching_print",
                 "theoretical_fills_respecting_queue")}
            for k in OV.OVERLAYS}}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
