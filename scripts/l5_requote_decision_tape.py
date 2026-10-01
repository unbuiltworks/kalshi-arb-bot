"""When the market moves away from L5's previous execution level while he is
IMBALANCED, what does he do next?

This is the behavioural question V1's stale-quote failure raised: our engine holds
its resting price by design (`_still_valid` returns True unconditionally on the
non-deficient side), and 98.4% of its modelled fills sat a median 33c through the
market. If L5 re-anchors instead, the patience thesis is wrong.

REUSES EXISTING EVIDENCE ONLY. No new collection, no recorder, no live trading.
  * L5 actions: data/l5/sep2026/*.json.gz (145,050 BTC15m fill rows)
  * fragment grouping: scripts.l5_behavior_census.actions_by_market, unchanged
  * latency: data/l5/ops/activity_20260901.jsonl, which carries BOTH the venue
    `timestamp` and our `_collected_utc`

WHAT IS NOT AVAILABLE, stated rather than invented: there is no Polymarket L2 book
tape aligned to L5's action times, so contemporaneous BBO, depth and
distance-from-touch are UNAVAILABLE. The market signal used instead is L5's own
execution price path, which is an observable, not a book. Every figure derived
from it is labelled a PROXY and never called a touch.

    python3 scripts/l5_requote_decision_tape.py
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from decimal import Decimal as D

sys.path.insert(0, ".")

from scripts.l5_behavior_census import actions_by_market          # noqa: E402

SRC = ("data/l5/sep2026/l5_activity_sep2026_raw.json.gz",
       "data/l5/sep2026/l5_activity_sep2223_raw.json.gz")
REALTIME = "data/l5/ops/activity_20260901.jsonl"
OUT = "analysis/l5/requote/DECISION_TAPE.jsonl"


def load_rows():
    rows = []
    for f in SRC:
        if not os.path.exists(f):
            continue
        d = json.load(gzip.open(f))
        rows.extend(d if isinstance(d, list) else d.get("rows") or [])
    return [r for r in rows if isinstance(r, dict)]


def latency():
    """Collection lag: our recorded collection time MINUS the venue event time.

    MEASURED, NOT ASSUMED, and the measurement says this is NOT a realtime feed:
    the median lag is hours, so `_collected_utc` is a BATCH collection stamp, not
    a realtime receipt. Consequently L5's event ordering comes only from the
    venue `timestamp`, and no reaction latency of L5's can be read from this
    evidence at all. A lag of hours is certainly not L5 acting hours later.
    """
    import datetime as dt
    if not os.path.exists(REALTIME):
        return {"available": False}
    lags = []
    for line in open(REALTIME):
        try:
            r = json.loads(line)
            ev = float(r["timestamp"])
            got = dt.datetime.fromisoformat(
                str(r["_collected_utc"]).replace("Z", "+00:00")).timestamp()
        except Exception:                                        # noqa: BLE001
            continue
        lags.append(got - ev)
    if not lags:
        return {"available": False}
    lags.sort()
    def q(p): return lags[min(len(lags) - 1, int(len(lags) * p))]
    return {"available": True, "n": len(lags), "min_s": round(lags[0], 1),
            "p10_s": round(q(.10), 1), "median_s": round(q(.50), 1),
            "p90_s": round(q(.90), 1), "p99_s": round(q(.99), 1),
            "max_s": round(lags[-1], 1),
            "note": ("COLLECTION LAG, not realtime latency: the median is "
                     "hours, so this is a batch pull. L5's ordering comes from "
                     "the venue timestamp alone, and his reaction time is NOT "
                     "measurable from this evidence."),
            "realtime_feed": False}


#: the venue grid. 21.4% of activity prices arrive off it (0.8099999757 for
#: 0.81), so a raw equality test on price compares FLOATING-POINT NOISE, not
#: price levels, and silently corrupts the same-price-versus-new-price split
#: this whole analysis turns on. Every price is snapped to the grid first.
GRID = D("0.01")


def own(outcome: str, px: D) -> D:
    """Price in the acting side's OWN terms, SNAPPED TO THE VENUE GRID.

    Activity rows price the outcome they name, so Up and Down are each already
    in own terms; only the grid snap is applied.
    """
    return D(str(px)).quantize(GRID)


def build(mkt):
    """Per-market action tape with inventory replayed in action order."""
    tape = []
    for slug, acts in mkt.items():
        up_q = dn_q = D(0)
        prev = None
        prev_same = {}                     # last action price per outcome
        for i, a in enumerate(acts):
            side = "up" if str(a["outcome"]).lower().startswith("u") else "down"
            px, size = own(a["outcome"], a["px"]), D(str(a["size"]))
            before = {"up": up_q, "down": dn_q,
                      "residual": up_q - dn_q,
                      "matched": min(up_q, dn_q)}
            if str(a.get("side") or "BUY").upper() == "BUY":
                if side == "up":
                    up_q += size
                else:
                    dn_q += size
            else:
                if side == "up":
                    up_q -= size
                else:
                    dn_q -= size
            row = {
                "slug": slug, "seq": i, "t": a["t"], "side": side,
                "px": str(px), "size": str(size),
                "fragments": a["fragments"],
                "action_side": str(a.get("side") or "BUY").upper(),
                "inv_before": {k: str(v) for k, v in before.items()},
                "inv_after": {"up": str(up_q), "down": str(dn_q),
                              "residual": str(up_q - dn_q),
                              "matched": str(min(up_q, dn_q))},
                "imbalanced_before": before["residual"] != 0,
                "imbalanced_after": (up_q - dn_q) != 0,
                "gap_s": (None if prev is None else a["t"] - prev["t"]),
                "prev_side": (None if prev is None else prev["side"]),
                "prev_px": (None if prev is None else str(prev["px"])),
                #: PROXY, not a touch: his own last execution on THIS side
                "prev_same_side_px": (str(prev_same[side])
                                      if side in prev_same else None),
                "d_px_vs_prev_same_side": (
                    str(px - prev_same[side]) if side in prev_same else None),
                "bbo": "UNAVAILABLE_NO_ALIGNED_BOOK_TAPE",
                "depth": "UNAVAILABLE_NO_ALIGNED_BOOK_TAPE",
                "distance_from_touch": "UNAVAILABLE_NO_ALIGNED_BOOK_TAPE",
            }
            tape.append(row)
            prev = {"t": a["t"], "side": side, "px": px}
            prev_same[side] = px
        #: label each action with what L5 did NEXT in the same market
        for j in range(len(tape) - len(acts), len(tape)):
            nxt = tape[j + 1] if (j + 1) < len(tape) and \
                tape[j + 1]["slug"] == slug else None
            if nxt is None:
                tape[j]["next_action"] = None
                continue
            same = nxt["side"] == tape[j]["side"]
            dpx = D(nxt["px"]) - D(tape[j]["px"])
            tape[j]["next_action"] = {
                "same_side": same, "gap_s": nxt["gap_s"],
                "d_px": str(dpx),
                "klass": ("D_OPPOSITE_SIDE" if not same else
                          ("B_SAME_SIDE_SAME_PRICE" if dpx == 0 else
                           "A_SAME_SIDE_NEW_PRICE")),
                "direction": ("flat" if dpx == 0 else
                              ("up" if dpx > 0 else "down")),
            }
    return tape


def main() -> int:
    rows = load_rows()
    mkt = actions_by_market(rows, kind="btc-updown-15m")
    tape = build(mkt)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        for r in tape:
            fh.write(json.dumps(r, default=str) + "\n")

    lat = latency()
    print("=" * 96)
    print("OBSERVATION LATENCY (our receipt MINUS venue event time)")
    print("=" * 96)
    print(json.dumps(lat, indent=1))

    print("\n" + "=" * 96)
    print(f"DECISION TAPE: {len(tape)} actions over {len(mkt)} BTC15m markets")
    print(f"written to {OUT}")
    print("=" * 96)
    print("contemporaneous BBO / depth / distance-from-touch: "
          "UNAVAILABLE (no aligned book tape). Market signal is L5's own "
          "execution price path, a PROXY.")

    #: THE QUESTION. Conditional on being imbalanced, what does he do next?
    for cond, label in ((True, "IMBALANCED (residual != 0 after the action)"),
                        (False, "FLAT (residual == 0)")):
        sub = [r for r in tape
               if r["imbalanced_after"] is cond and r["next_action"]]
        if not sub:
            continue
        k = Counter(r["next_action"]["klass"] for r in sub)
        n = len(sub)
        print(f"\n  {label}: n = {n}")
        for name in ("A_SAME_SIDE_NEW_PRICE", "B_SAME_SIDE_SAME_PRICE",
                     "D_OPPOSITE_SIDE"):
            print("    %-26s %6d  %5.1f%%" % (name, k[name],
                                              100.0 * k[name] / n))

    #: of the same-side continuations while imbalanced, how far does he move?
    moves = [abs(D(r["next_action"]["d_px"])) for r in tape
             if r["imbalanced_after"] and r["next_action"]
             and r["next_action"]["same_side"]]
    if moves:
        moves.sort()
        def q(p): return moves[min(len(moves) - 1, int(len(moves) * p))]
        print(f"\n  SAME-SIDE re-price distance while imbalanced, n = {len(moves)}")
        for p in (.10, .25, .50, .75, .90, .99):
            print("    p%-3d  %s" % (p * 100, q(p)))
        print("    share at EXACTLY the old price: %.1f%%" % (
            100.0 * sum(1 for m in moves if m == 0) / len(moves)))

    #: STATE DEPENDENCE: does the choice change with how imbalanced he is?
    print("\n" + "=" * 96)
    print("STATE DEPENDENCE: A / B / D by absolute residual at the time")
    print("=" * 96)
    buckets = ((D(0), D(5)), (D(5), D(20)), (D(20), D(100)),
               (D(100), D(500)), (D(500), D("1e12")))
    print("  %-18s%8s%10s%10s%10s" % ("|residual|", "n", "A new px",
                                      "B same px", "D opposite"))
    for lo, hi in buckets:
        sub = [r for r in tape if r["next_action"]
               and lo <= abs(D(r["inv_after"]["residual"])) < hi]
        if not sub:
            continue
        k = Counter(r["next_action"]["klass"] for r in sub)
        n = len(sub)
        print("  %-18s%8d%9.1f%%%9.1f%%%9.1f%%" % (
            f"[{lo}, {hi})" if hi < D("1e12") else f">= {lo}", n,
            100.0 * k["A_SAME_SIDE_NEW_PRICE"] / n,
            100.0 * k["B_SAME_SIDE_SAME_PRICE"] / n,
            100.0 * k["D_OPPOSITE_SIDE"] / n))

    #: and the gap distribution, so "next" is not read as "immediately"
    gaps = sorted(r["next_action"]["gap_s"] for r in tape
                  if r["imbalanced_after"] and r["next_action"]
                  and r["next_action"]["gap_s"] is not None)
    if gaps:
        def qg(p): return gaps[min(len(gaps) - 1, int(len(gaps) * p))]
        print(f"\n  SECONDS to the next action while imbalanced, n = {len(gaps)}")
        for p in (.10, .50, .90, .99):
            print("    p%-3d  %ss" % (p * 100, qg(p)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
