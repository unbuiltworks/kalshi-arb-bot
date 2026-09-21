import pytest

from src.temporal_crypto_assets import ASSETS, assets_for_venue, common_assets


def test_first_tier_is_three_venue_capable():
    assert common_assets("polymarket", "kalshi", "gemini", max_priority=1) == [
        "BTC",
        "ETH",
        "SOL",
        "XRP",
    ]


def test_kalshi_has_broader_second_tier():
    kalshi = assets_for_venue("kalshi", max_priority=2)
    assert {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"}.issubset(kalshi)


def test_gemini_aliases_match():
    assert assets_for_venue("gemini") == assets_for_venue("gemini_predictions")


def test_registry_counts_venues():
    assert ASSETS["SOL"].venue_count == 3
    assert ASSETS["DOGE"].venue_count == 1


def test_unknown_venue_rejected():
    with pytest.raises(ValueError):
        assets_for_venue("unknown")
