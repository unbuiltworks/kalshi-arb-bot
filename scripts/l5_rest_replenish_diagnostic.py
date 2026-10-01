"""Does L5's fill sequence look like SEQUENTIAL completion or CONTINUOUS
two-sided resting with replenishment?

TWO COMPETING MECHANISMS:

  A. OPEN -> deliberately COMPLETE -> bank/reset. Predicts: near-alternation
     between outcomes, a new price almost every action, short same-price runs,
     and essentially no both-outcome fills inside a tight time window.

  B. REST BOTH SIDES -> asynchronous fills -> REPLENISH -> periodic READJUST.
     Predicts: long runs of fills at ONE UNCHANGED price on a side, each side's
     price level moving independently of the other, both-outcome fills inside
     tight windows, and level RECURRENCE (a level left and later returned to).

EVIDENCE LIMIT, binding on every number below: this feed contains FILLS. It does
NOT contain orders, cancels, requotes, resting quotes or queue position. A run of
fills at one price is CONSISTENT WITH a quote resting there; it does not prove a
quote rested there, because repeated identical-price takes would look the same.
What follows is therefore discriminating evidence BETWEEN A and B, never direct
observation of resting behaviour.
"""
from __future__ import annotations

import gzip
import json
import sys
from collections import defaultdict
from decimal import Decimal as D

WINDOW_S = 900
F = float


def median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else None


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p * len(v)))] if v else None


def load_markets(path):
    o = json.loads(gzip.open(path, "rt").read())
    frag = defaultdict(list)
    for p in o["rows"]:
        s = str(p.get("slug") or "")
        if p.get("type") != "TRADE" or not s.startswith("btc-updown-15m"):
            continue
        frag[(s, p["timestamp"], p.get("outcome"), str(p.get("price")))].append(p)
    mkt = defaultdict(list)
    for (slug, ts, out, px), fr in frag.items():
        mkt[slug].append({"t": ts, "o": out, "px": D(str(px)),
                          "sz": sum(D(str(f.get("size") or 0)) for f in fr)})
    for s in mkt:
        mkt[s].sort(key=lambda a: (a["t"], str(a["o"])))
    return mkt


