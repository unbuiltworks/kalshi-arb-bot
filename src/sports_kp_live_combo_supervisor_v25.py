"""Production combo supervisor v25: prioritize implication-rich same-game baskets.

v24 materially improved the best observed same-Kalshi scalar-safe gap by ranking
MVE creation on executable Kalshi component economics.  This layer further
prioritizes same-game market-family combinations with deterministic implication
structure (especially GAME + SPREAD) before looser correlations such as SPREAD +
TOTAL. These structures are more likely to expose an MVE quote inconsistency while
remaining covered by the same scalar-safe identities already used by the live
executor.

This changes manufacturing order only. It does not infer probability, certify an
external venue, or alter q=1, the >=3c after-fee floor, RFQ/depth checks, capital,
position, final quote refresh, flatten/hard-halt, or settlement accounting.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v24 as v24
from src import sports_kp_mve_combo_generator as g

logger = v24.logger
_ORIGINAL = g._candidate_combos


def _parts(ticker: str) -> tuple[str, str]:
    text = str(ticker or "").upper()
    return tuple(text.split("-", 1)) if "-" in text else (text, text)  # type: ignore[return-value]


def _implication_tier(legs: tuple[Any, ...]) -> int:
    """Lower is better; only same-game distinct-family baskets are specialized."""
    parts = [_parts(getattr(leg, "event_ticker", "")) for leg in legs]
    games = {game for _family, game in parts}
    families = {family for family, _game in parts}
    if len(games) != 1 or len(families) < 2:
        return 4
    has_game = any(f.endswith("GAME") for f in families)
    has_spread = any(f.endswith("SPREAD") for f in families)
    has_total = any(f.endswith("TOTAL") for f in families)
    if has_game and has_spread:
        return 0
    if has_game and has_total:
        return 1
    if has_spread and has_total:
        return 2
    return 3


def _implication_first(collection, event_legs, *, max_candidates, min_locked):
    expanded = min(max(max_candidates * 5, max_candidates), 1200)
    raw = _ORIGINAL(
        collection,
        event_legs,
        max_candidates=expanded,
        min_locked=min_locked,
    )
    buckets: dict[int, list[Any]] = {i: [] for i in range(5)}
    for row in raw:
        buckets[_implication_tier(row[1])].append(row)
    for tier, rows in buckets.items():
        rows.sort(key=lambda row: (
            v24.v23._feedback_rank(v24.v23._family_signature_from_legs(row[1])),
            row[0],
            len(row[1]),
            max(leg.close_time for leg in row[1]),
        ))

    # Reserve broad exploration, but spend most slots on implication-rich rows.
    out: list[Any] = []
    cursors = {i: 0 for i in range(5)}
    pattern = (0, 0, 1, 2, 0, 3, 1, 4)
    while len(out) < max_candidates:
        added = False
        for tier in pattern:
            idx = cursors[tier]
            if idx < len(buckets[tier]) and len(out) < max_candidates:
                out.append(buckets[tier][idx])
                cursors[tier] += 1
                added = True
        if not added:
            break

    logger.warning("SPORTS_MVE_IMPLICATION_PRIORITY %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "raw_candidates": len(raw),
        "game_plus_spread": len(buckets[0]),
        "game_plus_total": len(buckets[1]),
        "spread_plus_total": len(buckets[2]),
        "other_same_game": len(buckets[3]),
        "baseline": len(buckets[4]),
        "returned_candidates": len(out),
        "manufacturing_order_only": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _implication_first
run = v24.run
main = v24.main

if __name__ == "__main__":
    main()
