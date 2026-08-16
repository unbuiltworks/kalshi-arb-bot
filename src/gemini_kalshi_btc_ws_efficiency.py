"""Settlement-adjusted profit per capital-hour for read-only BTC WS shadow logs.

Combines the exact consumable-liquidity settlement cohorts with the exact same
basis-margin-filtered capital-hour cohorts. This prevents reporting a P&L numerator
against a different capital denominator. Optional empirical post-close settlement
latency scenarios only lengthen the capital lock denominator; they never improve P&L.

Research only. No authentication or order-entry path.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import httpx

from src.gemini_kalshi_btc_ws_analyze import D, THRESHOLDS_MS
from src.gemini_kalshi_btc_ws_capital import settlement_delay_scenarios_from_summary
from src.gemini_kalshi_btc_ws_margin_capital import margin_capital_economics
from src.gemini_kalshi_btc_ws_settlement import (
    BASIS_MARGIN_THRESHOLDS_BPS,
    load_episodes_stream,
    settlement_adjusted_episodes,
)
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows


def _ratio(pnl: str, capital_hours: str) -> str | None:
    denominator = D(capital_hours)
    if denominator <= 0:
        return None
    return str(D(pnl) / denominator)


def _join_scenario(pnl_row: dict[str, Any], capital_row: dict[str, Any]) -> dict[str, Any]:
    pnl = str(pnl_row["settlement_adjusted_pnl_dollars"])
    capital_hours = str(capital_row["consumable_capital_hours"])
    return {
        "consumable_episodes": pnl_row["consumable_episodes"],
        "consumable_contracts": pnl_row["consumable_contracts"],
        "settlement_adjusted_pnl_dollars": pnl,
        "apparent_locked_profit_dollars": pnl_row["apparent_locked_profit_dollars"],
        "basis_effect_dollars": pnl_row["basis_effect_dollars"],
        "consumable_capital_deployed_dollars": capital_row["consumable_capital_deployed_dollars"],
        "consumable_capital_hours": capital_hours,
        "settlement_adjusted_profit_per_capital_hour": _ratio(pnl, capital_hours),
        "consumable_mismatch_episodes": pnl_row["consumable_mismatch_episodes"],
        "consumable_mismatch_rate": pnl_row["consumable_mismatch_rate"],
        "consumable_mismatch_rate_wilson_upper_95": pnl_row["consumable_mismatch_rate_wilson_upper_95"],
    }


def _cohort_efficiency(
    settlement: dict[str, Any],
    capital: dict[str, Any],
) -> dict[str, Any]:
    return {
        "entry": _join_scenario(settlement["entry"], capital["entry"]),
        "survival": {
            str(ms): _join_scenario(settlement["survival"][str(ms)], capital["survival"][str(ms)])
            for ms in THRESHOLDS_MS
        },
    }


def efficiency_summary(
    episodes,
    agreements: list[AgreementRow],
    settlement_delays_s: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    settlement = settlement_adjusted_episodes(episodes, agreements)
    capital = margin_capital_economics(episodes, agreements, settlement_delays_s)

    basis: dict[str, Any] = {}
    delayed: dict[str, Any] = {
        str(label): {} for label in (settlement_delays_s or {})
    }
    for threshold in BASIS_MARGIN_THRESHOLDS_BPS:
        key = str(threshold.normalize()) if threshold != 0 else "0"
        settlement_cohort = settlement["basis_margin_sensitivity"][key]
        capital_cohort = capital["basis_margin_capital"][key]
        basis[key] = {
            "min_abs_gemini_margin_bps": str(threshold),
            **_cohort_efficiency(settlement_cohort, capital_cohort),
        }
        for label in delayed:
            delayed[label][key] = {
                "min_abs_gemini_margin_bps": str(threshold),
                **_cohort_efficiency(
                    settlement_cohort,
                    capital["settlement_delay_adjusted_basis_margin_capital"][label][key],
                ),
            }

    return {
        "basis_margin_efficiency": basis,
        "settlement_delay_adjusted_basis_margin_efficiency": delayed,
        "efficiency_metric": "SETTLEMENT_ADJUSTED_PNL_DOLLARS_PER_FRESH_CONSUMABLE_CAPITAL_HOUR",
        "denominator_policy": "EXACT_SAME_MARGIN_AND_LATENCY_SURVIVAL_COHORT_AS_PNL_NUMERATOR",
        "settlement_delay_policy": "OPTIONAL_EMPIRICAL_POST_CLOSE_DELAY_INCREASES_DENOMINATOR_ONLY",
        "basis_uncertainty_policy": settlement["basis_uncertainty_policy"],
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


async def run(
    paths: list[Path],
    *,
    hours: int,
    gemini_limit: int,
    max_expiry_skew_s: float,
    settlement_delays_s: dict[str, Decimal] | None,
) -> dict[str, Any]:
    eps = load_episodes_stream(paths)
    async with httpx.AsyncClient(timeout=30.0) as http:
        gemini, kalshi = await asyncio.gather(
            _gemini_settled(http, gemini_limit),
            _kalshi_settled_paced(http, hours),
        )
    agreements = match_settled_true_windows(gemini, kalshi, max_expiry_skew_s)
    return efficiency_summary(eps, agreements, settlement_delays_s)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--gemini-limit", type=int, default=10000)
    parser.add_argument("--max-expiry-skew-s", type=float, default=30.0)
    parser.add_argument(
        "--settlement-latency-summary",
        type=Path,
        help="Optional JSON from gemini_kalshi_settlement_latency; adds BTC p50/p95 post-close delay",
    )
    args = parser.parse_args()

    delays = None
    if args.settlement_latency_summary is not None:
        payload = json.loads(args.settlement_latency_summary.read_text(encoding="utf-8"))
        delays = settlement_delay_scenarios_from_summary(payload)

    result = asyncio.run(run(
        args.paths,
        hours=args.hours,
        gemini_limit=args.gemini_limit,
        max_expiry_skew_s=args.max_expiry_skew_s,
        settlement_delays_s=delays,
    ))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
