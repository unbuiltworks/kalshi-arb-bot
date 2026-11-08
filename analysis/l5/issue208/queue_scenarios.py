"""Queue-aware replay under three queue-position assumptions (#208 item 3).

WHAT THIS CAN AND CANNOT BE. `src/l5_queue_depletion` establishes, and
verified by a log query returning zero `prints_at_our_price` records, that
THERE IS NO TRADE-PRINT SERIES for these recordings. So a fill can never be
asserted here; only bounded from the ladder:

  PROVEN      the opposite touch reached our price. SUFFICIENT, so a lower
              bound, and INDEPENDENT of where we sat in the queue.
  POSSIBLE    enough displayed size left the at-or-better region to clear
              the queue we assume was ahead of us. A decrease is a trade OR
              a cancellation and the ladder cannot tell them apart, so this
              is NECESSARY ONLY -- an upper bound.
  IMPOSSIBLE  the necessary condition never held. A hard negative whatever
              the trade/cancel mix was.

THE THREE SCENARIOS ARE QUEUE POSITIONS, NOT PROBABILITIES. A probability of
filling is not in this data and this module does not manufacture one. What
varies is the one unmeasured quantity -- how much of the displayed size at
our price is ahead of us:

  pessimistic   ALL of it. What `QD.fill_verdict` assumes, and what a
                late joiner at a busy level actually faces.
  base          HALF of it.
  optimistic    NONE of it, but still requiring our own size to have left
                the level, so "at the front" does not mean "filled for
                free" with no evidence of any trade at all.

`PROVEN` does not move between scenarios, by construction. Only the
necessary-condition arm does, which is why the spread between the three is a
statement about queue risk and nothing else.

WHY THE PRIMITIVES ARE CALLED AND NOT COPIED. `_at_or_better` and
`_crossed_by_other_side` carry the own-price/wire-price convention and the
side-dependent arithmetic that this program has got wrong more than once. A
module that keeps its own copy of a rule is a module that can disagree with
the one that actually decides, so these are imported.

NOTHING HERE IS A REALIZED RESULT. Every P&L below is settled-EQUIVALENT:
modelled fills, a measured residual constant, and a published fee schedule.
No pair is credited as completed and no dollar as earned.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src import l5_queue_depletion as QD          # noqa: E402

TICK = D("0.01")
UP, DOWN = "UP", "DOWN"

#: measured outcome of carrying ONE unmatched contract to settlement, from
#: the repaired union over 1.24M contracts. Negative, and the number the
#: completion decision is judged against.
RESIDUAL_PER_CONTRACT = D("-0.0236")

#: confirmed CREDITED maker rebate, $2.13 over 293 fills. Reported as its
#: own line and never folded into the pair price.
CONFIRMED_REBATE_PER_FILL = (D("2.13") / D(293)).quantize(D("0.000001"))

#: (name, fill arm, share of the displayed at-or-better size ahead of us).
#:
#: THE ARM IS PART OF THE SCENARIO, not a separate switch. PROVEN is the
#: sufficient condition and is INDEPENDENT of queue position, so holding it
#: fixed while varying the queue would produce three identical columns. The
#: honest ordering from least to most favourable is therefore:
#:
#:   pessimistic  only a fill the ladder PROVES -- the opposite touch
#:                reached our price. Queue position cannot help or hurt it.
#:   base         the necessary condition, with the WHOLE displayed size at
#:                our price assumed ahead of us.
#:   optimistic   the necessary condition, at the FRONT of the queue, still
#:                requiring our own size to have left the level.
SCENARIOS: Tuple[Tuple[str, str, D], ...] = (
    ("pessimistic", QD.PROVEN, D(1)),
    ("base", QD.POSSIBLE, D(1)),
    ("optimistic", QD.POSSIBLE, D(0)),
)

#: 0 is where ROUTE B rests (the touch); 2 is where the opening probe rests.
DEPTHS = (0, 2)

IN_SAMPLE = [
    ("analysis/l5/completion_requote/tape_20261028.jsonl", "flat"),
    ("analysis/l5/issue195/residual_book_samples_20261106_0300z_0315z.json",
     "wrapped"),
]
OUT_OF_SAMPLE = [
    ("data/l5_strand_replay/residual_books_20261025.jsonl", "flat"),
    ("analysis/l5/issue195/"
     "oos_shadow_residual_samples_20261106_1130z_1145z.json", "wrapped"),
]


# ----------------------------------------------------------------- loading
def load(path: str, shape: str) -> Dict[str, List[Dict[str, Any]]]:
    """-> {slug: [frame]}, time-ordered. Data plumbing only; no rule here."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    if shape == "flat":
        rows = [json.loads(ln) for ln in open(path) if ln.strip()]
    else:
        rows = [s.get("rec") or {} for s in
                json.load(open(path)).get("samples", [])]
    for r in rows:
        slug, bids, asks = r.get("slug"), r.get("bids"), r.get("asks")
        if bids is None or asks is None:
            row = ((r.get("book") or {}).get("row") or {})
            bids, asks = row.get("bids"), row.get("asks")
        t = r.get("t")
        if not slug or t is None or not bids or not asks:
            continue
        out.setdefault(slug, []).append({"t": float(t), "bids": bids,
                                         "asks": asks})
    for slug in out:
        out[slug].sort(key=lambda f: f["t"])
    return out


