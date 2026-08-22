"""Production combo supervisor v27: remove uncertified external prepricing from the
live-capable MVE manufacturing critical path.

v24+ ranks actual MVE creation exclusively on executable same-Kalshi component
economics. The inherited v19 preprice hook was nevertheless still fetching dozens
of Polymarket/Gemini books before candidate manufacture even though those prices
could no longer affect creation order and remain execution-ineligible. For short-
horizon/live sports this adds avoidable latency between inventory discovery and
RFQ creation.

This layer replaces that *pre-creation* external hook with a no-op. Existing
post-generation cross-venue shadow diagnostics remain available elsewhere in the
supervisor, and the direct K/P scanner continues independently. No execution or
risk gate changes: q=1, >=3c after fees, component/RFQ depth, capital/positions,
final quote refresh, flatten/hard-halt, and settlement accounting are unchanged.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v26 as v26
from src import sports_kp_mve_combo_generator_v2 as generator_v2

logger = v26.logger


async def _executable_kalshi_only_preprice(collection: dict[str, Any], event_legs: dict[str, list[Any]]):
    logger.warning("SPORTS_MVE_PREPRICE_BYPASS %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        "reason": "UNCERTIFIED_EXTERNAL_PRICES_DO_NOT_STEER_LIVE_CAPABLE_CREATION",
        "event_count": len(event_legs),
        "manufacturing_latency_reduced": True,
        "external_execution_enabled": False,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":")))
    return event_legs


generator_v2._candidate_preprice_hook = _executable_kalshi_only_preprice

run = v26.run
main = v26.main

if __name__ == "__main__":
    main()
