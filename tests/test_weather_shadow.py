from src.weather_forecast import event_date, parse_bracket
from src.weather_shadow import (
    CATEGORY,
    MODEL_STATUS,
    SETTLEMENT_STATUS,
    TRADE_CLASSIFICATION,
    build_shadow_row,
)


def test_current_dollar_fields_are_normalized_to_cents():
    market = {
        "ticker": "KXHIGHNY-26AUG20-B84.5",
        "event_ticker": "KXHIGHNY-26AUG20",
        "title": "Will the maximum temperature be 84-85° on Aug 20, 2026?",
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
    assert row.model_status == MODEL_STATUS == "MODEL_RESEARCH_ONLY"
    assert row.q1_yes_taker_fee_cents is not None
    assert row.q1_no_taker_fee_cents is not None
    assert row.station_hint == "KNYC"
    assert row.forecast_target_date == "2026-08-20"


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
    assert row.q1_yes_model_ev_cents is None
    assert row.q1_no_model_ev_cents is None


def test_event_date_and_integer_temperature_brackets_are_parsed_conservatively():
    assert event_date("KXHIGHNY-26AUG21") .isoformat() == "2026-08-21"
    assert parse_bracket("Will the maximum temperature be 82-83° on Aug 21, 2026?") == (81.5, 83.5)
    assert parse_bracket("Will the maximum temperature be >85° on Aug 21, 2026?") == (85.5, None)
    assert parse_bracket("Will the maximum temperature be <78° on Aug 21, 2026?") == (None, 77.5)


def test_forecast_context_produces_fair_value_and_after_fee_shadow_ev():
    market = {
        "ticker": "KXHIGHCHI-26AUG21-B80.5",
        "event_ticker": "KXHIGHCHI-26AUG21",
        "title": "Will the maximum temperature be 80-81° on Aug 21, 2026?",
        "yes_ask": 30,
        "no_ask": 72,
    }
    context = {
        "models": {"gfs": 80.4, "ecmwf": 80.8, "icon": 80.6},
        "mean_f": 80.6,
        "std_f": 2.2,
    }

    row = build_shadow_row("KXHIGHCHI", market, forecast_context=context)

    assert row.forecast_models == context["models"]
    assert row.forecast_mean_f == 80.6
    assert row.forecast_std_f == 2.2
    assert row.model_yes_probability is not None
    assert 0 < row.model_yes_probability < 1
    assert row.model_fair_yes_cents is not None
    assert row.q1_yes_model_ev_cents is not None
    assert row.q1_no_model_ev_cents is not None


def test_module_has_no_live_trading_surface():
    import src.weather_forecast as weather_forecast
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
    assert forbidden.isdisjoint(set(dir(weather_forecast)))
