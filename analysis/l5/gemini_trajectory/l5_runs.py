"""Canonical L5 same-side RUN and SWITCH statistics -- the inventory path,
not the pair lifecycle.

Every number here comes from L5's PUBLIC FILLS and observable market data.
Cancellations, resting orders and queue position are NOT observable in that
record and are never inferred: a "run" is a maximal streak of consecutive
fills on one OUTCOME (Up/Down) within one window, which is a statement about fills, not
about how many orders produced them.
"""
import collections
import csv
import gzip
import os
import statistics
import sys

HERE = os.path.dirname(__file__)
SEQ = os.path.join(HERE, "..", "l5_canonical_sequence.csv.gz")


def _f(v):
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def q(v, p):
    v = sorted(x for x in v if x is not None)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def load():
    win = collections.defaultdict(list)
    with gzip.open(SEQ, "rt") as fh:
        for r in csv.DictReader(fh):
            win[r.get("window_id")].append(r)
    for w in win.values():
        w.sort(key=lambda r: (_f(r.get("trade_seq")) or 0,
                              _f(r.get("timestamp")) or 0))
    return win


def main():
    win = load()
    runs, switches, per_window = [], [], []
    gap_all, gap_within, gap_across = [], [], []
    step_same, moved_same = [], 0
    dur_fills = collections.defaultdict(list)
    for wid, rows in win.items():
        # `side` is BUY/SELL (71,982 of 71,984 rows are BUY -- L5 buys both
        # outcomes). The INVENTORY side is `outcome` (Up/Down). Keying runs on
        # `side` finds 2 switches in 2,216 windows, which is a statement about
        # order direction, not about inventory.
        sides = [r.get("outcome") for r in rows]
        ts = [_f(r.get("timestamp")) for r in rows]
        px = [_f(r.get("price")) for r in rows]
        dur = _f(rows[0].get("duration_minutes")) or 0
        dur_fills[dur].append(len(rows))
        # runs
        cur_side, cur = sides[0], [0]
        for i in range(1, len(rows)):
            if sides[i] == cur_side:
                cur.append(i)
            else:
                runs.append((wid, cur_side, cur)); switches.append(i)
                cur_side, cur = sides[i], [i]
        runs.append((wid, cur_side, cur))
        nsw = sum(1 for a, b in zip(sides, sides[1:]) if a != b)
        span = (ts[-1] - ts[0]) if (ts[0] and ts[-1]) else None
        per_window.append({"fills": len(rows), "switches": nsw,
                           "span_s": span, "dur_min": dur,
                           "distinct_px": len(set(p for p in px if p is not None))})
        for i in range(1, len(rows)):
            if ts[i] is None or ts[i - 1] is None:
                continue
            g = ts[i] - ts[i - 1]
            gap_all.append(g)
            (gap_within if sides[i] == sides[i - 1] else gap_across).append(g)
            if sides[i] == sides[i - 1] and px[i] is not None and px[i - 1] is not None:
                step_same.append(px[i] - px[i - 1])
                if px[i] != px[i - 1]:
                    moved_same += 1
    rl = [len(r[2]) for r in runs]
    rpx = []
    for wid, side, idx in runs:
        rows = win[wid]
        rpx.append(len({_f(rows[i].get("price")) for i in idx}))
    print(f"windows {len(win)}   fills {sum(len(v) for v in win.values())}   "
          f"runs {len(runs)}   switches {sum(w['switches'] for w in per_window)}\n")
    print("1. FILLS PER SAME-SIDE RUN")
    print(f"   p25 {q(rl,.25)}  p50 {q(rl,.50)}  p75 {q(rl,.75)}  p90 {q(rl,.90)} "
          f" max {max(rl)}  mean {statistics.mean(rl):.2f}")
    print(f"   runs of length 1: {sum(1 for x in rl if x==1)} "
          f"({sum(1 for x in rl if x==1)/len(rl)*100:.1f}%)   "
          f">=3: {sum(1 for x in rl if x>=3)} ({sum(1 for x in rl if x>=3)/len(rl)*100:.1f}%)")
    print("\n2. DISTINCT PRICES PER RUN")
    print(f"   p50 {q(rpx,.50)}  p75 {q(rpx,.75)}  p90 {q(rpx,.90)}  max {max(rpx)}")
    print(f"   runs at ONE price: {sum(1 for x in rpx if x==1)/len(rpx)*100:.1f}%")
    print("\n3. SECONDS BETWEEN CONSECUTIVE FILLS")
    for lbl, v in (("all", gap_all), ("same side", gap_within),
                   ("across a switch", gap_across)):
        print(f"   {lbl:16} n {len(v):6d}  p25 {q(v,.25):7.2f}  p50 {q(v,.50):7.2f}"
              f"  p75 {q(v,.75):7.2f}  p90 {q(v,.90):7.2f}")
    print("\n4-6. PRICE STEP BETWEEN CONSECUTIVE SAME-SIDE FILLS")
    absst = [abs(x) for x in step_same]
    print(f"   n {len(step_same)}   |step| p50 {q(absst,.50):.4f}  p75 {q(absst,.75):.4f} "
          f" p90 {q(absst,.90):.4f}")
    print(f"   same-side fills AT A DIFFERENT PRICE than the previous one: "
          f"{moved_same}/{len(step_same)} = {moved_same/len(step_same)*100:.1f}%")
    print(f"   -> L5 KEEPS ACCUMULATING ONE SIDE WHILE ITS PRICE MOVES in "
          f"{moved_same/len(step_same)*100:.1f}% of same-side continuations")
    print("\n8. FILLS AND SWITCHES PER 5-MINUTE EQUIVALENT")
    for d in sorted(dur_fills):
        v = dur_fills[d]
        if len(v) < 20 or not d:
            continue
        print(f"   duration {d:5.0f} min  windows {len(v):5d}  fills/window p50 {q(v,.50):6.0f}"
              f"   -> per 5 min p50 {q(v,.50)*5.0/d:7.1f}")
    sw5 = [w["switches"] * 5.0 / w["dur_min"] for w in per_window
           if w["dur_min"]]
    f5 = [w["fills"] * 5.0 / w["dur_min"] for w in per_window if w["dur_min"]]
    print(f"   ALL windows: fills per 5 min  p50 {q(f5,.50):.1f}  p90 {q(f5,.90):.1f}")
    print(f"                switches per 5 min p50 {q(sw5,.50):.1f}  p90 {q(sw5,.90):.1f}")
    print("\n9. INVENTORY PATH (from the canonical cum/matched/residual columns)")
    imb = []
    for rows in win.values():
        for r in rows:
            u, d2 = _f(r.get("cum_up_qty")), _f(r.get("cum_down_qty"))
            if u is not None and d2 is not None:
                imb.append(abs(u - d2))
    print(f"   |cum_up - cum_down| at a fill: p25 {q(imb,.25):.2f}  p50 {q(imb,.50):.2f}"
          f"  p75 {q(imb,.75):.2f}  p90 {q(imb,.90):.2f}")
    nonzero = sum(1 for x in imb if x > 1e-9)
    print(f"   fills taken while IMBALANCED: {nonzero}/{len(imb)} = "
          f"{nonzero/len(imb)*100:.1f}%")
    print("\nUNKNOWN, not inferred: cancellations, resting orders, queue "
          "position. A run counts FILLS, never orders.")


if __name__ == "__main__":
    main()
