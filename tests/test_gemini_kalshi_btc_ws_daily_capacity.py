from decimal import Decimal

import pytest

from src.gemini_kalshi_btc_ws_daily_capacity import daily_capacity_summary


def _row(raw="0.02", stressed="0.01"):
    return {
        "consumable_episodes": 4,
        "consumable_contracts": "10",
        "basis_confidence_status": "PASSES_WILSON95_BREAK_EVEN",
        "settlement_adjusted_profit_per_capital_hour": raw,
        "wilson95_adverse_basis_stress_profit_per_capital_hour": stressed,
    }


def _summary(raw="0.02", stressed="0.01"):
    cohort = {
        "min_abs_gemini_margin_bps": "1",
        "entry": _row(raw, stressed),
        "survival": {"100": _row(raw, stressed)},
    }
    return {
        "basis_margin_efficiency": {"1": cohort},
        "settlement_delay_adjusted_basis_margin_efficiency": {
            "p95": {"1": cohort}
        },
    }


def test_projects_daily_capacity_and_required_capital_for_480_target():
    result = daily_capacity_summary(
        _summary(),
        capital_dollars=Decimal("1000"),
        utilization=Decimal("0.5"),
        target_daily_profit=Decimal("480"),
    )
    entry = result["basis_margin_daily_capacity"]["1"]["entry"]
    assert Decimal(entry["projected_settlement_adjusted_profit_per_day_dollars"]) == Decimal("240")
    assert Decimal(entry["projected_wilson95_stressed_profit_per_day_dollars"]) == Decimal("120")
    assert Decimal(entry["required_continuously_redeployed_capital_for_target_settlement_adjusted_dollars"]) == Decimal("2000")
    assert Decimal(entry["required_continuously_redeployed_capital_for_target_wilson95_stressed_dollars"]) == Decimal("4000")
    assert result["projection_warning"].startswith("MECHANICAL_CONTINUOUS_REDEPLOYMENT")
    assert result["read_only"] is True
    assert result["order_entry"] is False


def test_nonpositive_stressed_efficiency_never_claims_finite_required_capital():
    result = daily_capacity_summary(_summary(stressed="-0.01"))
    entry = result["basis_margin_daily_capacity"]["1"]["entry"]
    assert Decimal(entry["projected_wilson95_stressed_profit_per_day_dollars"]) < 0
    assert entry["required_continuously_redeployed_capital_for_target_wilson95_stressed_dollars"] is None


def test_missing_efficiency_remains_unresolved():
    result = daily_capacity_summary(_summary(raw=None, stressed=None))
    entry = result["basis_margin_daily_capacity"]["1"]["entry"]
    assert entry["projected_settlement_adjusted_profit_per_day_dollars"] is None
    assert entry["projected_wilson95_stressed_profit_per_day_dollars"] is None


@pytest.mark.parametrize(
    ("capital", "utilization", "target"),
    [
        (Decimal("0"), Decimal("1"), Decimal("480")),
        (Decimal("1000"), Decimal("-0.1"), Decimal("480")),
        (Decimal("1000"), Decimal("1.1"), Decimal("480")),
        (Decimal("1000"), Decimal("1"), Decimal("0")),
    ],
)
def test_invalid_projection_inputs_fail_closed(capital, utilization, target):
    with pytest.raises(ValueError):
        daily_capacity_summary(
            _summary(),
            capital_dollars=capital,
            utilization=utilization,
            target_daily_profit=target,
        )
