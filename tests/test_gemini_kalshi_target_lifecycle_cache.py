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


def test_extracts_nested_metadata_updated_envelope():
    payload = {
        "type": "market_lifecycle_v2",
        "msg": {
            "event_type": "metadata_updated",
            "market_ticker": "KXBTC15M-26AUG191500-00",
            "floor_strike": 117234.5,
        },
    }
    assert cache.extract_lifecycle_target(payload) == (
        "KXBTC15M-26AUG191500-00",
        Decimal("117234.5"),
    )


def test_extracts_created_additional_metadata_floor_strike():
    payload = {
        "type": "market_lifecycle_v2",
        "sid": 13,
        "msg": {
            "market_ticker": "KXETH15M-26AUG191515-15",
            "event_type": "created",
            "additional_metadata": {
                "strike_type": "greater",
                "floor_strike": "2090.69",
            },
        },
    }
    assert cache.extract_lifecycle_target(payload) == (
        "KXETH15M-26AUG191515-15",
        Decimal("2090.69"),
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


def test_relevant_filter_accepts_only_supported_crypto_15m_tickers():
    for ticker in (
        "KXBTC15M-26AUG191515-15",
        "KXETH15M-26AUG191515-15",
        "KXSOL15M-26AUG191515-15",
        "KXXRP15M-26AUG191515-15",
    ):
        assert cache._relevant_crypto_ticker(ticker)
    assert not cache._relevant_crypto_ticker("KXDOGE15M-26AUG191515-15")
    assert not cache._relevant_crypto_ticker("KXBTC1H-26AUG191500")


def test_remember_rejects_unrelated_lifecycle_market():
    cache._remember("KXDOGE15M-26AUG191515-15", Decimal("0.22"), "TEST")
    assert cache._TARGET_BY_TICKER == {}
