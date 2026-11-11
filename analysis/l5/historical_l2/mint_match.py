"""Were L5's 'impossible' maker buys complement mints rather than book fills?

THE OBSERVATION THAT FORCED THIS. Every L5 maker fill in the overlap is a BUY
(the log's makerAssetId is collateral, takerAssetId the token). Yet 26% of them
price at or ABOVE the prevailing ask one second earlier, and almost none land
INSIDE the spread -- 0.8%. Drift would smear rows across the middle. A clean
bimodal split says two different mechanisms, not one noisy one.

THE CANDIDATE MECHANISM. On this venue two BUYERS of opposite outcomes can be
matched by minting a complete set: each pays their price, $1 is minted, and
they take one token each. It needs NO offer on the buyer's own book -- only a
counterparty bidding the complement at 1-p or better. So the test is:

    p_fill + best_bid(complement) >= 1.00   -> a mint was feasible

WHAT WOULD FALSIFY IT. If the at/above-ask fills and the resting-bid fills have
the same feasibility rate, the mechanism does not separate them and this
explanation is wrong. The two rates are reported side by side for exactly that
reason, and the complement's book is read at the SAME lead as the fill's own.

NOTHING HERE IDENTIFIES ANYONE'S ORDER. `best_bid(complement)` is a price level
in a public book. It is not asserted to be L5's, nor his counterparty's.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import (Book, MAX_CARRY_S,
                                                  OFFSET_AT_S,
                                                  placement_consistency)

PAR = D("1.00")


def complements(d: dict) -> Dict[str, str]:
    """token -> the other token in the same market. From the dataset itself."""
    by_slug: Dict[str, set] = defaultdict(set)
    for i, coin in enumerate(d["coin"]):
        if coin == "btc":
            by_slug[d["market_slug"][i]].add(str(d["token_id"][i]))
    out: Dict[str, str] = {}
    for toks in by_slug.values():
        if len(toks) == 2:
            a, b = sorted(toks)
            out[a], out[b] = b, a
    return out


def feasible(p: Any, comp_bid: Any) -> Optional[bool]:
    """Could a complete-set mint have paid for this fill?"""
    if comp_bid is None:
        return None
    try:
        return (D(str(p)) + D(str(comp_bid))) >= PAR
    except Exception:                                         # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times",
                    default="analysis/l5/historical_l2/block_times.json")
    ap.add_argument("--out",
                    default="analysis/l5/historical_l2/mint_match.json")
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

    rows: List[Dict[str, Any]] = []
    for w in bf["rows"]:
        for lg in w["fills"]:
            if lg.get("l5_role") != "maker":
                continue
            t = bt.get(str(lg.get("block")))
            tok = str(lg["token_id"])
            if t is None or tok not in books:
                continue
            t = float(t)
            row: Dict[str, Any] = {"tx": lg["tx"], "token_id": tok,
                                   "price": lg["price"], "size": lg["size"],
                                   "market_slug": w["market_slug"]}
            for lead in OFFSET_AT_S:
                own = books[tok].at(t - lead)
                cls = placement_consistency(own, lg["price"])
                c_tok = comp.get(tok)
                cb = books[c_tok].at(t - lead) if c_tok in books else None
                cbid = None if cb is None else cb["best_bid"]
                row[f"T-{lead}"] = {
                    "class": cls,
                    "own_best_bid": None if own is None else own["best_bid"],
                    "own_best_ask": None if own is None else own["best_ask"],
                    "complement_best_bid": cbid,
                    "pair_sum": (None if cbid is None
                                 else str(D(str(lg["price"])) + D(str(cbid)))),
                    "mint_feasible": feasible(lg["price"], cbid),
                }
            rows.append(row)

    rep: Dict[str, Any] = {"n_maker_fills": len(rows), "by_lead": {}}
    for lead in OFFSET_AT_S:
        k = f"T-{lead}"
        tab: Dict[str, Any] = {}
        for cls in ("RESTING_BID_CONSISTENT", "AT_OR_ABOVE_ASK_NOT_MEASURABLE",
                    "INSIDE_SPREAD_IMPROVED_THE_TOUCH"):
            sub = [r for r in rows if r[k]["class"] == cls]
            known = [r for r in sub if r[k]["mint_feasible"] is not None]
            yes = sum(1 for r in known if r[k]["mint_feasible"])
            ps = sorted(D(r[k]["pair_sum"]) for r in known)
            tab[cls] = {
                "n": len(sub), "with_complement_book": len(known),
                "mint_feasible": yes,
                "mint_feasible_share": (round(yes / len(known), 4)
                                        if known else None),
                "pair_sum_p05": (str(ps[int(0.05 * (len(ps) - 1))]) if ps else None),
                "pair_sum_p50": (str(ps[len(ps) // 2]) if ps else None),
                "pair_sum_p95": (str(ps[int(0.95 * (len(ps) - 1))]) if ps else None),
            }
        rep["by_lead"][k] = tab
    rep["labels"] = (
        "'mint_feasible' means a complete-set mint COULD have paid for the "
        "fill at the complement's posted bid. It does not assert that is what "
        "happened, and no order here is attributed to any trader. pair_sum is "
        "fill price + the complement's best bid, a public price level.")
    rep["rows"] = rows[:120]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(json.dumps(rep["by_lead"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
