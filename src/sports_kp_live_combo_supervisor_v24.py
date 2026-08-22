"""Production combo supervisor v24: creation rank uses executable Kalshi economics.

The v16+ shadow prepricer intentionally allowed uncertified Polymarket/Gemini
component savings to influence MVE manufacturing order. That is useful research,
but the current live combo executor can only hedge the generated MVE with the
exact Kalshi component markets. Spending finite creation/RFQ capacity according
to an unexecutable replacement can therefore lower live-trade throughput.

v24 restores v8's pure same-Kalshi dual-direction candidate universe for actual
MVE creation, then reapplies the conservative exact-event duplicate filter,
same-game/different-family priority, and v23 process-local RFQ feedback. External
prepricing still runs and is logged as shadow research; it simply does not steer
creation until a separate certified cross-venue executor exists.

No execution threshold or order semantics change: q=1, >=3c after fees, current
RFQ/component depth, capital/position checks, final quote refresh, flatten/hard-
halt behavior, and settlement accounting remain unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v23 as v23
from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_mve_combo_generator as g

logger = v23.logger


def _candidate_key(collection: str, legs: tuple[Any, ...]) -> str:
    return collection + "|" + "|".join(sorted(
        f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in legs
    ))


def _kalshi_executable_feedback_candidates(collection, event_legs, *, max_candidates, min_locked):
    # Expand from the pre-v16 pure-Kalshi dual-direction generator. This uses
    # only executable Kalshi selected/complement component costs for pruning.
    expanded_cap = min(max(max_candidates * 6, max_candidates), 1400)
    raw = v8._dual_candidates(
        collection,
        event_legs,
        max_candidates=expanded_cap,
        min_locked=min_locked,
    )

    collection_ticker = str(collection.get("collection_ticker") or "")
    deduped = []
    seen: set[str] = set()
    duplicate_exact_event = 0
    for row in raw:
        legs = row[1]
        # Kalshi rejects nested/multiple selections from one exact event ticker
        # in combinations such as multiple full-game total thresholds. Preserve
        # v21's conservative pre-create filter.
        if len({leg.event_ticker for leg in legs}) != len(legs):
            duplicate_exact_event += 1
            continue
        key = _candidate_key(collection_ticker, legs)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    correlated = [row for row in deduped if v23._same_game_distinct_family(row[1])]
    baseline = [row for row in deduped if not v23._same_game_distinct_family(row[1])]
    correlated.sort(key=lambda row: (
        v23._feedback_rank(v23._family_signature_from_legs(row[1])),
        len(row[1]),
        row[0],
        max(leg.close_time for leg in row[1]),
    ))
    baseline.sort(key=lambda row: (row[0], max(leg.close_time for leg in row[1]), len(row[1])))

    out = []
    ci = bi = 0
    while len(out) < max_candidates and (ci < len(correlated) or bi < len(baseline)):
        for _ in range(3):
            if ci >= len(correlated) or len(out) >= max_candidates:
                break
            out.append(correlated[ci]); ci += 1
        if bi < len(baseline) and len(out) < max_candidates:
            out.append(baseline[bi]); bi += 1
        if ci >= len(correlated) and bi >= len(baseline):
            break

    logger.warning("SPORTS_MVE_EXECUTABLE_RANK %s", json.dumps({
        "collection": collection_ticker,
        "raw_pure_kalshi_candidates": len(raw),
        "duplicate_exact_event_rejected": duplicate_exact_event,
        "deduped_candidates": len(deduped),
        "same_game_correlated": len(correlated),
        "baseline": len(baseline),
        "returned_candidates": len(out),
        "creation_rank_uses_uncertified_external_prices": False,
        "external_preprice_remains_shadow": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _kalshi_executable_feedback_candidates

# v23's generate wrapper remains installed on base.generate_mves and continues
# to learn from actual RFQ outcomes produced by these executable-ranked baskets.
run = v23.run
main = v23.main

if __name__ == "__main__":
    main()
