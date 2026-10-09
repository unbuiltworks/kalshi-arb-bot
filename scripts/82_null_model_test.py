"""THE DECISIVE TEST -- is leg selection really a memoryless coin flip?

Grok's round-3 concession makes a strong, falsifiable claim:

  "A clone that simply posts (or takes) both legs with that fixed switch
   probability, at the observed cadence, with the observed size mix, and
   never actively closes residual would reproduce the statistical texture
   of the entire 760k-fill trace."

If true, there is nothing in the trace worth cloning: the trader supplies
an activity throttle and a size mix, the venue supplies everything else.
If false, whatever the null model fails to reproduce is exactly where the
real decision-making lives.

THE TEST. For every real window we keep the actual fills -- their count,
their timestamps, their sizes and the window's own price level -- and
regenerate ONLY the leg sequence, from a memoryless hazard at the measured
per-month switch rate. Then we recompute the whole inventory path and
compare the outcome distributions.

Holding everything else at its actual value is what makes this fair: it
isolates Grok's claim (leg choice is a coin flip) from the venue's
contribution (which fills arrive, at what prices and sizes).

PRICE COHERENCE. Legs and prices are coupled: the two outcomes are
complements, so an UP fill at 0.80 implies DOWN was about 0.20 at that
moment. A simulation that flipped the leg but kept the price would invent
a book that never existed. So when the simulated leg differs from the
actual leg, the price is complemented: p -> 1 - p. This preserves the
window's actual price level while changing only which side was taken.

NULL HYPOTHESIS: the real leg sequence achieves no better pair cost,
matched quantity or P&L than a memoryless sequence over the same fills.
Rejecting it means leg selection carries information that a coin flip does
not.

Read-only. No network.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import gzip
import json
import os
import random
import statistics
import sys
from collections import deque

EPS = 1e-9
REPS = 20
SEED = 20261009
# Measured per-fill switch hazard, from the corpus itself (not assumed).
HAZARD = {"2026-03": 0.421, "2026-04": 0.322, "2026-05": 0.281,
          "2026-06": 0.281, "2026-07": 0.278, "2026-08": 0.272}
DEFAULT_HAZARD = 0.28


def f(x, d=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def fifo_take(q, qty):
    spend = 0.0
    while qty > EPS and q:
        lot = q[0]
        use = min(lot[0], qty)
        spend += use * lot[1]
        lot[0] -= use
        qty -= use
        if lot[0] <= EPS:
            q.popleft()
    return spend


def fifo_qty(q):
    return sum(l[0] for l in q)


def fifo_spend(q):
    return sum(l[0] * l[1] for l in q)


def replay(legs, prices, sizes):
    """Return (matched_qty, matched_spend, resid_side, resid_qty, spend)."""
    q = {"UP": deque(), "DOWN": deque()}
    matched_qty = matched_spend = spend = 0.0
    for leg, px, sz in zip(legs, prices, sizes):
        if px is None or sz is None:
            continue
        q[leg].append([sz, px])
        spend += sz * px
        pairable = min(fifo_qty(q["UP"]), fifo_qty(q["DOWN"]))
        if pairable > EPS:
            su = fifo_take(q["UP"], pairable)
            sd = fifo_take(q["DOWN"], pairable)
            matched_qty += pairable
            matched_spend += su + sd
    uq, dq = fifo_qty(q["UP"]), fifo_qty(q["DOWN"])
    if uq > EPS:
        side, rq = "Up", uq
    elif dq > EPS:
        side, rq = "Down", dq
    else:
        side, rq = "", 0.0
    return matched_qty, matched_spend, side, rq, spend


def summarise(rows):
    """rows: list of (pair_cost|None, resid_qty, matched_qty, pnl|None)"""
    pcs = [r[0] for r in rows if r[0] is not None]
    res = [r[1] for r in rows]
    mat = [r[2] for r in rows]
    pnl = [r[3] for r in rows if r[3] is not None]
    out = {"n_windows": len(rows), "n_with_pair": len(pcs)}
    if pcs:
        s = sorted(pcs)
        out |= {
            "pair_cost_median": round(statistics.median(pcs), 5),
            "pair_cost_p25": round(s[int(len(s) * .25)], 5),
            "pair_cost_p75": round(s[int(len(s) * .75)], 5),
            "share_pair_cost_below_1": round(
                sum(1 for x in pcs if x < 1.0) / len(pcs), 5),
            "mean_locked_profit_per_pair": round(
                statistics.fmean([1.0 - x for x in pcs]), 6),
        }
    out |= {
        "matched_qty_mean": round(statistics.fmean(mat), 4) if mat else None,
        "residual_qty_mean": round(statistics.fmean(res), 4) if res else None,
    }
    if pnl:
        out |= {"pnl_mean": round(statistics.fmean(pnl), 4),
                "pnl_sum": round(sum(pnl), 2),
                "share_pnl_positive": round(
                    sum(1 for x in pnl if x > 0) / len(pnl), 5)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=REPS)
    a = ap.parse_args()
    rng = random.Random(SEED)

    settle = {}
    with gzip.open(os.path.join(a.dataset, "l5_window_summary.csv.gz"),
                   "rt", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            settle[r["event_slug"]] = (r["settled_outcome"] or "").strip()

    real_rows, sim_rows = [], []
    real_by_reg = collections.defaultdict(list)
    sim_by_reg = collections.defaultdict(list)
    paired_pc, paired_pnl = [], []
    real_runs, sim_runs = [], []
    n_fills = n_win = 0

    buf = []
    cur = None

    def flush():
        nonlocal n_win
        if not buf:
            return
        n_win += 1
        slug = buf[0]["event_slug"]
        month = buf[0]["datetime_utc"][:7]
        reg = "early" if month in ("2026-03", "2026-04", "2026-05") else (
            "late" if month == "2026-08" else "mid")
        haz = HAZARD.get(month, DEFAULT_HAZARD)
        st = settle.get(slug, "").lower()

        legs = [r["leg"] for r in buf]
        prices = [f(r["price"]) for r in buf]
        sizes = [f(r["size"]) for r in buf]

        mq, ms, rs, rq, sp = replay(legs, prices, sizes)
        rpc = (ms / mq) if mq > EPS else None
        proceeds = mq + (rq if (rs and rs.lower() == st) else 0.0)
        rpnl = proceeds - sp if st else None
        real_rows.append((rpc, rq, mq, rpnl))
        real_by_reg[reg].append((rpc, rq, mq, rpnl))

        # real run lengths
        rl, run = [], 1
        for i in range(1, len(legs)):
            if legs[i] == legs[i - 1]:
                run += 1
            else:
                rl.append(run)
                run = 1
        rl.append(run)
        real_runs.extend(rl)

        # simulated replicates
        pcs, pnls = [], []
        for _ in range(a.reps):
            slegs = [legs[0]]
            for _i in range(1, len(legs)):
                prev = slegs[-1]
                slegs.append(("DOWN" if prev == "UP" else "UP")
                             if rng.random() < haz else prev)
            # complement the price wherever the simulated side differs
            sprices = [p if (sl == al or p is None) else (1.0 - p)
                       for sl, al, p in zip(slegs, legs, prices)]
            smq, sms, srs, srq, ssp = replay(slegs, sprices, sizes)
            spc = (sms / smq) if smq > EPS else None
            sproc = smq + (srq if (srs and srs.lower() == st) else 0.0)
            spnl = sproc - ssp if st else None
            sim_rows.append((spc, srq, smq, spnl))
            sim_by_reg[reg].append((spc, srq, smq, spnl))
            if spc is not None:
                pcs.append(spc)
            if spnl is not None:
                pnls.append(spnl)
            srl, srun = [], 1
            for i in range(1, len(slegs)):
                if slegs[i] == slegs[i - 1]:
                    srun += 1
                else:
                    srl.append(srun)
                    srun = 1
            srl.append(srun)
            sim_runs.extend(srl)

        if rpc is not None and pcs:
            paired_pc.append(rpc - statistics.fmean(pcs))
        if rpnl is not None and pnls:
            paired_pnl.append(rpnl - statistics.fmean(pnls))

    for p in sorted(glob.glob(os.path.join(
            a.dataset, "fills", "l5_fills_enriched_part*.csv.gz"))):
        with gzip.open(p, "rt", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                n_fills += 1
                if r["event_slug"] != cur:
                    flush()
                    buf.clear()
                    cur = r["event_slug"]
                buf.append(r)
    flush()

    def ci(vals, fn, reps=2000, cap=20000):
        v = [x for x in vals if x is not None]
        if len(v) < 2:
            return None, None
        k, m = len(v), min(len(v), cap)
        out = sorted(fn([v[rng.randrange(k)] for _ in range(m)])
                     for _ in range(reps))
        return out[int(reps * .025)], out[int(reps * .975)]

    pclo, pchi = ci(paired_pc, statistics.fmean)
    pnlo, pnhi = ci(paired_pnl, statistics.fmean)
    dpc = statistics.fmean(paired_pc) if paired_pc else None
    dpnl = statistics.fmean(paired_pnl) if paired_pnl else None

    R = {
        "_what_this_tests":
            "Grok's round-3 claim that a memoryless constant-hazard clone "
            "would reproduce the statistical texture of the whole trace. "
            "Every window keeps its actual fills, sizes, timestamps and "
            "price level; only the LEG SEQUENCE is regenerated, from the "
            "measured per-month switch hazard. Prices are complemented "
            "(p -> 1-p) wherever the simulated side differs, so the "
            "simulated book stays coherent with the venue's real price "
            "level.",
        "fills": n_fills, "windows": n_win, "replicates_per_window": a.reps,
        "hazard_used_per_month": HAZARD,
        "REAL": summarise(real_rows),
        "SIMULATED_memoryless": summarise(sim_rows),
        "paired_within_window": {
            "_note": "real minus mean-of-replicates, per window. Negative "
                     "pair-cost difference means the REAL sequence achieved "
                     "a CHEAPER matched book than a coin flip could.",
            "pair_cost_real_minus_sim": None if dpc is None else round(dpc, 6),
            "pair_cost_ci95": [None if pclo is None else round(pclo, 6),
                               None if pchi is None else round(pchi, 6)],
            "n_windows_pair_cost": len(paired_pc),
            "pnl_real_minus_sim": None if dpnl is None else round(dpnl, 4),
            "pnl_ci95": [None if pnlo is None else round(pnlo, 4),
                         None if pnhi is None else round(pnhi, 4)],
            "n_windows_pnl": len(paired_pnl),
        },
        "run_length": {
            "real_mean": round(statistics.fmean(real_runs), 4),
            "simulated_mean": round(statistics.fmean(sim_runs), 4),
        },
        "by_regime": {k: {"REAL": summarise(real_by_reg[k]),
                          "SIMULATED": summarise(sim_by_reg[k])}
                      for k in ("early", "mid", "late")},
    }

    verd = []
    if pclo is not None:
        if pchi < 0:
            verd.append("REJECTED: the real leg sequence achieves a "
                        "significantly CHEAPER matched book than a "
                        "memoryless sequence over the same fills. Leg "
                        "selection carries information a coin flip does "
                        "not.")
        elif pclo > 0:
            verd.append("REJECTED IN REVERSE: the real sequence is WORSE "
                        "than a coin flip on pair cost.")
        else:
            verd.append("NOT REJECTED on pair cost: real and memoryless are "
                        "indistinguishable.")
    if pnlo is not None:
        if pnlo > 0:
            verd.append("REJECTED on P&L: the real sequence earns "
                        "significantly more than a memoryless one.")
        elif pnhi < 0:
            verd.append("REJECTED IN REVERSE on P&L: the real sequence "
                        "earns LESS than a memoryless one.")
        else:
            verd.append("NOT REJECTED on P&L.")
    R["VERDICT"] = verd

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1)
    print(json.dumps({k: R[k] for k in
                      ("fills", "windows", "REAL", "SIMULATED_memoryless",
                       "paired_within_window", "run_length", "VERDICT")},
                     indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
