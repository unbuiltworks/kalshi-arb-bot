"""Production combo supervisor v21: prefilter invalid duplicate-event MVE baskets.

Kalshi rejects nested selections from the same exact event (for example multiple
thresholds from one NFL total event) as duplicated_legs because one threshold
logically implies another. Filter those baskets before market creation so they
do not consume the durable MVE creation-attempt budget.

This is deliberately conservative: a manufactured basket may contain at most
one selected market per exact Kalshi event_ticker. Different families for the
same game remain eligible because they have distinct event tickers (for example
NFL spread + NFL total). No pricing, rule, quote, or order-entry gate is weakened.
"""
from __future__ import annotations

import json

from src import sports_kp_live_combo_supervisor_v20 as v20
from src import sports_kp_mve_combo_generator as g

logger = v20.v19.logger
_ORIGINAL_CANDIDATE_COMBOS = g._candidate_combos


def _candidate_combos_no_duplicate_events(collection, event_legs, *, max_candidates, min_locked):
    expanded_cap = min(max(max_candidates * 4, max_candidates), 1000)
    raw = _ORIGINAL_CANDIDATE_COMBOS(
        collection,
        event_legs,
        max_candidates=expanded_cap,
        min_locked=min_locked,
    )
    kept = []
    rejected = 0
    for hedge_all, legs in raw:
        event_ids = [leg.event_ticker for leg in legs]
        if len(set(event_ids)) != len(event_ids):
            rejected += 1
            continue
        kept.append((hedge_all, legs))
        if len(kept) >= max_candidates:
            break
    if rejected:
        logger.warning(
            "SPORTS_MVE_DUPLICATE_EVENT_PREFILTER %s",
            json.dumps(
                {
                    "collection": collection.get("collection_ticker"),
                    "legacy_candidates_examined": len(raw),
                    "duplicate_event_baskets_rejected": rejected,
                    "valid_candidates_returned": len(kept),
                    "market_creation_avoided": True,
                    "execution_semantics_changed": False,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
        )
    return kept


g._candidate_combos = _candidate_combos_no_duplicate_events

run = v20.run
main = v20.main

if __name__ == "__main__":
    main()
