from src.short_crypto_incentive_snapshot import (
    is_kalshi_crypto15_incentive,
    is_kalshi_target_15m_incentive,
    is_short_crypto_reward_row,
)


def test_gemini_short_crypto_reward_filter_accepts_btc15m():
    row = {"event_ticker": "BTC15M2608280100", "title": "Bitcoin 15 min"}
    assert is_short_crypto_reward_row(row) is True


def test_gemini_short_crypto_reward_filter_rejects_nonshort_crypto():
    assert is_short_crypto_reward_row({"title": "Bitcoin end of day"}) is False
    assert is_short_crypto_reward_row({"title": "NFL 15 min special"}) is False


def test_kalshi_crypto15_filter_accepts_series_or_description():
    assert is_kalshi_crypto15_incentive({"series_ticker": "KXBTC15M"}) is True
    assert is_kalshi_crypto15_incentive({"incentive_description": "Crypto 15min markets"}) is True


def test_kalshi_crypto15_filter_rejects_hourly_or_noncrypto():
    assert is_kalshi_crypto15_incentive({"series_ticker": "KXBTCD"}) is False
    assert is_kalshi_crypto15_incentive({"incentive_description": "Commodities 15min markets"}) is False


def test_kalshi_target_filter_accepts_actual_short_crypto_series():
    assert is_kalshi_target_15m_incentive({"series_ticker": "KXBTC15M"}) is True
    assert is_kalshi_target_15m_incentive({"market_ticker": "KXETH15M-26AUG272130-T123"}) is True
    assert is_kalshi_target_15m_incentive({"market_ticker": "KXSOL15M-26AUG272130-T123"}) is True
    assert is_kalshi_target_15m_incentive({"market_ticker": "KXXRP15M-26AUG272130-T123"}) is True


def test_kalshi_target_filter_rejects_crypto_lead_and_hourly_families():
    assert is_kalshi_target_15m_incentive({"market_ticker": "KXCRYPTOLEAD15M-26AUG272130-BTC"}) is False
    assert is_kalshi_target_15m_incentive({"series_ticker": "KXBTCD"}) is False
