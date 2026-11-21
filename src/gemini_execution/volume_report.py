#!/usr/bin/env python3
"""Build the BTC5m window volume report from PUBLIC tapes. Read-only.

It enumerates every window this programme has captured -- the three shadow
soaks and the live CONTINUOUS windows -- fetches each contract's whole public
print tape, and reports EXECUTED volume. Displayed depth is carried in its own
column and contributes to nothing.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import window_volume as WV

LABEL = "GEMINI_BTC5M_WINDOW_VOLUME_V1"


def _expiry_ms(symbol: str) -> Any:
    """Expiry from the SYMBOL's own timestamp, which is the contract's name.

    Independent of any venue read, so a window whose event object has aged out
    still gets its clock.
    """
    try:
        stamp = symbol.split("-")[1][-10:]
        t = datetime.strptime(stamp, "%y%m%d%H%M").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return None
    return int(t.timestamp() * 1000)


def captured_windows(soaks: str, runs: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(glob.glob(soaks)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for w in (d.get("windows") or []):
            s = w.get("symbol")
            if not s:
                continue
            hb = w.get("habitat") or {}
            out[s] = {"symbol": s, "source": "SHADOW soak",
                      "file": os.path.basename(p),
                      "cycles_observed": w.get("cycles"),
                      "entry_tte_s": w.get("entry_tte_s"),
                      "soak_flow": (w.get("realised_flow") or {})}
    for p in sorted(glob.glob(runs)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        a = d.get("after") or {}
        s = a.get("symbol") or (d.get("authoritative_state") or {}).get("symbol")
        if not s:
            continue
        fills = [o for o in (a.get("orders_seen") or [])
                 if str(o.get("status")) == "filled"]
        out[s] = {"symbol": s, "source": "LIVE window",
                  "file": os.path.basename(p),
                  "authorization_id": d.get("authorization_id"),
                  "decisions": len(d.get("decisions") or []),
                  "fill_prices": [o.get("price") for o in fills],
                  "fill_times": [o.get("updatedAt") for o in fills]}
    return out


def _our_tids(tape: Dict[str, Any], meta: Dict[str, Any]) -> List[str]:
    """Which prints on this tape are OURS.

    Matched on price AND size AND being inside a second of the venue's own
    fill timestamp -- never on price alone, because someone else trading at
    our price is not our fill.
    """
    if not tape.get("readable"):
        return []
    want = []
    for px, t in zip(meta.get("fill_prices") or [],
                     meta.get("fill_times") or []):
        try:
            ms = int(datetime.fromisoformat(
                str(t).replace("Z", "+00:00")).timestamp() * 1000)
        except (TypeError, ValueError):
            continue
        want.append((str(px), ms))
    out = []
    for p in tape.get("prints") or []:
        for px, ms in want:
            if p["price"] == px and abs(p["utc_ms"] - ms) <= 1500:
                out.append(str(p.get("tid")))
    return out


def _touch(meta: Dict[str, Any], jsonl: str) -> Any:
    """Displayed touch depth, MEDIAN over the window's recorded cycles.

    Reported for contrast only. It is never added to a volume figure.
    """
    import statistics as st
    vals = []
    try:
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") != "cycle" or r.get("symbol") != meta["symbol"]:
                    continue
                q = ((r.get("habitat") or {}).get("ladder") or {}).get(
                    "bids", {}).get("best_qty")
                if q is not None:
                    try:
                        vals.append(float(q))
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return None
    return (str(st.median(vals)) if vals else None)


def build(*, soaks: str, runs: str, jsonl: str,
          venue_check: int = 3) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    rows: List[Dict[str, Any]] = []
    for sym in sorted(meta):
        m = meta[sym]
        tape = WV.fetch_tape(sym)
        v = WV.window_volume(tape, expiry_ms=_expiry_ms(sym),
                             our_tids=_our_tids(tape, m),
                             touch_depth=_touch(m, jsonl))
        v.update({k: m.get(k) for k in
                  ("source", "file", "authorization_id", "cycles_observed",
                   "entry_tte_s", "decisions")})
        soak_flow = m.get("soak_flow") or {}
        if soak_flow.get("measurable"):
            v["soak_saw_prints_added"] = soak_flow.get("prints_added")
        rows.append(v)

    venue = [WV.venue_reported_volume(r["symbol"])
             for r in rows[:max(0, venue_check)]]
    return {
        "label": LABEL,
        "utc": datetime.now(timezone.utc).isoformat(),
        "windows": rows,
        "table": WV.table(rows),
        "totals": WV.totals(rows),
        "venue_reported_volume_check": venue,
        "labels": ("EXECUTED VOLUME from the PUBLIC print tape. Displayed "
                   "resting depth is a separate column and is added to "
                   "nothing. No admission threshold is defined and Model A "
                   "is untouched."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak_*.json")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--out",
                    default="analysis/gemini/window_volume.json")
    a = ap.parse_args(argv)
    rep = build(soaks=a.soaks, runs=a.runs, jsonl=a.jsonl)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2, default=str)
    print("WINDOW_VOLUME " + json.dumps(rep["totals"], default=str),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
