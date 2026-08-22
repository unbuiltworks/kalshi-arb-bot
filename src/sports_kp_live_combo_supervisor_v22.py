"""Production combo supervisor v22: prioritize correlated same-game MVE baskets.

The v21 generator safely filters nested selections from the same exact Kalshi
`event_ticker`, but its remaining ordering can still spend most bounded MVE/RFQ
capacity on unrelated cross-game baskets whose fair conjunction price is usually
well aligned with the component complements.

This layer changes *manufacturing order only*: it gives bounded priority to
baskets containing distinct market families from the same underlying game
(e.g. NFL spread + NFL total, or MLB game + total/prop). Those are the places
where correlation-dependent MVE pricing is most likely to diverge from the
component hedge basket.

No quote, fee, rule-certification, freshness, depth, quantity, capital, or order
entry semantics are changed. The existing v21 duplicate-event filter remains in
force, and baseline candidates are still interleaved so discovery does not
collapse onto one correlated family.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v21 as v21
from src import sports_kp_mve_combo_generator as g

logger = v21.logger
_ORIGINAL_CANDIDATE_COMBOS = g._candidate_combos


def _event_parts(ticker: str) -> tuple[str, str]:
    text = str(ticker or "").upper()
    if "-" not in text:
        return text, text
    family, suffix = text.split("-", 1)
    return family, suffix


def _correlation_score(legs: tuple[Any, ...]) -> int:
    """Count same-game/different-family pairs; exact events are already unique."""
    score = 0
    parts = [_event_parts(leg.event_ticker) for leg in legs]
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            family_i, game_i = parts[i]
            family_j, game_j = parts[j]
            if game_i == game_j and family_i != family_j:
                score += 1
    return score


def _candidate_combos_correlated_first(collection, event_legs, *, max_candidates, min_locked):
    expanded_cap = min(max(max_candidates * 5, max_candidates), 1200)
    raw = _ORIGINAL_CANDIDATE_COMBOS(
        collection,
        event_legs,
        max_candidates=expanded_cap,
        min_locked=min_locked,
    )
    correlated = []
    baseline = []
    for row in raw:
        score = _correlation_score(row[1])
        if score > 0:
            correlated.append((score, row))
        else:
            baseline.append(row)

    correlated.sort(
        key=lambda item: (
            -item[0],
            len(item[1][1]),
            item[1][0],
            max(leg.close_time for leg in item[1][1]),
        )
    )

    out = []
    ci = bi = 0
    # Spend most bounded creation capacity on correlated baskets while retaining
    # one baseline candidate in every four slots for broad discovery.
    while len(out) < max_candidates and (ci < len(correlated) or bi < len(baseline)):
        for _ in range(3):
            if ci >= len(correlated) or len(out) >= max_candidates:
                break
            out.append(correlated[ci][1])
            ci += 1
        if bi < len(baseline) and len(out) < max_candidates:
            out.append(baseline[bi])
            bi += 1
        if ci >= len(correlated) and bi >= len(baseline):
            break

    logger.warning(
        "SPORTS_MVE_CORRELATED_PRIORITY %s",
        json.dumps(
            {
                "collection": collection.get("collection_ticker"),
                "raw_candidates_examined": len(raw),
                "correlated_candidates": len(correlated),
                "baseline_candidates": len(baseline),
                "returned_candidates": len(out),
                "priority_mix": "3_CORRELATED_TO_1_BASELINE",
                "manufacturing_order_only": True,
                "execution_semantics_changed": False,
                "order_entry": False,
            },
            separators=(",", ":"),
        ),
    )
    return out


g._candidate_combos = _candidate_combos_correlated_first

run = v21.run
main = v21.main

if __name__ == "__main__":
    main()
