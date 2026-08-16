"""Settlement-adjusted profit per capital-hour for read-only BTC WS shadow logs.

Combines the exact consumable-liquidity settlement cohorts with the exact same
basis-margin-filtered capital-hour cohorts. This prevents reporting a P&L numerator
against a different capital denominator. Optional empirical post-close settlement
latency scenarios only lengthen the capital lock denominator; they never improve P&L.

Each exact cohort also carries a conservative one-sided Wilson-95 basis stress:
upper mismatch probability * consumable contracts * $1 maximum adverse payout swing.
Favorable 2x settlement mismatches receive no credit. This produces a stressed
profit-per-capital-hour on the same margin + latency-survival cohort rather than
mixing filtered P&L with an unfiltered denominator.

The same cohort reports the mathematically implied break-even adverse mismatch
probability: apparent locked profit / consumable contracts. Comparing the Wilson-95
upper mismatch probability against that break-even rate gives a non-arbitrary
confidence gate: if the upper bound is at or above break-even, the observed edge is
not yet robust to basis uncertainty at the stated confidence level.

For cohorts that do not pass, we also report the minimum additional independent
settled consumable episodes needed for the Wilson-95 upper bound to fall strictly
below break-even if every additional episode has no mismatch. This is a best-case
data-sufficiency target, not a forecast that future episodes will be clean.

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
from src.gemini_kalshi_lock_settlement_join import _wilson_upper_95
from src.gemini_kalshi_settlement_agreement import AgreementRow, _gemini_settled
from src.gemini_kalshi_settlement_probe import _kalshi_settled_paced, match_settled_true_windows


def _ratio(pnl: str, capital_hours: str) -> str | None:
    denominator = D(capital_hours)
    if denominator <= 0:
        return None
    return str(D(pnl) / denominator)


def _minimum_total_episodes_to_pass_wilson95(
    mismatches: int,
    current_episodes: int,
    break_even_rate: Decimal,
) -> int | None:
    """Best-case total episode count needed to pass if all future episodes are clean.

    The mismatch numerator is held fixed while the denominator grows. Because the
    Wilson upper bound monotonically falls in this clean-future scenario, binary
    search finds the first total n whose upper bound is strictly below the cohort's
    adverse basis break-even rate.
    """
    if current_episodes < 0 or mismatches < 0 or mismatches > current_episodes:
        raise ValueError("invalid episode/mismatch counts")
    if break_even_rate <= 0:
        return None
    if break_even_rate > 1:
        break_even_rate = D("1")

    def passes(n: int) -> bool:
        upper = _wilson_upper_95(mismatches, n)
        return upper is not None and D(str(upper)) < break_even_rate

    start = max(current_episodes, 1)
    if passes(start):
        return start

    hi = start
    # Cap only prevents pathological infinite search for vanishingly small edges.
    # 1e9 independent episodes is already operationally equivalent to "not viable".
    while hi < 1_000_000_000 and not passes(hi):
        hi = min(1_000_000_000, hi * 2)
    if not passes(hi):
        return None

    lo = start + 1
    while lo < hi:
        mid = (lo + hi) // 2
        if passes(mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def _join_scenario(pnl_row: dict[str, Any], capital_row: dict[str, Any]) -> dict[str, Any]:
    pnl = str(pnl_row["settlement_adjusted_pnl_dollars"])
    apparent = D(str(pnl_row["apparent_locked_profit_dollars"]))
    contracts = D(str(pnl_row["consumable_contracts"]))
    episodes = int(pnl_row["consumable_episodes"])
    mismatches = int(pnl_row["consumable_mismatch_episodes"])
    capital_hours = str(capital_row["consumable_capital_hours"])
    upper_raw = pnl_row.get("consumable_mismatch_rate_wilson_upper_95")

    stressed_loss = None
    stressed_pnl = None
    stressed_efficiency = None
    break_even_rate = None
    confidence_gap = None
    confidence_status = "UNRESOLVED_NO_CONSUMABLE_CONTRACTS"
    min_total_episodes = None
    additional_clean_episodes = None

    if contracts > 0:
        break_even_rate = apparent / contracts
        confidence_status = "UNRESOLVED_WILSON95_UNAVAILABLE"
        min_total_episodes = _minimum_total_episodes_to_pass_wilson95(
            mismatches,
            episodes,
            break_even_rate,
        )
        if min_total_episodes is not None:
            additional_clean_episodes = max(0, min_total_episodes - episodes)

    if upper_raw is not None:
        upper = D(str(upper_raw))
        if upper < 0 or upper > 1:
            raise ValueError("Wilson upper mismatch probability must be in [0,1]")
        if contracts < 0:
            raise ValueError("consumable_contracts cannot be negative")
        stressed_loss = upper * contracts
        stressed_pnl = apparent - stressed_loss
        hours = D(capital_hours)
        if hours > 0:
            stressed_efficiency = stressed_pnl / hours
        if break_even_rate is not None:
            confidence_gap = upper - break_even_rate
            confidence_status = (
                "PASSES_WILSON95_BREAK_EVEN"
                if confidence_gap < 0
                else "INSUFFICIENT_WILSON95_BASIS_MARGIN"
            )

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
        "consumable_mismatch_rate_wilson_upper_95": upper_raw,
        "basis_break_even_adverse_mismatch_rate": (
            str(break_even_rate) if break_even_rate is not None else None
        ),
        "wilson95_minus_break_even_mismatch_rate": (
            str(confidence_gap) if confidence_gap is not None else None
        ),
        "basis_confidence_status": confidence_status,
        "best_case_total_independent_episodes_needed_to_pass_wilson95": min_total_episodes,
        "best_case_additional_clean_episodes_needed_to_pass_wilson95": additional_clean_episodes,
        "wilson95_adverse_basis_stress_loss_dollars": (
            str(stressed_loss) if stressed_loss is not None else None
        ),
        "wilson95_adverse_basis_stress_pnl_dollars": (
            str(stressed_pnl) if stressed_pnl is not None else None
        ),
        "wilson95_adverse_basis_stress_profit_per_capital_hour": (
            str(stressed_efficiency) if stressed_efficiency is not None else None
        ),
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
        "basis_stress_efficiency_metric": "WILSON95_ONE_SIDED_ADVERSE_BASIS_STRESS_PNL_DOLLARS_PER_EXACT_SAME_FRESH_CONSUMABLE_CAPITAL_HOUR",
        "basis_stress_method": "WILSON95_UPPER_MISMATCH_PROBABILITY_X_$1_PER_CONSUMABLE_CONTRACT_SUBTRACTED_FROM_APPARENT_LOCKED_PNL",
        "basis_stress_interpretation": "ONE_SIDED_ADVERSE_STRESS_NOT_EXPECTED_VALUE_OR_FORECAST_FAVORABLE_2X_MISMATCHES_GET_NO_CREDIT",
        "basis_break_even_method": "APPARENT_LOCKED_PROFIT_DOLLARS_DIVIDED_BY_CONSUMABLE_CONTRACTS_EQUALS_MAX_ADVERSE_MISMATCH_PROBABILITY_BEFORE_EDGE_BREAKS_EVEN",
        "basis_confidence_gate": "PASS_ONLY_WHEN_WILSON95_UPPER_MISMATCH_PROBABILITY_IS_STRICTLY_BELOW_COHORT_BREAK_EVEN_ADVERSE_MISMATCH_RATE",
        "basis_sample_sufficiency_method": "MINIMUM_TOTAL_INDEPENDENT_SETTLED_CONSUMABLE_EPISODES_NEEDED_FOR_WILSON95_UPPER_TO_FALL_STRICTLY_BELOW_BREAK_EVEN_HOLDING_OBSERVED_MISMATCH_COUNT_FIXED_AND_ASSUMING_ALL_ADDITIONAL_EPISODES_ARE_CLEAN",
        "basis_sample_sufficiency_interpretation": "BEST_CASE_DATA_REQUIREMENT_NOT_A_FORECAST_OR_SAFETY_CERTIFICATION",
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
