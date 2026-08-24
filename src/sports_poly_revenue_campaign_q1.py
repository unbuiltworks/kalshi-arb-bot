"""Single-maker revenue campaign combining structural totals and strict MLB props.

The generic structural totals campaign gets first priority. Only when it has no exact
TOTAL candidate do we consult the direct MLB PROP inventory. Both paths reuse the same
v4 q1 post-only maker -> exact FOK hedge transport, so the enclosing supervisor still
owns one maker at a time and one shared hard-halt policy.
"""
from __future__ import annotations

from typing import Any

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_structural_totals
from src.sports_poly_direct_mlb_campaign_q1 import run_once as run_direct_mlb


async def run_once() -> dict[str, Any]:
    structural = await run_structural_totals()
    status = str(structural.get("status") or "")
    if status != "NO_EXACT_STRUCTURAL_TOTAL_Q1":
        return structural

    mlb = await run_direct_mlb()
    mlb = dict(mlb)
    mlb["structural_totals_status"] = status
    mlb["structural_totals_strict_markets"] = structural.get("strict_markets")
    mlb["structural_totals_strict_pairs"] = structural.get("strict_pairs")
    return mlb
