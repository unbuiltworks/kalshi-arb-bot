from decimal import Decimal
import json

import pytest

from src.cross_market_shadow import PairConfig, live_book_to_quote, load_pairs


def test_pair_config_requires_explicit_fee_rate(tmp_path):
    path = tmp_path / "pairs.json"
    path.write_text(
        json.dumps(
            {
                "pairs": [
                    {
                        "event_key": "A",
                        "fixture_id": "f",
                        "kalshi_ticker": "KXTEST-1",
                        "kalshi_line": 184.5,
                        "sport": "basketball",
                        "league": "NBA",
                        "market": "Total Points",
                    }
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="kalshi_taker_fee_rate is required"):
        load_pairs(str(path))


def test_live_book_quote_preserves_exact_cent_and_fractional_depth():
    pair = PairConfig(
        event_key="A",
        fixture_id="f",
        kalshi_ticker="KXTEST-1",
        kalshi_line=Decimal("184.5"),
        kalshi_taker_fee_rate=Decimal("0.07"),
        sport="basketball",
        league="NBA",
        market="Total Points",
    )
    quote = live_book_to_quote(pair, Decimal("0.3600"), Decimal("375.98"))
    assert quote is not None
    assert quote.no_price_cents == 36
    assert quote.no_depth_hundredths == 37598


def test_live_book_quote_skips_subcent_instead_of_rounding_down():
    pair = PairConfig(
        event_key="A",
        fixture_id="f",
        kalshi_ticker="KXTEST-1",
        kalshi_line=Decimal("184.5"),
        kalshi_taker_fee_rate=Decimal("0.07"),
        sport="basketball",
        league="NBA",
        market="Total Points",
    )
    assert live_book_to_quote(pair, Decimal("0.3610"), Decimal("10.00")) is None
