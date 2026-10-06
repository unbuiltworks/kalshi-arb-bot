"""The eight sequential-behaviour questions. Empirical rules, not a strategy.

NO STRATEGY IS FITTED HERE and no P&L is computed. Every number is a
description of observed behaviour, reported with its sample count, an effect
size, and -- crucially -- how many individual windows reproduce it.

THREE RULES THIS MODULE FOLLOWS THROUGHOUT:

  DISCOVERY IS SEPARATED FROM CONFIRMATION. btc-updown-15m-1787300100 was
  already inspected forensically, so it is reported but never pooled into the
  confirmation statistics. Nothing is tuned against the other eleven.

  MECHANISMS ARE NEVER MIXED. Resting-bid-consistent, inside-spread and
  mint-consistent executions are produced by different venue primitives, so a
  placement rule inferred from their mixture would describe none of them.
  Every placement and movement result is reported per mechanism.

  MOVEMENT CLAIMS CARRY A CONTROL. Fills are compared against archived
  observations in the same windows where L5 did NOT trade, so "he buys after a
  fall" has to beat the base rate of falls.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

from analysis.l5.microstructure.stats import (by_bucket, cliffs_delta, qs,
                                              reproduces, spearman)

DISCOVERY = "btc-updown-15m-1787300100"
MECHS = ("RESTING_BID_CONSISTENT", "MINT_CONSISTENT", "INSIDE_SPREAD")
LEADS = (1, 5, 10, 30)
HEAVY = 50.0          # shares; "materially heavy" on one side


def jl(p: str) -> List[dict]:
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


def flat(rows: List[dict]) -> List[dict]:
    """Lift the nested inventory snapshots to top level for the stats."""
    out = []
    for r in rows:
        g = dict(r)
        for k, v in (r.get("inv_before") or {}).items():
            g["before_" + k] = v
        for k, v in (r.get("inv_after") or {}).items():
            g["after_" + k] = v
        g["imbalance_signed_before"] = (r.get("inv_before") or {}).get(
            "imbalance_up_minus_down")
        out.append(g)
    return out


def q1_movement(fills: List[dict], ctrl: List[dict]) -> Dict[str, Any]:
    """What market movement precedes a purchase -- against the base rate."""
    out: Dict[str, Any] = {"question": (
        "does L5 add when an outcome falls, rises, holds, or when depth "
        "changes? Compared with archived seconds in the same windows where "
        "he did not trade.")}
    for mech in ("ALL",) + MECHS:
        sel = fills if mech == "ALL" else [r for r in fills
                                           if r["mechanism"] == mech]
        if len(sel) < 20:
            out[mech] = {"n": len(sel), "note": "too few"}
            continue
        blk: Dict[str, Any] = {"n": len(sel)}
        for L in LEADS:
            k = "own_bid_move_%ds_before" % L
            blk["own_%ds" % L] = {
                "fills": qs([r.get(k) for r in sel]),
                "control": qs([r.get(k) for r in ctrl]),
                "cliffs_delta_fills_vs_control": cliffs_delta(
                    [r.get(k) for r in sel], [r.get(k) for r in ctrl]),
            }
            kc = "comp_bid_tminus%d" % L
            blk["complement_bid_at_t_minus_%d" % L] = qs(
                [r.get(kc) for r in sel])
        blk["depth_at_touch"] = {
            "fills": qs([r.get("depth_at_own_bid") for r in sel]),
            "control": qs([r.get("depth_at_own_bid") for r in ctrl]),
            "cliffs_delta": cliffs_delta(
                [r.get("depth_at_own_bid") for r in sel],
                [r.get("depth_at_own_bid") for r in ctrl])}
        blk["total_bid_depth"] = {
            "cliffs_delta": cliffs_delta(
                [r.get("total_bid_depth_own") for r in sel],
                [r.get("total_bid_depth_own") for r in ctrl])}
        blk["spread"] = {
            "fills": qs([r.get("own_spread") for r in sel]),
            "control": qs([r.get("own_spread") for r in ctrl]),
            "cliffs_delta": cliffs_delta(
                [r.get("own_spread") for r in sel],
                [r.get("own_spread") for r in ctrl])}
        # DIRECTIONAL SKEW, NOT THE MEDIAN. These movement distributions are
        # mostly exact zeros (the touch simply did not move), so a per-window
        # median is 0 in most windows and the sign test degenerates to a tie
        # -- which is what an earlier version reported. share_negative minus
        # share_positive stays informative through the zero mass.
        def _skew(g, key="own_bid_move_5s_before"):
            v = [r.get(key) for r in g]
            v = [float(x) for x in v if x is not None]
            if len(v) < 20:
                return None
            neg = sum(1 for x in v if x < 0) / len(v)
            pos = sum(1 for x in v if x > 0) / len(v)
            return neg - pos
        blk["reproduces_downward_skew_5s"] = reproduces(sel, "market_slug",
                                                        _skew)
        blk["reproduces_downward_skew_30s"] = reproduces(
            sel, "market_slug",
            lambda g: _skew(g, "own_bid_move_30s_before"))
        blk["downward_skew_definition"] = (
            "share(move<0) - share(move>0) over the L seconds ending at the "
            "last causally available observation; positive means the touch "
            "more often FELL into the fill than rose")
        out[mech] = blk
    return out


def q2_quantity(fills: List[dict]) -> Dict[str, Any]:
    feats = ["price", "offset_ticks_below_bid", "own_spread",
             "own_bid_move_1s_before", "own_bid_move_5s_before",
             "own_bid_move_10s_before", "own_bid_move_30s_before",
             "depth_at_own_bid", "depth_within_1_tick",
             "depth_within_3_ticks", "total_bid_depth_own",
             "size_at_fill_price_before",
             "size_at_or_above_fill_price_before",
             "tte_s", "secs_since_open", "imbalance_abs_before",
             "imbalance_signed_before", "before_matched",
             "prev_fill_same_side_qty", "prev_fill_any_qty",
             "prev_fill_same_side_dt_s", "apparent_resting_s_at_price"]
    out: Dict[str, Any] = {"question": "what determines quantity?"}
    for mech in ("ALL",) + MECHS:
        sel = fills if mech == "ALL" else [r for r in fills
                                           if r["mechanism"] == mech]
        if len(sel) < 30:
            out[mech] = {"n": len(sel), "note": "too few"}
            continue
        q = [r["quantity"] for r in sel]
        blk: Dict[str, Any] = {"n": len(sel), "quantity": qs(q),
                               "spearman_vs": {}}
        for f in feats:
            blk["spearman_vs"][f] = spearman([r.get(f) for r in sel], q)
        blk["spearman_vs"] = dict(sorted(
            blk["spearman_vs"].items(),
            key=lambda kv: -(abs(kv[1].get("rho") or 0))))
        a = [r["quantity"] for r in sel if r.get("action_reduces_imbalance")]
        b = [r["quantity"] for r in sel
             if r.get("action_reduces_imbalance") is False]
        blk["quantity_when_action_reduces_imbalance_vs_increases"] = \
            cliffs_delta(a, b)
        a = [r["quantity"] for r in sel if r.get("bought_the_heavy_side")]
        b = [r["quantity"] for r in sel
             if r.get("bought_the_heavy_side") is False]
        blk["quantity_on_heavy_side_vs_light_side"] = cliffs_delta(a, b)
        blk["quantity_by_tte_bucket"] = by_bucket(
            sel, "tte_s", "quantity", (120, 300, 600))
        blk["quantity_by_depth_at_touch_bucket"] = by_bucket(
            sel, "depth_at_own_bid", "quantity", (50, 200, 800))
        blk["reproduces_rho_prev_qty"] = reproduces(
            sel, "market_slug",
            lambda g: spearman([r.get("prev_fill_same_side_qty") for r in g],
                               [r["quantity"] for r in g]).get("rho"))
        out[mech] = blk
    return out


def q3_reprice(fills: List[dict]) -> Dict[str, Any]:
    """Does his executed price follow the touch?"""
    pairs = []
    by: Dict[tuple, List[dict]] = defaultdict(list)
    for r in fills:
        by[(r["market_slug"], r["outcome"])].append(r)
    for k, g in by.items():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
        for a, b in zip(g, g[1:]):
            if a.get("own_bid") is None or b.get("own_bid") is None:
                continue
            pairs.append({
                "market_slug": b["market_slug"], "outcome": b["outcome"],
                "mechanism": b["mechanism"],
                "d_price": round(b["price"] - a["price"], 6),
                "d_touch": round(float(b["own_bid"]) - float(a["own_bid"]), 6),
                "dt_s": b["t"] - a["t"],
                "price_changed": abs(b["price"] - a["price"]) > 1e-9,
                "same_price": abs(b["price"] - a["price"]) <= 1e-9})
    out: Dict[str, Any] = {
        "question": "does his executed price move with the BBO?",
        "n_consecutive_same_side_pairs": len(pairs)}
    for mech in ("ALL",) + MECHS:
        sel = pairs if mech == "ALL" else [p for p in pairs
                                           if p["mechanism"] == mech]
        if len(sel) < 30:
            out[mech] = {"n": len(sel), "note": "too few"}
            continue
        out[mech] = {
            "n": len(sel),
            "spearman_dprice_vs_dtouch": spearman(
                [p["d_touch"] for p in sel], [p["d_price"] for p in sel]),
            "d_price": qs([p["d_price"] for p in sel]),
            "d_touch": qs([p["d_touch"] for p in sel]),
            "share_price_unchanged": round(
                sum(1 for p in sel if p["same_price"]) / len(sel), 4),
            "dt_when_price_changed": qs(
                [p["dt_s"] for p in sel if p["price_changed"]]),
            "dt_when_price_same": qs(
                [p["dt_s"] for p in sel if p["same_price"]]),
            "reproduces_rho": reproduces(
                sel, "market_slug",
                lambda g: spearman([p["d_touch"] for p in g],
                                   [p["d_price"] for p in g]).get("rho")),
        }
    return out


def q4_ladder(fills: List[dict]) -> Dict[str, Any]:
    """Minimum latent ladder consistent with the executions. AN INFERENCE."""
    out: Dict[str, Any] = {"question": (
        "does he appear to maintain multiple resting levels?"),
        "label": ("INFERENCE from executions only. Unfilled and cancelled "
                  "orders are unobservable, so these are LOWER BOUNDS on the "
                  "number of levels, never the ladder itself.")}
    # bursts defined two ways: same block (unorderable in time) and <=2s
    for name, keyfn in (("same_block",
                         lambda r: (r["market_slug"], r["outcome"],
                                    r["block"])),
                        ("within_2s",
                         lambda r: (r["market_slug"], r["outcome"],
                                    r["t"] // 2))):
        groups: Dict[Any, List[dict]] = defaultdict(list)
        for r in fills:
            groups[keyfn(r)].append(r)
        multi = [g for g in groups.values() if len(g) > 1]
        lv = [len({round(x["price"], 4) for x in g}) for g in multi]
        out[name] = {
            "n_groups": len(groups), "n_multi_fill_groups": len(multi),
            "share_of_fills_in_multi_groups": round(
                sum(len(g) for g in multi) / max(1, len(fills)), 4),
            "fills_per_group": qs([len(g) for g in multi]),
            "DISTINCT_PRICES_per_group": qs(lv),
            "groups_with_2plus_distinct_prices": sum(1 for x in lv if x >= 2),
            "groups_with_3plus_distinct_prices": sum(1 for x in lv if x >= 3),
            "max_distinct_prices_in_one_group": (max(lv) if lv else None),
            "price_span_ticks_per_group": qs(
                [round((max(x["price"] for x in g)
                        - min(x["price"] for x in g)) / 0.01)
                 for g in multi]),
        }
    out["latent_resting_evidence"] = {
        "apparent_resting_s_at_price": qs(
            [r.get("apparent_resting_s_at_price") for r in fills]),
        "share_price_existed_before": {
            "t-%ds" % L: round(
                sum(1 for r in fills
                    if r.get("price_existed_tminus%d" % L) is True)
                / max(1, sum(1 for r in fills
                             if r.get("price_existed_tminus%d" % L)
                             is not None)), 4) for L in LEADS},
        "share_price_at_or_above_touch_before": {
            "t-%ds" % L: round(
                sum(1 for r in fills
                    if r.get("price_at_or_above_touch_tminus%d" % L) is True)
                / max(1, sum(1 for r in fills
                             if r.get("price_at_or_above_touch_tminus%d" % L)
                             is not None)), 4) for L in LEADS},
        "caveat": ("size at a price is not ownership, and absence of size is "
                   "not proof he had no order there"),
    }
    return out


def q5_opposite(fills: List[dict]) -> Dict[str, Any]:
    """When does the opposite outcome begin filling?"""
    per: List[Dict[str, Any]] = []
    by: Dict[str, List[dict]] = defaultdict(list)
    for r in fills:
        by[r["market_slug"]].append(r)
    for slug, g in sorted(by.items()):
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
        first = g[0]
        opp = next((r for r in g if r["outcome"] != first["outcome"]), None)
        rec = {"market_slug": slug,
               "first_side": first["outcome"],
               "first_t_since_open": first["secs_since_open"],
               "first_price": first["price"],
               "first_qty": first["quantity"],
               "first_mechanism": first["mechanism"],
               "first_spread": first.get("own_spread"),
               "first_pair_sum_bids": first.get("pair_sum_bids")}
        if opp is None:
            rec["opposite_ever_filled"] = False
            per.append(rec)
            continue
        rec.update({
            "opposite_ever_filled": True,
            "opposite_t_since_open": opp["secs_since_open"],
            "dt_first_to_opposite_s": opp["t"] - first["t"],
            "opposite_price": opp["price"],
            "opposite_qty": opp["quantity"],
            "opposite_mechanism": opp["mechanism"],
            "opposite_spread": opp.get("own_spread"),
            "opposite_pair_sum_bids": opp.get("pair_sum_bids"),
            "own_bid_move_5s_before_opposite":
                opp.get("own_bid_move_5s_before"),
            "own_bid_move_30s_before_opposite":
                opp.get("own_bid_move_30s_before"),
            "inventory_on_first_side_when_opposite_started":
                abs((opp.get("inv_before") or {}).get(
                    "imbalance_up_minus_down") or 0),
            "fills_before_opposite": sum(1 for r in g if r["t"] < opp["t"]),
        })
        per.append(rec)
    done = [r for r in per if r.get("opposite_ever_filled")]
    return {
        "question": "when does the opposite outcome begin filling?",
        "n_windows": len(per),
        "windows_where_opposite_filled": len(done),
        "first_side_counts": {s: sum(1 for r in per if r["first_side"] == s)
                              for s in ("up", "down")},
        "dt_first_to_opposite_s": qs(
            [r["dt_first_to_opposite_s"] for r in done]),
        "fills_before_opposite": qs([r["fills_before_opposite"]
                                     for r in done]),
        "inventory_before_opposite_started": qs(
            [r["inventory_on_first_side_when_opposite_started"]
             for r in done]),
        "pair_sum_bids_at_first_vs_at_opposite": {
            "at_first": qs([r.get("first_pair_sum_bids") for r in done]),
            "at_opposite": qs([r.get("opposite_pair_sum_bids")
                               for r in done]),
            "compression_test": ("if two-sided acquisition needed the two "
                                 "touches to compress toward par, pair_sum "
                                 "should be materially higher at the opposite "
                                 "fill than at the first")},
        "spread_at_first_vs_opposite": {
            "at_first": qs([r.get("first_spread") for r in done]),
            "at_opposite": qs([r.get("opposite_spread") for r in done])},
        "movement_before_opposite": {
            "own_bid_move_5s": qs([r.get("own_bid_move_5s_before_opposite")
                                   for r in done]),
            "own_bid_move_30s": qs([r.get("own_bid_move_30s_before_opposite")
                                    for r in done])},
        "opposite_mechanism_counts": {
            m: sum(1 for r in done if r.get("opposite_mechanism") == m)
            for m in set(r.get("opposite_mechanism") for r in done)},
        "per_window": per,
    }


def q6_sizing_vs_inventory(fills: List[dict]) -> Dict[str, Any]:
    """Heavy UP: are subsequent UP quantities smaller than DOWN ones?"""
    out: Dict[str, Any] = {"question": (
        "how does sizing react to inventory, holding the book comparable?")}
    for heavy_side, sign in (("up", +1), ("down", -1)):
        sel = [r for r in fills
               if r.get("imbalance_signed_before") is not None
               and sign * float(r["imbalance_signed_before"]) >= HEAVY]
        same = [r["quantity"] for r in sel if r["outcome"] == heavy_side]
        opp = [r["quantity"] for r in sel if r["outcome"] != heavy_side]
        blk = {
            "n_fills_while_heavy_%s" % heavy_side: len(sel),
            "heavy_threshold_shares": HEAVY,
            "quantity_on_the_heavy_side": qs(same),
            "quantity_on_the_light_side": qs(opp),
            "cliffs_delta_heavy_minus_light": cliffs_delta(same, opp),
            "fill_count_split": {"heavy_side": len(same),
                                 "light_side": len(opp)},
        }
        # hold the book comparable: same depth-at-touch bucket
        for lo, hi in ((0, 50), (50, 200), (200, 800), (800, None)):
            s2 = [r for r in sel
                  if r.get("depth_at_own_bid") is not None
                  and float(r["depth_at_own_bid"]) >= lo
                  and (hi is None or float(r["depth_at_own_bid"]) < hi)]
            a = [r["quantity"] for r in s2 if r["outcome"] == heavy_side]
            b = [r["quantity"] for r in s2 if r["outcome"] != heavy_side]
            blk["depth_bucket_%s_%s" % (lo, hi)] = {
                "n_heavy": len(a), "n_light": len(b),
                "cliffs_delta": cliffs_delta(a, b)}
        out["heavy_%s" % heavy_side] = blk
    return out


def q7_stops(fills: List[dict]) -> Dict[str, Any]:
    """What is observable at the LAST fill on a side in a window?"""
    last: List[dict] = []
    by: Dict[tuple, List[dict]] = defaultdict(list)
    for r in fills:
        by[(r["market_slug"], r["outcome"])].append(r)
    for k, g in by.items():
        g.sort(key=lambda r: (r["t"], r.get("log_index") or 0))
        last.append(g[-1])
    allr = fills
    return {
        "question": "what is observable when a side stops being added to?",
        "no_cancellation_inferred": ("the archive cannot show a cancel. These "
                                     "are the conditions AT the last observed "
                                     "fill on a side, not evidence that an "
                                     "order was pulled."),
        "n_side_window_pairs": len(last),
        "tte_at_last_fill": qs([r["tte_s"] for r in last]),
        "tte_at_all_fills": qs([r["tte_s"] for r in allr]),
        "cliffs_delta_tte_last_vs_all": cliffs_delta(
            [r["tte_s"] for r in last], [r["tte_s"] for r in allr]),
        "residual_at_last_fill": qs(
            [(r.get("inv_after") or {}).get("residual") for r in last]),
        "imbalance_abs_at_last_fill": qs(
            [r.get("imbalance_abs_after") for r in last]),
        "movement_after_last_fill": {
            "own_bid_move_5s_after": qs(
                [r.get("own_bid_move_5s_after") for r in last]),
            "own_bid_move_30s_after": qs(
                [r.get("own_bid_move_30s_after") for r in last]),
            "vs_all_fills_30s_after": qs(
                [r.get("own_bid_move_30s_after") for r in allr]),
            "cliffs_delta_30s_after": cliffs_delta(
                [r.get("own_bid_move_30s_after") for r in last],
                [r.get("own_bid_move_30s_after") for r in allr])},
        "depth_at_last_fill": qs([r.get("depth_at_own_bid") for r in last]),
        "price_at_last_fill": qs([r["price"] for r in last]),
        "mechanism_at_last_fill": {
            m: sum(1 for r in last if r["mechanism"] == m)
            for m in set(r["mechanism"] for r in last)},
    }


def analyse(fills: List[dict], ctrl: List[dict], label: str) -> Dict[str, Any]:
    return {
        "population": label,
        "n_fills": len(fills),
        "n_control_observations": len(ctrl),
        "windows": sorted({r["market_slug"] for r in fills}),
        "mechanism_counts": {m: sum(1 for r in fills if r["mechanism"] == m)
                             for m in sorted({r["mechanism"]
                                              for r in fills})},
        "Q1_movement_before_purchase": q1_movement(fills, ctrl),
        "Q2_what_determines_quantity": q2_quantity(fills),
        "Q3_does_he_reprice_with_the_market": q3_reprice(fills),
        "Q4_minimum_latent_ladder_INFERRED": q4_ladder(fills),
        "Q5_when_does_the_opposite_side_start": q5_opposite(fills),
        "Q6_sizing_vs_inventory": q6_sizing_vs_inventory(fills),
        "Q7_what_precedes_stopping_a_side": q7_stops(fills),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--control", required=True)
    ap.add_argument("--contract-length", required=True)
    ap.add_argument("--discovery", default=DISCOVERY)
    ap.add_argument("--outdir", default="analysis/l5/microstructure")
    a = ap.parse_args(argv)

    ev = flat(jl(a.events))
    ct = jl(a.control)
    disc = [r for r in ev if r["market_slug"] == a.discovery]
    conf = [r for r in ev if r["market_slug"] != a.discovery]
    ctd = [r for r in ct if r["market_slug"] == a.discovery]
    ctc = [r for r in ct if r["market_slug"] != a.discovery]

    rep = {
        "contract_length": a.contract_length,
        "no_strategy_fitted": True, "no_pnl_computed": True,
        "discovery_window": a.discovery,
        "discovery_is_reported_but_never_pooled_into_confirmation": True,
        "resolution": ("whole seconds, event-driven archive; no "
                       "interpolation, no manufactured sub-second state"),
        "CONFIRMATION": analyse(conf, ctc, "confirmation (11 windows)")
        if conf else None,
        "DISCOVERY": analyse(disc, ctd, "discovery (1 window)")
        if disc else None,
    }
    os.makedirs(a.outdir, exist_ok=True)
    p = os.path.join(a.outdir, "BEHAVIOR_%s.json" % a.contract_length)
    with open(p, "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("written", p)
    for pop in ("CONFIRMATION", "DISCOVERY"):
        if not rep[pop]:
            continue
        r = rep[pop]
        print("\n===== %s  n_fills=%d  control=%d  windows=%d"
              % (pop, r["n_fills"], r["n_control_observations"],
                 len(r["windows"])))
        print(" mechanisms:", json.dumps(r["mechanism_counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
