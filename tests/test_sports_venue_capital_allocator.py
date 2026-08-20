from decimal import Decimal

import pytest

from src.sports_venue_capital_allocator import (
    RouteCapitalRequirement,
    VenueCapitalSnapshot,
    VenueCapitalUnavailable,
    assert_route_capacity,
    route_requirement,
)

D = Decimal


def _payloads(*, gemini_price="0.64", kalshi_yes_price="0.29", kalshi_side="bid"):
    gemini = {
        "side": "BUY",
        "price": gemini_price,
        "quantity": "1",
        "eventOutcome": "NO" if kalshi_side == "bid" else "YES",
    }
    kalshi = {
        "side": kalshi_side,
        "price": kalshi_yes_price,
        "count": "1",
    }
    return gemini, kalshi


def test_route_requirement_reserves_each_venue_leg_and_fees_separately():
    gemini, kalshi = _payloads()
    row = route_requirement(gemini, kalshi, kalshi_fee_multiplier=D("0.07"))
    assert row.gemini_required > D("0.64")
    assert row.kalshi_required > D("0.29")
    assert row.total_required == row.gemini_required + row.kalshi_required
    assert row.total_required < D("1")


def test_kalshi_no_leg_reserves_one_minus_yes_price_not_yes_price():
    gemini, kalshi = _payloads(gemini_price="0.20", kalshi_yes_price="0.25", kalshi_side="ask")
    row = route_requirement(gemini, kalshi, kalshi_fee_multiplier=D("0.07"))
    assert row.kalshi_contract_cost == D("0.75")


def test_fresh_snapshot_with_buffer_authorizes_both_legs():
    requirement = RouteCapitalRequirement(
        gemini_required=D("0.65"),
        kalshi_required=D("0.31"),
        total_required=D("0.96"),
        gemini_contract_cost=D("0.64"),
        kalshi_contract_cost=D("0.29"),
        gemini_fee=D("0.01"),
        kalshi_fee=D("0.02"),
    )
    snapshot = VenueCapitalSnapshot(D("10"), D("20"), observed_ms=1000)
    assert_route_capacity(snapshot, requirement, free_buffer=D("5"), now_ms=2000, max_snapshot_age_ms=5000)


def test_insufficient_second_venue_blocks_entire_pair_before_send():
    requirement = RouteCapitalRequirement(
        gemini_required=D("0.65"),
        kalshi_required=D("0.31"),
        total_required=D("0.96"),
        gemini_contract_cost=D("0.64"),
        kalshi_contract_cost=D("0.29"),
        gemini_fee=D("0.01"),
        kalshi_fee=D("0.02"),
    )
    snapshot = VenueCapitalSnapshot(D("100"), D("5.20"), observed_ms=1000)
    with pytest.raises(VenueCapitalUnavailable, match="Kalshi route capital unavailable"):
        assert_route_capacity(snapshot, requirement, free_buffer=D("5"), now_ms=2000, max_snapshot_age_ms=5000)


def test_stale_balance_snapshot_blocks_even_when_nominal_balances_are_large():
    requirement = RouteCapitalRequirement(
        gemini_required=D("0.50"),
        kalshi_required=D("0.50"),
        total_required=D("1.00"),
        gemini_contract_cost=D("0.49"),
        kalshi_contract_cost=D("0.49"),
        gemini_fee=D("0.01"),
        kalshi_fee=D("0.01"),
    )
    snapshot = VenueCapitalSnapshot(D("1000"), D("1000"), observed_ms=1000)
    with pytest.raises(VenueCapitalUnavailable, match="snapshot stale"):
        assert_route_capacity(snapshot, requirement, free_buffer=D("5"), now_ms=7001, max_snapshot_age_ms=5000)


def test_missing_snapshot_fails_closed():
    requirement = RouteCapitalRequirement(
        gemini_required=D("0.50"),
        kalshi_required=D("0.50"),
        total_required=D("1.00"),
        gemini_contract_cost=D("0.49"),
        kalshi_contract_cost=D("0.49"),
        gemini_fee=D("0.01"),
        kalshi_fee=D("0.01"),
    )
    with pytest.raises(VenueCapitalUnavailable, match="snapshot missing"):
        assert_route_capacity(None, requirement)
