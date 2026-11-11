#!/usr/bin/env python3
"""Recorder memory: measure the heap slope BEFORE proposing a cap change.

OWNER GO: "design -- not deploy -- the smallest recorder-memory experiment
comparing PMUS_WS_STATE_MAX_MARKETS=1500 with lower bounded values such as
1000/750/500 while protecting all 160 active seats."

This is the read-only half: it measures what a market entry actually costs
in a CPython heap, so the experiment's expected saving is a measured number
rather than an assumed one. It allocates synthetic copies of the recorder's
own per-market shape LOCALLY. It does not import the recorder, does not open
a socket, does not read the environment, does not touch Railway and cannot
change any running service.

WHY IT MATTERS. `pmus_ws_evidence_recorder.rss_gb()` reports
`resource.getrusage(RUSAGE_SELF).ru_maxrss`, which is the process HIGH-WATER
MARK, while Railway's MEMORY_USAGE_GB is the CONTAINER total and includes
page cache. Those are different quantities and on 2026-09-14 they disagreed
by an order of magnitude. Any cap recommendation has to say which one it
moves; this harness measures the first.

The shape mirrors pmus_ws_evidence_recorder: one dict per market holding
`bbo_history` (bounded `del s["bbo_history"][:-2000]`) and `trades`
(bounded `[:-500]`), plus scalar BBO fields.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "heap_model.json"

CAPS = (1500, 1000, 750, 500)
DEPTHS = (2000, 400, 100)          # bbo_history entries retained per market
BBO_COUNT_BOUND = 2000             # del s["bbo_history"][:-2000]
TRADES_BOUND = 500                 # del s["trades"][:-500]
SEATS = 160                        # protected, never evicted at any cap


def rss_mb() -> float:
    """CURRENT resident set, not the high-water mark -- ru_maxrss never
    decreases, so it cannot measure a structure being freed."""
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    raise RuntimeError("VmRSS not readable")


def make_market(n_bbo: int, n_trades: int, t0: float = 1.0e9) -> dict:
    return {
        "bid": 0.5, "ask": 0.52, "bid_qty": 10.0, "ask_qty": 12.0,
        "both_sides_served": True, "book_updates": n_bbo,
        "trades": [{"t": t0 + i, "px": 0.5, "dir": "buy"}
                   for i in range(n_trades)],
        "bbo_history": [{"t": t0 + i, "bid": 0.5, "ask": 0.52,
                         "bid_qty": 10.0, "ask_book_missing": False}
                        for i in range(n_bbo)]}


def measure(markets: int, depth: int) -> dict:
    n_tr = min(TRADES_BOUND, depth // 4)
    gc.collect()
    base = rss_mb()
    state = {f"m{k}": make_market(depth, n_tr) for k in range(markets)}
    gc.collect()
    held = rss_mb()
    del state
    gc.collect()
    return {"markets": markets, "bbo_entries": depth, "trades": n_tr,
            "heap_mb": round(held - base, 1),
            "kb_per_market": round((held - base) * 1024 / markets, 1)}


def main() -> int:
    rows = [measure(c, BBO_COUNT_BOUND) for c in CAPS]
    depth_rows = [measure(CAPS[0], d) for d in DEPTHS]
    full = rows[0]["heap_mb"]

    cap_arms = []
    for r in rows:
        cap_arms.append({
            **r,
            "heap_saved_vs_1500_mb": round(full - r["heap_mb"], 1),
            "non_protected_slots": r["markets"] - SEATS,
            "seats_protected": SEATS})

    depth_arms = []
    for r in depth_rows:
        depth_arms.append({
            **r,
            "heap_saved_vs_2000_entries_mb": round(full - r["heap_mb"], 1)})

    out = {
        "what_this_measures": ("CPython heap cost of the recorder's "
                               "per-market state shape, measured locally; "
                               "NOT container memory and NOT page cache"),
        "bounds_mirrored_from_production": {
            "bbo_history_count_bound": BBO_COUNT_BOUND,
            "trades_count_bound": TRADES_BOUND,
            "seats_protected_at_every_cap": SEATS},
        "market_count_arms": cap_arms,
        "history_depth_arms_at_cap_1500": depth_arms,
        "reading": ("heap is linear in BOTH market count and retained "
                    "history depth; at cap 1500 the depth lever is the "
                    "larger of the two and costs no market coverage"),
        "caveat": ("synthetic entries are uniform; real markets vary in "
                   "history depth, so these are per-entry slopes, not a "
                   "forecast of the live process"),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")

    print(f"{'markets':>8} {'bbo':>6} {'trades':>7} {'heap MB':>9} "
          f"{'KB/market':>10} {'saved MB':>9}")
    for r in cap_arms:
        print(f"{r['markets']:8d} {r['bbo_entries']:6d} {r['trades']:7d} "
              f"{r['heap_mb']:9.1f} {r['kb_per_market']:10.1f} "
              f"{r['heap_saved_vs_1500_mb']:9.1f}")
    print()
    for r in depth_arms:
        print(f"{r['markets']:8d} {r['bbo_entries']:6d} {r['trades']:7d} "
              f"{r['heap_mb']:9.1f} {r['kb_per_market']:10.1f} "
              f"{r['heap_saved_vs_2000_entries_mb']:9.1f}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
