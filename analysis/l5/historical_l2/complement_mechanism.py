"""When L5's matched inventory GROWS, what mechanism delivered the share?

Item 3 of the acquisition question: does the complement arrive because a
moving price reaches an order he already had, or because he crosses for it?

METHOD. Walk his fills in chain order (block, then log index) and track
yes/no share counts. A fill is a MATCHING fill to the extent it raises
min(yes, no); only that increment is attributed, so a fill that merely extends
the long side is not counted as matching at all. Each matching increment is
labelled by the mechanism of the fill that produced it:

  REACHED_RESTING   maker fill at or below the prevailing bid 1 s earlier
  REACHED_MINT      maker fill at or above the prevailing ask, complement bid
                    at par -- a complete-set mint could have produced it
  CROSSED_PAR_PAIR  taker, inside a transaction carrying both outcomes
  CROSSED_SINGLE    taker, one-sided transaction
  UNKNOWN_BOOK      no book within carry distance

TIME TO COMPLEMENT is measured per matching increment: from the block time of
the earliest still-unmatched long-side fill to the block time of the matching
fill. Block timestamps are real, and their median gap (about 3 s) is the floor
on this measurement's resolution.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List

from analysis.l5.historical_l2.bbo_offset import Book, placement_consistency
from analysis.l5.historical_l2.mint_match import complements

ONE = D("1")
LEAD = 1.0


def _pct(xs: List[float], q: float):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1)))], 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/august_replay/block_times.json")
    ap.add_argument("--out", default="analysis/l5/historical_l2/"
                                     "complement_mechanism.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        books[str(d["token_id"][i])].add(
            d["captured_at"][i].timestamp(),
            {"best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i]})
    for b in books.values():
        b.sort()
    comp = complements(d)
    bt = {int(k): float(v) for k, v in
          json.load(open(a.block_times))["block_times"].items()}
    bf = json.load(open(a.backfill))

    tx_tokens: Dict[str, set] = defaultdict(set)
    for w in bf["rows"]:
        for lg in w["fills"]:
            tx_tokens[lg["tx"]].add(str(lg["token_id"]))
    atomic = {h for h, toks in tx_tokens.items()
              if any(comp.get(t) in toks for t in toks)}

    matched_by: Counter = Counter()
    waits: Dict[str, List[float]] = defaultdict(list)
    fills_per_market: List[int] = []

    for w in bf["rows"]:
        fills = sorted(w["fills"],
                       key=lambda l: (int(l.get("block") or 0),
                                      int(l.get("log_index") or 0)))
        fills_per_market.append(len(fills))
        held: Dict[str, List[list]] = defaultdict(list)   # token -> [[qty, t]]
        for lg in fills:
            tok = str(lg["token_id"])
            ctok = comp.get(tok)
            blk = int(lg.get("block") or 0)
            t = bt.get(blk)
            qty = D(str(lg["size"]))

            if lg.get("l5_role") == "taker":
                mech = ("CROSSED_PAR_PAIR" if lg["tx"] in atomic
                        else "CROSSED_SINGLE")
            elif t is None or tok not in books:
                mech = "UNKNOWN_BOOK"
            else:
                snap = books[tok].at(t - LEAD)
                cls = placement_consistency(snap, lg["price"])
                if cls == "AT_OR_ABOVE_ASK_NOT_MEASURABLE":
                    cb = (books[ctok].at(t - LEAD)
                          if ctok in books else None)
                    bid = None if not cb else cb.get("best_bid")
                    mech = ("REACHED_MINT" if bid is not None and
                            D(str(lg["price"])) + D(str(bid)) >= ONE
                            else "UNKNOWN_BOOK")
                elif cls == "NO_BOOK":
                    mech = "UNKNOWN_BOOK"
                else:
                    mech = "REACHED_RESTING"

            # how much of this fill MATCHES against unmatched complement?
            left = qty
            while left > 0 and ctok and held.get(ctok):
                lot = held[ctok][0]
                take = min(left, lot[0])
                matched_by[mech] += float(take)
                if t is not None and lot[1] is not None:
                    waits[mech].append(t - lot[1])
                lot[0] -= take
                left -= take
                if lot[0] <= 0:
                    held[ctok].pop(0)
            if left > 0:
                held[tok].append([left, t])

    tot = sum(matched_by.values()) or 1.0
    reached = sum(v for k, v in matched_by.items() if k.startswith("REACHED"))
    crossed = sum(v for k, v in matched_by.items() if k.startswith("CROSSED"))
    rep: Dict[str, Any] = {
        "markets": len(fills_per_market),
        # the median over ALL backfilled windows is 0 because he is not in
        # every market; the figure that means anything is over the ones he
        # actually traded, so both are reported rather than the flattering one
        "markets_with_any_fill": sum(1 for x in fills_per_market if x),
        "fills_per_market_p50_all_windows": _pct(
            [float(x) for x in fills_per_market], 0.5),
        "fills_per_market_p50_where_he_traded": _pct(
            [float(x) for x in fills_per_market if x], 0.5),
        "matched_shares_total": round(tot, 1),
        "by_mechanism": {
            k: {"matched_shares": round(v, 1),
                "share_of_matched": round(v / tot, 4),
                "time_to_complement_s_p50": _pct(waits[k], 0.50),
                "time_to_complement_s_p90": _pct(waits[k], 0.90)}
            for k, v in matched_by.most_common()},
        "headline": {
            "reached_share_of_matched": round(reached / tot, 4),
            "crossed_share_of_matched": round(crossed / tot, 4),
        },
        "LABEL": "OBSERVED_FILLS_NOT_L5_ORDERS",
        "labels": ("Matching is FIFO against the unmatched complement lots "
                   "he actually held; only the matching increment of a fill "
                   "is attributed, so an extend is not counted as matching. "
                   "REACHED_MINT is mint-FEASIBLE, never confirmed. "
                   "CROSSED_PAR_PAIR is confirmed from both of his own legs "
                   "in one transaction. Resolution is bounded below by the "
                   "block gap, median about 3 s."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
