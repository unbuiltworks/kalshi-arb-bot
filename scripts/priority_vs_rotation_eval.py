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

import argparse
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


def real_tape(root, cycle_s: float = 60.0):
    """Derive per-cycle activity + qualifying windows from the persisted
    authenticated recorder tape (kind=ws rows on the durable volume).

    OBSERVATION-BIASED by construction: the tape only carries markets a
    planner actually subscribed to, so unsubscribed markets contribute
    nothing and the comparison measures re-planning over the observed
    universe, not true full-universe discovery. Label results
    accordingly. Qualifying windows are a documented heuristic: maximal
    activity spans (gaps <= 2 cycles) at least 5 cycles long containing
    both buy and sell prints (two-sided flow)."""
    per: dict[str, list] = {}
    t0 = t1 = None
    for t, msg in rec.iter_recent_tape_rows(Path(root)):
        found: list = []
        rec._walk_market_events(msg, found)
        for ev in found:
            if ev["kind"] == "trade" and ev.get("price"):
                d = rec._trade_direction(ev, {}).upper()
                per.setdefault(ev["slug"], []).append((t, d))
                t0 = t if t0 is None else min(t0, t)
                t1 = t if t1 is None else max(t1, t)
    if not per:
        return [], {}, [], 0
    n_cycles = int((t1 - t0) // cycle_s) + 1
    slugs = sorted(per)
    activity = {s: [0] * n_cycles for s in slugs}
    dirs = {s: [set() for _ in range(n_cycles)] for s in slugs}
    for s, prints in per.items():
        for t, d in prints:
            c = int((t - t0) // cycle_s)
            activity[s][c] += 1
            if d in ("BUY", "SELL"):
                dirs[s][c].add(d)
    qualifying = []
    for s in slugs:
        start = None
        gap = 0
        seen: set = set()
        for c in range(n_cycles + 1):
            live = c < n_cycles and activity[s][c] > 0
            if live:
                if start is None:
                    start, seen = c, set()
                seen |= dirs[s][c]
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 2 or c == n_cycles:
                    end = c - gap
                    if end - start + 1 >= 5 and seen == {"BUY", "SELL"}:
                        qualifying.append((s, start, end - start + 1))
                    start, gap = None, 0
    return slugs, activity, qualifying, n_cycles


def run_planner(kind: str, slugs, activity, qualifying,
                n_cycles: int = N_CYCLES, cap: int = CAP):
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
    for cycle in range(n_cycles):
        if kind == "priority":
            p = pq.plan(targets, state, cap, cycle, prev, now=now)
            prev = p["state"]
            subs = p["subscribe"]
        else:
            p = rec.subscription_plan(targets, state, cap, cycle, now=now)
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
        "qualifying_detected_slugs": sorted(q_first_seen),
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape-root", default=None,
                    help="recorder state root holding raw/pmus_ws_*.jsonl "
                         "(e.g. /state/pmus-ws); omit for synthetic")
    ap.add_argument("--cycle-seconds", type=float, default=60.0)
    ap.add_argument("--cap", type=int, default=CAP)
    args = ap.parse_args()
    if args.tape_root:
        slugs, activity, qualifying, n_cycles = real_tape(
            args.tape_root, args.cycle_seconds)
        if not slugs:
            print(json.dumps({"error": "no_trade_events_in_tape",
                              "root": args.tape_root}))
            return 1
        label = (f"REAL recorder tape ({len(slugs)} tape-visible markets "
                 f"x {n_cycles} cycles of {args.cycle_seconds}s, cap "
                 f"{args.cap}); OBSERVATION-BIASED: only markets some "
                 "planner subscribed to appear; SHADOW/THEORETICAL "
                 "observation quality only — no P&L claim")
    else:
        slugs, activity, qualifying = synthetic_tape()
        n_cycles = N_CYCLES
        label = ("SYNTHETIC activity tape (150 markets x 200 cycles, "
                 "cap 40); pass --tape-root on the durable host for the "
                 "real tape; observation quality only — no P&L claim")
    rows = [run_planner(k, slugs, activity, qualifying,
                        n_cycles=n_cycles, cap=args.cap)
            for k in ("rotation", "priority")]
    d_rot, d_pri = (set(r["qualifying_detected_slugs"]) for r in rows)
    out = {"labels": label, "results": rows,
           "detection_diff": {"rotation_only": sorted(d_rot - d_pri),
                              "priority_only": sorted(d_pri - d_rot)}}
    OUT.write_text(json.dumps(out, indent=1))
    hdr = f"{'metric':<42}{'rotation':>12}{'priority':>12}"
    print(hdr)
    for key in rows[0]:
        if key in ("planner", "qualifying_detected_slugs"):
            continue
        print(f"{key:<42}{str(rows[0][key]):>12}{str(rows[1][key]):>12}")
    print("detection_diff:", json.dumps(out["detection_diff"]))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
