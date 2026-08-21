"""Production combo supervisor v11: Gemini + pregame Polymarket hedge shadow.

Extends v10 with strict pregame Polymarket NFL moneyline/spread replacement-cost
measurement.  All prior q=1 execution gates are unchanged. Cross-venue component
replacements remain shadow-only until settlement-rule fingerprints are certified.
"""
from __future__ import annotations

import json
import os
from typing import Any

from src import sports_kp_live_combo_supervisor_v10 as v10
from src.sports_kp_mve_poly_pregame_shadow import annotate_poly_pregame

base = v10.base
logger = v10.logger
_ORIGINAL_GENERATE = base.generate_mves


async def _poly_pregame_generate() -> dict[str, Any]:
    result = await _ORIGINAL_GENERATE()
    if not isinstance(result, dict):
        return result
    try:
        horizon = int(os.getenv("MVE_SCAN_HORIZON_HOURS", "24"))
        enriched = await annotate_poly_pregame(result, v10.v9.v8._MARKETS, horizon_hours=horizon)
        logger.warning(
            "SPORTS_MVE_POLY_PREGAME_SHADOW %s",
            json.dumps(
                {
                    "markets_scored": len(enriched.get("ranked") or []),
                    "pregame_events_scanned": enriched.get("poly_pregame_shadow_events_scanned", 0),
                    "matched_kalshi_components": enriched.get("poly_pregame_shadow_matched_kalshi_components", 0),
                    "polymarket_books": enriched.get("poly_pregame_shadow_books", 0),
                    "positive_economic_candidates": enriched.get("poly_pregame_shadow_candidates", 0),
                    "best": enriched.get("poly_pregame_shadow_best"),
                    "live_execution_enabled": False,
                    "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED",
                    "order_entry": False,
                },
                separators=(",", ":"),
                default=str,
            ),
        )
        return enriched
    except Exception as exc:
        logger.error(
            "SPORTS_MVE_POLY_PREGAME_SHADOW_ERROR %s",
            json.dumps({"type": type(exc).__name__, "detail": str(exc)[:240], "order_entry": False}),
        )
        result["poly_pregame_shadow_error"] = type(exc).__name__
        result["poly_pregame_shadow_execution_enabled"] = False
        return result


base.generate_mves = _poly_pregame_generate
run = base.run
main = base.main


if __name__ == "__main__":
    main()
