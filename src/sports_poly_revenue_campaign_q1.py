"""Single-maker revenue campaign combining structural totals and strict MLB props.

The generic structural totals campaign gets first priority. Only when it has no exact
TOTAL candidate do we consult the direct MLB PROP inventory. If both current live
families are empty, a cached read-only scan of the direct all-sports market surface
runs to expose the next mechanically certifiable family without changing order entry.
"""
from __future__ import annotations

from typing import Any

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_structural_totals
from src.sports_poly_direct_mlb_campaign_q1 import run_once as run_direct_mlb
from src.sports_poly_direct_sports_diagnostic import run_once as run_direct_sports_diagnostic


async def run_once() -> dict[str, Any]:
    structural = await run_structural_totals()
    structural_status = str(structural.get("status") or "")
    if structural_status != "NO_EXACT_STRUCTURAL_TOTAL_Q1":
        return structural

    mlb = await run_direct_mlb()
    mlb_status = str(mlb.get("status") or "")
    if mlb_status != "NO_EXACT_DIRECT_MLB_PROP_Q1":
        mlb = dict(mlb)
        mlb["structural_totals_status"] = structural_status
        return mlb

    diagnostic = dict(await run_direct_sports_diagnostic())
    diagnostic["structural_totals_status"] = structural_status
    diagnostic["direct_mlb_status"] = mlb_status
    diagnostic["structural_totals_strict_markets"] = structural.get("strict_markets")
    diagnostic["structural_totals_strict_pairs"] = structural.get("strict_pairs")
    diagnostic["direct_mlb_strict_markets"] = mlb.get("strict_markets")
    diagnostic["direct_mlb_strict_pairs"] = mlb.get("strict_pairs")
    return diagnostic