def touches(frame: Mapping[str, Any]) -> Optional[Tuple[D, D]]:
    b = [(D(p), D(s)) for p, s in frame["bids"]]
    a = [(D(p), D(s)) for p, s in frame["asks"]]
    if not b or not a:
        return None
    bb, ba = max(p for p, _ in b), min(p for p, _ in a)
    return (bb, ba) if ba > bb else None


# ------------------------------------------------------------- one leg
def leg_outcome(side: str, own_px: D, frames: Sequence[Mapping[str, Any]],
                *, start: int, queue_fraction: D, own_qty: D,
                ttl_s: Optional[float]) -> Dict[str, Any]:
    """Bounded verdict for one resting leg under one queue assumption.

    A TTL cancels the order, which ends its chance of filling AND resets
    nothing in our favour: a cancelled order's queue position is gone.
    """
    q0 = QD._at_or_better(side, own_px, frames[start])
    if q0 is None:
        return {"verdict": None, "reason": "QUEUE_UNREADABLE"}
    # our own size must clear even at the front of the queue, so that
    # "optimistic" still requires evidence that something traded here.
    need = max(q0 * queue_fraction, own_qty)
    t0 = float(frames[start]["t"])
    cleared = D(0)
    prev = q0
    proven_at: Optional[int] = None
    possible_at: Optional[int] = None
    # BOTH CONDITIONS ARE TRACKED TO THE END OF THE WALK, and this is not a
    # detail. Returning at the first condition met lets an early POSSIBLE
    # mask a PROVEN that happens later -- which made the optimistic queue
    # assumption report FEWER proven pairs than the pessimistic one, an
    # impossibility, because PROVEN does not depend on queue position at
    # all. The two arms are independent and are counted independently.
    for i in range(start + 1, len(frames)):
        f = frames[i]
        if ttl_s is not None and (float(f["t"]) - t0) > ttl_s:
            break                       # cancelled; nothing after it counts
        if proven_at is None and QD._crossed_by_other_side(
                side, own_px, f) is True:
            proven_at = i
        cur = QD._at_or_better(side, own_px, f)
        if cur is not None:
            if cur < prev:
                cleared += (prev - cur)
            prev = cur
        if possible_at is None and cleared >= need:
            possible_at = i
        if proven_at is not None and possible_at is not None:
            break
    held = None
    idx = proven_at if proven_at is not None else possible_at
    if idx is not None:
        held = round(float(frames[idx]["t"]) - t0, 1)
    if proven_at is not None:
        verdict, reason = QD.PROVEN, "OPPOSITE_TOUCH_REACHED"
    elif possible_at is not None:
        verdict, reason = QD.POSSIBLE, "QUEUE_COULD_HAVE_CLEARED"
    else:
        verdict = QD.IMPOSSIBLE
        reason = ("TTL_CANCELLED" if ttl_s is not None
                  and (float(frames[-1]["t"]) - t0) > ttl_s
                  else "QUEUE_NEVER_CLEARED")
    return {"verdict": verdict, "reason": reason, "queue_ahead": str(q0),
            "need": str(need), "cleared": str(cleared),
            "proven": proven_at is not None,
            "possible": possible_at is not None,
            "proven_at": proven_at, "possible_at": possible_at,
            # THE FRAME INDEX, returned rather than recoverable from
            # `seconds_held`. That duration is rounded for reporting, so
            # recovering an index from it by matching float timestamps fails
            # -- which silently sent a sequential replay's completions down
            # a no-runway branch and reported a 0% completion rate.
            "fill_index": idx,
            "seconds_held": held}


