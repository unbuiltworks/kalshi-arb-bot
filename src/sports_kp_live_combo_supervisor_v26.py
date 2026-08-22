"""Production combo supervisor v26: filter Kalshi-redundant GAME+SPREAD baskets.

v25 intentionally probed implication-rich same-game baskets. Production returned
Kalshi `duplicated_legs` for GAME + SPREAD selections because the exchange itself
recognizes that the selected moneyline/spread outcomes logically include one
another. Continuing to submit those baskets would only burn finite MVE creation
budget.

This layer conservatively rejects any same-game GAME+SPREAD basket before market
creation and prioritizes valid same-game SPREAD+TOTAL, then GAME+TOTAL, then other
same-game families, while retaining broad exploration. This is manufacturing
selection only; q=1, >=3c after-fee execution, RFQ/depth/capital/final-refresh,
flatten/hard-halt, and settlement rules are unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v25 as v25
from src import sports_kp_mve_combo_generator as g

logger = v25.logger
_ORIGINAL = v25.v24.g._candidate_combos if hasattr(v25.v24, "g") else g._candidate_combos


def _parts(ticker: str) -> tuple[str, str]:
    text = str(ticker or "").upper()
    return tuple(text.split("-", 1)) if "-" in text else (text, text)  # type: ignore[return-value]


def _families_and_games(legs: tuple[Any, ...]) -> tuple[set[str], set[str]]:
    parts = [_parts(getattr(leg, "event_ticker", "")) for leg in legs]
    return {f for f, _g in parts}, {game for _f, game in parts}


def _is_redundant_game_spread(legs: tuple[Any, ...]) -> bool:
    families, games = _families_and_games(legs)
    return bool(
        len(games) == 1
        and any(f.endswith("GAME") for f in families)
        and any(f.endswith("SPREAD") for f in families)
    )


def _valid_tier(legs: tuple[Any, ...]) -> int:
    families, games = _families_and_games(legs)
    if len(games) != 1 or len(families) < 2:
        return 4
    has_game = any(f.endswith("GAME") for f in families)
    has_spread = any(f.endswith("SPREAD") for f in families)
    has_total = any(f.endswith("TOTAL") for f in families)
    if has_spread and has_total:
        return 0
    if has_game and has_total:
        return 1
    return 2


def _valid_implication_rank(collection, event_legs, *, max_candidates, min_locked):
    expanded = min(max(max_candidates * 6, max_candidates), 1400)
    raw = _ORIGINAL(
        collection,
        event_legs,
        max_candidates=expanded,
        min_locked=min_locked,
    )
    redundant = 0
    buckets: dict[int, list[Any]] = {i: [] for i in range(5)}
    for row in raw:
        if _is_redundant_game_spread(row[1]):
            redundant += 1
            continue
        buckets[_valid_tier(row[1])].append(row)
    for rows in buckets.values():
        rows.sort(key=lambda row: (
            v25.v24.v23._feedback_rank(v25.v24.v23._family_signature_from_legs(row[1])),
            row[0],
            len(row[1]),
            max(leg.close_time for leg in row[1]),
        ))

    out: list[Any] = []
    cursors = {i: 0 for i in range(5)}
    # 4/8 spread+total, 1/8 game+total, 1/8 other same-game, 2/8 baseline.
    pattern = (0, 0, 1, 0, 2, 4, 0, 4)
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

    logger.warning("SPORTS_MVE_VALID_CORRELATION_PRIORITY %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "raw_candidates": len(raw),
        "redundant_game_spread_rejected_precreate": redundant,
        "spread_plus_total": len(buckets[0]),
        "game_plus_total": len(buckets[1]),
        "other_same_game": len(buckets[2]),
        "baseline": len(buckets[4]),
        "returned_candidates": len(out),
        "known_duplicated_legs_avoided": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _valid_implication_rank
run = v25.run
main = v25.main

if __name__ == "__main__":
    main()
