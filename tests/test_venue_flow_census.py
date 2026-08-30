from decimal import Decimal as D

from src.venue_flow_census import PassiveTrade, census, q_aware_residual_cap_ceiling


def test_policy_free_raw_ceiling_uses_min_side_flow():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("100")),
        PassiveTrade("gemini", "m1", "NO", D("60")),
    ]
    out = census(trades)
    assert out["raw_contract_matched_ceiling"] == "60"
    assert out["raw_residual_if_all_flow_captured"] == "40"
    assert out["raw_matched_to_residual_ratio"] == "1.5"


def test_alternation_rate_is_informational():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("1"), 1),
        PassiveTrade("gemini", "m1", "NO", D("1"), 2),
        PassiveTrade("gemini", "m1", "YES", D("1"), 3),
        PassiveTrade("gemini", "m1", "YES", D("1"), 4),
    ]
    out = census(trades)
    d = out["markets_detail"]["gemini:m1"]
    assert d["alternation_rate_informational_only"] == 2 / 3
    assert d["two_sided_flow"] is True


def test_one_sided_window_has_zero_raw_matched_ceiling():
    out = census([PassiveTrade("gemini", "m1", "YES", D("12"))])
    assert out["raw_contract_matched_ceiling"] == "0"
    assert out["raw_residual_if_all_flow_captured"] == "12"
    assert out["raw_matched_to_residual_ratio"] == "0"
    assert out["two_sided_window_fraction"] == 0.0


def test_q_aware_event_cap_prevents_large_sweep_from_flattering_ceiling():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("140"), 1),
        PassiveTrade("gemini", "m1", "NO", D("140"), 2),
    ]
    c = q_aware_residual_cap_ceiling(trades, q=D("10"), residual_cap=D("10"))
    assert c["matched"] == D("10")
    assert c["residual"] == D("0")


def test_residual_cap_declines_heavy_side_until_light_side_arrives():
    trades = [
        PassiveTrade("gemini", "m1", "YES", D("100"), 1),
        PassiveTrade("gemini", "m1", "YES", D("100"), 2),
        PassiveTrade("gemini", "m1", "NO", D("100"), 3),
        PassiveTrade("gemini", "m1", "NO", D("100"), 4),
    ]
    c = q_aware_residual_cap_ceiling(trades, q=D("10"), residual_cap=D("10"))
    # First YES consumes the residual budget; second heavy-side YES is declined.
    # The first NO balances the book. Once balanced, the next NO may use the
    # residual budget in the opposite direction, ending at the cap rather than
    # exceeding it.
    assert c["yes_accepted"] == D("10")
    assert c["no_accepted"] == D("20")
    assert c["matched"] == D("10")
    assert c["residual"] == D("10")
    assert c["skipped_heavy_events"] == 1


def test_census_reports_q_aware_cap_curve_and_dollar_ceiling():
    trades = [
        PassiveTrade("gemini", "a", "YES", D("100"), 1),
        PassiveTrade("gemini", "a", "NO", D("100"), 2),
        PassiveTrade("gemini", "a", "YES", D("100"), 3),
    ]
    out = census(trades, q=D("10"), residual_caps=(D("10"),))
    q10 = out["q_aware_residual_cap_curve"]["10"]
    assert q10["matched_ceiling"] == "10"
    assert q10["ending_residual"] == "10"
    assert q10["matched_to_residual_ratio"] == "1"
    assert q10["l5_calibrated_gross_locked_dollar_ceiling"] == "0.390"
    assert out["classification"] == "READ_ONLY_POLICY_FREE_VENUE_FLOW_CENSUS_NOT_REALIZED"
    assert out["order_entry"] is False
