"""Are the two outcome tokens two books, or one book in two conventions?

THIS EXISTS BECAUSE ITS ABSENCE PRODUCED A WRONG HEADLINE. On 2026-09-21 the
"complement buyer pool" was reported as liquidity the own book could not see,
and an executor requirement was derived from it. Both were wrong: measured
level by level over the historical L2, token A's ASK ladder is token B's BID
ladder mirrored at 99.9% price-level and 99.1% size agreement. Resting a NO bid
IS resting a YES offer. There is one queue.

The touch-level version of this -- complement_bid + own_ask == 1 -- is what
makes the mint-feasibility test an identity (see mint_onchain.py). This module
is the ladder-wide version, which is the one that decides whether a separate
complement pool exists at all.

A VENUE THAT FAILED THIS TEST would be genuinely interesting: it would have two
fillable populations and the complement pool would be real liquidity. None of
the three venues in scope does. PMUS publishes a single bestBid/bestAsk per
market; Kalshi publishes yes_dollars/no_dollars ladders that are complements of
each other.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def mirror(levels_prices, levels_sizes, par: float = 1.0) -> Dict[float, float]:
    """A bid ladder expressed in the complement's price terms."""
    return {round(par - float(p), 4): float(s)
            for p, s in zip(levels_prices or [], levels_sizes or [])}


def ladder(prices, sizes) -> Dict[float, float]:
    return {round(float(p), 4): float(s) for p, s in zip(prices or [], sizes or [])}


def compare(own: Optional[dict], complement: Optional[dict],
            par: float = 1.0) -> Optional[Dict[str, Any]]:
    """Own ASK ladder against the complement's BID ladder, mirrored.

    Returns None when either side is unmeasured -- an empty ladder is not
    evidence of disagreement.
    """
    if not own or not complement:
        return None
    a = ladder(own.get("ask_prices"), own.get("ask_sizes"))
    b = mirror(complement.get("bid_prices"), complement.get("bid_sizes"), par)
    if not a or not b:
        return None
    shared = set(a) & set(b)
    only = set(a) ^ set(b)
    eq = sum(1 for k in shared if abs(a[k] - b[k]) <= 1e-6)
    return {"levels_shared": len(shared), "levels_on_one_side_only": len(only),
            "sizes_equal": eq, "sizes_differing": len(shared) - eq}


def verdict(totals: Dict[str, int]) -> Dict[str, Any]:
    lv = totals["levels_shared"] + totals["levels_on_one_side_only"]
    sz = totals["sizes_equal"] + totals["sizes_differing"]
    pa = totals["levels_shared"] / lv if lv else None
    sa = totals["sizes_equal"] / sz if sz else None
    one_book = bool(pa is not None and sa is not None and pa >= 0.95 and sa >= 0.95)
    return {"price_level_agreement": pa, "size_agreement": sa,
            "verdict": ("ONE_BOOK_TWO_CONVENTIONS" if one_book
                        else "TWO_SEPARATE_POPULATIONS"),
            "meaning": ("ONE_BOOK_TWO_CONVENTIONS means there is no separately "
                        "fillable complement pool: the complement's bids are "
                        "this token's offers. Visible-book depth is the queue.")}
