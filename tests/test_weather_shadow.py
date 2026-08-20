from src.weather_shadow import (
    CATEGORY,
    SETTLEMENT_STATUS,
    TRADE_CLASSIFICATION,
    build_shadow_row,
)


def test_current_dollar_fields_are_normalized_to_cents():
    market = {
        "ticker": "KXHIGHNY-26AUG20-B84.5",
        "event_ticker": "KXHIGHNY-26AUG20",
        "title": "NYC high temperature",
        "yes_bid_dollars": "0.41",
        "yes_ask_dollars": "0.44",
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.58",
        "volume_fp": "123.0",
        "open_interest_fp": "45.0",
    }

    row = build_shadow_row("KXHIGHNY", market, observed_at="2026-08-20T14:00:00+00:00")

    assert row.yes_bid_cents == 41
    assert row.yes_ask_cents == 44
    assert row.no_bid_cents == 55
    assert row.no_ask_cents == 58
    assert row.volume == 123.0
    assert row.open_interest == 45.0
    assert row.category == CATEGORY == "weather"
    assert row.classification == TRADE_CLASSIFICATION == "SHADOW"
    assert row.settlement_status == SETTLEMENT_STATUS == "SETTLEMENT_AUDIT_REQUIRED"
    assert row.q1_yes_taker_fee_cents is not None
    assert row.q1_no_taker_fee_cents is not None


def test_legacy_integer_cent_fields_remain_supported_for_read_only_replay():
    market = {
        "ticker": "KXHIGHCHI-26AUG20-B80.5",
        "yes_bid": 31,
        "yes_ask": 35,
        "no_bid": 64,
        "no_ask": 68,
    }

    row = build_shadow_row("KXHIGHCHI", market)

    assert row.yes_bid_cents == 31
    assert row.yes_ask_cents == 35
    assert row.no_bid_cents == 64
    assert row.no_ask_cents == 68


def test_missing_prices_never_become_fake_zero_quotes():
    row = build_shadow_row("KXHIGHMIA", {"ticker": "KXHIGHMIA-X"})

    assert row.yes_bid_cents is None
    assert row.yes_ask_cents is None
    assert row.no_bid_cents is None
    assert row.no_ask_cents is None
    assert row.q1_yes_taker_fee_cents is None
    assert row.q1_no_taker_fee_cents is None


def test_module_has_no_live_trading_surface():
    import src.weather_shadow as weather_shadow

    forbidden = {
        "place_order",
        "cancel_order",
        "buy_yes",
        "buy_no",
        "submit_order",
        "execute_trade",
    }
    assert forbidden.isdisjoint(set(dir(weather_shadow)))
