"""Production combo supervisor v16: preprice before MVE manufacture.

Uses exact, bounded Polymarket q=1 books to rank NFL moneyline/spread/full-game-
total component complements *before* Kalshi MVE creation.  Ranking economics are
shadow-only. Original Kalshi Leg execution facts are preserved, all generated
MVEs are still scored by the existing conservative path, and cross-venue order
entry remains disabled until separate rule certification/executor validation.
"""
from __future__ import annotations

from decimal import Decimal
import itertools
import json
from typing import Any

# Import v15 first so strict NFL aliases, spread polarity, and total semantics are
# installed before preprice metadata is parsed.
from src import sports_kp_live_combo_supervisor_v15 as v15
from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_mve_combo_generator_v2 as generator_v2
from src import sports_kp_mve_poly_preprice as preprice

base = v15.v11.base if hasattr(v15, "v11") else v8.base
generator = v8.generator
logger = v8.logger
D = Decimal

# At this point v8 has already installed its dual-direction candidate function.
_LEGACY_DUAL_CANDIDATES = generator._candidate_combos


def _candidate_key(collection: str, legs: tuple[Any, ...]) -> str:
    return collection + "|" + "|".join(sorted(
        f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in legs
    ))


def _prepriced_normal_candidates(
    collection: dict[str, Any],
    event_legs: dict[str, list[Any]],
    *,
    max_candidates: int,
    min_locked: Decimal,
) -> list[tuple[Decimal, tuple[Any, ...]]]:
    lo, hi = generator._collection_leg_bounds(collection)
    single_per_event = bool(collection.get("is_single_market_per_event", True))
    all_yes = bool(collection.get("is_all_yes", False))

    pools: list[Any] = []
    for rows in event_legs.values():
        eligible = [leg for leg in rows if not all_yes or leg.side == "yes"]
        eligible.sort(key=lambda leg: (preprice.effective_complement_cost(leg), leg.hedge_all_in))
        # A few more per event than the legacy Kalshi-only ranker, because a
        # seemingly expensive Kalshi complement can be very cheap externally.
        pools.extend(eligible[:6])
    pools.sort(key=lambda leg: (preprice.effective_complement_cost(leg), leg.hedge_all_in))
    pools = pools[:32]

    threshold = generator.ONE - min_locked
    ranked: list[tuple[Decimal, Decimal, tuple[Any, ...]]] = []
    for n in generator.SUPPORTED_LEGS:
        if n < lo or n > hi or len(pools) < n:
            continue
        for legs in itertools.combinations(pools, n):
            if single_per_event and len({leg.event_ticker for leg in legs}) != n:
                continue
            effective = sum((preprice.effective_complement_cost(leg) for leg in legs), generator.ZERO)
            # Combo YES cannot cost less than zero, so baskets whose prepriced
            # hedge already exceeds the target floor cannot become profitable.
            if effective >= threshold:
                continue
            actual_kalshi = sum((leg.hedge_all_in for leg in legs), generator.ZERO)
            ranked.append((effective, actual_kalshi, legs))
    ranked.sort(key=lambda item: (
        item[0],
        max(leg.close_time for leg in item[2]),
        -len(item[2]),
        item[1],
    ))
    return [(actual, legs) for _effective, actual, legs in ranked[:max_candidates]]


def _prepriced_candidates(
    collection: dict[str, Any],
    event_legs: dict[str, list[Any]],
    *,
    max_candidates: int,
    min_locked: Decimal,
):
    normal = _prepriced_normal_candidates(
        collection, event_legs, max_candidates=max_candidates, min_locked=min_locked
    )
    # Preserve a bounded share of the existing dual-direction exploration. This
    # keeps the inverse tail alive while directing most creation capacity toward
    # cross-venue-prepriced normal coverage.
    legacy = _LEGACY_DUAL_CANDIDATES(
        collection, event_legs, max_candidates=max_candidates, min_locked=min_locked
    )

    collection_ticker = str(collection.get("collection_ticker") or "")
    out: list[tuple[Decimal, tuple[Any, ...]]] = []
    seen: set[str] = set()
    ni = li = 0
    # 3 prepriced normal candidates : 1 legacy candidate.
    while len(out) < max_candidates and (ni < len(normal) or li < len(legacy)):
        for _ in range(3):
            if ni >= len(normal) or len(out) >= max_candidates:
                break
            row = normal[ni]; ni += 1
            key = _candidate_key(collection_ticker, row[1])
            if key not in seen:
                seen.add(key); out.append(row)
        if li < len(legacy) and len(out) < max_candidates:
            row = legacy[li]; li += 1
            key = _candidate_key(collection_ticker, row[1])
            if key not in seen:
                seen.add(key); out.append(row)
        if ni >= len(normal) and li >= len(legacy):
            break

    preview = []
    for actual, legs in out[:8]:
        effective = sum((preprice.effective_complement_cost(leg) for leg in legs), generator.ZERO)
        preview.append({
            "effective_external_rank_cost": str(effective),
            "kalshi_component_cost": str(actual),
            "legs": [leg.selected_payload() for leg in legs],
            "external_sources": [
                {"market_ticker": leg.market_ticker, **src}
                for leg in legs
                for src in [preprice.source_for_leg(leg)] if src is not None
            ],
        })
    logger.warning("SPORTS_MVE_PREPRICE_RANK %s", json.dumps({
        "collection": collection_ticker,
        "prepriced_normal_candidates": len(normal),
        "legacy_candidates": len(legacy),
        "returned_candidates": len(out),
        "preview": preview,
        "ranking_only": True,
        "execution_eligible": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


generator_v2._candidate_preprice_hook = preprice.preprice_hook
generator._candidate_combos = _prepriced_candidates

run = v15.run
main = v15.main

if __name__ == "__main__":
    main()
