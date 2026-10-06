"""A control sample, because "he buys when the price falls" needs a baseline.

THE PROBLEM THIS SOLVES. Conditioning only on fills can "discover" that L5
buys after a fall when falls are simply common in these books. Every movement
claim in this study is therefore reported against a control drawn from the
SAME windows and the SAME archived observation timestamps, differing only in
that no L5 fill occurred there.

HOW THE CONTROL IS DRAWN. For each window and each token, take every genuine
archived observation second that is NOT within +/- `exclude_s` of an L5 fill
on that token, and compute the identical movement features at that second.
Excluding the neighbourhood of fills keeps the control from being
contaminated by the very events it is the baseline for.

This is a baseline for MOVEMENT and DEPTH only. It is not a counterfactual
for quantity: L5 has no quantity at a second where he did not trade.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from analysis.l5.historical_l2.bbo_offset import MAX_CARRY_S, Book

LEADS = (1, 5, 10, 30)


def build_control(books: Dict[str, Book], tok_up: str, tok_dn: str,
                  start: int, end: int, fill_secs: Dict[str, Set[int]], *,
                  slug: str, clen: str, exclude_s: int = 5
                  ) -> List[Dict[str, Any]]:
    lab = {tok_up: "up", tok_dn: "down"}
    comp = {tok_up: tok_dn, tok_dn: tok_up}
    out: List[Dict[str, Any]] = []
    for tok in (tok_up, tok_dn):
        bk, bkc = books[tok], books[comp[tok]]
        bad: Set[int] = set()
        for s in fill_secs.get(tok, set()):
            for k in range(-exclude_s, exclude_s + 1):
                bad.add(s + k)
        for ts in bk.t:
            t = int(ts)
            if not (start <= t < end) or t in bad:
                continue
            s_own = bk.at(float(t), max_age_s=MAX_CARRY_S)
            s_comp = bkc.at(float(t), max_age_s=MAX_CARRY_S)
            if s_own is None:
                continue
            row: Dict[str, Any] = {
                "market_slug": slug, "contract_length": clen,
                "t": t, "tte_s": end - t, "secs_since_open": t - start,
                "outcome": lab[tok],
                "own_bid": s_own.get("best_bid"),
                "own_ask": s_own.get("best_ask"),
                "comp_bid": (s_comp or {}).get("best_bid"),
                "own_spread": (None if s_own.get("best_ask") is None
                               or s_own.get("best_bid") is None
                               else round(float(s_own["best_ask"])
                                          - float(s_own["best_bid"]), 6)),
                "total_bid_depth_own": sum(
                    float(x) for x in (s_own.get("bid_sizes") or [])),
                "bid_levels_own": len(s_own.get("bid_prices") or []),
                "depth_at_own_bid": (
                    sum(float(s) for p, s in zip(s_own.get("bid_prices") or [],
                                                 s_own.get("bid_sizes") or [])
                        if s_own.get("best_bid") is not None
                        and abs(float(p) - float(s_own["best_bid"])) < 1e-9)),
                "is_control": True,
            }
            bb0 = s_own.get("best_bid")
            for L in LEADS:
                a = bk.at(t - L, max_age_s=MAX_CARRY_S)
                bbL = (a or {}).get("best_bid")
                row["own_bid_move_%ds_before" % L] = (
                    round(bb0 - bbL, 6) if None not in (bb0, bbL) else None)
            out.append(row)
    return out
