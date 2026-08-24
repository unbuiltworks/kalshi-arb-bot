"""Production combo supervisor v19: complete NFL + MLB game + MLB prop prepricing.

Adds direct Gemini/Kalshi MLB prop economics to v18 after the existing NFL,
Polymarket MLB prop, and three-venue MLB game preprice pass.  The Gemini prop
adapter uses exact normal-event identity and snapshot pricing only; it is strictly
ranking-only because full Gemini prop lifecycle rule equivalence is not certified.
"""
from __future__ import annotations

import json

from src import sports_kp_live_combo_supervisor_v18 as v18
from src import sports_kp_mve_combo_generator_v2 as generator_v2
from src import sports_kp_mve_mlb_preprice as mlb_preprice
from src import sports_kp_mve_mlb_gemini_prop_preprice as gemini_prop

logger = v18.v17.logger


async def _composite_preprice(collection, event_legs):
    event_legs = await mlb_preprice.preprice_hook(collection, event_legs)
    result = await gemini_prop.preprice(event_legs)
    logger.warning("SPORTS_MVE_MLB_GEMINI_PROP_PREPRICE %s", json.dumps({
        "collection": collection.get("collection_ticker"),
        **result,
        "guaranteed_arb": False,
        "cross_venue_order_entry": False,
    }, separators=(",", ":"), default=str))
    return event_legs


generator_v2._candidate_preprice_hook = _composite_preprice

run = v18.run
main = v18.main

if __name__ == "__main__":
    main()
