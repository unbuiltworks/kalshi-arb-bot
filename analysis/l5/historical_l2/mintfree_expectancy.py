"""TEST A: does L5's MINT-FREE acquisition have positive full-inventory
expectancy?

THE QUESTION, AND WHY SUBTRACTION WOULD NOT ANSWER IT.

`mint_contribution.py` showed that ~40.5% of L5's purchased shares arrive on
two channels PMUS does not offer: MINT_FEASIBLE (24.1%) and the CONFIRMED
ATOMIC_PAR_PAIR (16.4%). Every economic figure we hold mixes all four
channels, so none of them prices the part PMUS could actually execute.

It is tempting to take aggregate P&L and subtract the mint channels' share.
That is wrong, and the error is not small. Matching is a SEQUENCE: a
RESTING_BID purchase of UP was frequently paired against a DOWN share that
arrived by mint. Remove the mint fills and that UP share has NO COMPLEMENT --
it becomes directional residual carried to settlement, not a matched pair with
its profit removed. The mint channel was not only buying shares, it was
PROVIDING THE OTHER SIDE. So the only honest method is to replay the
remaining fills in order and let inventory, matching and residual fall out.

WHAT IS REPLAYED

Fills whose channel is RESTING_BID, in block order, per market. Inventory is
carried per token. Matching is FIFO against the opposite outcome's unmatched
lots, which is the strongest claim the record supports: the chain does not say
which fill paired with which, but it does say the order they arrived in.

ACCOUNTING, FULL INVENTORY

  matched pair   pays exactly $1.00 at settlement, whatever the outcome.
                 edge = 1.00 - (cost_up + cost_down).
  residual       pays $1.00 if its token is the winner and $0.00 otherwise,
                 from the parquet's own `is_winning_outcome` -- VENUE-SOURCED,
                 not inferred from a terminal book.

Nothing is marked to market. Nothing is netted against incentives: rebates and
rewards are not computed here at all, because this file answers whether the
TRADING is viable on its own.

OBSERVED vs HYPOTHETICAL, kept apart

The headline figures are OBSERVED L5 economics on L5's venue: Polymarket CLOB,
whose trading fee on these markets was zero. PMUS execution is a DIFFERENT
question and appears only in the sensitivity section, where the PMUS taker and
maker fee coefficients are applied to the same share path. A PMUS figure is
never presented as something L5 earned.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import Book, placement_consistency
from analysis.l5.historical_l2.mint_contribution import channel
from analysis.l5.historical_l2.mint_match import complements

LEAD = 1.0
PAR = D("1.00")

#: PMUS fee coefficients, for the sensitivity arm only. Taker since
#: 2026-09-17; maker as captured. These are NOT L5's fees.
PMUS_TAKER_COEF = D("0.0695")
PMUS_MAKER_COEF = D("0.0125")


def pmus_fee(price: D, qty: D, coef: D) -> D:
    """PMUS's own fee form: coefficient x qty x p x (1-p)."""
    return (coef * qty * price * (PAR - price)).quantize(D("0.000001"))


