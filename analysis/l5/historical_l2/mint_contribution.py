"""How much of L5's matched inventory arrives through each acquisition channel?

`mint_match.py` answered a yes/no question per fill: COULD a complete-set mint
have paid for it. This answers the quantitative one the question implies: of
the shares L5 actually bought, and of the shares that actually ended up
MATCHED, how many arrived on each channel?

CHANNELS, defined only by what the public record can separate:

  RESTING_BID        maker fill at or below the prevailing best bid one second
                     earlier -- consistent with a resting order being reached.
  MINT_FEASIBLE      maker fill at or ABOVE the prevailing best ask, where the
                     complement's posted bid made p + bid(complement) >= $1.
                     A complete-set mint could have produced it. NOT confirmed.
  AT_ASK_NO_MINT     at or above the ask, but the complement's bid did not
                     reach par. Neither channel explains it.
  ATOMIC_PAR_PAIR    L5 is the taker leg AND the same transaction carries his
                     taker fill on the complement. Both legs settle in one
                     atomic transaction and the size-weighted pair price is
                     read off the chain, not assumed. This is a CONFIRMED
                     complete-set acquisition, not a feasible one.
  TAKER_SINGLE       taker leg with no complement leg in the transaction.
  UNCLASSIFIED       no book within carry distance at the lead.

CONFIRMED vs FEASIBLE. A MINT_FEASIBLE maker fill is not confirmed: proving a
mint paid for it needs the counterparty's legs, and the stored backfill keeps
only L5's own logs. ATOMIC_PAR_PAIR is different and is confirmed, because both
of its legs are L5's own and both are in the backfill.

A CORRECTION THIS SCRIPT FORCED. An earlier transaction-level test over the
760,817-fill activity corpus found ZERO transactions carrying L5 on both
outcomes and concluded he never acquires both legs at once. That conclusion was
wrong. The activity API reports only ONE leg of an atomic two-leg transaction:
for all 145 transactions the chain shows two-sided here, the corpus carries a
single leg and a single outcomeIndex. The API cannot answer the question; only
the chain logs can.

MATCHED inventory is min(up_shares, down_shares) per market, from L5's own
fills in the window. Channel attribution of matched shares is proportional to
each channel's share of purchases on the scarcer side -- matching does not
record which fill paired with which, so no stronger claim is made.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, Optional

from analysis.l5.historical_l2.bbo_offset import Book, placement_consistency
from analysis.l5.historical_l2.mint_match import complements

PAR = D("1.00")
LEAD = 1.0          # the lead mint_match reports as most informative


def channel(cls: str, price: Any, comp_bid: Any) -> str:
    if cls == "RESTING_BID_CONSISTENT":
        return "RESTING_BID"
    if cls == "INSIDE_SPREAD_IMPROVED_THE_TOUCH":
        return "RESTING_BID"
    if cls != "AT_OR_ABOVE_ASK_NOT_MEASURABLE":
        return "UNCLASSIFIED"
    if comp_bid is None:
        return "AT_ASK_NO_MINT"
    return ("MINT_FEASIBLE"
            if D(str(price)) + D(str(comp_bid)) >= PAR else "AT_ASK_NO_MINT")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/august_replay/block_times.json")
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/mint_contribution.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        books[str(d["token_id"][i])].add(
            d["captured_at"][i].timestamp(),
            {"best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
             "bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i],
             "seconds_to_close": d["seconds_to_close"][i]})
    for b in books.values():
        b.sort()
    comp = complements(d)
    bt = json.load(open(a.block_times))["block_times"]
    bf = json.load(open(a.backfill))

    # ---- on-chain self-mint test, within the overlap -------------------
    tx_tokens: Dict[str, set] = defaultdict(set)
    for w in bf["rows"]:
        for lg in w["fills"]:
            tx_tokens[lg["tx"]].add(str(lg["token_id"]))
    atomic_pair = {h for h, toks in tx_tokens.items()
                   if any(comp.get(t) in toks for t in toks)}
    # size-weighted pair price of each atomic two-sided transaction
    legs: Dict[str, Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for w in bf["rows"]:
        for lg in w["fills"]:
            if lg["tx"] in atomic_pair:
                legs[lg["tx"]][str(lg["token_id"])].append(lg)
    pair_px: Counter = Counter()
    for h, by_tok in legs.items():
        ts = sorted(by_tok)
        if len(ts) != 2:
            continue
        tot = D(0)
        for t in ts:
            sh = sum(D(str(l["size"])) for l in by_tok[t])
            cost = sum(D(str(l["size"])) * D(str(l["price"]))
                       for l in by_tok[t])
            tot += cost / sh
        pair_px[str(tot.quantize(D("0.01")))] += 1
    two_sided = len(atomic_pair)
    self_mint = {
        "distinct_tx": len(tx_tokens),
        "tx_with_l5_on_both_outcomes": two_sided,
        "atomic_pair_price_histogram": dict(pair_px),
        "verdict": ("NO_ATOMIC_PAIR_OBSERVED" if two_sided == 0
                    else "ATOMIC_COMPLETE_SET_PURCHASE_CONFIRMED"),
    }

    # ---- channel attribution of purchased shares -----------------------
    per_market: Dict[str, Any] = {}
    tot_shares: Counter = Counter()
    tot_cost: Dict[str, D] = defaultdict(lambda: D(0))
    for w in bf["rows"]:
        side_shares: Dict[str, D] = defaultdict(lambda: D(0))
        side_chan: Dict[str, Counter] = defaultdict(Counter)
        for lg in w["fills"]:
            tok = str(lg["token_id"])
            sz = D(str(lg["size"]))
            if lg.get("l5_role") == "taker":
                ch = ("ATOMIC_PAR_PAIR" if lg["tx"] in atomic_pair
                      else "TAKER_SINGLE")
            else:
                t = bt.get(str(lg.get("block")))
                if t is None or tok not in books:
                    ch = "UNCLASSIFIED"
                else:
                    own = books[tok].at(float(t) - LEAD)
                    cls = placement_consistency(own, lg["price"])
                    c_tok = comp.get(tok)
                    cb = (books[c_tok].at(float(t) - LEAD)
                          if c_tok in books else None)
                    ch = channel(cls, lg["price"],
                                 None if cb is None else cb["best_bid"])
            side_shares[tok] += sz
            side_chan[tok][ch] += sz
            tot_shares[ch] += int(sz)
            tot_cost[ch] += sz * D(str(lg["price"]))

        toks = list(side_shares)
        if len(toks) == 2:
            matched = min(side_shares[toks[0]], side_shares[toks[1]])
            scarce = min(toks, key=lambda t: side_shares[t])
        else:
            matched, scarce = D(0), (toks[0] if toks else None)
        per_market[w["market_slug"]] = {
            "shares_by_token": {t: str(v) for t, v in side_shares.items()},
            "matched_shares": str(matched),
            "purchased_shares": str(sum(side_shares.values())),
            "matched_frac": (float(2 * matched / sum(side_shares.values()))
                             if sum(side_shares.values()) else None),
            "scarce_side_channels": ({k: str(v)
                                      for k, v in side_chan[scarce].items()}
                                     if scarce else {}),
        }

    # matched-share attribution: proportional on the scarcer side
    matched_attr: Dict[str, float] = defaultdict(float)
    for m in per_market.values():
        tot = sum(D(v) for v in m["scarce_side_channels"].values())
        if not tot:
            continue
        mt = D(m["matched_shares"])
        for ch, v in m["scarce_side_channels"].items():
            matched_attr[ch] += float(mt * D(v) / tot)

    all_sh = sum(tot_shares.values()) or 1
    all_mt = sum(matched_attr.values()) or 1.0
    rep = {
        "lead_s": LEAD,
        "atomic_pair_test_overlap": self_mint,
        "purchased_shares_by_channel": {
            ch: {"shares": n, "share_of_purchases": round(n / all_sh, 4),
                 "avg_price": str((tot_cost[ch] / D(n)).quantize(D("0.0001")))
                 if n else None}
            for ch, n in tot_shares.most_common()},
        "matched_shares_by_channel": {
            ch: {"matched_shares": round(v, 1),
                 "share_of_matched": round(v / all_mt, 4)}
            for ch, v in sorted(matched_attr.items(),
                                key=lambda kv: -kv[1])},
        "markets": len(per_market),
        "per_market": per_market,
        "labels": (
            "No mint is confirmed: the stored backfill keeps only L5's own "
            "logs, so the complementary buyer and the $1 mint of a candidate "
            "transaction are not observable here. MINT_FEASIBLE means a mint "
            "COULD have paid for the fill at the complement's posted bid. "
            "Matched-share attribution is proportional on the scarcer side; "
            "fill-to-fill pairing is not recorded by the venue. "
            "ATOMIC_PAR_PAIR is confirmed from both of L5's own legs in one "
            "transaction; its pair price is read off the chain. The activity "
            "API reports only one leg of such a transaction and cannot be "
            "used for this test."),
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps({k: rep[k] for k in
                      ("atomic_pair_test_overlap",
                       "purchased_shares_by_channel",
                       "matched_shares_by_channel", "markets")},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
