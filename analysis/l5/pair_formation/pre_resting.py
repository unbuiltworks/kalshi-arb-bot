"""Could L5's complement have been RESTING before his first fill? P2.

WHY IT MATTERS. If his two fills are two arrivals against orders he already had
on the book, then a clone that waits to see fill one before quoting leg two is
not running his policy at all -- it is chasing flow that has already gone. That
would produce naked residuals from a perfectly good behavioural model, which is
exactly what we observe. BTC15m's complement arrives a median 26 s after the
first leg but a p10 of 3 s, and a three-second complement is hard to explain by
reacting to anything.

THIS PRODUCES BOUNDS, NOT A CLAIM. We cannot see when L5 placed an order; the
venue publishes fills. So for each first/complement sequence we ask the only
answerable question: WHAT IS THE LATEST PLACEMENT TIME CONSISTENT WITH THE
OBSERVED FILL, and does that latest time fall before his first fill?

THE DISCRIMINATOR IS maker/taker, and the record carries it: 55,639 maker
fills against 2,665 taker across the history, so L5 is ~95 % maker.

  * A TAKER complement executes on submission. It was therefore submitted at
    the fill time, which is after the first leg -- REQUIRES_REACTIVE_PLACEMENT
    by construction. (An act, not a standing order being hit.)
  * A MAKER complement was hit by someone else, so the order was on the book
    BEFORE that trade arrived. The latest possible placement is the fill time
    minus the shortest round trip in which a new order could be placed and
    then hit.
  * If that latest possible placement falls before the first fill, the order
    CANNOT have been a reaction to it: CONSISTENT_WITH_PRE_RESTING.
  * Otherwise both stories fit and the honest answer is UNRESOLVED.

THE FLOOR IS STRUCTURAL, NOT A GUESS. A fill's timestamp is its block's
timestamp. A reactive placement needs the first fill's block, then a submission,
then an arriving taker in a LATER block -- so the complement cannot be reactive
if it shares a block interval with the first leg. Polygon's ~2.1 s block time is
therefore a hard lower bound on reactive latency, and it is reported alongside
more conservative judgement values rather than instead of them.

Read-only over public history. No claim is made about what L5 actually did.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence

# Polygon's nominal block time. A structural floor on reactive latency: the
# complement's block must be later than the first leg's.
BLOCK_S = 2.1
# reported together, so no single latency choice decides the answer
LATENCY_GRID = (BLOCK_S, 5.0, 10.0)

CLASSES = ("CONSISTENT_WITH_PRE_RESTING", "REQUIRES_REACTIVE_PLACEMENT",
           "UNRESOLVED")
# OUR measured observation lag: the venue publishes L5's fill this long after
# it happened. A separate question from L5's own latency, and the one that
# decides what OUR clone can do -- he reacts to his own fill instantly, we
# cannot react to it until it is published.
OUR_OBSERVATION_LAG_S = 15.16


def sequences(windows: Dict[str, List[dict]]) -> List[Dict[str, Any]]:
    """First leg and the complement that first makes the window two-sided."""
    out = []
    for fills in windows.values():
        if not fills:
            continue
        first = fills[0]
        comp = next((f for f in fills[1:]
                     if f["outcome"] != first["outcome"]), None)
        if comp is None:
            continue
        out.append({
            "market_slug": first["market_slug"],
            "first_side": first["outcome"], "first_price": first["price"],
            "first_t": float(first["timestamp"]),
            "first_maker_taker": first.get("maker_taker"),
            "comp_side": comp["outcome"], "comp_price": comp["price"],
            "comp_t": float(comp["timestamp"]),
            "comp_maker_taker": comp.get("maker_taker"),
            "gap_s": round(float(comp["timestamp"])
                           - float(first["timestamp"]), 1),
            "same_transaction": bool(
                comp.get("transaction_hash")
                and comp["transaction_hash"] == first.get("transaction_hash")),
        })
    return out


def classify(seq: Dict[str, Any], *, latency_s: float) -> Dict[str, Any]:
    """The bound, for one sequence, at one assumed reactive latency."""
    mt = str(seq.get("comp_maker_taker") or "unknown").lower()
    gap = float(seq["gap_s"])
    # the latest moment an order could have been placed and still produce this
    # fill; for a maker fill it must predate the counterparty's arrival
    latest_place = seq["comp_t"] - latency_s
    base = {"latency_assumed_s": latency_s,
            "latest_possible_placement_t": round(latest_place, 1),
            "latest_placement_before_first_fill":
                bool(latest_place < seq["first_t"]),
            # a maker fill happens AT the resting limit, so the price is not
            # free: it is exactly the fill price. A taker's limit is only
            # bounded at or above it.
            "implied_limit_price": seq["comp_price"],
            "price_bound": ("exactly the fill price: a maker fill executes at "
                            "its own resting limit"
                            if mt == "maker" else
                            "at or beyond the fill price: a taker's limit is "
                            "only bounded on one side")}
    if mt == "taker":
        return {**base, "bound": "REQUIRES_REACTIVE_PLACEMENT",
                "why": ("a taker order executes on submission, so it was "
                        "submitted at the fill -- after the first leg. An act, "
                        "not a standing order being hit")}
    if mt != "maker":
        return {**base, "bound": "UNRESOLVED",
                "why": ("the venue did not publish whether this fill was maker "
                        "or taker, and the whole discriminator rests on it")}
    if gap < latency_s:
        return {**base, "bound": "CONSISTENT_WITH_PRE_RESTING",
                "why": (f"a maker fill {gap}s after the first leg, against a "
                        f"{latency_s}s floor on placing an order and having it "
                        f"hit: it could not have been a reaction to the first "
                        f"leg, so it was already resting")}
    return {**base, "bound": "UNRESOLVED",
            "why": (f"a maker fill {gap}s after the first leg leaves room for "
                    f"either story at a {latency_s}s floor: already resting, "
                    f"or placed in response and then hit")}


def report(windows: Dict[str, List[dict]]) -> Dict[str, Any]:
    seqs = sequences(windows)
    grid: Dict[str, Any] = {}
    for L in LATENCY_GRID:
        rows = [classify(s, latency_s=L) for s in seqs]
        c = Counter(r["bound"] for r in rows)
        grid[f"latency_{L}s"] = {
            **{k: c.get(k, 0) for k in CLASSES},
            "pre_resting_rate": (round(c.get("CONSISTENT_WITH_PRE_RESTING", 0)
                                       / len(rows), 4) if rows else None),
            "is_structural_floor": L == BLOCK_S,
        }
    mt = Counter(str(s.get("comp_maker_taker") or "unknown") for s in seqs)
    gaps = sorted(s["gap_s"] for s in seqs)
    # A DIFFERENT QUESTION, AND THE ONE THAT BINDS US. Whether L5 was
    # pre-resting is about him. Whether a reactive clone could have placed the
    # complement AT ALL is about our observation lag, and it does not depend on
    # resolving his behaviour: if the complement filled before we could even
    # learn of the first leg, no reactive policy of ours reaches it.
    unreachable = [s for s in seqs if s["gap_s"] < OUR_OBSERVATION_LAG_S]
    return {
        "sequences": len(seqs),
        "complement_maker_taker": dict(mt),
        "gap_s": ({"p10": gaps[int(len(gaps) * 0.1)],
                   "median": gaps[len(gaps) // 2],
                   "p90": gaps[int(len(gaps) * 0.9)]} if gaps else None),
        "same_transaction": sum(1 for s in seqs if s["same_transaction"]),
        "by_assumed_latency": grid,
        "unreachable_by_a_reactive_clone": {
            "n": len(unreachable), "of": len(seqs),
            "rate": (round(len(unreachable) / len(seqs), 4) if seqs else None),
            "our_observation_lag_s": OUR_OBSERVATION_LAG_S,
            "why": ("the complement filled before the venue published the "
                    "first leg, so no reactive policy of ours could have "
                    "placed it in time. Independent of whether L5 was "
                    "pre-resting: this is our lag, not his behaviour"),
        },
        "block_s": BLOCK_S,
        "labels": (
            "BOUNDS, not a claim. The venue publishes fills, not placements, "
            "so the answerable question is the LATEST placement consistent "
            "with the observed fill. CONSISTENT_WITH_PRE_RESTING means a "
            "reaction is ruled out, not that pre-resting is proved. The "
            f"{BLOCK_S}s row is a structural floor -- a reactive complement "
            "needs a later block than the first leg -- and the others are "
            "judgement, reported so no single latency choice decides it."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    from analysis.l5.pair_formation import btc15m as B
    ws = B.load(a.seq or B.SEQ, limit=a.limit or None)
    print(json.dumps(report(ws), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
