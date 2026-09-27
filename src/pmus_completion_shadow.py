"""Read-only clip-capacity completion shadow over the PMUS BTC15m book tape.

NOTHING HERE CAN TRADE. It replays a recorded tape. No transport, no order
verb, no credential, no arm token. It moves no money.

Governed by analysis/pmus/CLIP_CAPACITY_PREREG.md, which fixed every rule,
threshold and clip size before any row was scored.

THE TWO LOOKAHEAD GUARDS, structural rather than remembered:
  1. completion is sized to the residual held AT THE TRIGGER; the end-of-window
     residual is never visible to a decision;
  2. the residual SIDE is whatever the forward walk holds at that instant, never
     the side that turns out to be residual at settlement.
Settlement is read exactly once, by the scorer, after the walk has finished.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from typing import Any, Dict, List, Optional, Sequence, Tuple

THETA_TAKER = D("0.0695")
THETA_MAKER_MODELED = D("0.0125")   # MODELED only. Credited rebate is $0.


def _d(v) -> Optional[D]:
    if v is None:
        return None
    try:
        return D(str(v))
    except Exception:
        return None


def taker_fee(price: D, qty: D) -> D:
    return THETA_TAKER * qty * price * (D(1) - price)


def modeled_maker_rebate(price: D, qty: D) -> D:
    """NEVER added into net. Reported on its own line, always."""
    return THETA_MAKER_MODELED * qty * price * (D(1) - price)


def walk_ladder(levels: Sequence[Sequence[str]], want: D
                ) -> Tuple[D, D, bool]:
    """Consume `want` contracts across ACTUAL recorded levels.

    Returns (filled_qty, total_cost, short). `short` is True when displayed
    depth could not cover `want` -- the shortfall is NEVER filled at the touch
    price and never at a guessed one. That remainder is UNKNOWN.
    """
    filled = D(0)
    cost = D(0)
    for lvl in levels or []:
        if filled >= want:
            break
        px = _d(lvl[0] if len(lvl) > 0 else None)
        qty = _d(lvl[1] if len(lvl) > 1 else None)
        if px is None or qty is None or qty <= 0:
            continue
        take = want - filled
        if take > qty:
            take = qty
        filled += take
        cost += take * px
    return filled, cost, filled < want


def own_price(side: str, px: D) -> D:
    """Venue prices are always the LONG (UP/YES) side. DOWN's own price is 1-p."""
    return px if side == "up" else (D(1) - px)


def complement(side: str) -> str:
    return "down" if side == "up" else "up"


def buy_levels(row: Dict[str, Any], side: str) -> List[List[str]]:
    """Levels a TAKER buying `side` would consume, in that side's own terms.

    Buying UP lifts the offers at their quoted price. Buying DOWN is selling
    YES, which hits the bids, and each bid at p costs (1-p) in DOWN's terms.
    """
    if side == "up":
        return [[str(p), str(q)] for p, q in
                ((lvl[0], lvl[1]) for lvl in (row.get("offers") or []))]
    out = []
    for lvl in (row.get("bids") or []):
        p = _d(lvl[0])
        if p is None:
            continue
        out.append([str(D(1) - p), str(lvl[1])])
    return out
