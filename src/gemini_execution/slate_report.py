#!/usr/bin/env python3
"""Enumerate the live Gemini slate and diagnose each contract's own tape."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import slate as SL
from src.gemini_execution import window_volume as WV


def build(*, max_events: int, max_contracts: int,
          pause_s: float = 0.12) -> Dict[str, Any]:
    en = SL.enumerate_slate(max_events=max_events)
    rows = SL.live_contracts(en["events"])
    cands = [r for r in rows if r.get("candidate")]
    # STRATIFIED BY SERIES, SOONEST EXPIRY FIRST. The first pass sorted by
    # newest expiry and spent its whole budget on far-future sports contracts
    # that had never traded -- which says nothing about any series. Flow lives
    # in what is about to settle, and every series deserves a look.
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in cands:
        groups.setdefault(str(r.get("series") or r.get("category")
                              or "UNKNOWN"), []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: str(r.get("expiry") or "9999"))
    per = max(1, max_contracts // max(1, len(groups)))
    picked: List[Dict[str, Any]] = []
    for k in sorted(groups):
        picked.extend(groups[k][:per])
    # spend anything left over on the soonest-expiring overall
    if len(picked) < max_contracts:
        seen = {r["symbol"] for r in picked}
        rest = sorted((r for r in cands if r["symbol"] not in seen),
                      key=lambda r: str(r.get("expiry") or "9999"))
        picked.extend(rest[:max_contracts - len(picked)])
    looked: List[Dict[str, Any]] = []
    for r in picked[:max_contracts]:
        t = WV.fetch_tape(r["symbol"])
        r = dict(r)
        r["tape"] = (SL.tape_diagnostics(t.get("prints") or [])
                     if t.get("readable")
                     else {"readable": False, "why": t.get("why")})
        looked.append(r)
        time.sleep(pause_s)
    ranked = SL.rank_rows(looked)
    return {
        "label": "GEMINI_SLATE_V1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "events_enumerated": en["n"],
        "events_reported_by_venue": en["total_reported"],
        "unreadable_pages": en["unreadable_pages"],
        "contracts_found": len(rows),
        "contracts_unresolved": len(cands),
        "contracts_tape_read": len(looked),
        "by_series": SL.by_series(looked),
        "ranked": ranked,
        "labels": ("PUBLIC READS ONLY. Ranked by ALTERNATION then "
                   "two-sidedness then activity -- never by headline volume, "
                   "which is what made BTC5m look attractive. Nothing here "
                   "places, sizes or authorises anything."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-events", type=int, default=1100)
    ap.add_argument("--max-contracts", type=int, default=140)
    ap.add_argument("--out", default="analysis/gemini/slate.json")
    a = ap.parse_args(argv)
    rep = build(max_events=a.max_events, max_contracts=a.max_contracts)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    print("SLATE events=%s/%s contracts=%s unresolved=%s read=%s" % (
        rep["events_enumerated"], rep["events_reported_by_venue"],
        rep["contracts_found"], rep["contracts_unresolved"],
        rep["contracts_tape_read"]), flush=True)
    for k, v in sorted(rep["by_series"].items(),
                       key=lambda kv: -(kv[1]["two_sided_share"] or 0))[:12]:
        print("  %-16s n=%-3s prints=%-6s two_sided=%-5s alt=%-6s bal=%s" % (
            k[:16], v["contracts"], v["prints"], v["two_sided_share"],
            v["median_alternation_rate"], v["median_side_balance"]),
            flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
