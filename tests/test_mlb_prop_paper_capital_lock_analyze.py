from decimal import Decimal

from src.cross_venue_ledger import make_event
from src.mlb_prop_paper_capital_lock_analyze import analyze_paper_capital_lock

D = Decimal


def _fill(venue: str, side: str, *, ts_ms: int = 1000, price: str = "0.45", fee: str = "0.01"):
    return make_event(
        trade_group_id="g1",
        settlement_key="hits|game|player|1",
        category="mlb_hits",
        venue=venue,
        market_id=f"{venue}-m1",
        event_type="fill",
        side=side,
        quantity=D("1"),
        price=D(price),
        fee=D(fee),
        ts_ms=ts_ms,
    )


def _fee(venue: str, *, fee: str, market_id: str | None = None, ts_ms: int = 1000):
    return make_event(
        trade_group_id="g1",
        settlement_key="hits|game|player|1",
        category="mlb_hits",
        venue=venue,
        market_id=market_id or f"{venue}-m1",
        event_type="fee",
        fee=D(fee),
        ts_ms=ts_ms,
    )


def _settlement(
    venue: str,
    *,
    ts_ms: int,
    payout: str,
    settlement_key: str = "hits|game|player|1",
    category: str = "mlb_hits",
    side: str | None = None,
):
    return make_event(
        trade_group_id="g1",
        settlement_key=settlement_key,
        category=category,
        venue=venue,
        market_id=f"{venue}-m1",
        event_type="settlement",
        side=side,
        payout=D(payout),
        ts_ms=ts_ms,
    )


def test_uses_later_venue_settlement_for_true_capital_lock():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES", price="0.44", fee="0.01"),
        _fill("kalshi", "NO", price="0.50", fee="0.01"),
        _settlement("polymarket_us", ts_ms=3_601_000, payout="1"),
        _settlement("kalshi", ts_ms=7_201_000, payout="0"),
    ])

    assert result["verified_settlement_lock_groups"] == 1
    assert result["all_filled_groups_resolved"] is True
    assert result["opportunity_duration_is_capital_lock"] is False
    group = result["groups"][0]
    assert group["capital_lock_ms"] == 7_200_000
    assert Decimal(group["capital"]) == Decimal("0.96")
    assert Decimal(group["capital_lock_hours"]) == Decimal("1.92")
    assert Decimal(group["realized_profit"]) == Decimal("0.04")
    assert Decimal(result["conservative_realized_profit_per_capital_hour"]) == Decimal("0.04") / Decimal("1.92")


def test_actual_microprobe_separate_fee_events_are_included_in_capital_and_profit():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES", price="0.44", fee="0"),
        _fill("kalshi", "NO", price="0.50", fee="0"),
        _fee("polymarket_us", fee="0.01"),
        _fee("kalshi", fee="0.02"),
        _settlement("polymarket_us", ts_ms=3_601_000, payout="1"),
        _settlement("kalshi", ts_ms=7_201_000, payout="0"),
    ])

    assert result["verified_settlement_lock_groups"] == 1
    group = result["groups"][0]
    assert Decimal(group["poly_fee"]) == Decimal("0.01")
    assert Decimal(group["kalshi_fee"]) == Decimal("0.02")
    assert Decimal(group["total_fee"]) == Decimal("0.03")
    assert Decimal(group["capital"]) == Decimal("0.97")
    assert Decimal(group["capital_lock_hours"]) == Decimal("1.94")
    assert Decimal(group["realized_profit"]) == Decimal("0.03")


def test_embedded_and_separate_nonzero_fee_representations_fail_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES", price="0.44", fee="0.01"),
        _fill("kalshi", "NO", price="0.50", fee="0"),
        _fee("polymarket_us", fee="0.01"),
        _fee("kalshi", fee="0.01"),
        _settlement("polymarket_us", ts_ms=3_601_000, payout="1"),
        _settlement("kalshi", ts_ms=7_201_000, payout="0"),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED"


def test_fee_event_for_wrong_market_leg_fails_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES", price="0.44", fee="0"),
        _fill("kalshi", "NO", price="0.50", fee="0"),
        _fee("polymarket_us", fee="0.01", market_id="polymarket_us-wrong-market"),
        _fee("kalshi", fee="0.01"),
        _settlement("polymarket_us", ts_ms=3_601_000, payout="1"),
        _settlement("kalshi", ts_ms=7_201_000, payout="0"),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0
    assert result["groups"][0]["status"] == "AMBIGUOUS_OR_MISMATCHED_FEE_ACCOUNTING_FAIL_CLOSED"


def test_missing_second_settlement_is_right_censored_and_withholds_efficiency():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES"),
        _fill("kalshi", "NO"),
        _settlement("polymarket_us", ts_ms=5000, payout="1"),
    ])

    assert result["right_censored_groups"] == 1
    assert result["all_filled_groups_resolved"] is False
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["capital_lock_hours"] is None


def test_nonpositive_or_fallback_settlement_timestamp_fails_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES"),
        _fill("kalshi", "NO"),
        _settlement("polymarket_us", ts_ms=1000, payout="1"),
        _settlement("kalshi", ts_ms=5000, payout="0"),
    ])

    assert result["invalid_groups"] == 1
    assert result["conservative_realized_profit_per_capital_hour"] is None
    assert result["groups"][0]["status"] == "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED"


def test_settlement_key_or_category_mismatch_fails_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES"),
        _fill("kalshi", "NO"),
        _settlement("polymarket_us", ts_ms=5000, payout="1", settlement_key="wrong|key"),
        _settlement("kalshi", ts_ms=5000, payout="0"),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0
    assert result["groups"][0]["status"] == "INVALID_SETTLEMENT_IDENTITY_TIMING_OR_PAYOUT_FAIL_CLOSED"


def test_settlement_side_mismatch_fails_closed_when_side_is_present():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES"),
        _fill("kalshi", "NO"),
        _settlement("polymarket_us", ts_ms=5000, payout="1", side="NO"),
        _settlement("kalshi", ts_ms=5000, payout="0", side="NO"),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0


def test_settlement_payout_above_filled_quantity_fails_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES"),
        _fill("kalshi", "NO"),
        _settlement("polymarket_us", ts_ms=5000, payout="2"),
        _settlement("kalshi", ts_ms=5000, payout="0"),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0
    assert result["conservative_realized_profit_per_capital_hour"] is None


def test_nonsimultaneous_synthetic_fills_fail_closed():
    result = analyze_paper_capital_lock([
        _fill("polymarket_us", "YES", ts_ms=1000),
        _fill("kalshi", "NO", ts_ms=1001),
    ])

    assert result["invalid_groups"] == 1
    assert result["verified_settlement_lock_groups"] == 0
    assert result["live_probe_ready"] is False
    assert result["order_entry"] is False
