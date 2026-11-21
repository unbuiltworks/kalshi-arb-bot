#!/usr/bin/env python3
"""Run the three mechanism studies over every reconstructed BTC5m window."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.gemini_execution import mechanism as MX
from src.gemini_execution import metadata as MD
from src.gemini_execution import tape_replay as TR
from src.gemini_execution import window_volume as WV
from src.gemini_execution.large_print_report import cycles_by_symbol
from src.gemini_execution.volume_report import _expiry_ms, captured_windows


def strike_for(symbol: str) -> Any:
    """The published reference at the window's open. Contemporaneous."""
    try:
        tk = MD.event_ticker(symbol)
        e = MD.fetch_event(tk) if tk else None
        for c in (((e or {}).get("event") or {}).get("contracts") or []):
            if c.get("instrumentSymbol") == symbol:
                s = c.get("strike")
                return s.get("value") if isinstance(s, dict) else None
    except Exception:                                          # noqa: BLE001
        return None
    return None


def build(*, jsonl: str, runs: str, soaks: str) -> Dict[str, Any]:
    meta = captured_windows(soaks, runs)
    cyc = cycles_by_symbol(jsonl, runs)
    candles = MX.fetch_candles()
    bars = candles.get("bars") or []

    approaches: List[Dict[str, Any]] = []
    under: List[Dict[str, Any]] = []
    all_prints: List[Dict[str, Any]] = []
    all_books: List[Dict[str, Any]] = []

    for sym in sorted(meta):
        t = WV.fetch_tape(sym)
        if not t.get("readable"):
            continue
        exp = _expiry_ms(sym)
        for p in t["prints"]:
            p["tte_s"] = (round((exp - p["utc_ms"]) / 1000.0, 3)
                          if exp else None)
        cs = cyc.get(sym) or []
        all_prints.extend(t["prints"])
        all_books.extend([c.get("depth") for c in cs if c.get("depth")])
        try:
            res = MD.resolution_for(sym, reads=1)
        except Exception as exc:                               # noqa: BLE001
            res = {"verdict": "UNREADABLE",
                   "why": "%s: %s" % (type(exc).__name__, exc)}
        w = TR.replay_window(symbol=sym, policy=TR.POLICY_B, cycles=cs,
                             prints=t["prints"], resolution=res,
                             expiry_ms=exp)
        if not w.get("sufficient"):
            continue
        for ep in w["missed_complement_episodes"]:
            a = MX.approach(ep, cs, t["prints"])
            a["symbol"] = sym
            approaches.append(a)
        # one underlying row per FIRST fill in this window
        k = strike_for(sym)
        inv = w.get("inventory") or {}
        first = (w.get("fills") or [None])[0]
        if first is not None:
            basis = inv.get("effective_matched_pair_cost")
            row = MX.underlying_state(
                at_ms=first.get("utc_ms"), strike=k,
                held_leg=first.get("leg"), tte_s=first.get("tte_s"),
                bars=bars)
            row.update({
                "symbol": sym,
                "complete_reached": bool(w.get("complement_fills")),
                "par_constrained": bool(
                    basis is not None and float(basis) <= 1.0
                    and float(inv.get("matched_qty") or 0) > 0),
            })
            under.append(row)

    pres = MX.presence(all_prints, all_books)
    return {
        "label": "GEMINI_MECHANISM_V1",
        "utc": datetime.now(timezone.utc).isoformat(),
        "windows_offered": len(meta),
        "closest_approach": {
            "census": MX.approach_census(approaches), "rows": approaches},
        "underlying": {
            "candles": {k: candles.get(k) for k in
                        ("readable", "n", "first_ms", "last_ms")},
            "contrast": MX.underlying_contrast(under), "rows": under},
        "liquidity_presence": {
            "sizing": pres, "verdict": MX.presence_verdict(pres)},
        "labels": ("SHADOW. No orders, no sizing decision, no authorisation. "
                   "The causal question -- whether a larger quote would "
                   "attract flow -- is labelled unknowable, not estimated."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl",
                    default="analysis/gemini/continuous_v1/continuous_v1.jsonl")
    ap.add_argument("--runs", default="analysis/gemini/continuous_run_*.json")
    ap.add_argument("--soaks",
                    default="analysis/gemini/continuous_shadow_soak*.json")
    ap.add_argument("--out", default="analysis/gemini/mechanism.json")
    a = ap.parse_args(argv)
    rep = build(jsonl=a.jsonl, runs=a.runs, soaks=a.soaks)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=2, default=str)
    c = rep["closest_approach"]["census"]
    print("APPROACH episodes=%s %s" % (c["episodes"],
                                       json.dumps(c["by_bucket"])), flush=True)
    print("  median closest gap ticks=%s  toward_us_share=%s  depth_built=%s/%s"
          % (c["median_closest_gap_ticks"],
             c["share_where_midpoint_moved_toward_us"],
             c["episodes_where_opposite_depth_built"], c["depth_observable_on"]),
          flush=True)
    u = rep["underlying"]["contrast"]
    print("UNDERLYING states=%s recyclable=%s leakage=%s supports_selector=%s"
          % (u["states"], u["recyclable"], u["leakage"],
             u["supports_a_selector"]), flush=True)
    v = rep["liquidity_presence"]["verdict"]
    print("PRESENCE verdict=%s size/print=%s size/touch=%s"
          % (v["verdict"], v["our_size_over_median_print"],
             v["our_size_over_median_touch_depth"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
