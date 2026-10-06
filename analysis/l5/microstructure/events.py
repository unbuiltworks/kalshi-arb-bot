"""One row per L5 fill: everything observable immediately BEFORE the action.

HISTORICAL STUDY ONLY. No strategy, no fitting, no P&L objective. This module
reconstructs state; it does not score anything.

CAUSALITY IS THE WHOLE POINT. Every "before" field is read with Book.at(t),
which returns the latest archived observation at or BEFORE t, and the row
carries `obs_age_s` so a reader can see how fresh that observation was. The
archive is EVENT-DRIVEN at whole-second resolution (L2_RESOLUTION.json: every
microsecond zero, zero duplicate (token, second) pairs, 0.24% of consecutive
pairs repeating a ladder), so a gap means the book did not move and the last
row IS the current state. Nothing is interpolated and no millisecond state is
manufactured. "After" fields exist only for describing consequences and are
never used to classify the action.

TIMESTAMP UNCERTAINTY IS CARRIED, NOT HIDDEN. An L5 fill is timed by its
BLOCK timestamp. Polygon blocks are ~2 s apart, so every fill in a block
shares one second and the true instant lies somewhere in that block's
interval. Each row records the block, how many L5 fills shared it, and the
measured interval to the neighbouring block, so the ambiguity is visible. The
log_index gives a deterministic ORDER within a block even though it gives no
finer time.

WHAT A FILL CANNOT TELL US, stated on every row that touches it: the archive
shows size at a price, never ownership. `size_at_fill_price_before` is depth
at a price level, NOT L5's order. Absence of size at a price is NOT proof L5
had no order there, and unfilled or cancelled L5 orders are invisible in
principle.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from analysis.l5.historical_l2.bbo_offset import (MAX_CARRY_S, Book,
                                                  offset_ticks,
                                                  placement_consistency)
from analysis.l5.historical_l2.mint_match import feasible

TICK = 0.01
LEADS = (1, 5, 10, 30)
FOLLOWS = (1, 5, 10, 30)
#: how far back to look for continuous size at a price
RESTING_LOOKBACK_S = 300.0


def size_at(row: Optional[dict], price: float, side: str = "bid") -> Optional[float]:
    if not row:
        return None
    ps, ss = row.get(side + "_prices") or [], row.get(side + "_sizes") or []
    tot = 0.0
    for p, s in zip(ps, ss):
        if abs(float(p) - price) < 1e-9:
            tot += float(s)
    return tot


def depth_window(row: Optional[dict], price: float, ticks: int,
                 side: str = "bid") -> Optional[float]:
    """Total size within +/- `ticks` of `price` on one side of the book."""
    if not row:
        return None
    ps, ss = row.get(side + "_prices") or [], row.get(side + "_sizes") or []
    lo, hi = price - ticks * TICK - 1e-9, price + ticks * TICK + 1e-9
    return sum(float(s) for p, s in zip(ps, ss) if lo <= float(p) <= hi)


def size_at_or_above(row: Optional[dict], price: float) -> Optional[float]:
    """Bid size at prices >= `price`. The closest the archive gets to
    'quantity ahead of an order at this price' -- and it is still only depth
    at price levels, never attributed to anyone."""
    if not row:
        return None
    ps, ss = row.get("bid_prices") or [], row.get("bid_sizes") or []
    return sum(float(s) for p, s in zip(ps, ss) if float(p) >= price - 1e-9)


def total_depth(row: Optional[dict], side: str = "bid") -> Optional[float]:
    if not row:
        return None
    return sum(float(s) for s in (row.get(side + "_sizes") or []))


def n_levels(row: Optional[dict], side: str = "bid") -> Optional[int]:
    if not row:
        return None
    return len(row.get(side + "_prices") or [])


class FifoPairs:
    """Matched inventory and MARGINAL pair cost, matched FIFO.

    `matched` is min(up, down) by construction; the marginal pair cost is the
    cost of the pairs THIS fill actually formed, which is what a placement
    rule would be exposed to -- an average over the whole window would hide
    it. A fill that forms no pair returns None rather than 0.
    """

    def __init__(self) -> None:
        self.q: Dict[str, Deque[List[float]]] = {"up": deque(), "down": deque()}
        self.inv = {"up": 0.0, "down": 0.0}
        self.cost = {"up": 0.0, "down": 0.0}
        self.matched = 0.0
        self.pair_cost_sum = 0.0

    def buy(self, side: str, px: float, qty: float) -> Dict[str, Any]:
        self.inv[side] += qty
        self.cost[side] += px * qty
        self.q[side].append([px, qty])
        other = "down" if side == "up" else "up"
        formed = 0.0
        wsum = 0.0
        while self.q[side] and self.q[other]:
            a, b = self.q[side][0], self.q[other][0]
            take = min(a[1], b[1])
            formed += take
            wsum += (a[0] + b[0]) * take
            a[1] -= take
            b[1] -= take
            if a[1] <= 1e-12:
                self.q[side].popleft()
            if b[1] <= 1e-12:
                self.q[other].popleft()
        self.matched += formed
        self.pair_cost_sum += wsum
        return {"pairs_formed_by_this_fill": round(formed, 6),
                "marginal_pair_cost": (round(wsum / formed, 6)
                                       if formed > 0 else None)}

    def snap(self) -> Dict[str, Any]:
        up, dn = self.inv["up"], self.inv["down"]
        return {"inv_up": round(up, 4), "inv_down": round(dn, 4),
                "matched": round(self.matched, 4),
                "residual": round(abs(up - dn), 4),
                "imbalance_up_minus_down": round(up - dn, 4),
                "running_pair_cost": (round(self.pair_cost_sum / self.matched, 6)
                                      if self.matched > 0 else None)}


def classify(snap_own: Optional[dict], snap_comp: Optional[dict],
             price: float) -> Tuple[str, Optional[bool]]:
    """The four mechanisms, kept separate everywhere downstream."""
    cls = placement_consistency(snap_own, price)
    mint = None
    if snap_comp is not None:
        mint = feasible(price, snap_comp.get("best_bid"))
    if cls == "RESTING_BID_CONSISTENT":
        return "RESTING_BID_CONSISTENT", mint
    if cls == "INSIDE_SPREAD_IMPROVED_THE_TOUCH":
        return "INSIDE_SPREAD", mint
    if cls == "AT_OR_ABOVE_ASK_NOT_MEASURABLE":
        return ("MINT_CONSISTENT" if mint else "UNRESOLVED_ABOVE_ASK"), mint
    return "UNRESOLVED_NO_BOOK", mint


def build(books: Dict[str, Book], tok_up: str, tok_dn: str, start: int,
          end: int, fills: List[dict], *, slug: str, clen: str,
          block_times: Dict[int, int]) -> List[Dict[str, Any]]:
    """Chronological, deterministic order: (ts, block, log_index)."""
    lab = {tok_up: "up", tok_dn: "down"}
    comp = {tok_up: tok_dn, tok_dn: tok_up}
    ev = [f for f in fills
          if f.get("_ts") is not None and start <= f["_ts"] < end
          and f["token_id"] in lab]
    ev.sort(key=lambda f: (f["_ts"], f.get("block") or 0,
                           f.get("log_index") or 0))
    per_block: Dict[int, int] = {}
    for f in ev:
        per_block[f.get("block")] = per_block.get(f.get("block"), 0) + 1
    bl = sorted(block_times)
    pos = {b: i for i, b in enumerate(bl)}

    led = FifoPairs()
    prev: Dict[str, Optional[dict]] = {"any": None, "up": None, "down": None}
    out: List[Dict[str, Any]] = []
    for i, f in enumerate(ev):
        t = int(f["_ts"])
        tok = f["token_id"]
        side = lab[tok]
        other = lab[comp[tok]]
        px, qty = float(f["price"]), float(f["size"])
        bk_own, bk_comp = books[tok], books[comp[tok]]

        s_own = bk_own.at(t - 1, max_age_s=MAX_CARRY_S)
        s_comp = bk_comp.at(t - 1, max_age_s=MAX_CARRY_S)
        s_own_at = bk_own.at(float(t), max_age_s=MAX_CARRY_S)
        cls, mint = classify(s_own, s_comp, px)

        # --- block-timestamp uncertainty, carried rather than hidden
        b = f.get("block")
        j = pos.get(b)
        nb = (block_times[bl[j + 1]] - block_times[b]
              if j is not None and j + 1 < len(bl) else None)
        pb = (block_times[b] - block_times[bl[j - 1]]
              if j is not None and j > 0 else None)

        # --- movement BEFORE (causal) and AFTER (consequence only)
        mv: Dict[str, Any] = {}
        # THE REFERENCE INSTANT IS t-1, THE LAST CAUSALLY AVAILABLE ONE, SO
        # THE LAG MUST BE t-1-L. An earlier version lagged from t-L while
        # referencing t-1, which made the 1 s "move" identically zero by
        # construction (both reads landed on the same observation) and
        # shifted every other lead by a second against the control, whose
        # reference is its own observation second.
        for L in LEADS:
            a = bk_own.at(t - 1 - L, max_age_s=MAX_CARRY_S)
            c = bk_comp.at(t - 1 - L, max_age_s=MAX_CARRY_S)
            mv["own_bid_tminus%d" % L] = (a or {}).get("best_bid")
            mv["comp_bid_tminus%d" % L] = (c or {}).get("best_bid")
            bb0 = (s_own or {}).get("best_bid")
            bbL = (a or {}).get("best_bid")
            mv["own_bid_move_%ds_before" % L] = (
                round(bb0 - bbL, 6) if None not in (bb0, bbL) else None)
            # did our price exist, with size, at this lead? These probe the
            # STATE L seconds before the fill, so they read at t-L, not at
            # t-1-L which is the reference for a MOVE ending at t-1.
            a2 = bk_own.at(t - L, max_age_s=MAX_CARRY_S)
            mv["price_existed_tminus%d" % L] = (
                None if a2 is None else (size_at(a2, px) or 0) > 0)
            mv["price_at_or_above_touch_tminus%d" % L] = (
                None if a2 is None or a2.get("best_bid") is None
                else px >= float(a2["best_bid"]) - 1e-9)
        for L in FOLLOWS:
            a = bk_own.at(t + L, max_age_s=MAX_CARRY_S)
            bb0 = (s_own_at or {}).get("best_bid")
            bbL = (a or {}).get("best_bid")
            mv["own_bid_move_%ds_after" % L] = (
                round(bbL - bb0, 6) if None not in (bb0, bbL) else None)

        # --- apparent resting duration: continuous size at our price before t
        rest_s = bk_own.level_present_s(__import__("decimal").Decimal(str(px)),
                                        until=float(t),
                                        lookback_s=RESTING_LOOKBACK_S)

        pre = led.snap()
        m = led.buy(side, px, qty)
        post = led.snap()
        d_imb = abs(post["imbalance_up_minus_down"]) - \
            abs(pre["imbalance_up_minus_down"])

        p_any, p_side = prev["any"], prev[side]
        row = {
            # identity
            "market_slug": slug, "contract_length": clen, "coin": "btc",
            "seq_in_window": i, "t": t, "tte_s": end - t,
            "secs_since_open": t - start,
            "token_id": tok, "outcome": side, "price": px, "quantity": qty,
            # timestamp precision / uncertainty
            "block": b, "log_index": f.get("log_index"),
            # the transaction is what makes a complete-set purchase ATOMIC;
            # keying pairs on the block alone conflates coincidence with one
            # event, which is how the first pass nearly mis-read these.
            "tx": f.get("tx"), "l5_role": f.get("l5_role"),
            "l5_fills_sharing_this_block": per_block.get(b),
            "block_interval_to_next_s": nb, "block_interval_from_prev_s": pb,
            "timestamp_precision": "whole seconds, from the block timestamp",
            # last known book before the action, and how stale it was
            "obs_ts_own": (None if s_own is None
                           else t - 1 - (s_own.get("carried_s") or 0)),
            "obs_age_s_own": (None if s_own is None
                              else 1 + (s_own.get("carried_s") or 0)),
            "obs_age_s_comp": (None if s_comp is None
                               else 1 + (s_comp.get("carried_s") or 0)),
            # both-outcome BBO and spread
            "own_bid": (s_own or {}).get("best_bid"),
            "own_ask": (s_own or {}).get("best_ask"),
            "comp_bid": (s_comp or {}).get("best_bid"),
            "comp_ask": (s_comp or {}).get("best_ask"),
            "own_spread": (None if s_own is None
                           or s_own.get("best_ask") is None
                           or s_own.get("best_bid") is None
                           else round(float(s_own["best_ask"])
                                      - float(s_own["best_bid"]), 6)),
            "pair_sum_bids": (None if s_own is None or s_comp is None
                              or s_own.get("best_bid") is None
                              or s_comp.get("best_bid") is None
                              else round(float(s_own["best_bid"])
                                         + float(s_comp["best_bid"]), 6)),
            # distance of the fill from the touch
            "offset_ticks_below_bid": offset_ticks(
                (s_own or {}).get("best_bid"), px),
            "ticks_below_ask": (
                None if s_own is None or s_own.get("best_ask") is None
                else int(round((float(s_own["best_ask"]) - px) / TICK))),
            # depth at and around the fill price (full ladder, not just BBO)
            "size_at_fill_price_before": size_at(s_own, px),
            "size_at_or_above_fill_price_before": size_at_or_above(s_own, px),
            "depth_within_1_tick": depth_window(s_own, px, 1),
            "depth_within_3_ticks": depth_window(s_own, px, 3),
            "depth_within_5_ticks": depth_window(s_own, px, 5),
            "depth_at_own_bid": size_at(s_own, (s_own or {}).get("best_bid")
                                        if (s_own or {}).get("best_bid")
                                        is not None else px),
            "total_bid_depth_own": total_depth(s_own, "bid"),
            "total_ask_depth_own": total_depth(s_own, "ask"),
            "bid_levels_own": n_levels(s_own, "bid"),
            "total_bid_depth_comp": total_depth(s_comp, "bid"),
            # mechanism, kept separate downstream
            "mechanism": cls, "mint_feasible": mint,
            # latent-order evidence
            "apparent_resting_s_at_price": rest_s,
            # sequence context
            "prev_fill_same_side_dt_s": (None if p_side is None
                                         else t - p_side["t"]),
            "prev_fill_same_side_price": (None if p_side is None
                                          else p_side["price"]),
            "prev_fill_same_side_qty": (None if p_side is None
                                        else p_side["quantity"]),
            "prev_fill_any_dt_s": (None if p_any is None else t - p_any["t"]),
            "prev_fill_any_side": (None if p_any is None
                                   else p_any["outcome"]),
            "prev_fill_any_price": (None if p_any is None
                                    else p_any["price"]),
            "prev_fill_any_qty": (None if p_any is None
                                  else p_any["quantity"]),
            "is_first_fill_in_window": p_any is None,
            "is_first_fill_on_this_side": p_side is None,
            # inventory state BEFORE and AFTER
            "inv_before": pre, "inv_after": post,
            "pairs_formed_by_this_fill": m["pairs_formed_by_this_fill"],
            "marginal_pair_cost": m["marginal_pair_cost"],
            "imbalance_abs_before": abs(pre["imbalance_up_minus_down"]),
            "imbalance_abs_after": abs(post["imbalance_up_minus_down"]),
            "imbalance_delta": round(d_imb, 4),
            "action_reduces_imbalance": d_imb < 0,
            "bought_the_heavy_side": (
                pre["imbalance_up_minus_down"] > 0 if side == "up"
                else pre["imbalance_up_minus_down"] < 0),
            "ownership_caveat": ("size_at_* are depth at PRICE LEVELS and are "
                                 "never attributed to L5; absence of size is "
                                 "not proof he had no order there"),
        }
        row.update(mv)
        out.append(row)
        prev["any"] = row
        prev[side] = row
    return out
