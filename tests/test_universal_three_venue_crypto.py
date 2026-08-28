from datetime import datetime, timezone

from src.gemini_kalshi_aligned_watch import Pair
from src.universal_three_venue_crypto import (
    PolyCandidate,
    join_three_venues,
    poly_candidates,
)

UTC = timezone.utc


def test_poly_candidates_requires_crypto_15m_updown() -> None:
    rows = [
        {
            "category": "crypto",
            "slug": "btc-15-minute-up-or-down",
            "question": "Bitcoin 15 minutes Up or Down?",
            "description": "BTC 15 minute market",
            "endDate": "2026-08-16T02:15:00Z",
        },
        {
            "category": "sports",
            "slug": "btc-15-minute-up-or-down-sports",
            "question": "Bitcoin 15 minutes Up or Down?",
            "endDate": "2026-08-16T02:15:00Z",
        },
    ]
    got = poly_candidates(rows)
    assert len(got) == 1
    assert got[0].asset == "BTC"


def test_three_venue_join_requires_same_asset_and_window() -> None:
    end = datetime(2026, 8, 16, 2, 15, tzinfo=UTC)
    pairs = [Pair("BTC", end, "GEMI-BTC", "KXBTC")]
    poly = [
        PolyCandidate("BTC", "poly-btc", end, "BTC", ""),
        PolyCandidate("ETH", "poly-eth", end, "ETH", ""),
        PolyCandidate("BTC", "poly-stale", datetime(2026, 8, 16, 2, 20, tzinfo=UTC), "BTC", ""),
    ]
    got = join_three_venues(pairs, poly, max_poly_window_skew_s=120)
    assert len(got) == 1
    assert got[0].poly_slug == "poly-btc"
    assert got[0].poly_skew_s == 0


def test_cluster_is_always_read_only_and_requires_rule_audit() -> None:
    end = datetime(2026, 8, 16, 2, 15, tzinfo=UTC)
    got = join_three_venues(
        [Pair("BTC", end, "GEMI-BTC", "KXBTC")],
        [PolyCandidate("BTC", "poly-btc", end, "BTC", "")],
        max_poly_window_skew_s=1,
    )[0].as_dict()
    assert got["polymarket_join_status"] == "NEEDS_RULE_AUDIT"
    assert got["read_only"] is True
    assert got["order_entry"] is False
