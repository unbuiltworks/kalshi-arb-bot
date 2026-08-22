"""Production combo supervisor v29: prioritize prop baskets with durable RFQ learning.

Recent live RFQs proved the throughput path is healthy but repeatedly sampled simple
two-leg structures remained negative after conservative fees. This layer changes
manufacturing order only: prioritize three-leg prop/derivative baskets, suppress
repeatedly negative family+selected-side signatures from priority, and retain
bounded broad exploration.

v23's signature already includes selected-side orientation, but its feedback was
process-local. This version hydrates that manufacturing-only feedback from Postgres
before each MVE cycle and atomically persists only new observation deltas afterward,
so Railway redeploys do not erase which exact patterns quoted closest to the bound.
Persistence failure falls back to process-local learning and cannot authorize a
trade.

No execution invariant changes: q=1, >=3c after conservative fees, exact rule/RFQ
and component depth, capital/position checks, final quote refresh, freshness/skew,
flatten/hard-halt, one live attempt per process, and settlement accounting remain
unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v28 as v28
from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_live_combo_supervisor_v23 as v23
from src import sports_kp_mve_combo_generator as g
from src import sports_mve_rfq_feedback_store as feedback_store

logger = v28.logger
base = v28.base
_ORIGINAL_GENERATE = base.generate_mves

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
        "durable_side_aware_feedback": True,
        "manufacturing_order_only": True,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


def _replace_feedback(snapshot: dict[str, dict[str, Any]]) -> None:
    v23._FEEDBACK.clear()
    for signature, row in snapshot.items():
        v23._FEEDBACK[signature].update(row)


async def _durable_feedback_generate():
    hydrated = False
    durable_rows = 0
    try:
        durable = await feedback_store.load_feedback()
        if durable:
            _replace_feedback(durable)
        hydrated = True
        durable_rows = len(durable)
    except Exception as exc:
        logger.warning("SPORTS_MVE_RFQ_FEEDBACK_LOAD_FALLBACK %s", json.dumps({
            "reason": type(exc).__name__, "detail": str(exc)[:180],
            "process_local_feedback_retained": True,
            "execution_gate_changed": False, "order_entry": False,
        }, separators=(",", ":")))

    before = feedback_store.snapshot(v23._FEEDBACK)
    result = await _ORIGINAL_GENERATE()
    after = feedback_store.snapshot(v23._FEEDBACK)

    persisted = 0
    try:
        persisted = await feedback_store.merge_feedback_delta(before, after)
    except Exception as exc:
        logger.warning("SPORTS_MVE_RFQ_FEEDBACK_PERSIST_FALLBACK %s", json.dumps({
            "reason": type(exc).__name__, "detail": str(exc)[:180],
            "process_local_feedback_retained": True,
            "execution_gate_changed": False, "order_entry": False,
        }, separators=(",", ":")))

    logger.warning("SPORTS_MVE_RFQ_FEEDBACK_DURABLE %s", json.dumps({
        "hydrated": hydrated,
        "durable_signatures_loaded": durable_rows,
        "signatures_updated_this_cycle": persisted,
        "in_memory_signatures": len(v23._FEEDBACK),
        "signature_includes_selected_side": True,
        "restart_safe_learning": hydrated,
        "manufacturing_order_only": True,
        "live_threshold_changed": False,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return result


g._candidate_combos = _v29_candidates
base.generate_mves = _durable_feedback_generate

run = v28.run
main = v28.main

if __name__ == "__main__":
    main()
