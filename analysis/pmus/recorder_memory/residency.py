#!/usr/bin/env python3
"""How long does a NON-SEATED market survive in `mstate`, per cap?

Read-only. Takes the production `state_prune` telemetry as observed in the
recorder's own log stream and applies Little's law to get the steady-state
residency of a market that holds no seat. Residency is the quantity the
experiment actually trades away: admission scores a COLD market from
`trades_1h` / `two_sided_trades` accumulated in `mstate`, so a market evicted
before it has an hour of history can never be promoted.

`prune_state` never evicts anything in `keep` (subscribed + protected), so all
160 seats are safe at EVERY cap by construction, not by tuning -- a seated
market is skipped before it is even added to the eviction candidate list.
Lowering the cap therefore cannot lose a seat; it can only shorten how long
the UNSEATED universe is observed.

No network call, no environment read, no production contact.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "residency.json"

# PMUS_WS_STATUS {"state_prune": ...} rows, deployment 32ac7295, verbatim.
# Every row read kept=1500, evicted_idle=0, protected=160, max_idle_s=7200.
SAMPLES = [
    ("2026-09-14T01:51:14.949", 30), ("2026-09-14T01:52:25.479", 35),
    ("2026-09-14T01:53:34.953", 69), ("2026-09-14T01:54:44.983", 77),
    ("2026-09-14T01:56:04.777", 49), ("2026-09-14T01:57:04.793", 48),
    ("2026-09-14T01:58:14.130", 75), ("2026-09-14T01:59:23.659", 78),
    ("2026-09-14T02:00:33.031", 24), ("2026-09-14T02:01:54.372", 28),
    ("2026-09-14T02:03:03.261", 31), ("2026-09-14T02:04:13.322", 82),
    ("2026-09-14T02:05:22.991", 30),
]
SPAN_S = 848.04          # 01:51:14.95 -> 02:05:22.99
SEATS = 160              # protected, never evicted at any cap
CAPS = (1500, 1000, 750, 500)
TRADES_1H_WINDOW_S = 3600.0     # the window admission evidence needs


def main() -> int:
    evicted = [n for _t, n in SAMPLES]
    cycles = len(SAMPLES) - 1
    period = SPAN_S / cycles
    # SPAN_S spans the 12 INTERVALS between 13 samples, and each sample
    # reports the cycle ENDING at its timestamp, so the evictions that fall
    # inside the span are samples 1..12. Counting all 13 over 12 intervals
    # would inflate the rate by one cycle.
    inside = sum(evicted[1:])
    rate = inside / SPAN_S                     # markets evicted per second

    arms = []
    for cap in CAPS:
        slots = cap - SEATS
        resid = slots / rate
        arms.append({
            "max_markets": cap,
            "seats_protected": SEATS,
            "non_protected_slots": slots,
            "mean_residency_s": round(resid, 1),
            "mean_residency_min": round(resid / 60.0, 1),
            "fraction_of_trades_1h_window":
                round(resid / TRADES_1H_WINDOW_S, 3),
            "covers_full_trades_1h_window": resid >= TRADES_1H_WINDOW_S})

    out = {
        "method": ("Little's law on steady-state eviction: a dict held at "
                   "its cap with arrivals equal to evictions gives mean "
                   "residency = non_protected_slots / eviction_rate"),
        "observed": {
            "deployment": "32ac7295-67d3-4ba0-9270-de4a3e0c6529",
            "samples": len(SAMPLES),
            "span_s": SPAN_S,
            "mean_cycle_s": round(period, 2),
            "evicted_over_cap_per_cycle": {
                "min": min(evicted), "max": max(evicted),
                "mean": round(inside / cycles, 2)},
            "eviction_rate_per_s": round(rate, 4),
            "eviction_rate_per_h": round(rate * 3600, 1),
            "evicted_idle_every_sample": 0,
            "kept_every_sample": 1500,
            "protected_every_sample": SEATS},
        "assumption": ("the eviction rate is set by ARRIVALS (COLD rotation "
                       "admitting newly-observed markets) and is therefore "
                       "cap-invariant at steady state; residency then scales "
                       "linearly with the non-protected slot count"),
        "finding": ("evicted_idle is 0 in every sample: NOTHING reaches the "
                    "2 h idle rule, so eviction is driven entirely by the "
                    "cap and the dict is saturated and thrashing"),
        "arms": arms,
        "headline": ("at the CURRENT cap of 1500 the mean unseated market "
                     "already survives less than the 3600 s that trades_1h "
                     "needs; every lower cap makes that strictly worse"),
    }
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")

    print(f"eviction rate {rate:.4f}/s ({rate*3600:.0f}/h), "
          f"cycle {period:.1f}s, mean {inside/cycles:.1f}/cycle\n")
    print(f"{'cap':>6} {'slots':>6} {'residency':>11} {'of 1h window':>13}")
    for a in arms:
        print(f"{a['max_markets']:6d} {a['non_protected_slots']:6d} "
              f"{a['mean_residency_min']:9.1f}m "
              f"{a['fraction_of_trades_1h_window']*100:12.1f}%")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
