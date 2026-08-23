"""Read-only breadth audit for Gemini/Kalshi MLB final-lag candidate families.

This module intentionally has no order transport. It inventories exact Gemini/Kalshi
prop overlaps already discovered by the audited hot-shadow pipeline and reports the
remaining settlement-rule blockers for each family. The output is diagnostic only;
it cannot certify a family or authorize live execution.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from typing import Any

import httpx

from src.gemini_kalshi_mlb_prop_hot_shadow import discover_hot
from src.mlb_prop_rule_certifications import assess_prop_pair

AUDIT_FAMILIES = (
    "baseball_player_hits",
    "baseball_player_hits_runs_rbis",
    "baseball_player_total_bases",
    "baseball_player_strikeouts",
)


def _native_direct(row: Any) -> bool:
    return bool(
        getattr(row.gemini_side, "side", None) in {"YES", "CONTRACT"}
        and getattr(row.kalshi_side, "side", None) in {"YES", "CONTRACT"}
    )


def summarize(hot: dict[str, Any]) -> dict[str, Any]:
    counts = Counter()
    direct = Counter()
    examples: dict[str, list[dict[str, str]]] = {family: [] for family in AUDIT_FAMILIES}
    for key, row in hot.items():
        family = str(getattr(row, "family", ""))
        if family not in AUDIT_FAMILIES:
            continue
        counts[family] += 1
        if _native_direct(row):
            direct[family] += 1
            if len(examples[family]) < 5:
                examples[family].append({
                    "contract_key": str(key),
                    "player": str(getattr(row, "player", "")),
                    "game_slug": str(getattr(row, "game_slug", "")),
                    "gemini_symbol": str(row.gemini_side.venue_market_id),
                    "kalshi_ticker": str(row.kalshi_side.venue_market_id),
                })

    families: dict[str, Any] = {}
    for family in AUDIT_FAMILIES:
        assessment = assess_prop_pair("gemini", "kalshi", family, proof=None)
        families[family] = {
            "exact_overlap_keys": int(counts[family]),
            "native_direct_keys": int(direct[family]),
            "rule_status": assessment.status.value,
            "exact_settlement_equivalent": assessment.exact_settlement_equivalent,
            "execution_eligible": assessment.execution_eligible,
            "remaining_blockers": list(assessment.remaining_blockers),
            "examples": examples[family],
        }
    return {
        "diagnostic_only": True,
        "order_entry": False,
        "certifies_execution": False,
        "families": families,
    }


async def main_async() -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        hot, _market_to_keys, diagnostics = await discover_hot(http)
    payload = summarize(hot)
    payload["discovery"] = diagnostics
    print("MLB_GK_FINAL_LAG_FAMILY_AUDIT " + json.dumps(payload, separators=(",", ":"), default=str), flush=True)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
