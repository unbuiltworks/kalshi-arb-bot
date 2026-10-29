#!/usr/bin/env python3
"""Run the complement-reachability study across every first fill. Read-only.

Seeds from the actual venue fills AND from the hypothetical first fills the
large-print forensic identified, then replays each forward to settlement under
every arm. Sends nothing; Model A unchanged.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import complement as CM
from src.gemini_execution import large_prints as LP
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol, _ms
from src.gemini_execution.volume_report import _expiry_ms, captured_windows

LABEL = "GEMINI_L5_COMPLEMENT_REACHABILITY_V1"


def seeds(runs: str, soaks: str, cyc: Dict[str, Any],
          tapes: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every first fill we can seed from: ACTUAL venue fills first."""
    out: List[Dict[str, Any]] = []
    for p in sorted(glob.glob(runs)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        after = d.get("after") or {}
        sym = after.get("symbol")
        for o in (after.get("orders_seen") or []):
            if str(o.get("status")) != "filled" or not sym:
                continue
            out.append({"kind": "ACTUAL", "symbol": sym,
                        "leg": "YES" if str(o.get("outcome")).lower() == "yes"
                        else "NO",
                        "qty": o.get("filledQuantity"),
                        "price": o.get("price"),
                        "utc_ms": _ms(o.get("updatedAt")),
                        "source": d.get("authorization_id")})
    # hypothetical first fills: a large print that our native quote would have
    # been reached by, in a window where we have a book
    for sym, tape in tapes.items():
        rows = LP.forensic(symbol=sym, prints=tape.get("prints") or [],
                           cycles=cyc.get(sym) or [], resolution={},
                           min_size="10")
        for r in rows:
            if not r.get("evaluable"):
                continue
            t = (r.get("quote_tests") or {}).get("NATIVE") or {}
            if not t.get("would_reach_us"):
                continue
            ab = (t.get("absorbed") or {}).get("0.01") or {}
            got = ab.get("hypothetically_filled")
            if not got or float(got) <= 0:
                continue
            out.append({"kind": "HYPOTHETICAL", "symbol": sym,
                        "leg": r["reachable_leg"], "qty": got,
                        "price": t["price"],
                        "utc_ms": r["print"]["utc_ms"],
                        "source": "tid %s (%s contracts)" % (
                            r["tid"], r["print"]["amount"])})
    return out


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

    sd = seeds(runs, soaks, cyc, tapes)
    by_arm: Dict[str, List[Dict[str, Any]]] = {a: [] for a in CM.ARMS}
    for s in sd:
        sym = s["symbol"]
        if sym not in tapes:
            continue
        for arm in CM.ARMS:
            tr = CM.trace(symbol=sym, arm=arm, seed_leg=s["leg"],
                          seed_qty=s["qty"], seed_price=s["price"],
                          seed_ms=s["utc_ms"], cycles=cyc.get(sym) or [],
                          prints=tapes[sym]["prints"],
                          resolution=resolutions.get(sym) or {},
                          expiry_ms=_expiry_ms(sym))
            tr["seed_kind"] = s["kind"]
            tr["seed_source"] = s["source"]
            by_arm[arm].append(tr)

    summaries = {a: CM.arm_summary(v) for a, v in by_arm.items()}
    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "seeds": [{k: s[k] for k in
                   ("kind", "symbol", "leg", "qty", "price", "source")}
                  for s in sd],
        "by_arm": summaries,
        "complement_flow_coverage": CM.pooled_flow_coverage(
            by_arm.get(CM.NATIVE) or []),
        "verdict": CM.leakage_verdict(summaries),
        "traces": by_arm,
        "labels": ("SHADOW. Every action comes from the FROZEN policy; the "
                   "arms change only a COMPLETE price or how long a quote "
                   "rests. Nothing was sent and every fill is THEORETICAL."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak_*.json")
    ap.add_argument("--out",
                    default="analysis/gemini/complement_reachability.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print("COMPLEMENT " + json.dumps(rep["verdict"], default=str)[:600],
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
