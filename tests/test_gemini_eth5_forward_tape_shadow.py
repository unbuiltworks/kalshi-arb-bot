from datetime import datetime, timezone

from src.gemini_eth5_forward_tape_shadow import events_from_rows


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
