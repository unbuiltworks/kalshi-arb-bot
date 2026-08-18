from decimal import Decimal as D

import pytest

from src.sports_kgp_micro_guard import RuleApproval, build_micro_plan
from src.three_venue_pricing_core import PricedQuote
from src.universal_prediction_quant import LockOpportunity, MarketQuote


NOW = 1_800_000_000_000
FP = "a" * 64
KEY = "baseball|mlb|player_prop|game|hits|player-1|ge|1"
FINAL_SOURCE = {
    "kalshi": "kalshi_public_orderbook_depth1_final",
    "gemini": "gemini_depth10_100ms_final",
    "polymarket": "polymarket_public_book_final",
}


def approval(a="kalshi", b="gemini", *, approved=True):
    markets = {"kalshi": "KX-PROP", "gemini": "GEMI-PROP", "polymarket": "poly-prop"}
    return RuleApproval(
        settlement_key=KEY,
        venue_a=a,
        market_a=markets[a],
        source_side_a="YES",
        venue_b=b,
        market_b=markets[b],
        source_side_b="YES",
        rule_fingerprint_sha256=FP,
        approved_for_execution=approved,
    )


def priced(venue, *, yes="0.40", no="0.60", received=NOW - 20, confidence="FULL_RULES_AUDITED_EXECUTABLE", depth_source=None):
    markets = {"kalshi": "KX-PROP", "gemini": "GEMI-PROP", "polymarket": "poly-prop", "sportsbook:fanduel": "fd"}
    q = MarketQuote(
        venue=venue,
        market_id=f"{markets[venue]}#YES",
        settlement_key=KEY,
        category="sports_mlb_prop",
        yes_cost=D(yes),
        no_cost=D(no),
        yes_depth=D("1"),
        no_depth=D("1"),
        received_ms=received,
        settlement_confidence=confidence,
        expected_settlement_minutes=180,
    )
    return PricedQuote(
        quote=q,
        raw_yes_ask=D(yes),
        raw_no_ask=D(no),
        yes_fee=D("0"),
        no_fee=D("0"),
        depth_source=depth_source or FINAL_SOURCE.get(venue, "sportsbook_test"),
        source_side="YES",
    )


def opp(*, yes_venue="kalshi", no_venue="gemini", profit="0.05", confidence="FULL_RULES_AUDITED_EXECUTABLE", skew=10):
    cost = D("1") - D(profit)
    return LockOpportunity(
        settlement_key=KEY,
        category="sports_mlb_prop",
        yes_venue=yes_venue,
        yes_market_id={"kalshi": "KX-PROP#YES", "gemini": "GEMI-PROP#YES", "polymarket": "poly-prop#YES", "sportsbook:fanduel": "fd#YES"}[yes_venue],
        no_venue=no_venue,
        no_market_id={"kalshi": "KX-PROP#YES", "gemini": "GEMI-PROP#YES", "polymarket": "poly-prop#YES", "sportsbook:fanduel": "fd#YES"}[no_venue],
        unit_cost=cost,
        unit_profit=D(profit),
        max_quantity=D("1"),
        max_capital=cost,
        max_locked_profit=D(profit),
        quote_skew_ms=skew,
        settlement_confidence=confidence,
        expected_settlement_minutes=180,
        capital_velocity_score=D("1"),
    )


def test_certified_kalshi_gemini_q1_plan_passes():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55")]
    plan = build_micro_plan(opp(profit="0.05"), rows, approval(), now_ms=NOW)
    assert plan.quantity == D("1")
    assert plan.capital == D("0.95")
    assert plan.locked_profit == D("0.05")
    assert {plan.yes_leg.venue, plan.no_leg.venue} == {"kalshi", "gemini"}


def test_rule_risk_confidence_can_never_execute():
    rows = [priced("kalshi", yes="0.40", no="0.60", confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"), priced("gemini", yes="0.45", no="0.55", confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE")]
    with pytest.raises(PermissionError, match="fully rule-audited"):
        build_micro_plan(opp(profit="0.05", confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"), rows, approval(), now_ms=NOW)


def test_sportsbook_pair_is_rejected_even_if_mislabeled_executable():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("sportsbook:fanduel", yes="0.45", no="0.55")]
    bad = RuleApproval(KEY, "kalshi", "KX-PROP", "YES", "sportsbook:fanduel", "fd", "YES", FP, True)
    with pytest.raises(ValueError, match="prediction-market-only|K/G/P"):
        build_micro_plan(opp(no_venue="sportsbook:fanduel", profit="0.05"), rows, bad, now_ms=NOW)


def test_unapproved_rule_record_is_rejected():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55")]
    with pytest.raises(PermissionError, match="not execution-approved"):
        build_micro_plan(opp(profit="0.05"), rows, approval(approved=False), now_ms=NOW)


def test_non_final_execution_source_is_rejected():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55", depth_source="event_prices_q1_probe_only")]
    with pytest.raises(PermissionError, match="approved final execution book"):
        build_micro_plan(opp(profit="0.05"), rows, approval(), now_ms=NOW)


def test_stale_book_is_rejected():
    rows = [priced("kalshi", yes="0.40", no="0.60", received=NOW - 101), priced("gemini", yes="0.45", no="0.55")]
    with pytest.raises(ValueError, match="stale"):
        build_micro_plan(opp(profit="0.05"), rows, approval(), now_ms=NOW)


def test_quote_skew_over_50ms_is_rejected():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55")]
    with pytest.raises(ValueError, match="quote skew"):
        build_micro_plan(opp(profit="0.05", skew=51), rows, approval(), now_ms=NOW)


def test_edge_below_3c_is_rejected():
    rows = [priced("kalshi", yes="0.49", no="0.51"), priced("gemini", yes="0.48", no="0.52")]
    with pytest.raises(ValueError, match="edge below"):
        build_micro_plan(opp(profit="0.02"), rows, approval(), now_ms=NOW)


def test_changed_market_id_is_rejected():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55")]
    wrong = RuleApproval(KEY, "kalshi", "OTHER", "YES", "gemini", "GEMI-PROP", "YES", FP, True)
    with pytest.raises(PermissionError, match="market ID changed"):
        build_micro_plan(opp(profit="0.05"), rows, wrong, now_ms=NOW)


def test_changed_economics_are_rejected_before_execution():
    rows = [priced("kalshi", yes="0.41", no="0.59"), priced("gemini", yes="0.45", no="0.55")]
    with pytest.raises(ValueError, match="economics changed"):
        build_micro_plan(opp(profit="0.05"), rows, approval(), now_ms=NOW)


def test_explicit_cap_can_fail_closed_below_one_contract_cost():
    rows = [priced("kalshi", yes="0.40", no="0.60"), priced("gemini", yes="0.45", no="0.55")]
    with pytest.raises(ValueError, match="capital ceiling"):
        build_micro_plan(opp(profit="0.05"), rows, approval(), now_ms=NOW, max_capital=D("0.90"))


def test_all_three_prediction_pair_shapes_are_supported_by_guard():
    pairs = [("kalshi", "gemini"), ("kalshi", "polymarket"), ("gemini", "polymarket")]
    for a, b in pairs:
        rows = [priced(a, yes="0.40", no="0.60"), priced(b, yes="0.45", no="0.55")]
        plan = build_micro_plan(opp(yes_venue=a, no_venue=b, profit="0.05"), rows, approval(a, b), now_ms=NOW)
        assert {plan.yes_leg.venue, plan.no_leg.venue} == {a, b}
