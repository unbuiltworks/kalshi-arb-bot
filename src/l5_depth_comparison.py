"""Did 1 tick or 2 ticks behind the touch actually fill better? READ-ONLY.

WHY THIS IS NOT A CALCULATION. A deeper quote shows a larger quoted edge by
construction -- 1 tick behind on a one-tick book prices the pair at 0.97, 2
ticks at 0.95 -- so comparing quoted edges always "proves" that deeper is
better and proves nothing at all. What decides it is the product of the edge
and the probability BOTH legs fill, and that probability is a measurement, not
a parameter. Depth that never fills has an infinite quoted edge and earns
nothing.

So this reads the ORDER AND FILL RECORDS and reports, per depth:

  - orders placed at that depth, and how many filled at all
  - the single-leg fill rate, which is what the owner observes directly
  - the BOTH-LEG rate per window, which is what the pair economics need
  - realized edge per matched pair, from recorded fill prices and commissions
  - the break-even single-leg fill rate the measured edge requires

A depth with too few orders to say anything reports INSUFFICIENT_SAMPLE
rather than a number. That refusal is the point: this exists to stop us
assuming 2 ticks is better because its quoted edge is larger.

NOTHING HERE SUBMITS, CANCELS, SETTLES OR WRITES ANYTHING.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src import l5_two_sided_probe as TS

#: below this many orders at a depth, no rate is reported. A fill rate from
#: three orders is not evidence; naming it a number invites acting on it.
MIN_ORDERS_FOR_A_RATE = 20
#: and below this many WINDOWS, no both-leg rate is reported. The both-leg
#: rate is the binding quantity and needs whole windows, not orders.
MIN_WINDOWS_FOR_A_PAIR_RATE = 10


def _d(v: Any) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def depth_of(order: Mapping[str, Any], *, recorded_only: bool = False
             ) -> Optional[int]:
    """How many ticks behind the touch this order rested, if recordable.

    ONLY A RECORDED DEPTH IS A DEPTH, which is what `recorded_only` enforces
    and what `compare` now uses.

    WHY, measured on the production tape. The first version fell back to
    deriving the depth from the touch at placement and the order's own price.
    Run against the real volume that attributed 7,228 records into buckets
    from depth 0 to depth 96 -- 1,134 at depth 0, 757 at depth 2 -- and NONE
    of them were probe orders, because nothing had been placed. They were the
    ordinary L5-mirroring decisions, whose prices come from L5's own actions,
    so the arithmetic was measuring how far L5 quoted from the touch and
    reporting it as our resting depth. Every row also read orders_filled 0,
    because a shadow record never fills, so the rates were structurally zero
    and meaningless.

    That is worse than UNKNOWN_DEPTH: it manufactures a sample. The derived
    value is still computed for diagnostics, and it is no longer allowed to
    feed a rate or a verdict.
    """
    for key in ("depth_ticks", "ts_depth_ticks"):
        if order.get(key) is not None:
            try:
                return int(order[key])
            except (TypeError, ValueError):
                return None
    if recorded_only:
        return None
    px = _d(order.get("price"))
    tick = _d(order.get("tick")) or D("0.01")
    side = str(order.get("side") or "").upper()
    # the touch in OWN-OUTCOME terms at the moment of placement
    touch = _d(order.get("touch_at_placement"))
    if touch is None:
        bid, ask = _d(order.get("bid_at_placement")), _d(
            order.get("ask_at_placement"))
        if side == "UP" and bid is not None:
            touch = bid
        elif side == "DOWN" and ask is not None:
            touch = D(1) - ask
    if px is None or touch is None or tick <= 0:
        return None
    steps = (touch - px) / tick
    if steps != steps.to_integral_value() or steps < 0:
        return None
    return int(steps)


def _filled(order: Mapping[str, Any]) -> D:
    for key in ("filled_qty", "cum_quantity", "cumQuantity", "filled"):
        v = _d(order.get(key))
        if v is not None:
            return v
    return D(0)


def compare(orders: Iterable[Mapping[str, Any]],
            depths: Iterable[int] = (1, 2)) -> Dict[str, Any]:
    """Per-depth fill statistics from actual records. No simulation.

    `orders` are our own order records: each needs a side, a price, a market
    (the window), a filled quantity, and either a recorded depth or the touch
    it was placed against. A commission, where recorded, is used for the
    realized edge; where it is absent the edge is reported as None rather
    than as the quoted edge, because an unpaid fee is not a zero fee.
    """
    rows = [dict(o) for o in orders]
    buckets: Dict[Any, List[Dict[str, Any]]] = {}
    unknown = 0
    derived_only = 0
    for o in rows:
        d = depth_of(o, recorded_only=True)
        if d is None:
            unknown += 1
            # was it derivable at all? Reported, never counted.
            if depth_of(o) is not None:
                derived_only += 1
            continue
        buckets.setdefault(d, []).append(o)

    out: Dict[str, Any] = {
        "orders_read": len(rows), "unknown_depth": unknown,
        "derived_depth_NOT_counted": derived_only,
        "labels_attribution": (
            "only an order whose RECORDED depth_ticks the probe wrote is "
            "counted. derived_depth_NOT_counted is how many could have had a "
            "depth inferred from the touch and their own price; inferring it "
            "attributed 7,228 ordinary L5-mirroring records into buckets "
            "from 0 to 96 ticks on the production tape, measuring how far "
            "L5 quoted from the touch rather than where we rested. A "
            "manufactured sample is worse than no sample."),
        "min_orders_for_a_rate": MIN_ORDERS_FOR_A_RATE,
        "min_windows_for_a_pair_rate": MIN_WINDOWS_FOR_A_PAIR_RATE,
        "depths": {}}
    for d in sorted(set(int(x) for x in depths) | set(
            k for k in buckets if isinstance(k, int))):
        got = buckets.get(d, [])
        n = len(got)
        rec: Dict[str, Any] = {"orders": n}
        if n < MIN_ORDERS_FOR_A_RATE:
            rec["single_leg_fill_rate"] = None
            rec["verdict"] = "INSUFFICIENT_SAMPLE"
            rec["labels"] = ("fewer than %d orders at this depth. No rate is "
                             "reported: a fill rate from a handful of orders "
                             "is not evidence, and naming it a number "
                             "invites acting on it."
                             % MIN_ORDERS_FOR_A_RATE)
            out["depths"][str(d)] = rec
            continue
        filled = [o for o in got if _filled(o) > 0]
        rec["orders_filled"] = len(filled)
        rec["single_leg_fill_rate"] = str(
            (D(len(filled)) / D(n)).quantize(D("0.0001")))

        # BOTH LEGS, PER WINDOW. The binding quantity: a matched pair needs
        # an UP fill and a DOWN fill in the SAME market.
        windows: Dict[str, set] = {}
        for o in got:
            mkt = str(o.get("market") or "")
            windows.setdefault(mkt, set())
            if _filled(o) > 0:
                windows[mkt].add(str(o.get("side") or "").upper())
        complete = [m for m, sides in windows.items()
                    if {"UP", "DOWN"} <= sides]
        rec["windows"] = len(windows)
        rec["windows_both_legs_filled"] = len(complete)
        if len(windows) < MIN_WINDOWS_FOR_A_PAIR_RATE:
            rec["both_leg_rate"] = None
            rec["labels_pair"] = ("fewer than %d windows at this depth, so "
                                  "the both-leg rate -- the quantity the "
                                  "pair economics actually need -- is NOT "
                                  "reported." % MIN_WINDOWS_FOR_A_PAIR_RATE)
        else:
            rec["both_leg_rate"] = str(
                (D(len(complete)) / D(len(windows))).quantize(D("0.0001")))

        # REALIZED edge per matched pair, from recorded prices and
        # commissions. Absent commissions make it None, never the quoted edge.
        pair_edges: List[D] = []
        missing_fee = 0
        for m in complete:
            legs = {}
            for o in got:
                if str(o.get("market")) != m or _filled(o) <= 0:
                    continue
                legs[str(o.get("side")).upper()] = o
            if {"UP", "DOWN"} - set(legs):
                continue
            px_u, px_d = _d(legs["UP"].get("price")), _d(
                legs["DOWN"].get("price"))
            fee_u, fee_d = _d(legs["UP"].get("commission")), _d(
                legs["DOWN"].get("commission"))
            if px_u is None or px_d is None:
                continue
            if fee_u is None or fee_d is None:
                missing_fee += 1
                continue
            q = min(_filled(legs["UP"]), _filled(legs["DOWN"]))
            if q <= 0:
                continue
            pair_edges.append((q * (D(1) - px_u - px_d) - fee_u - fee_d) / q)
        rec["pairs_priced"] = len(pair_edges)
        rec["pairs_missing_commission"] = missing_fee
        if pair_edges:
            realized = sum(pair_edges, D(0)) / D(len(pair_edges))
            rec["realized_edge_per_contract"] = str(
                realized.quantize(D("0.00001")))
            be = TS.min_completion_probability(
                matched_edge=realized, residual_alpha=D("0.195"))
            rec["break_even_single_leg_rate_if_held"] = (
                None if be is None else str(be.quantize(D("0.0001"))))
        else:
            rec["realized_edge_per_contract"] = None
            rec["labels_edge"] = ("no pair had BOTH legs' fill price and "
                                  "commission recorded, so no realized edge "
                                  "is reported. A missing commission is not "
                                  "a zero commission.")
        rec["verdict"] = "MEASURED"
        out["depths"][str(d)] = rec

    measured = {k: v for k, v in out["depths"].items()
                if v.get("verdict") == "MEASURED"
                and v.get("both_leg_rate") is not None
                and v.get("realized_edge_per_contract") is not None}
    if len(measured) < 2:
        out["better_depth"] = None
        out["conclusion"] = "NOT_COMPARABLE"
        out["labels"] = ("fewer than two depths have BOTH a both-leg rate and "
                         "a realized edge, so no comparison is made. The "
                         "quoted edge is NOT used as a substitute: a deeper "
                         "quote always shows a larger quoted edge and that "
                         "says nothing about what it earns.")
        return out
    # EXPECTED VALUE PER WINDOW, which is what the comparison has to be on:
    # the realized edge weighted by the measured probability of getting it.
    scored = {k: D(v["realized_edge_per_contract"]) * D(v["both_leg_rate"])
              for k, v in measured.items()}
    best = max(scored, key=lambda k: scored[k])
    out["ev_per_window_per_contract"] = {k: str(v) for k, v in scored.items()}
    out["better_depth"] = int(best)
    out["conclusion"] = "MEASURED"
    out["labels"] = ("compared on realized edge TIMES the measured both-leg "
                     "fill rate, not on the quoted edge. Both inputs are "
                     "records of what happened.")
    return out


def from_tape(path: str, limit: int = 200000) -> Dict[str, Any]:
    """Read order records from a decision tape and compare. READ-ONLY."""
    if not path or not os.path.exists(path):
        return {"ok": False, "reason": "TAPE_NOT_FOUND", "path": path}
    orders: List[Dict[str, Any]] = []
    bad = 0
    with open(path) as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            try:
                doc = json.loads(line)
            except ValueError:
                bad += 1
                continue
            rec = (doc.get("record") or {})
            o = rec.get("ORDER") or {}
            if not o.get("client_order_id"):
                continue
            intent = o.get("intent") or {}
            side = str(intent.get("side") or rec.get("side") or "").upper()
            # THE BBO LIVES AT row.pmus.bbo, NOT AT row.bid. Reading
            # row["bid"] returned None for every one of the 7,310 records on
            # the production tape, so every order was UNKNOWN_DEPTH and the
            # comparison could never attribute a single one.
            bbo = ((doc.get("row") or {}).get("pmus") or {}).get("bbo") or {}
            # PREFER THE RECORDED DEPTH. The probe now writes depth_ticks and
            # the touch it placed against onto each leg, which is the only
            # unambiguous source; the BBO is the fallback for orders placed
            # before that was recorded.
            stage = rec.get("TWO_SIDED_PROBE") or {}
            legs = {str(l.get("side") or "").upper(): l
                    for l in (stage.get("legs") or [])
                    if isinstance(l, Mapping)}
            leg = legs.get(side) or {}
            orders.append({"market": rec.get("market") or intent.get("market"),
                           "side": side,
                           "price": intent.get("price"),
                           "qty": intent.get("qty"),
                           "filled_qty": o.get("filled_qty"),
                           "commission": o.get("commission"),
                           "depth_ticks": (o.get("depth_ticks")
                                           if o.get("depth_ticks") is not None
                                           else leg.get("depth_ticks")
                                           if leg.get("depth_ticks") is not None
                                           else stage.get("depth_ticks")),
                           "touch_at_placement": leg.get(
                               "touch_at_placement"),
                           "bid_at_placement": bbo.get("bid"),
                           "ask_at_placement": bbo.get("ask")})
    out = compare(orders)
    out["ok"] = True
    out["tape"] = path
    out["unparseable_lines"] = bad
    return out
