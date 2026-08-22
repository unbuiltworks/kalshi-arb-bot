from decimal import Decimal

import pytest

from src.cross_venue_ledger import make_event
from src.weather_paper_capital_lock_analyze import analyze_weather_paper_capital_lock


def _fill(group="g1", *, ts_ms=1_000, price="0.36", qty="1", side="NO", fee="0"):
    return make_event(
        trade_group_id=group,
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fill",
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        ts_ms=ts_ms,
        external_id=f"{group}:fill",
    )


def _fee(group="g1", *, ts_ms=1_000, fee="0.02", side="NO"):
    return make_event(
        trade_group_id=group,
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="fee",
        side=side,
        fee=Decimal(fee),
        ts_ms=ts_ms,
        external_id=f"{group}:fee",
    )


def _settlement(group="g1", *, ts_ms=3_601_000, payout="1", side="NO", certified=True):
    external_id = (
        f"weather-certified-settlement:{group}:settlement"
        if certified
        else f"{group}:settlement"
    )
    return make_event(
        trade_group_id=group,
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B92.5",
        event_type="settlement",
        side=side,
        payout=Decimal(payout),
        ts_ms=ts_ms,
        external_id=external_id,
    )


def test_verified_weather_fill_uses_fill_to_official_settlement_capital_lock():
    result = analyze_weather_paper_capital_lock([_fill(), _fee(), _settlement()])
    assert result["filled_trade_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 1
    assert result["right_censored_groups"] == 0
    assert result["invalid_groups"] == 0
    assert result["all_filled_groups_resolved"] is True
    row = result["groups"][0]
    assert row["capital"] == "0.38"
    assert row["capital_lock_ms"] == 3_600_000
    assert Decimal(row["capital_lock_hours"]) == Decimal("0.38")
    assert row["realized_profit"] == "0.62"
    assert Decimal(row["realized_profit_per_capital_hour"]) == Decimal("0.62") / Decimal("0.38")
    assert Decimal(result["conservative_realized_profit_per_capital_hour"]) == Decimal("0.62") / Decimal("0.38")


def test_missing_settlement_is_right_censored_and_withholds_aggregate_efficiency():
    result = analyze_weather_paper_capital_lock([_fill(), _fee()])
    assert result["right_censored_groups"] == 1
    assert result["all_filled_groups_resolved"] is False
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "RIGHT_CENSORED_AWAITING_OFFICIAL_SETTLEMENT"


def test_unaudited_settlement_event_is_right_censored_not_realized():
    result = analyze_weather_paper_capital_lock([_fill(), _fee(), _settlement(certified=False)])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["right_censored_groups"] == 1
    assert result["invalid_groups"] == 0
    assert result["all_filled_groups_resolved"] is False
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "RIGHT_CENSORED_UNAUDITED_SETTLEMENT_EVENT"


def test_losing_weather_fill_records_full_cost_loss():
    result = analyze_weather_paper_capital_lock([_fill(side="YES"), _fee(side="YES"), _settlement(side="YES", payout="0")])
    row = result["groups"][0]
    assert row["realized_profit"] == "-0.38"


def test_duplicate_settlement_fails_closed():
    result = analyze_weather_paper_capital_lock([
        _fill(),
        _fee(),
        _settlement(),
        _settlement(ts_ms=3_602_000),
    ])
    assert result["invalid_groups"] == 1
    assert result["all_filled_groups_resolved"] is False
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "AMBIGUOUS_SETTLEMENT_FAIL_CLOSED"


def test_wrong_market_or_nonlater_settlement_fails_closed():
    bad_market = make_event(
        trade_group_id="g1",
        settlement_key="weather:KXHIGHMIA-26AUG21-B92.5",
        category="weather",
        venue="kalshi",
        market_id="KXHIGHMIA-26AUG21-B90.5",
        event_type="settlement",
        side="NO",
        payout=Decimal("1"),
        ts_ms=3_601_000,
        external_id="weather-certified-settlement:g1:settlement:bad",
    )
    result = analyze_weather_paper_capital_lock([_fill(), _fee(), bad_market])
    assert result["invalid_groups"] == 1
    assert result["groups"][0]["status"] == "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED"

    result = analyze_weather_paper_capital_lock([_fill(), _fee(), _settlement(ts_ms=1_000)])
    assert result["invalid_groups"] == 1
    assert result["groups"][0]["status"] == "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED"


def test_fractional_binary_settlement_payout_fails_closed():
    result = analyze_weather_paper_capital_lock([
        _fill(qty="2"),
        _fee(fee="0.03"),
        _settlement(payout="1"),
    ])
    assert result["verified_settlement_lock_groups"] == 0
    assert result["invalid_groups"] == 1
    assert result["all_filled_groups_resolved"] is False
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED"


def test_explicit_and_embedded_fee_double_representation_fails_closed():
    result = analyze_weather_paper_capital_lock([_fill(fee="0.02"), _fee(), _settlement()])
    assert result["invalid_groups"] == 1
    assert result["groups"][0]["status"] == "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED"
    assert result["conservative_realized_profit_per_capital_hour"] is None


def test_zero_explicit_fee_cannot_override_positive_embedded_fill_fee():
    result = analyze_weather_paper_capital_lock([
        _fill(fee="0.02"),
        _fee(fee="0"),
        _settlement(),
    ])
    assert result["invalid_groups"] == 1
    assert result["groups"][0]["status"] == "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED"
    assert result["conservative_realized_profit_per_capital_hour"] is None


def test_nonfinite_fill_values_are_rejected_at_ledger_boundary():
    for field, value in (("price", "NaN"), ("qty", "Infinity"), ("fee", "NaN")):
        kwargs = {field: value}
        with pytest.raises(ValueError, match="finite Decimal"):
            _fill(**kwargs)


def test_nonfinite_explicit_fee_is_rejected_at_ledger_boundary():
    with pytest.raises(ValueError, match="finite Decimal"):
        _fee(fee="NaN")


def test_nonfinite_settlement_payout_is_rejected_at_ledger_boundary():
    with pytest.raises(ValueError, match="finite Decimal"):
        _settlement(payout="NaN")
