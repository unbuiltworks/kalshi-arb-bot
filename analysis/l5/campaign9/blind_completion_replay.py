#!/usr/bin/env python3
"""What did the engine miss while it waited for the next L5 action?

THE MECHANISM, ESTABLISHED FROM THE CODE FIRST.

A maker completion for inventory we already hold is priced by
`TS.complement_order`, called from `l5_live_execution.handle(row, ...)`.
`handle` takes an L5 ACTION ROW, and the only caller is the ingest loop at
`l5_pmus_live_runner.py:2450`, immediately after `ingest:admitted`. So the
maker completion is evaluated ONCE PER ADMITTED L5 ACTION and at no other
time. Neither timer path substitutes:

  * `maybe_ts_completion` (TS_COMPLETION_EVERY_S = 10s) decides whether to
    CROSS. It needs L5_TS_COMPLETION_CROSS plus an armed transport, and in
    the MAKER_ONLY production configuration it is a logged proposal only.
    Across seven live Oct-24 windows its 70 evaluations are 40
    ALREADY_MATCHED, 18 WORST_CASE_CEILING_UNKNOWN, 12
    COMPLETION_PRICE_OUT_OF_BAND, and `authorized_to_cross` is false in
    every one.
  * `maybe_completion_requote` reprices an EXISTING resting completion. It
    cannot create one where none rests.

So when a completion is refused at the moment an action arrives, nothing
rests, and nothing is reconsidered until the next action. That is not a
hypothesis: it is the documented cause of the 1945z loss recorded in
`l5_pmus_live_runner.py` -- twelve COMPLEMENT_PAIR_ABOVE_CAP refusals, 121
residual samples all `order_resting: false`, an executable exit worth
-$0.0144 at 204s, and the engine's first legal opportunity at 600s by which
time the best exit was -$0.5348.

WHAT THIS SCRIPT MEASURES. The `L5_SHADOW_RESIDUAL_BOOK_SAMPLE` tape samples
the complement book every 5 seconds while inventory is unmatched, driven by
`UNMATCHED_INVENTORY_NOT_ORDER_STATE` -- independent of order state AND of
L5 actions. It is the only instrument that sees the blind interval.

It measures the IMPROVEMENT FORGONE, not an absolute pair cost: the first
leg's cost is absent from this tape, but it is a constant within a window,
so it cancels out of a difference. For each blind interval of length `gap`,
compare the complement price the engine WOULD have seen (the price at the
interval's start, when the action arrived) against the BEST price available
inside the interval. The difference is what action-triggering costs, in
cents of own price per contract.

THREE DISCOUNTS, APPLIED NOT MENTIONED.
  1. QUEUE. `maker_join_own_px` is the DISPLAYED touch. Joining it queues
     behind `queue_ahead_bound` shares and the venue publishes no per-order
     queue position, so a touch is not a fill. Intervals are reported split
     by whether queue ahead is under 50x the quantity needed -- and almost
     none are.
  2. FEES. Maker completions pay no taker fee, and PMUS credited $0.0000 of
     maker rebate across 55 fill events, so the fee term on a maker-to-maker
     pair is zero. Nothing is added for a rebate.
  3. TIME. An improvement inside the last 180s cannot be acted on: the
     deadline sweep cancels at the cutoff. Those samples are excluded.
"""
from __future__ import annotations

import glob
import json
import pathlib
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[3]
TAPES = "analysis/l5/residual_exit/preserved/btc15m_20261026_*.ndjson"

#: the action-triggered engine's measured submission cadence (C8, two
#: uninterrupted windows: 34.1s and 34.5s submit-to-submit).
BLIND_S = 34.0
#: no completion can be placed inside the pre-close cancellation margin.
CANCEL_MARGIN_S = 180.0
#: the queue-ahead multiple below which a displayed touch is even arguably
#: reachable. 50x the needed quantity is already generous.
QUEUE_REACHABLE_MULT = D("50")


#: SIX SHADOW VARIANTS SAMPLE THE SAME WINDOW -- RESIDUAL_URGENCY, ARGMAX,
#: ARGMAX@TWO_SIDED, ARGMAX@TWO_SIDED_LADDER, PAIR_AVAILABLE and an
#: unnamed one, all writing the same label with their own modelled
#: inventory. An earlier version of this script did not filter them, and the
#: interval walker then compared one variant's book sample against
#: another's, inventing 32-86 cents of "forgone" price out of interleaving.
#: One variant at a time, always.
VARIANT = "RESIDUAL_URGENCY"


