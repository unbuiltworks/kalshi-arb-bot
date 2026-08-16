"""Capital-scaled daily capacity view for read-only BTC Gemini/Kalshi shadow economics.

Consumes the JSON output of ``gemini_kalshi_btc_ws_efficiency`` and translates
profit-per-capital-hour into a deliberately mechanical full-utilization daily
capacity view. This is not a forecast: it assumes the observed cohort efficiency
can be continuously redeployed for the stated utilization fraction.

The primary purpose is to make the $480/day north-star auditable without hiding
capital requirements. Both settlement-adjusted and Wilson-95 adverse-basis-stressed
efficiency are carried through. Cohorts with non-positive or unavailable stressed
efficiency never claim a finite capital requirement.

Research only. No authentication or order-entry path.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

D = Decimal
HOURS_PER_DAY = D("24")
DEFAULT_TARGET_DAILY_PROFIT = D("480")
DEFAULT_CAPITAL_DOLLARS = D("1000")


def _project_row(
    row: dict[str, Any],
    *,
    capital_dollars: Decimal,
    utilization: Decimal,
    target_daily_profit: Decimal,
) -> dict[str, Any]:
    raw_eff = row.get("settlement_adjusted_profit_per_capital_hour")
    stressed_eff = row.get("wilson95_adverse_basis_stress_profit_per_capital_hour")

    def project(value: Any) -> tuple[str | None, str | None]:
        if value is None:
            return None, None
        eff = D(str(value))
        daily = eff * capital_dollars * HOURS_PER_DAY * utilization
        required = None
        if eff > 0 and utilization > 0:
            required = target_daily_profit / (eff * HOURS_PER_DAY * utilization)
        return str(daily), (str(required) if required is not None else None)

    settlement_daily, settlement_required = project(raw_eff)
    stressed_daily, stressed_required = project(stressed_eff)
    return {
        "consumable_episodes": row.get("consumable_episodes"),
        "consumable_contracts": row.get("consumable_contracts"),
        "basis_confidence_status": row.get("basis_confidence_status"),
        "settlement_adjusted_profit_per_capital_hour": raw_eff,
        "wilson95_adverse_basis_stress_profit_per_capital_hour": stressed_eff,
        "projected_settlement_adjusted_profit_per_day_dollars": settlement_daily,
        "projected_wilson95_stressed_profit_per_day_dollars": stressed_daily,
        "required_continuously_redeployed_capital_for_target_settlement_adjusted_dollars": settlement_required,
        "required_continuously_redeployed_capital_for_target_wilson95_stressed_dollars": stressed_required,
    }


def daily_capacity_summary(
    efficiency: dict[str, Any],
    *,
    capital_dollars: Decimal = DEFAULT_CAPITAL_DOLLARS,
    utilization: Decimal = D("1"),
    target_daily_profit: Decimal = DEFAULT_TARGET_DAILY_PROFIT,
) -> dict[str, Any]:
    if capital_dollars <= 0:
        raise ValueError("capital_dollars must be positive")
    if utilization < 0 or utilization > 1:
        raise ValueError("utilization must be in [0,1]")
    if target_daily_profit <= 0:
        raise ValueError("target_daily_profit must be positive")

    basis_out: dict[str, Any] = {}
    for threshold, cohort in (efficiency.get("basis_margin_efficiency") or {}).items():
        basis_out[str(threshold)] = {
            "min_abs_gemini_margin_bps": cohort.get("min_abs_gemini_margin_bps"),
            "entry": _project_row(
                cohort["entry"],
                capital_dollars=capital_dollars,
                utilization=utilization,
                target_daily_profit=target_daily_profit,
            ),
            "survival": {
                str(ms): _project_row(
                    row,
                    capital_dollars=capital_dollars,
                    utilization=utilization,
                    target_daily_profit=target_daily_profit,
                )
                for ms, row in (cohort.get("survival") or {}).items()
            },
        }

    delayed_out: dict[str, Any] = {}
    for label, thresholds in (efficiency.get("settlement_delay_adjusted_basis_margin_efficiency") or {}).items():
        delayed_out[str(label)] = {}
        for threshold, cohort in thresholds.items():
            delayed_out[str(label)][str(threshold)] = {
                "min_abs_gemini_margin_bps": cohort.get("min_abs_gemini_margin_bps"),
                "entry": _project_row(
                    cohort["entry"],
                    capital_dollars=capital_dollars,
                    utilization=utilization,
                    target_daily_profit=target_daily_profit,
                ),
                "survival": {
                    str(ms): _project_row(
                        row,
                        capital_dollars=capital_dollars,
                        utilization=utilization,
                        target_daily_profit=target_daily_profit,
                    )
                    for ms, row in (cohort.get("survival") or {}).items()
                },
            }

    return {
        "capital_dollars": str(capital_dollars),
        "assumed_capital_utilization_fraction": str(utilization),
        "target_daily_profit_dollars": str(target_daily_profit),
        "basis_margin_daily_capacity": basis_out,
        "settlement_delay_adjusted_basis_margin_daily_capacity": delayed_out,
        "projection_formula": "PROFIT_PER_CAPITAL_HOUR_X_CAPITAL_DOLLARS_X_24_X_UTILIZATION",
        "required_capital_formula": "TARGET_DAILY_PROFIT_DIVIDED_BY_PROFIT_PER_CAPITAL_HOUR_X_24_X_UTILIZATION",
        "projection_warning": "MECHANICAL_CONTINUOUS_REDEPLOYMENT_CAPACITY_NOT_A_FORECAST_OPPORTUNITY_ARRIVAL_AND_CAPACITY_ARE_NOT_GUARANTEED",
        "basis_policy": "USE_WILSON95_STRESSED_VIEW_FOR_CONSERVATIVE_CAPACITY_INTERPRETATION",
        "classification": "INDEX_MISMATCH_RESEARCH_ONLY",
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("efficiency_summary", type=Path)
    parser.add_argument("--capital-dollars", type=Decimal, default=DEFAULT_CAPITAL_DOLLARS)
    parser.add_argument("--utilization", type=Decimal, default=D("1"))
    parser.add_argument("--target-daily-profit", type=Decimal, default=DEFAULT_TARGET_DAILY_PROFIT)
    args = parser.parse_args()
    payload = json.loads(args.efficiency_summary.read_text(encoding="utf-8"))
    result = daily_capacity_summary(
        payload,
        capital_dollars=args.capital_dollars,
        utilization=args.utilization,
        target_daily_profit=args.target_daily_profit,
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