class Market:
    """Inventory for one market, replayed in arrival order.

    Two FIFO queues of unmatched lots, one per outcome token. A new lot is
    matched against the opposite queue first; whatever is left rests as
    directional inventory and is carried to settlement.
    """

    def __init__(self, slug: str, tok_a: str, tok_b: str,
                 match_window_s: Optional[float] = None) -> None:
        #: A COMPLEMENT THAT ARRIVES MUCH LATER IS NOT THE SAME TRADE.
        #: Unbounded FIFO will pair an UP bought in minute 2 with a DOWN
        #: bought in minute 9 and call the sum a pair cost, which is how a
        #: cheap-pair strategy can be made to look expensive (or the
        #: reverse). `complement_mechanism.json` measures L5's own
        #: time-to-complement at p50 27s, so a bounded window is the more
        #: faithful rule and it is reported alongside the unbounded one
        #: rather than instead of it.
        self.match_window_s = match_window_s
        self.slug = slug
        self.toks = (tok_a, tok_b)
        self.unmatched: Dict[str, deque] = {tok_a: deque(), tok_b: deque()}
        self.expired: Dict[str, list] = {tok_a: [], tok_b: []}
        self.pairs: List[Tuple[D, D, D]] = []      # qty, cost_a, cost_b
        self.purchased: Dict[str, D] = {tok_a: D(0), tok_b: D(0)}
        self.cost: Dict[str, D] = {tok_a: D(0), tok_b: D(0)}
        self.n_fills = 0
        self.maker_qty = D(0)
        self.taker_qty = D(0)

    def other(self, tok: str) -> str:
        return self.toks[1] if tok == self.toks[0] else self.toks[0]

    def add(self, tok: str, qty: D, price: D, role: str,
            ts: Optional[float] = None) -> None:
        self.n_fills += 1
        self.purchased[tok] += qty
        self.cost[tok] += qty * price
        if role == "maker":
            self.maker_qty += qty
        else:
            self.taker_qty += qty
        left = qty
        opp = self.unmatched[self.other(tok)]
        if self.match_window_s is not None and ts is not None:
            # retire lots that can no longer pair; they are residual
            while opp and (ts - opp[0][2]) > self.match_window_s:
                oq, op, ots = opp.popleft()
                self.expired[self.other(tok)].append((oq, op))
        while left > 0 and opp:
            oq, op = opp[0][0], opp[0][1]
            take = min(left, oq)
            ca, cb = ((take * price, take * op) if tok == self.toks[0]
                      else (take * op, take * price))
            self.pairs.append((take, ca, cb))
            left -= take
            if oq - take == 0:
                opp.popleft()
            else:
                opp[0] = (oq - take, op, opp[0][2])
        if left > 0:
            self.unmatched[tok].append((left, price, ts if ts else 0.0))

    def report(self, winner_tok: Optional[str]) -> Dict[str, Any]:
        matched_qty = sum(p[0] for p in self.pairs)
        matched_cost = sum(p[1] + p[2] for p in self.pairs)
        matched_settle = matched_qty * PAR
        matched_pnl = matched_settle - matched_cost
        resid: Dict[str, Tuple[D, D]] = {}
        resid_cost = D(0)
        resid_settle = D(0)
        for tok, q in self.unmatched.items():
            rq = sum(x[0] for x in q) + sum(x[0] for x in self.expired[tok])
            rc = (sum(x[0] * x[1] for x in q)
                  + sum(x[0] * x[1] for x in self.expired[tok]))
            if rq:
                resid[tok] = (rq, rc)
                resid_cost += rc
                if winner_tok is not None and tok == winner_tok:
                    resid_settle += rq * PAR
        resid_pnl = resid_settle - resid_cost
        pair_cost_avg = (matched_cost / matched_qty) if matched_qty else None
        return {
            "slug": self.slug,
            "n_fills": self.n_fills,
            "purchased_shares": str(sum(self.purchased.values())),
            "maker_shares": str(self.maker_qty),
            "taker_shares": str(self.taker_qty),
            "matched_shares": str(matched_qty),
            "matched_pair_cost_avg": (str(pair_cost_avg.quantize(D("0.0001")))
                                      if pair_cost_avg is not None else None),
            "matched_pnl": str(matched_pnl.quantize(D("0.01"))),
            "residual_shares": str(sum(v[0] for v in resid.values())),
            "residual_cost": str(resid_cost.quantize(D("0.01"))),
            "residual_settle": str(resid_settle.quantize(D("0.01"))),
            "residual_pnl": str(resid_pnl.quantize(D("0.01"))),
            "full_inventory_pnl": str((matched_pnl + resid_pnl)
                                      .quantize(D("0.01"))),
            "settlement_known": winner_tok is not None,
            "matched_frac": (str((matched_qty * 2 /
                                  sum(self.purchased.values()))
                                 .quantize(D("0.0001")))
                             if sum(self.purchased.values()) else None),
        }