def main(argv):
    mkt = load_markets(argv[1])
    print(f"markets={len(mkt)} "
          f"actions={sum(len(v) for v in mkt.values())}")

    same_px_runs = []          # consecutive same-side fills at an UNCHANGED px
    side_runs = []             # consecutive fills on one outcome before flip
    qty_at_level = []          # size accumulated at a level before it changes
    level_change_gap = []      # seconds between price-level changes, per side
    recurrence = 0
    levels_total = 0
    held_while_other_moved = 0
    other_moved_events = 0
    both_within = {0: 0, 1: 0, 2: 0, 5: 0}
    pair_opportunities = 0
    late_runs = []
    runs_by_resid = defaultdict(list)
    alternations = 0
    followups = 0

    for slug, acts in mkt.items():
        try:
            ep = int(slug.split("-")[-1])
        except ValueError:
            continue
        qty = {"Up": D(0), "Down": D(0)}
        cur = {}               # outcome -> current price level
        cur_qty = defaultdict(D)
        cur_since = {}
        seen_levels = defaultdict(set)
        run_px = {}
        run_len = defaultdict(int)
        side_run = 0
        prev_o = None
        last_change_t = {}

        for i, a in enumerate(acts):
            o, other = a["o"], ("Down" if a["o"] == "Up" else "Up")
            resid = abs(qty["Up"] - qty["Down"])

            # ---- PER-OUTCOME run of fills at an unchanged price. Tracked per
            # outcome so a fill on the OTHER side does not break the run: with
            # two quotes resting, interleaving is expected and is not repricing.
            if run_px.get(o) == a["px"]:
                run_len[o] += 1
            else:
                if run_len[o]:
                    same_px_runs.append(run_len[o])
                    runs_by_resid[0 if resid == 0 else
                                  (1 if resid < 50 else 2)].append(run_len[o])
                    if ep + WINDOW_S - a["t"] <= 120:
                        late_runs.append(run_len[o])
                run_px[o], run_len[o] = a["px"], 1

            # ---- outcome run (how many on one side before the other fills)
            if prev_o == o:
                side_run += 1
            else:
                if side_run:
                    side_runs.append(side_run)
                side_run = 1
                if prev_o is not None:
                    alternations += 1
            if prev_o is not None:
                followups += 1

            # ---- level bookkeeping: did THIS side's level change?
            if o in cur and cur[o] != a["px"]:
                qty_at_level.append(cur_qty[o])
                if o in cur_since:
                    level_change_gap.append(a["t"] - cur_since[o])
                last_change_t[o] = a["t"]
                # did the OTHER side keep its level across this change? Only
                # a change in the other side's level at the SAME timestamp
                # counts as the two sides moving together.
                if other in cur:
                    other_moved_events += 1
                    moved_together = any(
                        x["o"] == other and x["t"] == a["t"]
                        and x["px"] != cur[other] for x in acts)
                    if not moved_together:
                        held_while_other_moved += 1
                cur_qty[o] = D(0)
                cur_since[o] = a["t"]
            elif o not in cur:
                cur_since[o] = a["t"]
            if a["px"] in seen_levels[o] and cur.get(o) != a["px"]:
                recurrence += 1
            if cur.get(o) != a["px"]:
                levels_total += 1
            seen_levels[o].add(a["px"])
            cur[o] = a["px"]
            cur_qty[o] += a["sz"]

            prev_o = o
            qty[o] += a["sz"]

        for o_ in list(run_len):
            if run_len[o_]:
                same_px_runs.append(run_len[o_])
        if side_run:
            side_runs.append(side_run)

        # ---- both-outcome fills inside tight windows
        for w in both_within:
            ts_up = [x["t"] for x in acts if x["o"] == "Up"]
            ts_dn = [x["t"] for x in acts if x["o"] == "Down"]
            j = 0
            for t in ts_up:
                while j < len(ts_dn) and ts_dn[j] < t - w:
                    j += 1
                if j < len(ts_dn) and abs(ts_dn[j] - t) <= w:
                    both_within[w] += 1
        pair_opportunities += min(
            sum(1 for x in acts if x["o"] == "Up"),
            sum(1 for x in acts if x["o"] == "Down"))

    print("\n=========== DISCRIMINATING MEASUREMENTS ===========")
    print("\n1. consecutive same-side fills at an UNCHANGED price (runs)")
    print(f"   n_runs={len(same_px_runs)} median={median(same_px_runs)} "
          f"p75={pct(same_px_runs,.75)} p90={pct(same_px_runs,.90)} "
          f"p99={pct(same_px_runs,.99)} max={max(same_px_runs)}")
    print(f"   share of runs with length 1 (a new price every fill): "
          f"{sum(1 for x in same_px_runs if x==1)/len(same_px_runs):.4f}")
    print(f"   share of runs with length >= 3: "
          f"{sum(1 for x in same_px_runs if x>=3)/len(same_px_runs):.4f}")

    print("\n2. runs of one outcome filling before the opposite outcome fills")
    print(f"   n={len(side_runs)} median={median(side_runs)} "
          f"p90={pct(side_runs,.90)} max={max(side_runs)}")
    print(f"   alternation rate (next fill flips outcome): "
          f"{alternations/followups:.4f}")

    print("\n3. quantity accumulated at a level before that level changes")
    print(f"   n={len(qty_at_level)} median={median(qty_at_level)} "
          f"p90={pct(qty_at_level,.90)}")

    print("\n4. seconds a side holds one level before changing it")
    print(f"   n={len(level_change_gap)} median={median(level_change_gap)}s "
          f"p90={pct(level_change_gap,.90)}s max={max(level_change_gap)}s")

    print("\n5. one side's level held while the other side's level changed")
    print(f"   events where a side changed level with the other side present: "
          f"{other_moved_events}")
    print(f"   of those, the other side's level was UNCHANGED: "
          f"{held_while_other_moved} "
          f"({held_while_other_moved/other_moved_events:.4f})")

    print("\n6. price-level RECURRENCE (a level left, then returned to)")
    print(f"   level changes={levels_total} returns to a previously used "
          f"level={recurrence} ({recurrence/levels_total:.4f})")

    print("\n7. both-outcome fills inside tight time windows")
    for w in (0, 1, 2, 5):
        print(f"   within {w}s: {both_within[w]:7d} "
              f"({both_within[w]/pair_opportunities:.4f} of min(up,down) fills)")

    print("\n8. same-price run length vs residual size at the time")
    for k, lab in ((0, "residual == 0"), (1, "residual 1-49"),
                   (2, "residual >= 50")):
        v = runs_by_resid[k]
        if v:
            print(f"   {lab:<18} n={len(v):7d} median_run={median(v)} "
                  f"p90={pct(v,.90)}")

    print("\n9. final 120 seconds")
    print(f"   same-price runs beginning in the last 120s: n={len(late_runs)} "
          f"median={median(late_runs)} p90={pct(late_runs,.90)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
