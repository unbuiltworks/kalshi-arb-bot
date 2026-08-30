from decimal import Decimal as D

from src.venue_flow_census import PassiveTrade, census


def test_policy_free_ceiling_uses_min_side_flow():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("100")),
        PassiveTrade("gemini", "m1", "NO", D("60")),
    ]
    out = census(trades)
    assert out["max_matched_formation_ceiling"] == "60"
    assert out["minimum_residual_if_all_flow_captured"] == "40"
    assert out["matched_to_residual_ceiling_ratio"] == "1.5"


def test_alternation_rate_counts_side_flips():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("1")),
        PassiveTrade("gemini", "m1", "NO", D("1")),
        PassiveTrade("gemini", "m1", "YES", D("1")),
        PassiveTrade("gemini", "m1", "YES", D("1")),
    ]
    out = census(trades)
    d = out["markets_detail"]["gemini:m1"]
    assert d["alternation_rate"] == 2 / 3
    assert d["two_sided_flow"] is True


def test_one_sided_window_has_zero_matched_ceiling():
    out = census([PassiveTrade("gemini", "m1", "YES", D("12"))])
    assert out["max_matched_formation_ceiling"] == "0"
    assert out["minimum_residual_if_all_flow_captured"] == "12"
    assert out["matched_to_residual_ceiling_ratio"] == "0"
    assert out["two_sided_window_fraction"] == 0.0


def test_aggregate_ratio_is_from_aggregate_flow_not_average_of_ratios():
    trades = [
        PassiveTrade("gemini", "a", "YES", D("10")),
        PassiveTrade("gemini", "a", "NO", D("5")),
        PassiveTrade("gemini", "b", "YES", D("100")),
        PassiveTrade("gemini", "b", "NO", D("80")),
    ]
    out = census(trades)
    assert out["max_matched_formation_ceiling"] == "85"
    assert out["minimum_residual_if_all_flow_captured"] == "25"
    assert out["matched_to_residual_ceiling_ratio"] == "3.4"
    assert out["classification"] == "READ_ONLY_POLICY_FREE_VENUE_FLOW_CENSUS_NOT_REALIZED"
    assert out["order_entry"] is False
