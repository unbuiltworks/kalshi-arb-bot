"""Production combo supervisor v31: hard-constraint manufacturing + quality pacing.

The first 600-attempt v30 sample produced zero executable candidates. Generic
2/3-leg baskets repeatedly priced several cents through the scalar-safe floor,
so spending the full creation allowance on every pass is not justified.

This layer changes manufacturing order only:
* search a wider ranked universe before redundancy filtering so rejected rows do
  not silently shrink/divert the shortlist;
* prioritize deterministic same-game SPREAD-YES + TOTAL-NO contradictions when
  the spread margin threshold is at least the total threshold (the two selected
  outcomes cannot both settle YES because total points are at least winning margin);
* next prefer valid same-game SPREAD+TOTAL structures;
* if no hard contradiction is present, return only a small exploration set so
  the finite daily creation allowance is conserved for better live windows.

No execution semantics change. Existing q=1, >=3c conservative after-fee lock,
exact quote/depth/capital/rule/freshness checks, one live attempt per process,
flatten/hard-halt and settlement accounting remain unchanged.
"""
from __future__ import annotations

from decimal import Decimal
import json
import re
from typing import Any

from src import sports_kp_live_combo_supervisor_v30 as v30
from src import sports_kp_live_combo_supervisor_v29 as v29
from src import sports_kp_mve_combo_generator as g

logger = v30.logger
D = Decimal
_TRAILING_NUMBER = re.compile(r"(\d+(?:\.\d+)?)$")


def _encoded_threshold(market_ticker: str) -> Decimal | None:
    """Return the encoded half-point threshold ordinal from a sports ticker.

    Current NFL/WNBA spread and total tickers encode e.g. -DEN21 / -27 for
    >20.5 / >26.5.  Comparing the encoded ordinals is sufficient and avoids
    inventing a threshold when the ticker does not end numerically.
    """
    tail = str(market_ticker or "").upper().rsplit("-", 1)[-1]
    match = _TRAILING_NUMBER.search(tail)
    if not match:
        return None
    try:
        return D(match.group(1))
    except Exception:
        return None


def _hard_spread_total_contradiction(legs: tuple[Any, ...]) -> bool:
    spreads: list[tuple[str, Decimal]] = []
    totals: list[tuple[str, Decimal]] = []
    for leg in legs:
        family, game = v29._parts(getattr(leg, "event_ticker", ""))
        side = str(getattr(leg, "side", "") or "").lower()
        threshold = _encoded_threshold(getattr(leg, "market_ticker", ""))
        if threshold is None:
            continue
        if family.endswith("SPREAD") and side == "yes":
            spreads.append((game, threshold))
        elif family.endswith("TOTAL") and side == "no":
            totals.append((game, threshold))
    return any(game_s == game_t and spread_cut >= total_cut
               for game_s, spread_cut in spreads
               for game_t, total_cut in totals)


def _same_game_spread_total(legs: tuple[Any, ...]) -> bool:
    spread_games: set[str] = set()
    total_games: set[str] = set()
    for leg in legs:
        family, game = v29._parts(getattr(leg, "event_ticker", ""))
        if family.endswith("SPREAD"):
            spread_games.add(game)
        elif family.endswith("TOTAL"):
            total_games.add(game)
    return bool(spread_games & total_games)


def _key(collection_ticker: str, legs: tuple[Any, ...]) -> str:
    return collection_ticker + "|" + "|".join(sorted(
        f"{getattr(x, 'event_ticker', '')}:{getattr(x, 'market_ticker', '')}:{getattr(x, 'side', '')}"
        for x in legs
    ))


def _v31_candidates(collection, event_legs, *, max_candidates, min_locked):
    # Ask v29 for a much wider *ranked* universe, then apply v30's pairwise
    # invalidity rule.  v30 previously filtered only after v29 had truncated to
    # max_candidates, which could turn an 80-row shortlist into ~60 valid rows.
    expanded = min(max(max_candidates * 8, 320), 1200)
    raw = v29._v29_candidates(
        collection,
        event_legs,
        max_candidates=expanded,
        min_locked=min_locked,
    )
    valid: list[Any] = []
    rejected_redundant = 0
    seen: set[str] = set()
    collection_ticker = str(collection.get("collection_ticker") or "")
    for row in raw:
        legs = row[1]
        if v30._pairwise_redundant_game_spread(legs):
            rejected_redundant += 1
            continue
        key = _key(collection_ticker, legs)
        if key in seen:
            continue
        seen.add(key)
        valid.append(row)

    hard = [row for row in valid if _hard_spread_total_contradiction(row[1])]
    correlated = [row for row in valid
                  if not _hard_spread_total_contradiction(row[1])
                  and _same_game_spread_total(row[1])]
    exploration = [row for row in valid
                   if not _hard_spread_total_contradiction(row[1])
                   and not _same_game_spread_total(row[1])]

    # Within each class retain prior economic/feedback ordering as much as
    # possible, with lower current component all-in cost first.
    for rows in (hard, correlated, exploration):
        rows.sort(key=lambda row: (row[0], max(x.close_time for x in row[1]), len(row[1])))

    out: list[Any] = []
    if hard:
        # Hard constraints deserve most of a full 12-RFQ pass, while retaining
        # correlation and exploration slots to avoid overfitting one rule class.
        for rows, limit in ((hard, 8), (correlated, 3), (exploration, 1)):
            for row in rows[:limit]:
                if len(out) < max_candidates:
                    out.append(row)
    else:
        # No mathematically constrained structure: do not burn 12 finite MVE
        # creates on the same generic distribution that failed the 600-attempt
        # sample.  Keep a bounded four-row learning pass.
        for rows, limit in ((correlated, 3), (exploration, 1)):
            for row in rows[:limit]:
                if len(out) < min(max_candidates, 4):
                    out.append(row)

    logger.warning("SPORTS_MVE_V31_HARD_CONSTRAINT_PRIORITY %s", json.dumps({
        "collection": collection_ticker,
        "raw_ranked_candidates": len(raw),
        "pairwise_game_spread_rejected": rejected_redundant,
        "valid_after_filter": len(valid),
        "hard_spread_yes_total_no": len(hard),
        "same_game_spread_total_other": len(correlated),
        "generic_exploration": len(exploration),
        "returned_candidates": len(out),
        "quality_paced": True,
        "full_pass_only_with_hard_constraint": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _v31_candidates

run = v30.run
main = v30.main

if __name__ == "__main__":
    main()
