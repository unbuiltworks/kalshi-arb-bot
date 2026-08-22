"""Production combo supervisor v29: diversify manufacturing toward 3-leg prop baskets.

Recent live RFQs proved the throughput path is healthy (12 creations/pass, zero
Kalshi 429s) but repeatedly sampled simple two-leg NFL structures remained
negative after conservative fees.  This layer changes *manufacturing order only*:

* prioritize three-leg baskets containing player/derivative prop families;
* prefer same-underlying-game three-leg prop baskets when available;
* suppress repeatedly observed negative side-aware signatures from priority;
* retain bounded two-leg and broad exploration so the selector can adapt.

No execution invariant changes: q=1, >=3c after conservative fees, exact RFQ and
component depth, capital/position checks, final quote refresh, flatten/hard-halt,
one live attempt per process, and settlement accounting remain unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v28 as v28
from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_live_combo_supervisor_v23 as v23
from src import sports_kp_mve_combo_generator as g

logger = v28.logger

_CORE_FAMILIES = ("GAME", "SPREAD", "TOTAL")


def _parts(ticker: str) -> tuple[str, str]:
    text = str(ticker or "").upper()
    return tuple(text.split("-", 1)) if "-" in text else (text, text)  # type: ignore[return-value]


def _family(leg: Any) -> str:
    return _parts(getattr(leg, "event_ticker", ""))[0]


def _game_key(leg: Any) -> str:
    return _parts(getattr(leg, "event_ticker", ""))[1]


def _is_prop_family(family: str) -> bool:
    u = str(family or "").upper()
    return not any(u.endswith(x) for x in _CORE_FAMILIES)


def _redundant_game_spread(legs: tuple[Any, ...]) -> bool:
    games = {_game_key(x) for x in legs}
    families = {_family(x) for x in legs}
    return bool(
        len(games) == 1
        and any(f.endswith("GAME") for f in families)
        and any(f.endswith("SPREAD") for f in families)
    )


def _proven_negative(legs: tuple[Any, ...]) -> bool:
    sig = v23._family_signature_from_legs(legs)
    row = v23._FEEDBACK.get(sig)
    if not row:
        return False
    observations = int(row.get("observations") or 0)
    best = v23.D(str(row.get("best_lock") or "-9"))
    # Four observations is enough to stop spending priority budget on a pattern
    # that has never reached break-even.  It remains in the exploration bucket.
    return observations >= 4 and best < v23.ZERO


def _tier(legs: tuple[Any, ...]) -> int:
    families = [_family(x) for x in legs]
    prop_count = sum(1 for f in families if _is_prop_family(f))
    games = {_game_key(x) for x in legs}
    if _proven_negative(legs):
        return 6
    if len(legs) == 3 and prop_count >= 1 and len(games) == 1:
        return 0
    if len(legs) == 3 and prop_count >= 2:
        return 1
    if len(legs) == 3 and prop_count >= 1:
        return 2
    if len(games) == 1 and len(set(families)) >= 2:
        return 3
    if len(legs) == 3:
        return 4
    return 5


def _v29_candidates(collection, event_legs, *, max_candidates, min_locked):
    # Start from the pure same-Kalshi dual-direction universe so uncertified
    # external prices cannot steer live-capable manufacturing.
    expanded = min(max(max_candidates * 18, max_candidates), 3000)
    raw = v8._dual_candidates(
        collection,
        event_legs,
        max_candidates=expanded,
        min_locked=min_locked,
    )

    collection_ticker = str(collection.get("collection_ticker") or "")
    seen: set[str] = set()
    buckets: dict[int, list[Any]] = {i: [] for i in range(7)}
    duplicate_exact_event = 0
    redundant = 0
    for row in raw:
        legs = row[1]
        if len({leg.event_ticker for leg in legs}) != len(legs):
            duplicate_exact_event += 1
            continue
        if _redundant_game_spread(legs):
            redundant += 1
            continue
        key = collection_ticker + "|" + "|".join(sorted(
            f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in legs
        ))
        if key in seen:
            continue
        seen.add(key)
        buckets[_tier(legs)].append(row)

    for rows in buckets.values():
        rows.sort(key=lambda row: (
            v23._feedback_rank(v23._family_signature_from_legs(row[1])),
            row[0],
            max(leg.close_time for leg in row[1]),
        ))

    # For a 12-RFQ pass this targets 8 prop-heavy 3-leg rows, 1 other 3-leg,
    # 2 correlated/broad two-leg rows, and only 1 proven-negative exploration.
    pattern = (0, 1, 0, 2, 0, 1, 3, 0, 2, 4, 5, 6)
    out: list[Any] = []
    cursors = {i: 0 for i in range(7)}
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

    logger.warning("SPORTS_MVE_V29_PROP3_PRIORITY %s", json.dumps({
        "collection": collection_ticker,
        "raw_candidates": len(raw),
        "duplicate_exact_event_rejected": duplicate_exact_event,
        "redundant_game_spread_rejected": redundant,
        "same_game_prop3": len(buckets[0]),
        "multi_prop3": len(buckets[1]),
        "other_prop3": len(buckets[2]),
        "same_game_correlated": len(buckets[3]),
        "other_three_leg": len(buckets[4]),
        "broad_exploration": len(buckets[5]),
        "proven_negative_deprioritized": len(buckets[6]),
        "returned_candidates": len(out),
        "manufacturing_order_only": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


g._candidate_combos = _v29_candidates

run = v28.run
main = v28.main

if __name__ == "__main__":
    main()
