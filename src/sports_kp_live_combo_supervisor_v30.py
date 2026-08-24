"""Production combo supervisor v30: remove pairwise GAME+SPREAD redundancies.

v29 broadened manufacturing toward 3-leg prop-heavy baskets.  Kalshi then proved
that a GAME+SPREAD implication remains invalid even when a third unrelated leg is
present.  This layer applies that rule pairwise across every candidate before MVE
creation, preserving the v29 ranking and all live execution invariants.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v29 as v29
from src import sports_kp_mve_combo_generator as g

logger = v29.logger


def _pairwise_redundant_game_spread(legs: tuple[Any, ...]) -> bool:
    game_rows: list[tuple[str, str]] = []
    spread_rows: list[tuple[str, str]] = []
    for leg in legs:
        family, game = v29._parts(getattr(leg, "event_ticker", ""))
        if family.endswith("GAME"):
            game_rows.append((family, game))
        elif family.endswith("SPREAD"):
            spread_rows.append((family, game))
    return any(game_a == game_b for _fa, game_a in game_rows for _fb, game_b in spread_rows)


def _v30_candidates(collection, event_legs, *, max_candidates, min_locked):
    raw = v29._v29_candidates(
        collection,
        event_legs,
        max_candidates=max_candidates,
        min_locked=min_locked,
    )
    out = []
    rejected = 0
    for row in raw:
        if _pairwise_redundant_game_spread(row[1]):
            rejected += 1
            continue
        out.append(row)
        if len(out) >= max_candidates:
            break
    logger.warning("SPORTS_MVE_V30_PAIRWISE_REDUNDANCY_FILTER %s", json.dumps({
        "collection": str(collection.get("collection_ticker") or ""),
        "input_candidates": len(raw),
        "pairwise_game_spread_rejected": rejected,
        "returned_candidates": len(out),
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _v30_candidates

run = v29.run
main = v29.main

if __name__ == "__main__":
    main()