def filled(outcome: Mapping[str, Any], *, arm: str) -> Optional[bool]:
    """Did this leg fill, under the named arm?

    `QD.PROVEN` counts only the sufficient condition -- a genuine lower
    bound. `QD.POSSIBLE` counts the necessary one too, which is an upper
    bound because a decrease in displayed size may have been a cancellation.
    """
    if outcome.get("verdict") is None:
        return None
    if arm == QD.PROVEN:
        return bool(outcome.get("proven"))
    return bool(outcome.get("proven") or outcome.get("possible"))


# ------------------------------------------------------------- economics
def window_pnl(*, pair_cost: D, qty: D, up_filled: bool, dn_filled: bool,
               rebate_per_fill: D) -> Dict[str, Any]:
    """Settled-EQUIVALENT outcome for one entry, both legs accounted apart.

    A matched pair redeems exactly $1.00 a share. A single filled leg is
    carried to settlement at the measured constant. Rebates are their own
    line and are never folded into the pair price.
    """
    legs = int(up_filled) + int(dn_filled)
    rebate = rebate_per_fill * qty * D(legs)
    if up_filled and dn_filled:
        return {"state": "PAIR", "matched_qty": str(qty),
                "matched_pnl": str(((D(1) - pair_cost) * qty)
                                   .quantize(D("0.000001"))),
                "residual_pnl": "0", "rebate": str(rebate),
                "legs_filled": legs}
    if legs == 1:
        return {"state": "RESIDUAL", "matched_qty": "0", "matched_pnl": "0",
                "residual_pnl": str((RESIDUAL_PER_CONTRACT * qty)
                                    .quantize(D("0.000001"))),
                "rebate": str(rebate), "legs_filled": 1}
    return {"state": "NO_FILL", "matched_qty": "0", "matched_pnl": "0",
            "residual_pnl": "0", "rebate": "0", "legs_filled": 0}


def breakeven_completion_rate(pair_cost: Any) -> Optional[D]:
    """P(second leg fills) at which a window with a filled FIRST leg breaks
    even, given the measured residual constant.

        q*(1 - pair) + (1 - q*)*residual = 0
        q* = -residual / ((1 - pair) - residual)

    NOTE WHAT THIS IS NOT. It is not the rate at which completing beats
    carrying -- attempting a completion costs nothing extra when it fails,
    so attempting weakly dominates for any q. It is the rate at which the
    WINDOW, having taken one leg, is expected to end non-negative.
    """
    p = QD._d(pair_cost)
    if p is None:
        return None
    edge = D(1) - p
    denom = edge - RESIDUAL_PER_CONTRACT
    if denom <= 0:
        return None
    return (-RESIDUAL_PER_CONTRACT / denom).quantize(D("0.0001"))


# ------------------------------------------------------------- the replay
def replay(sources: Sequence[Tuple[str, str]], *, depth_ticks: int,
           qty: D = D(1), ttl_s: Optional[float] = None,
           rebate_per_fill: D = D(0),
           root: str = "") -> List[Dict[str, Any]]:
    """Rest BOTH legs at `depth_ticks` behind their own touch, every frame."""
    rows: List[Dict[str, Any]] = []
    for path, shape in sources:
        full = os.path.join(root, path) if root else path
        if not os.path.exists(full):
            continue
        for slug, frames in sorted(load(full, shape).items()):
            for i in range(len(frames) - 1):
                tt = touches(frames[i])
                if tt is None:
                    continue
                bid, ask = tt
                up_own = bid - depth_ticks * TICK
                dn_own = (D(1) - ask) - depth_ticks * TICK
                if up_own <= 0 or dn_own <= 0:
                    continue
                pair = up_own + dn_own
                for name, arm, frac in SCENARIOS:
                    up = leg_outcome(UP, up_own, frames, start=i,
                                     queue_fraction=frac, own_qty=qty,
                                     ttl_s=ttl_s)
                    dn = leg_outcome(DOWN, dn_own, frames, start=i,
                                     queue_fraction=frac, own_qty=qty,
                                     ttl_s=ttl_s)
                    uf, df = filled(up, arm=arm), filled(dn, arm=arm)
                    if uf is None or df is None:
                        continue
                    pnl = window_pnl(pair_cost=pair, qty=qty, up_filled=uf,
                                     dn_filled=df,
                                     rebate_per_fill=rebate_per_fill)
                    rows.append({"source": os.path.basename(path),
                                 "slug": slug, "frame": i,
                                 "scenario": name, "depth_ticks": depth_ticks,
                                 "pair_cost": str(pair),
                                 "up_verdict": up.get("verdict"),
                                 "dn_verdict": dn.get("verdict"),
                                 "up_queue_ahead": up.get("queue_ahead"),
                                 "dn_queue_ahead": dn.get("queue_ahead"),
                                 **pnl})
    return rows


