"""Single-maker revenue campaign combining structural totals and strict MLB props.

Structural totals retain first priority. When no exact total is available, MLB props
flow through the participation-gated v3 maker-frontier wrapper. The MLB campaign owns
its separate live switch, so the shared structural live flag cannot arm MLB implicitly.
"""
from __future__ import annotations

from typing import Any

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_structural_totals
from src.sports_poly_direct_mlb_campaign_q1_v3 import run_once as run_direct_mlb_v3
from src.sports_poly_direct_sports_diagnostic import run_once as run_direct_sports_diagnostic


MLB_EMPTY_STATUSES = {
    "NO_STRICT_MLB_LADDERS_V2",
    "NO_LIVE_PARTICIPATION_CERTIFIED_MLB_PAIR",
    "LIVE_MLB_CERTIFIED_BUT_NO_NEAR_TOUCH_3C_Q1",
}


async def run_once() -> dict[str, Any]:
    structural = await run_structural_totals()
    structural_status = str(structural.get("status") or "")
    if structural_status != "NO_EXACT_STRUCTURAL_TOTAL_Q1":
        return structural

    mlb = dict(await run_direct_mlb_v3())
    mlb_status = str(mlb.get("status") or "")
    mlb["structural_totals_status"] = structural_status
    if mlb_status not in MLB_EMPTY_STATUSES:
        return mlb

    diagnostic = dict(await run_direct_sports_diagnostic())
    diagnostic["structural_totals_status"] = structural_status
    diagnostic["direct_mlb_v3_status"] = mlb_status
    diagnostic["direct_mlb_v3"] = mlb
    diagnostic["structural_totals_strict_markets"] = structural.get("strict_markets")
    diagnostic["structural_totals_strict_pairs"] = structural.get("strict_pairs")
    return diagnostic
