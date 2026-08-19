from decimal import Decimal

from src import gemini_kalshi_target_lifecycle_cache as cache


def setup_function():
    cache._TARGET_BY_TICKER.clear()
    cache._WATCHED_TICKERS.clear()


def test_extracts_documented_top_level_floor_strike():
    payload = {
        "type": "metadata_updated",
        "market_ticker": "KXXRP15M-26AUG191500-00",
        "floor_strike": "1.0342",
    }
    assert cache.extract_lifecycle_target(payload) == (
        "KXXRP15M-26AUG191500-00",
        Decimal("1.0342"),
    )


def test_extracts_nested_lifecycle_envelope():
    payload = {
        "type": "market_lifecycle_v2",
        "msg": {
            "event_type": "metadata_updated",
            "ticker": "KXBTC15M-26AUG191500-00",
            "floor_strike": 117234.5,
        },
    }
    assert cache.extract_lifecycle_target(payload) == (
        "KXBTC15M-26AUG191500-00",
        Decimal("117234.5"),
    )


def test_never_infers_target_without_numeric_floor_strike():
    assert cache.extract_lifecycle_target({
        "type": "metadata_updated",
        "market_ticker": "KXETH15M-26AUG191500-00",
        "title": "ETH above 2084.4",
    }) is None
    assert cache.extract_lifecycle_target({
        "type": "metadata_updated",
        "market_ticker": "KXETH15M-26AUG191500-00",
        "floor_strike": "not-a-number",
    }) is None


def test_cache_is_exact_ticker_keyed():
    cache._remember("KXSOL15M-26AUG191500-00", Decimal("84.125"), "TEST")
    row = cache._target_for_ticker("KXSOL15M-26AUG191500-00")
    assert row is not None
    assert row[0] == Decimal("84.125")
    assert row[1] == "TEST"
    assert cache._target_for_ticker("KXSOL15M-26AUG191515-15") is None
