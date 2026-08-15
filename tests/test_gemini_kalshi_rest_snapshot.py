from decimal import Decimal

import pytest

from src.gemini_kalshi_rest_snapshot import gemini_rest_prices


def test_gemini_rest_prices_reads_documented_best_bid_ask():
    event = {
        "contracts": [{
            "instrumentSymbol": "GEMI-BTC15M2608141945-UP",
            "prices": {"bestBid": "0.47", "bestAsk": "0.51"},
        }]
    }
    bid, ask = gemini_rest_prices(event, "GEMI-BTC15M2608141945-UP")
    assert bid == Decimal("0.47")
    assert ask == Decimal("0.51")


def test_gemini_rest_prices_rejects_missing_prices():
    event = {
        "contracts": [{
            "instrumentSymbol": "GEMI-BTC15M2608141945-UP",
            "prices": {},
        }]
    }
    with pytest.raises(ValueError):
        gemini_rest_prices(event, "GEMI-BTC15M2608141945-UP")


def test_gemini_rest_prices_rejects_wrong_symbol():
    event = {
        "contracts": [{
            "instrumentSymbol": "GEMI-ETH15M2608141945-UP",
            "prices": {"bestBid": "0.47", "bestAsk": "0.51"},
        }]
    }
    with pytest.raises(ValueError):
        gemini_rest_prices(event, "GEMI-BTC15M2608141945-UP")
