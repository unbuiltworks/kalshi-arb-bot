"""OPENING-30: every window aligned at T=0, first 30 seconds, native resolution.

NO STRATEGY, NO OPTIMIZATION. Clusters are built from the normalized event
sequences AFTER they are constructed; nothing is grouped to make a number look
better, and no P&L enters the clustering or the labelling.

WHAT T=0 MEANS. The market's own opening second, taken from the slug's epoch
(verified against market_end_at minus the contract length). Every genuine
archived observation and every L5 fill in [T+0, T+30] is emitted at its NATIVE
second. Where no observation exists for a second, NOTHING IS MANUFACTURED --
the sequence simply has no event there, and the preceding observation remains
the causally available state.

THE NORMALIZED SEQUENCE is a compact string per window built only from
observable categories, so two windows can be compared without reference to
their price levels:

  O   an archived book observation that changed the touch on either outcome
  u/d an L5 fill on up / down, suffixed by its mechanism initial
      (r resting-bid-consistent, m mint-consistent, i inside-spread,
       x unresolved)
  .   a second with no event

Clustering is then exact-prefix agreement on that string, which is a
description, not a fit.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book
from analysis.l5.microstructure.load import l5_fills, load
from analysis.l5.microstructure.stats import cliffs_delta, qs, spearman

HORIZON = 30
MECH_INITIAL = {"RESTING_BID_CONSISTENT": "r", "MINT_CONSISTENT": "m",
                "INSIDE_SPREAD": "i", "UNRESOLVED_ABOVE_ASK": "x",
                "UNRESOLVED_NO_BOOK": "x"}


def window_opening(books: Dict[str, Book], tok_up: str, tok_dn: str,
                   start: int, events: List[dict], slug: str, clen: str
                   ) -> Dict[str, Any]:
    lab = {tok_up: "up", tok_dn: "down"}
    end = start + HORIZON
    # every GENUINE observation in the horizon, per token, at its own second
    obs: List[Dict[str, Any]] = []
    for tok in (tok_up, tok_dn):
        bk = books.get(tok)
        if bk is None:
            continue
        for ts, row in zip(bk.t, bk.rows):
            if start <= ts <= end:
                obs.append({"t": int(ts), "rel": int(ts) - start,
                            "outcome": lab[tok], "kind": "BOOK",
                            "best_bid": row.get("best_bid"),
                            "best_ask": row.get("best_ask"),
                            "top_bid_size": (
                                (row.get("bid_sizes") or [None])[0]
                                if row.get("bid_sizes") else None),
                            "bid_levels": len(row.get("bid_prices") or []),
                            "total_bid_depth": sum(
                                float(x) for x in
                                (row.get("bid_sizes") or []))})
    fl = [r for r in events
          if r["market_slug"] == slug and 0 <= r["secs_since_open"] <= HORIZON]
    fl.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
    stream = sorted(
        obs + [{"t": r["t"], "rel": r["secs_since_open"],
                "outcome": r["outcome"], "kind": "L5_FILL",
                "price": r["price"], "quantity": r["quantity"],
                "mechanism": r["mechanism"],
                "own_bid": r.get("own_bid"), "own_ask": r.get("own_ask"),
                "offset_ticks_below_bid": r.get("offset_ticks_below_bid"),
                "ticks_below_ask": r.get("ticks_below_ask"),
                "obs_age_s_own": r.get("obs_age_s_own"),
                "apparent_resting_s_at_price":
                    r.get("apparent_resting_s_at_price"),
                "price_existed_tminus5": r.get("price_existed_tminus5"),
                "price_existed_tminus10": r.get("price_existed_tminus10"),
                "inv_after": r.get("inv_after"),
                "log_index": r.get("log_index")}
               for r in fl],
        key=lambda x: (x["t"], 0 if x["kind"] == "BOOK" else 1))

    # opening book: the first observation at or before T+0 on each outcome
    open_bk = {}
    for tok in (tok_up, tok_dn):
        bk = books.get(tok)
        s = bk.at(float(start), max_age_s=MAX_CARRY_S) if bk else None
        open_bk[lab[tok]] = None if s is None else {
            "best_bid": s.get("best_bid"), "best_ask": s.get("best_ask"),
            "carried_s": s.get("carried_s"),
            "bid_levels": len(s.get("bid_prices") or []),
            "total_bid_depth": sum(float(x)
                                   for x in (s.get("bid_sizes") or [])),
            "top_bid_size": ((s.get("bid_sizes") or [None])[0]
                             if s.get("bid_sizes") else None)}

    # normalized per-second sequence string
    seq = []
    by_sec: Dict[int, List[dict]] = defaultdict(list)
    for e in stream:
        by_sec[e["rel"]].append(e)
    prev_touch: Dict[str, Any] = {}
    for s in range(0, HORIZON + 1):
        toks = ""
        for e in by_sec.get(s, []):
            if e["kind"] == "BOOK":
                if prev_touch.get(e["outcome"]) != e["best_bid"]:
                    prev_touch[e["outcome"]] = e["best_bid"]
                    toks += "O"
            else:
                toks += ("u" if e["outcome"] == "up" else "d") \
                    + MECH_INITIAL.get(e["mechanism"], "x")
        seq.append(toks if toks else ".")
    seq_str = "|".join(seq)

    first = fl[0] if fl else None
    opp = None
    if first:
        opp = next((r for r in fl if r["outcome"] != first["outcome"]), None)
    gaps = [b["t"] - a["t"] for a, b in zip(fl, fl[1:])]
    return {
        "market_slug": slug, "contract_length": clen,
        "window_start_utc": start, "horizon_s": HORIZON,
        "opening_book": open_bk,
        "n_observations_in_horizon": len(obs),
        "n_l5_fills_in_horizon": len(fl),
        "normalized_sequence": seq_str,
        "sequence_prefix_8": "|".join(seq[:8]),
        "first_fill": None if first is None else {
            "secs_after_open": first["secs_since_open"],
            "tte_s": first["tte_s"], "outcome": first["outcome"],
            "price": first["price"], "quantity": first["quantity"],
            "mechanism": first["mechanism"],
            "own_bid": first.get("own_bid"), "own_ask": first.get("own_ask"),
            "offset_ticks_below_bid": first.get("offset_ticks_below_bid"),
            "ticks_below_ask": first.get("ticks_below_ask"),
            "spread": first.get("own_spread"),
            "pair_sum_bids": first.get("pair_sum_bids"),
            "obs_age_s": first.get("obs_age_s_own"),
            "apparent_resting_s_at_price":
                first.get("apparent_resting_s_at_price"),
            "price_existed_tminus5": first.get("price_existed_tminus5"),
            "price_existed_tminus10": first.get("price_existed_tminus10"),
            "own_bid_move_5s_before": first.get("own_bid_move_5s_before"),
            "own_bid_move_10s_before": first.get("own_bid_move_10s_before"),
        },
        "first_opposite_fill": None if opp is None else {
            "secs_after_open": opp["secs_since_open"],
            "dt_from_first_s": opp["t"] - first["t"],
            "outcome": opp["outcome"], "price": opp["price"],
            "quantity": opp["quantity"], "mechanism": opp["mechanism"],
            "own_bid_move_5s_before": opp.get("own_bid_move_5s_before"),
            "pair_sum_bids": opp.get("pair_sum_bids")},
        "inter_fill_gaps_s": gaps,
        "second_fill": None if len(fl) < 2 else {
            "dt_from_first_s": fl[1]["t"] - fl[0]["t"],
            "outcome": fl[1]["outcome"], "price": fl[1]["price"],
            "quantity": fl[1]["quantity"],
            "same_side_as_first": fl[1]["outcome"] == fl[0]["outcome"],
            "same_price_as_first": abs(fl[1]["price"] - fl[0]["price"]) < 1e-9,
            "price_vs_first": ("same" if abs(fl[1]["price"] - fl[0]["price"])
                               < 1e-9 else
                               "lower" if fl[1]["price"] < fl[0]["price"]
                               else "higher")},
        "third_fill": None if len(fl) < 3 else {
            "dt_from_first_s": fl[2]["t"] - fl[0]["t"],
            "outcome": fl[2]["outcome"], "price": fl[2]["price"],
            "quantity": fl[2]["quantity"]},
        "inventory_at_horizon": (fl[-1].get("inv_after") if fl else None),
        "fills": [{"rel": r["secs_since_open"], "outcome": r["outcome"],
                   "price": r["price"], "quantity": r["quantity"],
                   "mechanism": r["mechanism"],
                   "offset_ticks_below_bid": r.get("offset_ticks_below_bid"),
                   "inv_after": r.get("inv_after")} for r in fl],
        "event_stream": stream,
    }


def analyse(wins: List[Dict[str, Any]], label: str) -> Dict[str, Any]:
    got = [w for w in wins if w["first_fill"]]
    ff = [w["first_fill"] for w in got]
    opp = [w["first_opposite_fill"] for w in got if w["first_opposite_fill"]]
    sec = [w["second_fill"] for w in got if w["second_fill"]]
    out: Dict[str, Any] = {
        "population": label, "n_windows": len(wins),
        "windows_with_a_fill_in_first_30s": len(got),
        "share_with_a_fill_in_first_30s": (round(len(got) / len(wins), 4)
                                           if wins else None),
        # Q1 when
        "Q1_first_fill_secs_after_open": qs([f["secs_after_open"]
                                             for f in ff]),
        "Q1_first_fill_tte_s": qs([f["tte_s"] for f in ff]),
        # Q2 where
        "Q2_where": {
            "first_side_counts": dict(Counter(f["outcome"] for f in ff)),
            "offset_ticks_below_bid": qs([f["offset_ticks_below_bid"]
                                          for f in ff]),
            "ticks_below_ask": qs([f["ticks_below_ask"] for f in ff]),
            "price": qs([f["price"] for f in ff]),
            "spread_at_entry": qs([f["spread"] for f in ff]),
            "pair_sum_bids_at_entry": qs([f["pair_sum_bids"] for f in ff]),
            "mechanism_counts": dict(Counter(f["mechanism"] for f in ff)),
            "move_5s_before_entry": qs([f["own_bid_move_5s_before"]
                                        for f in ff]),
            "move_10s_before_entry": qs([f["own_bid_move_10s_before"]
                                         for f in ff]),
        },
        # Q3 how much
        "Q3_first_quantity": {
            "quantity": qs([f["quantity"] for f in ff]),
            "distinct_values": len({round(f["quantity"], 6) for f in ff}),
            "spearman_vs_price": spearman([f["price"] for f in ff],
                                          [f["quantity"] for f in ff]),
            "spearman_vs_offset": spearman([f["offset_ticks_below_bid"]
                                            for f in ff],
                                           [f["quantity"] for f in ff]),
            "spearman_vs_move5s": spearman([f["own_bid_move_5s_before"]
                                            for f in ff],
                                           [f["quantity"] for f in ff]),
            "spearman_vs_secs_after_open": spearman(
                [f["secs_after_open"] for f in ff],
                [f["quantity"] for f in ff]),
        },
        # Q4 both outcomes?
        "Q4_two_sided": {
            "windows_with_an_opposite_fill_in_30s": len(opp),
            "share": (round(len(opp) / len(got), 4) if got else None),
            "dt_first_to_opposite_s": qs([o["dt_from_first_s"]
                                          for o in opp]),
            "same_second_share": (round(sum(1 for o in opp
                                            if o["dt_from_first_s"] == 0)
                                        / len(opp), 4) if opp else None),
            "opposite_mechanism_counts": dict(Counter(o["mechanism"]
                                                      for o in opp)),
            "move_5s_before_opposite": qs([o["own_bid_move_5s_before"]
                                           for o in opp]),
            "pair_sum_at_opposite": qs([o["pair_sum_bids"] for o in opp]),
        },
        # Q5 waiting liquidity?
        "Q5_liquidity_waiting": {
            "apparent_resting_s_at_first_fill_price": qs(
                [f["apparent_resting_s_at_price"] for f in ff]),
            "share_price_existed_5s_before": (
                round(sum(1 for f in ff if f["price_existed_tminus5"] is True)
                      / max(1, sum(1 for f in ff
                                   if f["price_existed_tminus5"]
                                   is not None)), 4)),
            "share_price_existed_10s_before": (
                round(sum(1 for f in ff
                          if f["price_existed_tminus10"] is True)
                      / max(1, sum(1 for f in ff
                                   if f["price_existed_tminus10"]
                                   is not None)), 4)),
            "mechanism_counts_first_fill": dict(Counter(f["mechanism"]
                                                        for f in ff)),
            "caveat": ("size at a price is not ownership; absence of size is "
                       "not proof he had no order there. Before T+0 the "
                       "market did not exist, so 'resting before the market "
                       "reached it' can only be assessed within the window."),
        },
        # Q6 what happens after the first fill
        "Q6_after_first_fill": {
            "windows_with_a_second_fill_in_30s": len(sec),
            "dt_first_to_second_s": qs([s["dt_from_first_s"] for s in sec]),
            "second_same_side_share": (round(sum(1 for s in sec
                                                 if s["same_side_as_first"])
                                             / len(sec), 4) if sec else None),
            "second_price_vs_first": dict(Counter(s["price_vs_first"]
                                                  for s in sec)),
            "inter_fill_gaps_in_horizon": qs(
                [g for w in got for g in w["inter_fill_gaps_s"]]),
            "fills_in_horizon": qs([w["n_l5_fills_in_horizon"]
                                    for w in got]),
        },
        # Q7 does opening inventory steer the next action
        "Q7_opening_inventory_effect": {
            "note": ("at the first fill inventory is zero by construction, so "
                     "this tests the SECOND action against the first"),
            "second_fill_side_given_first": {
                "same_side": sum(1 for s in sec if s["same_side_as_first"]),
                "opposite_side": sum(1 for s in sec
                                     if not s["same_side_as_first"])},
            "spearman_first_qty_vs_second_qty": spearman(
                [w["first_fill"]["quantity"] for w in got if w["second_fill"]],
                [w["second_fill"]["quantity"] for w in got
                 if w["second_fill"]]),
            "second_qty_when_same_side_vs_opposite": cliffs_delta(
                [s["quantity"] for s in sec if s["same_side_as_first"]],
                [s["quantity"] for s in sec if not s["same_side_as_first"]]),
        },
        "sequence_prefix_clusters": dict(
            Counter(w["sequence_prefix_8"] for w in wins).most_common(12)),
        "clustering_note": ("exact-prefix agreement on the normalized "
                            "sequence, built after the sequences existed; no "
                            "P&L enters the clustering"),
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--backfill", required=True)
    ap.add_argument("--block-times", required=True)
    ap.add_argument("--events", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--window-s", type=int, required=True)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = [json.loads(l) for l in open(a.events, encoding="utf-8")
          if l.strip()]
    fills_by_slug, bt, _ = l5_fills(a.backfill, a.block_times)
    traded = {s for s, v in fills_by_slug.items() if v}
    books, wins = load(a.parquet, contract_length=a.contract_length,
                       slugs=traded)
    out: List[Dict[str, Any]] = []
    for slug in sorted(wins):
        toks = wins[slug]["tokens"]
        up = next((t for t, o in toks.items() if str(o).lower() == "up"), None)
        dn = next((t for t, o in toks.items() if str(o).lower() == "down"),
                  None)
        if up is None or dn is None:
            continue
        out.append(window_opening(books, up, dn,
                                  int(slug.rsplit("-", 1)[1]), ev, slug,
                                  a.contract_length))
    rep = {"contract_length": a.contract_length, "horizon_s": HORIZON,
           "no_strategy_no_optimization": True,
           "resolution": ("native whole seconds; a second with no archived "
                          "observation carries NO event and nothing is "
                          "manufactured"),
           "analysis": analyse(out, a.contract_length),
           "per_window": out}
    os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "OPENING30_%s.json" % a.contract_length)
    with open(p, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("written", p)
    A = rep["analysis"]
    print(json.dumps({k: v for k, v in A.items()
                      if k != "sequence_prefix_clusters"}, indent=1,
                     default=str)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
