"""Independent entry-economics tests. No venue or replay dependencies."""
from src.l5_entry_economics import evaluate_opening as assess


def test_observed_residual_scenario_needs_over_ninety_percent_completion():
    r = assess(completed_net_per_contract="0.02",
               residual_net_per_contract="-0.1855",
               completion_probability_given_first_fill="0.90")
    assert r["status"] == "SCENARIO_ONLY"
    assert r["admissible"] is False
    assert r["scenario_clears_edge"] is False
    assert r["break_even_completion_probability"].startswith("0.9026")


def test_strong_scenario_is_still_not_a_live_admission():
    r = assess(completed_net_per_contract="0.02",
               residual_net_per_contract="-0.1855",
               completion_probability_given_first_fill="0.95")
    assert r["scenario_clears_edge"] is True
    assert r["admissible"] is False


def test_missing_or_invalid_probability_fails_closed():
    for p in (None, "", "NaN", "Infinity", -0.01, 1.01, True):
        r = assess(completed_net_per_contract="0.02",
                   residual_net_per_contract="-0.1855",
                   completion_probability_given_first_fill=p)
        assert r["status"] == "UNKNOWN"
        assert r["admissible"] is False


def test_non_improving_completion_fails_closed():
    r = assess(completed_net_per_contract="-0.20",
               residual_net_per_contract="-0.10",
               completion_probability_given_first_fill="0.9")
    assert r["status"] == "UNKNOWN"


def test_net_edge_floor_is_strict_and_units_are_per_contract():
    r = assess(completed_net_per_contract="0.04",
               residual_net_per_contract="-0.06",
               completion_probability_given_first_fill="0.7",
               minimum_net_edge_per_contract="0.01")
    assert r["conditional_expected_net_per_contract"] == "0.010"
    assert r["scenario_clears_edge"] is False
