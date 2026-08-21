"""Production combo supervisor v10: cross-venue component-cost shadow.

Keeps v9's restart-safe creation budget and all existing live q=1 gates. After a
normal generator run, strictly matched Gemini NFL books are used to ask whether
component hedges would be cheaper cross-venue. This is economics-only shadow:
Gemini replacements remain execution-ineligible until exact settlement rules are
fingerprinted and approved.
"""
from __future__ import annotations

import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v9 as v9
from src.sports_kp_mve_cross_venue_shadow import annotate_generated_result

base = v9.base
logger = v9.logger
_ORIGINAL_GENERATE = base.generate_mves


async def _cross_venue_shadow_generate() -> dict[str, Any]:
    result = await _ORIGINAL_GENERATE()
    if not isinstance(result, dict):
        return result
    try:
        enriched = await annotate_generated_result(result, v9.v8._MARKETS)
        logger.warning(
            "SPORTS_MVE_CROSS_VENUE_SHADOW %s",
            json.dumps(
                {
                    "markets_scored": len(enriched.get("ranked") or []),
                    "matched_kalshi_components": enriched.get("cross_venue_shadow_matched_kalshi_components", 0),
                    "gemini_books": enriched.get("cross_venue_shadow_gemini_books", 0),
                    "positive_economic_candidates": enriched.get("cross_venue_shadow_candidates", 0),
                    "best": enriched.get("cross_venue_shadow_best"),
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
        # Cross-venue research must never block the proven Kalshi generator or
        # alter the live q=1 execution gate.
        logger.error(
            "SPORTS_MVE_CROSS_VENUE_SHADOW_ERROR %s",
            json.dumps({"type": type(exc).__name__, "detail": str(exc)[:240], "order_entry": False}),
        )
        result["cross_venue_shadow_error"] = type(exc).__name__
        result["cross_venue_shadow_execution_enabled"] = False
        return result


base.generate_mves = _cross_venue_shadow_generate
run = base.run
main = base.main


if __name__ == "__main__":
    main()
