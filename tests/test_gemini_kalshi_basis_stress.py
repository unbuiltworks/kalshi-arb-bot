from decimal import Decimal

from src.gemini_kalshi_basis_stress import stress_scenario, stress_summary


def _scenario(*, contracts="10", apparent="0.50", upper=0.2):
    return {
        "consumable_contracts": contracts,
        "apparent_locked_profit_dollars": apparent,
        "consumable_mismatch_rate_wilson_upper_95": upper,
    }


def test_stress_scenario_converts_basis_uncertainty_to_dollars():
    result = stress_scenario(_scenario())
    assert Decimal(result["wilson95_adverse_basis_stress_loss_dollars"]) == Decimal("2.0")
    assert Decimal(result["wilson95_adverse_basis_stress_pnl_dollars"]) == Decimal("-1.50")


def test_stress_scenario_no_sample_does_not_invent_certainty():
    result = stress_scenario(_scenario(contracts="0", apparent="0", upper=None))
    assert result["wilson95_adverse_basis_stress_loss_dollars"] is None
    assert result["wilson95_adverse_basis_stress_pnl_dollars"] is None


def test_stress_summary_applies_to_latency_and_margin_cohorts():
    source = {
        "entry": _scenario(upper=0.1),
        "survival": {"50": _scenario(contracts="5", apparent="0.25", upper=0.1)},
        "basis_margin_sensitivity": {
            "1": {
                "min_abs_gemini_margin_bps": "1",
                "entry": _scenario(contracts="4", apparent="0.40", upper=0.05),
                "survival": {"50": _scenario(contracts="2", apparent="0.20", upper=0.05)},
            }
        },
    }
    result = stress_summary(source)
    assert Decimal(result["entry"]["wilson95_adverse_basis_stress_pnl_dollars"]) == Decimal("-0.50")
    assert Decimal(result["survival"]["50"]["wilson95_adverse_basis_stress_pnl_dollars"]) == Decimal("-0.25")
    assert Decimal(result["basis_margin_sensitivity"]["1"]["entry"]["wilson95_adverse_basis_stress_pnl_dollars"]) == Decimal("0.20")
    assert result["favorable_basis_mismatch_credit"] is False
    assert result["read_only"] is True
    assert result["order_entry"] is False


def test_invalid_probability_fails_closed():
    try:
        stress_scenario(_scenario(upper=1.01))
    except ValueError as exc:
        assert "probability" in str(exc)
    else:
        raise AssertionError("expected invalid Wilson probability to fail closed")