def samples():
    out = []
    for f in sorted(glob.glob(str(ROOT / TAPES))):
        win = pathlib.Path(f).stem
        rows = []
        for line in open(f):
            r = json.loads(line)
            if not str(r.get("label", "")).endswith("RESIDUAL_BOOK_SAMPLE"):
                continue
            b = json.loads(r["body"])
            if b.get("shadow_variant") != VARIANT:
                continue
            reach = b.get("reachability") or {}
            top = reach.get("opposite_top_of_book") or {}
            mq = reach.get("maker_quote") or {}
            behind = reach.get("maker_quote_behind_touch") or {}
            if not top.get("maker_join_own_px"):
                continue
            rows.append({
                "t": float(b["t"]),
                "residual": D(str(b.get("residual") or 0)),
                "order_resting": bool(b.get("order_resting")),
                "join_own": D(str(top["maker_join_own_px"])),
                "behind_own": (D(str(behind["own_px"]))
                               if behind.get("own_px") else None),
                "queue_ahead": D(str(mq.get("queue_ahead_bound") or 0)),
                "qty_needed": D(str(mq.get("qty_needed") or 0)),
            })
        if rows:
            rows.sort(key=lambda r: r["t"])
            out.append((win, rows))
    return out


def main() -> int:
    report = {}
    tot_cheap = D("0")
    tot_int = 0
    reachable_int = 0
    for win, rows in samples():
        t_end = rows[-1]["t"]
        # only while unmatched AND nothing resting AND outside the margin:
        # exactly the state in which an action-triggered engine is blind and
        # a completion could still be placed.
        live = [r for r in rows
                if r["residual"] != 0 and not r["order_resting"]
                and (t_end - r["t"]) > CANCEL_MARGIN_S]
        if not live:
            report[win] = {"eligible_samples": 0}
            continue
        # walk the eligible span in BLIND_S intervals
        intervals = []
        i = 0
        while i < len(live):
            t0 = live[i]["t"]
            grp = [r for r in live[i:] if r["t"] < t0 + BLIND_S]
            if len(grp) >= 2:
                seen = grp[0]["join_own"]        # what the action saw
                best = min(r["join_own"] for r in grp)
                forgone = seen - best            # own price, so lower=better
                q = min(r["queue_ahead"] for r in grp)
                need = max(r["qty_needed"] for r in grp) or D("1")
                reachable = q <= QUEUE_REACHABLE_MULT * need
                intervals.append({
                    "t0": t0, "n": len(grp),
                    "seen_own": str(seen), "best_own": str(best),
                    "forgone_cents": str(round(forgone * 100, 2)),
                    "min_queue_ahead": str(q),
                    "queue_multiple_of_need": str(round(q / need, 1)),
                    "queue_reachable": reachable,
                })
                tot_int += 1
                if reachable:
                    reachable_int += 1
                if forgone > 0:
                    tot_cheap += forgone
            i += max(1, len(grp))
        pos = [iv for iv in intervals if D(iv["forgone_cents"]) > 0]
        report[win] = {
            "samples": len(rows),
            "eligible_samples": len(live),
            "blind_intervals": len(intervals),
            "intervals_with_a_better_price_inside": len(pos),
            "mean_forgone_cents_when_better": (
                str(round(sum(D(iv["forgone_cents"]) for iv in pos)
                          / len(pos), 2)) if pos else None),
            "max_forgone_cents": (
                str(max(D(iv["forgone_cents"]) for iv in intervals))
                if intervals else None),
            "intervals_queue_reachable": sum(
                1 for iv in intervals if iv["queue_reachable"]),
            "worst_examples": sorted(
                intervals, key=lambda iv: -D(iv["forgone_cents"]))[:3],
        }

    print(json.dumps({
        "windows": report,
        "totals": {
            "blind_intervals": tot_int,
            "intervals_queue_reachable": reachable_int,
            "sum_forgone_own_price_cents": str(round(tot_cheap * 100, 2)),
        },
        "labels": {
            "forgone": ("improvement in COMPLEMENT OWN PRICE available "
                        "inside a blind interval but not at its start. The "
                        "first leg's cost is constant within a window and "
                        "cancels from the difference."),
            "not_a_fill": ("a displayed touch is not a fill. "
                           "queue_reachable is the generous test "
                           "(queue ahead <= 50x the quantity needed); it is "
                           "the only column that could make any of this "
                           "executable."),
            "inventory": ("this tape's inventory is MODELLED (shadow "
                          "variant), so the residual quantities are not "
                          "venue positions. The BOOKS are real and sampled "
                          "every 5s, which is what the measurement rests "
                          "on."),
        },
    }, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
