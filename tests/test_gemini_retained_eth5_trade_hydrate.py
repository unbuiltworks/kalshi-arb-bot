from datetime import datetime, timezone

from src.gemini_retained_eth5_trade_hydrate import hydrate_rows, normalize_public_trade, target_markets


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def plan(at: str, ticker: str = "ETH05M-A", bid: str = "0.23", ask: str = "0.76") -> dict:
    return {
        "type": "SHADOW_PLAN",
        "observed_at": at,
        "ticker": ticker,
        "symbol": f"GEMI-{ticker}-UP",
        "asset": "ETH",
        "horizon": "5m",
        "yes_bid": bid,
        "yes_ask": ask,
        "read_only": True,
        "order_entry": False,
    }


def trade(at: str, *, tid: int, side: str, price: str = "0.40", amount: str = "1") -> dict:
    return {
        "timestampms": ms(at),
        "tid": tid,
        "type": side,
        "price": price,
        "amount": amount,
        "exchange": "gemini",
    }


def test_target_markets_uses_eth5_plans_only():
    rows = [
        plan("2026-08-28T12:00:00Z"),
        {**plan("2026-08-28T12:00:01Z", ticker="BTC05M-X"), "asset": "BTC"},
        {**plan("2026-08-28T12:00:02Z", ticker="ETH15M-X"), "horizon": "15m"},
    ]
    found = target_markets(rows)
    assert list(found) == ["ETH05M-A"]
    assert found["ETH05M-A"]["plan_rows"] == 1


def test_rest_aggressor_side_maps_to_websocket_buyer_maker():
    market = {"ticker": "ETH05M-A", "symbol": "GEMI-ETH05M-A-UP"}
    sell = normalize_public_trade(market=market, trade=trade("2026-08-28T12:00:01Z", tid=1, side="sell"))
    buy = normalize_public_trade(market=market, trade=trade("2026-08-28T12:00:02Z", tid=2, side="buy"))
    assert sell is not None and sell["buyer_maker"] is True
    assert buy is not None and buy["buyer_maker"] is False
    assert sell["quantity"] == "1"
    assert sell["source"] == "GEMINI_PUBLIC_REST_TRADE_HISTORY"


def test_hydrate_filters_prequote_and_postcapture_and_merges_chronologically():
    rows = [
        {"type": "WS_CONNECTED", "observed_at": "2026-08-28T12:00:00Z"},
        plan("2026-08-28T12:00:10Z"),
        plan("2026-08-28T12:00:30Z", bid="0.24", ask="0.75"),
        {"type": "CHECKPOINT", "observed_at": "2026-08-28T12:01:00Z"},
    ]
    histories = {
        "ETH05M-A": [
            trade("2026-08-28T12:00:05Z", tid=1, side="sell"),
            trade("2026-08-28T12:00:20Z", tid=2, side="sell"),
            trade("2026-08-28T12:01:01Z", tid=3, side="buy"),
        ]
    }
    hydrated, summary = hydrate_rows(rows, histories)
    assert summary["history_rows_fetched"] == 3
    assert summary["history_rows_added"] == 1
    assert summary["history_rows_pre_quote_filtered"] == 1
    assert summary["history_rows_post_capture_filtered"] == 1
    types = [row["type"] for row in hydrated]
    assert types == ["WS_CONNECTED", "SHADOW_PLAN", "PUBLIC_TRADE", "SHADOW_PLAN", "CHECKPOINT"]
    assert hydrated[2]["trade_id"] == 2


def test_hydrate_deduplicates_existing_trade_id():
    existing = {
        "type": "PUBLIC_TRADE",
        "observed_at": "2026-08-28T12:00:20Z",
        "ticker": "ETH05M-A",
        "symbol": "GEMI-ETH05M-A-UP",
        "asset": "ETH",
        "horizon": "5m",
        "price": "0.40",
        "quantity": "1",
        "buyer_maker": True,
        "trade_id": 7,
    }
    rows = [plan("2026-08-28T12:00:10Z"), existing, {"type": "END", "observed_at": "2026-08-28T12:01:00Z"}]
    histories = {"ETH05M-A": [trade("2026-08-28T12:00:20Z", tid=7, side="sell")]}
    hydrated, summary = hydrate_rows(rows, histories)
    assert summary["history_rows_added"] == 0
    assert summary["history_rows_duplicate_filtered"] == 1
    assert sum(row.get("type") == "PUBLIC_TRADE" for row in hydrated) == 1


def test_source_book_wins_same_timestamp_tie_before_hydrated_trade():
    rows = [plan("2026-08-28T12:00:10Z"), {"type": "END", "observed_at": "2026-08-28T12:01:00Z"}]
    histories = {"ETH05M-A": [trade("2026-08-28T12:00:10Z", tid=8, side="sell")]}
    hydrated, summary = hydrate_rows(rows, histories)
    assert summary["history_rows_added"] == 1
    assert hydrated[0]["type"] == "SHADOW_PLAN"
    assert hydrated[1]["type"] == "PUBLIC_TRADE"
