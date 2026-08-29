import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
from io import StringIO
import json

from src.gemini_eth5_forward_tape_shadow import (
    events_from_rows,
    seed_is_needed,
    seed_state_if_needed,
)
from src.gemini_l5_tape_dynamic_shadow import MarketState


def iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def event(ticker: str, expiry_ms: int, *, symbol: str | None = None, state: str = "open") -> dict:
    return {
        "ticker": ticker,
        "expiryDate": iso_from_ms(expiry_ms),
        "contracts": [
            {
                "instrumentSymbol": symbol or f"GEMI-{ticker}-UP",
                "marketState": state,
                "expiryDate": iso_from_ms(expiry_ms),
                "priceIncrement": "0.01",
            }
        ],
    }


def stats() -> dict[str, int]:
    return {
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "errors": 0,
        "discoveries": 0,
        "subscriptions": 0,
        "rest_seed_attempts": 0,
        "rest_seed_pending": 0,
        "rest_seed_errors": 0,
        "rest_seeds": 0,
        "control_messages": 0,
        "subscription_errors": 0,
    }


def market_state() -> MarketState:
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    ticker = "ETH05M2608290010"
    found = events_from_rows([event(ticker, now + 300_000)], now_ms=now, max_seconds_to_expiry=1800)
    return MarketState(next(iter(found.values())))


def test_forward_discovery_keeps_multiple_eth5_markets_in_window():
    now = 1_800_000_000_000
    rows = [
        event("ETH05M2608290010", now + 5 * 60_000),
        event("ETH05M2608290015", now + 10 * 60_000),
        event("ETH05M2608290020", now + 15 * 60_000),
    ]
    found = events_from_rows(rows, now_ms=now, max_seconds_to_expiry=1800)
    assert len(found) == 3
    assert {item.ticker for item in found.values()} == {row["ticker"] for row in rows}


def test_forward_discovery_rejects_wrong_family_expired_and_too_far():
    now = 1_800_000_000_000
    rows = [
        event("BTC05M2608290010", now + 5 * 60_000),
        event("ETH15M2608290015", now + 10 * 60_000),
        event("ETH05M2608290000", now - 1),
        event("ETH05M2608290100", now + 31 * 60_000),
        event("ETH05M2608290010", now + 5 * 60_000, state="closed"),
        event("ETH05M2608290015", now + 10 * 60_000, symbol="GEMI-ETH05M2608290015-DOWN"),
    ]
    assert events_from_rows(rows, now_ms=now, max_seconds_to_expiry=1800) == {}


def test_forward_discovery_preserves_event_identity():
    now = 1_800_000_000_000
    ticker = "ETH05M2608290010"
    found = events_from_rows([event(ticker, now + 300_000)], now_ms=now, max_seconds_to_expiry=1800)
    item = next(iter(found.values()))
    assert item.asset == "ETH"
    assert item.horizon == "5m"
    assert item.ticker == ticker
    assert item.symbol == f"GEMI-{ticker}-UP"
    assert str(item.price_increment) == "0.01"


def test_incomplete_future_book_stays_seed_pending_and_retries_until_success():
    state = market_state()
    out = StringIO()
    counters = stats()
    calls = 0

    async def fake_book(_http, _symbol):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f"incomplete Gemini book for {state.event.symbol}")
        return D("0.36"), D("10"), D("0.70"), D("12")

    assert seed_is_needed(state) is True
    first = asyncio.run(seed_state_if_needed(
        http=object(), state=state, fh=out, stats=counters, attempt=1, book_fn=fake_book
    ))
    assert first is False
    assert state.book_updates == 0
    assert counters["rest_seed_attempts"] == 1
    assert counters["rest_seed_pending"] == 1
    assert counters["rest_seed_errors"] == 0
    assert counters["errors"] == 0
    assert seed_is_needed(state) is True

    second = asyncio.run(seed_state_if_needed(
        http=object(), state=state, fh=out, stats=counters, attempt=2, book_fn=fake_book
    ))
    assert second is True
    assert state.book_updates == 1
    assert counters["rest_seed_attempts"] == 2
    assert counters["rest_seeds"] == 1
    assert seed_is_needed(state) is False

    third = asyncio.run(seed_state_if_needed(
        http=object(), state=state, fh=out, stats=counters, attempt=3, book_fn=fake_book
    ))
    assert third is False
    assert calls == 2
    assert counters["rest_seed_attempts"] == 2

    types = [json.loads(line)["type"] for line in out.getvalue().splitlines()]
    assert types[0] == "REST_SEED_PENDING"
    assert "SHADOW_PLAN" in types
    assert types[-1] == "REST_SEED_SUCCESS"


def test_unexpected_seed_failure_is_counted_as_error():
    state = market_state()
    out = StringIO()
    counters = stats()

    async def failing_book(_http, _symbol):
        raise RuntimeError("unexpected public book failure")

    seeded = asyncio.run(seed_state_if_needed(
        http=object(), state=state, fh=out, stats=counters, attempt=1, book_fn=failing_book
    ))
    assert seeded is False
    assert counters["rest_seed_errors"] == 1
    assert counters["errors"] == 1
    row = json.loads(out.getvalue().splitlines()[0])
    assert row["type"] == "REST_SEED_ERROR"