def summarise(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    keys = sorted({(r["scenario"], r["depth_ticks"]) for r in rows},
                  key=lambda k: (k[1],
                                 [n for n, _, _ in SCENARIOS].index(k[0])))
    for scen, depth in keys:
        sel = [r for r in rows
               if r["scenario"] == scen and r["depth_ticks"] == depth]
        if not sel:
            continue
        pairs = [r for r in sel if r["state"] == "PAIR"]
        resid = [r for r in sel if r["state"] == "RESIDUAL"]
        nofill = [r for r in sel if r["state"] == "NO_FILL"]
        matched = sum(D(r["matched_pnl"]) for r in sel)
        residual = sum(D(r["residual_pnl"]) for r in sel)
        rebate = sum(D(r["rebate"]) for r in sel)
        cheap = [r for r in pairs if D(r["pair_cost"]) <= D("0.96")]
        dear = [r for r in pairs if D(r["pair_cost"]) > D("0.96")]
        out.append({"scenario": scen, "depth_ticks": depth,
                    "entries": len(sel), "pairs": len(pairs),
                    "residual": len(resid), "no_fill": len(nofill),
                    "pairs_at_or_below_096": len(cheap),
                    "pairs_above_096": len(dear),
                    "matched_pnl": str(matched.quantize(D("0.0001"))),
                    "residual_pnl": str(residual.quantize(D("0.0001"))),
                    "rebate": str(rebate.quantize(D("0.0001"))),
                    "net": str((matched + residual + rebate)
                               .quantize(D("0.0001"))),
                    "net_per_entry": str(((matched + residual + rebate)
                                          / D(len(sel)))
                                         .quantize(D("0.000001")))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=("oos", "in", "both"), default="oos")
    ap.add_argument("--ttl", type=float, default=0.0,
                    help="seconds; 0 means hold to the end of the recording")
    ap.add_argument("--rebate", default="0",
                    help="per filled contract; 0 until credited")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    root = os.path.join(os.path.dirname(__file__), "..", "..", "..")
    groups = {"oos": [("OUT OF SAMPLE", OUT_OF_SAMPLE)],
              "in": [("IN SAMPLE", IN_SAMPLE)],
              "both": [("IN SAMPLE", IN_SAMPLE),
                       ("OUT OF SAMPLE", OUT_OF_SAMPLE)]}[a.group]
    ttl = a.ttl or None
    allrows: List[Dict[str, Any]] = []
    for label, sources in groups:
        print("=" * 78)
        print("%s   TTL: %s   rebate/contract: %s"
              % (label, ("%.0fs" % ttl) if ttl else "to end of recording",
                 a.rebate))
        print("scenarios: " + ";  ".join(
            "%s = %s, queue ahead %s" % (n, arm, f)
            for n, arm, f in SCENARIOS))
        print("=" * 78)
        rows = []
        for d in DEPTHS:
            rows += replay(sources, depth_ticks=d, ttl_s=ttl,
                           rebate_per_fill=D(a.rebate), root=root)
        allrows += rows
        hdr = ("  d  scenario      entries  pairs  <=.96  >.96  resid  "
               "nofill   matched   residual    rebate       net   net/entry")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for s in summarise(rows):
            print("  %-2d %-13s %7d %6d %6d %5d %6d %7d %9s %10s %9s %9s %11s"
                  % (s["depth_ticks"], s["scenario"], s["entries"],
                     s["pairs"], s["pairs_at_or_below_096"],
                     s["pairs_above_096"], s["residual"], s["no_fill"],
                     s["matched_pnl"], s["residual_pnl"], s["rebate"],
                     s["net"], s["net_per_entry"]))
        print()
    print("BREAK-EVEN COMPLETION RATE, given the first leg filled")
    print("  pair cost   gross edge/ct   q* (second leg must fill)")
    for p in ("0.8700", "0.9300", "0.9400", "0.9500", "0.9600", "0.9800",
              "0.9900"):
        q = breakeven_completion_rate(p)
        print("  %-11s %-15s %s" % (p, str(D(1) - D(p)),
                                    ("%.1f%%" % (q * 100)) if q else "n/a"))
    print()
    print("NOT A REALIZED RESULT. No trade-print series exists for these")
    print("recordings, so every fill above is a BOUND from the ladder, and")
    print("every P&L is settled-equivalent: modelled fills, a measured")
    print("residual constant and a published fee schedule.")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"rows": allrows, "summary": summarise(allrows)}, fh)
        print("\nwrote", a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
