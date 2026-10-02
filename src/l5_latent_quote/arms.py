"""Price arms P1-P5. Parallel experimental arms, never adaptive hindsight.

An arm that would be marketable is REFUSED_CROSS and is not placed. Nothing here
can cross: every returned price is asserted strictly below the prevailing ask.
"""
from __future__ import annotations
from . import config as C
from .state import depth_at


def _r2(x):
    return round(round(x / C.TICK) * C.TICK, 2)


def build(side, s, bk):
    """Return {arm: quote|None}. `side` is 'up' or 'dn'; we only ever BUY."""
    bid, ask = s.get(f"{side}_bid"), s.get(f"{side}_ask")
    out = {}
    cands = {}
    if bid is not None:
        cands["P1_AT_BID"] = _r2(bid)
        cands["P2_BEHIND_BID"] = _r2(bid - C.TICK)
        cands["P3_IMPROVE_BID"] = _r2(bid + C.TICK)
    # P4 basis-constrained: highest passive price keeping projected pair cost <= cap
    other = "dn" if side == "up" else "up"
    ob = s.get(f"l5_obs_{other}_basis")
    if ob is not None and bid is not None:
        cands["P4_BASIS"] = _r2(min(bid, C.PAIR_COST_CAP - ob))
    # P5 state-driven: lean inside when the complement is expensive, behind when
    # cheap -- CLAMPED to stay strictly passive (defect D4). The spec requires
    # "never cross", so a state-driven price is bounded by ask - 1 tick rather
    # than allowed to propose a marketable price and be refused every time.
    if bid is not None:
        oc = s.get(f"{other}_ask")
        step = C.TICK if (oc is not None and oc >= 0.55) else -C.TICK
        want = bid + step
        if ask is not None:
            want = min(want, ask - C.TICK)
        cands["P5_STATE"] = _r2(want)

    for arm in C.ARMS:
        px = cands.get(arm)
        if px is None:
            out[arm] = None
            continue
        if px < C.TICK - 1e-9 or px > 1.0 - C.TICK + 1e-9:
            out[arm] = {"status": "REFUSED_RANGE", "price": px}
            continue
        if ask is not None and px >= ask - 1e-9:
            out[arm] = {"status": "REFUSED_CROSS", "price": px, "ask": ask}
            continue
        q_ahead = depth_at(bk, "bid", px)
        out[arm] = {"status": "PLACEABLE", "price": px, "size": C.SHADOW_SIZE,
                    "queue_ahead": q_ahead, "bid_at_place": bid, "ask_at_place": ask,
                    "passive_margin_ticks": round((ask - px) / C.TICK, 2) if ask else None}
    for arm, v in out.items():
        if v and v.get("status") == "PLACEABLE":
            assert ask is None or v["price"] < ask, "arm would cross -- refused by assertion"
    return out
