import pytest

from src.kalshi_live_book import LiveKalshiBooks


class DummyKey:
    pass


def _books(monkeypatch):
    monkeypatch.setattr("src.kalshi_live_book.load_private_key", lambda _=None: DummyKey())
    return LiveKalshiBooks(["A", "B"], api_key_id="test")


@pytest.mark.asyncio
async def test_snapshot_and_current_single_level_delta(monkeypatch):
    books = _books(monkeypatch)
    books._validate_sequence({"seq": 2})
    await books.apply_snapshot(
        {
            "seq": 2,
            "msg": {
                "market_ticker": "A",
                "yes_dollars_fp": [["0.6100", "5.00"], ["0.6300", "3.50"]],
            },
        }
    )

    quote = await books.best_no_ask("A")
    assert quote is not None
    assert str(quote.price_dollars) == "0.3700"
    assert str(quote.count) == "3.50"

    books._validate_sequence({"seq": 3})
    await books.apply_delta(
        {
            "seq": 3,
            "msg": {
                "market_ticker": "A",
                "side": "yes",
                "price_dollars": "0.6300",
                "delta_fp": "-3.50",
                "ts_ms": 123,
            },
        }
    )
    quote = await books.best_no_ask("A")
    assert quote is not None
    assert str(quote.price_dollars) == "0.3900"
    assert str(quote.count) == "5.00"


@pytest.mark.asyncio
async def test_sequence_is_subscription_wide_not_per_ticker(monkeypatch):
    books = _books(monkeypatch)

    books._validate_sequence({"seq": 10})
    await books.apply_snapshot(
        {"seq": 10, "msg": {"market_ticker": "A", "yes_dollars_fp": [["0.50", "1.00"]]}}
    )
    books._validate_sequence({"seq": 11})
    await books.apply_snapshot(
        {"seq": 11, "msg": {"market_ticker": "B", "yes_dollars_fp": [["0.60", "1.00"]]}}
    )
    books._validate_sequence({"seq": 12})
    await books.apply_delta(
        {
            "seq": 12,
            "msg": {
                "market_ticker": "A",
                "side": "yes",
                "price_dollars": "0.51",
                "delta_fp": "2.00",
            },
        }
    )

    quote = await books.best_no_ask("A")
    assert quote is not None
    assert str(quote.price_dollars) == "0.49"


def test_sequence_gap_fails_closed(monkeypatch):
    books = _books(monkeypatch)
    books._validate_sequence({"seq": 5})
    with pytest.raises(RuntimeError, match="sequence gap"):
        books._validate_sequence({"seq": 7})
