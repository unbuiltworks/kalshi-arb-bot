from datetime import datetime, timezone

from src.gemini_l5_short_crypto_universe import family, symbols


def test_generates_btc_and_eth_5m_and_15m_windows():
    now = datetime(2026, 9, 13, 11, 2, 30, tzinfo=timezone.utc)
    out = symbols(now, count_per_family=2)
    assert out == [
        "GEMI-BTC05M2609131105-UP",
        "GEMI-BTC05M2609131110-UP",
        "GEMI-BTC15M2609131115-UP",
        "GEMI-BTC15M2609131130-UP",
        "GEMI-ETH05M2609131105-UP",
        "GEMI-ETH05M2609131110-UP",
        "GEMI-ETH15M2609131115-UP",
        "GEMI-ETH15M2609131130-UP",
    ]


def test_exact_boundary_advances_to_next_window():
    now = datetime(2026, 9, 13, 11, 15, 0, tzinfo=timezone.utc)
    out = symbols(now, count_per_family=1)
    assert out == [
        "GEMI-BTC05M2609131120-UP",
        "GEMI-BTC15M2609131130-UP",
        "GEMI-ETH05M2609131120-UP",
        "GEMI-ETH15M2609131130-UP",
    ]


def test_family_labels_short_crypto_symbols():
    assert family("GEMI-BTC05M2609131105-UP") == "BTC05M"
    assert family("GEMI-BTC15M2609131115-UP") == "BTC15M"
    assert family("GEMI-ETH05M2609131105-UP") == "ETH05M"
    assert family("GEMI-ETH15M2609131115-UP") == "ETH15M"
