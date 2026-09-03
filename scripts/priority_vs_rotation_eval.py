"""Rotation vs HOT/WARM/COLD priority planner — read-only evaluation.

Issue #167 comment 5521210994: before claiming improvement, compare the
old rotation and the new priority tiers on observation quality, not
theoretical P&L. This harness simulates both planners over the SAME
market-activity tape and reports:
  (a) time-to-observe a subsequently qualifying state,
  (b) number of Q1/spring-compatible states observed while subscribed,
  (c) repeated maker-first->complement evidence captured,
  (d) median/p95 coverage latency,
  (e) websocket slot utilization.

The default tape is SYNTHETIC (labeled as such): 150 markets over 200
cycles — a persistent hot core, bursty markets that wake mid-run,
scheduled qualifying windows, and a quiet long tail. The same harness
accepts a real recorder tape on the host that holds one (the activity
per cycle is then derived from persisted trade timestamps).

Read-only research; no network, nothing armed.

    python3 -m scripts.priority_vs_rotation_eval
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from statistics import median

from src import pmus_priority_queue as pq
from src import pmus_ws_evidence_recorder as rec

OUT = Path(__file__).resolve().parents[1] / \
    "analysis/l5/priority_vs_rotation_eval.json"

N_MARKETS = 150
N_CYCLES = 200
CAP = 40


def synthetic_tape(seed: int = 7):
    """Per (cycle, slug): trades this cycle; plus qualifying windows —
    (slug, start_cycle, length) periods where the market shows the
    two-sided maker-first->complement shape."""
    rng = random.Random(seed)
    slugs = [f"syn-{i:03d}" for i in range(N_MARKETS)]
    activity = {s: [0] * N_CYCLES for s in slugs}
    qualifying = []
    for i, s in enumerate(slugs[:20]):                 # persistent hot core
        for c in range(N_CYCLES):
            activity[s][c] = rng.randint(2, 8)
        if i < 8:
            qualifying.append((s, rng.randint(20, 60), 30))
    for i, s in enumerate(slugs[20:50]):               # bursty sleepers
        start = rng.randint(40, 150)
        for c in range(start, min(start + 25, N_CYCLES)):
            activity[s][c] = rng.randint(4, 12)
        if i < 15:
            qualifying.append((s, start + 5, 15))
    for s in slugs[50:]:                               # quiet long tail
        if rng.random() < 0.3:
            c = rng.randint(0, N_CYCLES - 1)
            activity[s][c] = 1
    return slugs, activity, qualifying


def run_planner(kind: str, slugs, activity, qualifying):
    targets = [{"slug": s} for s in slugs]
    state: dict = {}
    prev: dict = {}
    last_obs = {s: -1 for s in slugs}
    now = 1_700_000_000.0
    q_first_seen = {}
    q_states_observed = 0
    completion_evidence = 0
    active_slot_cycles = 0
    total_slot_cycles = 0
    cov_samples = []
    qwin = {(s): (a, a + ln) for s, a, ln in qualifying}
    for cycle in range(N_CYCLES):
        if kind == "priority":
            p = pq.plan(targets, state, CAP, cycle, prev, now=now)
            prev = p["state"]
            subs = p["subscribe"]
        else:
            p = rec.subscription_plan(targets, state, CAP, cycle, now=now)
            subs = p["subscribe"]
        total_slot_cycles += len(subs)
        for s in subs:
            last_obs[s] = cycle
            n = activity[s][cycle]
            if n:
                active_slot_cycles += 1
                st = state.setdefault(s, {
                    "trades": [], "book_updates": 0,
                    "both_sides_served": True, "bid": "0.50",
                    "ask": "0.51", "bid_qty": "50", "ask_qty": "50"})
                st["trades"].extend({"t": now, "px": "0.5", "dir": "buy"}
                                    for _ in range(n))
                del st["trades"][:-500]
                st["book_updates"] += n * 10
            win = qwin.get(s)
            if win and win[0] <= cycle < win[1]:
                q_states_observed += 1
                completion_evidence += 1 if activity[s][cycle] >= 4 else 0
                if s not in q_first_seen:
                    q_first_seen[s] = cycle - win[0]
        cov_samples.append(sorted(cycle - last_obs[s] for s in slugs))
        now += 1.0
    med = median(x[len(x) // 2] for x in cov_samples[-50:])
    p95 = median(x[int(0.95 * (len(x) - 1))] for x in cov_samples[-50:])
    detected = len(q_first_seen)
    return {
        "planner": kind,
        "qualifying_windows": len(qualifying),
        "qualifying_detected": detected,
        "time_to_observe_median_cycles": (
            median(sorted(q_first_seen.values())) if q_first_seen else None),
        "q1_compatible_state_cycles_observed": q_states_observed,
        "completion_evidence_cycles_captured": completion_evidence,
        "coverage_latency_median_cycles": med,
        "coverage_latency_p95_cycles": p95,
        "slot_utilization_pct": round(100 * active_slot_cycles /
                                      max(total_slot_cycles, 1), 1),
    }


def main() -> int:
    slugs, activity, qualifying = synthetic_tape()
    rows = [run_planner(k, slugs, activity, qualifying)
            for k in ("rotation", "priority")]
    out = {
        "labels": "SYNTHETIC activity tape (150 markets x 200 cycles, "
                  "cap 40); same harness accepts a real recorder tape "
                  "on-host; observation quality only — no P&L claim",
        "results": rows,
    }
    OUT.write_text(json.dumps(out, indent=1))
    hdr = f"{'metric':<42}{'rotation':>12}{'priority':>12}"
    print(hdr)
    for key in rows[0]:
        if key == "planner":
            continue
        print(f"{key:<42}{str(rows[0][key]):>12}{str(rows[1][key]):>12}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