def build(argv=None) -> Dict[str, Any]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="data/zenodo_22084977/updown_15m.parquet")
    ap.add_argument("--backfill", default="analysis/l5/august_replay/l5_backfill.json")
    ap.add_argument("--block-times", default="analysis/l5/august_replay/block_times.json")
    ap.add_argument("--channels", nargs="*", default=["RESTING_BID"],
                    help="channels to INCLUDE in the replay")
    ap.add_argument("--match-window-s", type=float, default=None,
                    help="a complement arriving later than this is residual")
    ap.add_argument("--pmus-fees", action="store_true",
                    help="SENSITIVITY ONLY: charge PMUS fee coefficients on "
                         "the same share path. Never what L5 paid.")
    ap.add_argument("--out", default="analysis/l5/historical_l2/mintfree_expectancy.json")
    a = ap.parse_args(argv)

    import pyarrow.parquet as pq
    d = pq.read_table(a.parquet).to_pydict()
    books: Dict[str, Book] = defaultdict(Book)
    winner_of: Dict[str, bool] = {}
    slug_of: Dict[str, str] = {}
    for i, coin in enumerate(d["coin"]):
        if coin != "btc":
            continue
        tok = str(d["token_id"][i])
        books[tok].add(
            d["captured_at"][i].timestamp(),
            {"best_bid": d["best_bid"][i], "best_ask": d["best_ask"][i],
             "bid_prices": d["bid_prices"][i], "bid_sizes": d["bid_sizes"][i],
             "seconds_to_close": d["seconds_to_close"][i]})
        w = d["is_winning_outcome"][i]
        if w is not None:
            winner_of[tok] = bool(w)
        slug_of[tok] = str(d["market_slug"][i])
    for b in books.values():
        b.sort()
    comp = complements(d)
    bt = json.load(open(a.block_times))["block_times"]
    bf = json.load(open(a.backfill))

    tx_tokens: Dict[str, set] = defaultdict(set)
    for w in bf["rows"]:
        for lg in w["fills"]:
            tx_tokens[lg["tx"]].add(str(lg["token_id"]))
    atomic_pair = {h for h, toks in tx_tokens.items()
                   if any(comp.get(t) in toks for t in toks)}

    include = set(a.channels)
    chan_counts: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"fills": 0, "shares": D(0)})
    markets: List[Dict[str, Any]] = []
    skipped_no_settlement: List[str] = []

    for w in bf["rows"]:
        toks = sorted({str(lg["token_id"]) for lg in w["fills"]})
        if len(toks) != 2:
            # a window where only one outcome ever traded cannot form a pair;
            # it is still replayed, with the complement taken from the map.
            if len(toks) == 1 and comp.get(toks[0]):
                toks = sorted([toks[0], comp[toks[0]]])
            elif not toks:
                continue
        rows = []
        for lg in w["fills"]:
            tok = str(lg["token_id"])
            sz = D(str(lg["size"]))
            px = D(str(lg["price"]))
            role = str(lg.get("l5_role"))
            if role == "taker":
                ch = ("ATOMIC_PAR_PAIR" if lg["tx"] in atomic_pair
                      else "TAKER_SINGLE")
            else:
                t = bt.get(str(lg.get("block")))
                if t is None or tok not in books:
                    ch = "UNCLASSIFIED"
                else:
                    snap = books[tok].at(float(t) - LEAD)
                    if snap is None:
                        ch = "UNCLASSIFIED"
                    else:
                        cls = placement_consistency(snap, px)
                        ct = comp.get(tok)
                        cb = None
                        if ct and ct in books:
                            cs = books[ct].at(float(t) - LEAD)
                            if cs is not None:
                                cb = cs.get("best_bid")
                        ch = channel(cls, px, cb)
            chan_counts[ch]["fills"] += 1
            chan_counts[ch]["shares"] += sz
            rows.append((int(lg["block"]), int(lg["log_index"]), tok, sz, px,
                         role, ch, float(bt.get(str(lg.get("block")), 0) or 0)))
        rows.sort(key=lambda r: (r[0], r[1]))
        m = Market(w["market_slug"], toks[0], toks[1],
                   match_window_s=a.match_window_s)
        fees = D(0)
        for _b, _li, tok, sz, px, role, ch, ts in rows:
            if ch not in include or tok not in m.unmatched:
                continue
            m.add(tok, sz, px, role, ts=ts)
            if a.pmus_fees:
                fees += pmus_fee(px, sz, PMUS_MAKER_COEF if role == "maker"
                                 else PMUS_TAKER_COEF)
        if m.n_fills == 0:
            continue
        wt = None
        for t in toks:
            if winner_of.get(t) is True:
                wt = t
        if wt is None and not any(t in winner_of for t in toks):
            skipped_no_settlement.append(w["market_slug"])
        rep = m.report(wt)
        rep["pmus_fees_sensitivity"] = str(fees.quantize(D("0.01")))
        if a.pmus_fees:
            rep["full_inventory_pnl"] = str(
                (D(rep["full_inventory_pnl"]) - fees).quantize(D("0.01")))
        markets.append(rep)

    def agg(key: str) -> D:
        return sum(D(r[key]) for r in markets)

    tot_pnl = agg("full_inventory_pnl")
    tot_matched = agg("matched_pnl")
    tot_resid = agg("residual_pnl")
    gross_edge = sum(
        (D(r["matched_shares"]) * PAR - D(r["matched_shares"]) *
         D(r["matched_pair_cost_avg"])) if r["matched_pair_cost_avg"] else D(0)
        for r in markets)
    return {
        "channels_included": sorted(include),
        "match_window_s": a.match_window_s,
        "pmus_fees_applied": bool(a.pmus_fees),
        "pmus_fees_total": str(agg("pmus_fees_sensitivity").quantize(D("0.01"))),
        "markets_with_included_fills": len(markets),
        "markets_in_backfill": len(bf["rows"]),
        "included_fills": sum(r["n_fills"] for r in markets),
        "included_shares": str(agg("purchased_shares")),
        "matched_shares": str(agg("matched_shares")),
        "residual_shares": str(agg("residual_shares")),
        "full_inventory_pnl_ex_incentives": str(tot_pnl.quantize(D("0.01"))),
        "matched_pair_pnl": str(tot_matched.quantize(D("0.01"))),
        "residual_pnl": str(tot_resid.quantize(D("0.01"))),
        "gross_matched_edge": str(gross_edge.quantize(D("0.01"))),
        "residual_share_of_gross_edge": (
            str((-tot_resid / gross_edge).quantize(D("0.0001")))
            if gross_edge else None),
        "markets_without_venue_settlement": skipped_no_settlement,
        "channel_census_all_fills": {
            k: {"fills": v["fills"], "shares": str(v["shares"])}
            for k, v in sorted(chan_counts.items())},
        "per_market": markets,
        "LABELS": {
            "venue": "Polymarket CLOB -- L5's own venue, zero trading fee "
                     "on these markets",
            "settlement": "VENUE-SOURCED from the parquet's "
                          "is_winning_outcome, never inferred from a "
                          "terminal book",
            "incentives": "NOT included and NOT netted; this prices the "
                          "trading only",
            "matching": "FIFO in block order against the opposite outcome's "
                        "unmatched lots",
            "not_a_subtraction": "mint fills are removed from the SHARE PATH "
                                 "before matching, so shares they used to "
                                 "complement become residual",
        },
    }


def main(argv=None) -> int:
    out = build(argv)
    import sys
    path = "analysis/l5/historical_l2/mintfree_expectancy.json"
    for i, x in enumerate(argv or sys.argv[1:]):
        if x == "--out":
            path = (argv or sys.argv[1:])[i + 1]
    with open(path, "w", encoding="utf8") as fh:
        json.dump(out, fh, indent=1, default=str)
    keys = ["channels_included", "markets_with_included_fills",
            "included_fills", "included_shares", "matched_shares",
            "residual_shares", "full_inventory_pnl_ex_incentives",
            "matched_pair_pnl", "residual_pnl", "gross_matched_edge",
            "residual_share_of_gross_edge"]
    for k in keys:
        print(f"{k} = {out[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
