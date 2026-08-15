"""Confidence overlay for BTC Gemini/Kalshi settlement-adjusted shadow economics.

This read-only analyzer wraps ``gemini_kalshi_btc_ws_settlement`` and adds a
95% Wilson upper confidence bound to the observed settlement-mismatch rate for
each entry/latency/margin cohort. A zero-mismatch small sample must not be read as
zero basis risk. No order-entry or authentication path exists here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from src.gemini_kalshi_btc_ws_settlement import run as settlement_run


def wilson_upper_95(events: int, trials: int) -> float | None:
    if trials <= 0:
        return None
    if events < 0 or events > trials:
        raise ValueError("invalid binomial counts")
    z = 1.959963984540054
    p = events / trials
    z2 = z * z
    center = p + z2 / (2 * trials)
    radius = z * math.sqrt((p * (1 - p) + z2 / (4 * trials)) / trials)
    return (center + radius) / (1 + z2 / trials)


def enrich_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    out = dict(scenario)
    trials = int(out.get("consumable_episodes") or 0)
    adverse = int(out.get("adverse_mismatch_consumable_episodes") or 0)
    favorable = int(out.get("favorable_mismatch_consumable_episodes") or 0)
    mismatches = adverse + favorable
    out["observed_mismatch_consumable_episodes"] = mismatches
    out["observed_mismatch_rate"] = (mismatches / trials) if trials else None
    out["mismatch_rate_wilson_upper_95"] = wilson_upper_95(mismatches, trials)
    out["confidence_warning"] = (
        "ZERO_OBSERVED_MISMATCHES_DOES_NOT_IMPLY_ZERO_BASIS_RISK"
        if trials and mismatches == 0
        else "SMALL_SAMPLE_BOUND_NOT_A_FORECAST"
    )
    return out


def confidence_overlay(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    out["entry"] = enrich_scenario(result["entry"])
    out["survival"] = {
        str(ms): enrich_scenario(scenario)
        for ms, scenario in result.get("survival", {}).items()
    }

    sensitivity: dict[str, Any] = {}
    for threshold, cohort in result.get("basis_margin_sensitivity", {}).items():
        sensitivity[str(threshold)] = {
            **{k: v for k, v in cohort.items() if k not in {"entry", "survival"}},
            "entry": enrich_scenario(cohort["entry"]),
            "survival": {
                str(ms): enrich_scenario(scenario)
                for ms, scenario in cohort.get("survival", {}).items()
            },
        }
    out["basis_margin_sensitivity"] = sensitivity
    out["confidence_method"] = "95%_WILSON_UPPER_BOUND_ON_CONSUMABLE_SETTLEMENT_MISMATCH_RATE"
    out["confidence_scope"] = "RETROSPECTIVE_SHADOW_SAMPLE_NOT_LIVE_RISK_CERTIFICATION"
    out["read_only"] = True
    out["order_entry"] = False
    return out


async def run(
    paths: list[Path],
    *,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
) -> dict[str, Any]:
    base = await settlement_run(
        paths,
        hours=hours,
        gemini_limit=gemini_limit,
        max_expiry_skew_s=max_expiry_skew_s,
    )
    return confidence_overlay(base)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=10000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    args = parser.parse_args()
    result = asyncio.run(run(
        args.paths,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_expiry_skew_s=args.max_expiry_skew_s,
    ))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
