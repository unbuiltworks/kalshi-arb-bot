#!/usr/bin/env python3
"""THE TWO RUNNING LANES, compared only at TTE <= 300.

The whole-window BTC15m promotion is withdrawn, so nothing here measures a
15-minute contract's first ten minutes. `REACHABLE_TTE_S` is read from Model
A's own frozen `quote_between_tte_s`, never set.

TWO HALVES, AND THEY COME FROM DIFFERENT PLACES:

  the VENUE half  -- prints, notional, two-sidedness -- is public tape,
                     fetched here, restricted to the reachable interval.
  the LANE half   -- what Model A wanted, what the adapter vetoed, capital --
                     is the running lane's own append-only evidence.

The BTC15m lane runs on Railway and persists to a volume this container
cannot read, so its lane half is reported as UNREADABLE_FROM_HERE rather
than as zero. Its venue half is measured exactly as BTC5m's is.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from src.gemini_execution import backfill as BF
from src.gemini_execution import contract_cohort as CC
from src.gemini_execution import lanes as LN
from src.gemini_execution import metadata as MD
from src.gemini_execution import window_volume as WV


def _expiry_ms(symbol):
    terms = MD.terms_for(symbol) or {}
    raw = terms.get("expiry") or terms.get("expiryDate")
    if not raw:
        return None
    return datetime.fromisoformat(
        str(raw).replace("Z", "+00:00")).timestamp() * 1000


def venue_half(cohort, *, windows: int, pause: float = 0.1):
    """The cohort's OWN flow inside the interval Model A can reach."""
    tapes = {}
    for tk in BF.tickers_back(cohort, windows=windows):
        sym = cohort.symbol_for(tk)
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        exp = _expiry_ms(sym)
        if exp is None:
            continue
        tapes[sym] = LN.tape_in_window(t["prints"], expiry_ms=exp)
        time.sleep(pause)
    return tapes


def _roll(tapes):
    n = len(tapes)
    prints = sum(int(v["prints"]) for v in tapes.values())
    notional = sum(float(v["notional_usd"]) for v in tapes.values())
    two = sum(1 for v in tapes.values() if v["two_sided"])
    return {
        "contracts": n,
        "prints": prints,
        "notional_usd": round(notional, 2),
        "prints_per_contract": (round(prints / n, 2) if n else None),
        "notional_per_contract": (round(notional / n, 2) if n else None),
        "two_sided_contracts": two,
        "two_sided_share": (round(two / n, 3) if n else None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", type=int, default=24)
    ap.add_argument("--btc5m-evidence",
                    default="analysis/gemini/btc5m_v2/continuous_v1.jsonl")
    ap.add_argument("--btc15m-evidence", default=None,
                    help="unreadable from this container while the lane "
                         "runs on Railway; pass a path if one is exported")
    ap.add_argument("--out", default="analysis/gemini/lanes_prospective.json")
    a = ap.parse_args(argv)

    lo, hi = LN.quoting_window()
    out = {"utc": datetime.now(timezone.utc).isoformat(),
           "quoting_window_tte_s": [lo, hi],
           "quoting_window_source": ("READ from the frozen surface's "
                                     "TTE_policy. Not set here."),
           "lanes": {}}
    for co, ev in ((CC.BTC5M, a.btc5m_evidence),
                   (CC.BTC15M, a.btc15m_evidence)):
        tapes = venue_half(co, windows=a.windows)
        lane = {"venue_half": _roll(tapes), "per_contract": tapes}
        if ev and os.path.exists(ev):
            ws = LN.lane_windows(ev)
            lane["lane_half"] = LN.lane_census(ws, tapes)
            lane["the_sequence"] = LN.the_sequence(ws)
        else:
            lane["lane_half"] = {
                "state": "UNREADABLE_FROM_HERE",
                "why": ("this lane persists to a Railway volume; the "
                        "container running this report cannot mount it. "
                        "Absent, not zero."),
                "evidence_path": ev}
        out["lanes"][co.name] = lane

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=2, default=str)

    print("PROSPECTIVE LANE COMPARISON at TTE <= %.0fs (Model A's own "
          "window)" % hi, flush=True)
    for name, lane in out["lanes"].items():
        v = lane["venue_half"]
        print("  %-7s contracts=%-3s prints=%-5s $%-9s per-contract "
              "prints=%-6s $%-8s two_sided=%s" % (
                  name, v["contracts"], v["prints"], v["notional_usd"],
                  v["prints_per_contract"], v["notional_per_contract"],
                  v["two_sided_share"]), flush=True)
        lh = lane["lane_half"]
        if lh.get("state") == "UNREADABLE_FROM_HERE":
            print("          lane half: UNREADABLE_FROM_HERE (%s)"
                  % lh["evidence_path"], flush=True)
        else:
            print("          lane half: windows=%s quotes=%s (%s/window) "
                  "vetoes=%s | fills=%s (%s)" % (
                      lh["windows"], lh["model_a_quotes"],
                      lh["quotes_per_window"], lh["adapter_vetoes"],
                      lh["first_fills"],
                      "absent, not zero"), flush=True)
            s = lane["the_sequence"]
            print("          trigger: occurrences=%s can_fire=%s" % (
                s["occurrences"], s["can_fire_in_these_lanes"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
