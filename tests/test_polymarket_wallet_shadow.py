from src.polymarket_wallet_shadow import (
    TRADERS,
    best_book_levels,
    classify_market,
    copyability_observation,
    parse_window_minutes,
    summarize,
)


def test_parse_crypto_window_minutes():
    assert parse_window_minutes("Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET") == 15
    assert parse_window_minutes("Ethereum Up or Down - August 27, 11:58PM-12:03AM ET") == 5
    assert parse_window_minutes("Will Team A win?") is None


def test_market_family_classifier():
    assert classify_market({"title": "Bitcoin Up or Down - August 27, 12:40AM-12:45AM ET"}) == "crypto_updown_5m"
    assert classify_market({"title": "FC Barcelona vs. Athletic Club: O/U 3.5"}) == "sports_total"
    assert classify_market({"title": "Spread: Arsenal FC (-1.5)"}) == "sports_spread"
    assert classify_market({"title": "Exact Score: Aston Villa FC 2 - 1 Arsenal FC?"}) == "sports_exact_score"
    assert classify_market({"title": "Will Leeds United FC win on 2026-08-30?"}) == "sports_moneyline_binary"


def test_best_book_levels_do_not_assume_api_sort_order():
    levels = best_book_levels({
        "bids": [{"price": "0.41", "size": "20"}, {"price": "0.44", "size": "9"}],
        "asks": [{"price": "0.51", "size": "8"}, {"price": "0.47", "size": "11"}],
    })
    assert levels == {
        "best_bid": 0.44,
        "best_bid_size": 9.0,
        "best_ask": 0.47,
        "best_ask_size": 11.0,
    }


def test_copyability_buy_and_sell_slippage():
    book = {
        "bids": [{"price": "0.48", "size": "10"}],
        "asks": [{"price": "0.52", "size": "12"}],
    }
    buy = copyability_observation(
        {"transactionHash": "a", "timestamp": 100, "conditionId": "c", "asset": "1", "side": "BUY", "outcome": "Up", "price": 0.50, "size": 5},
        book,
        observed_at=102,
    )
    assert round(buy["slippage_cost_per_share"], 6) == 0.02
    assert buy["still_executable_at_or_better"] is False
    assert buy["detected_age_seconds"] == 2.0

    sell = copyability_observation(
        {"transactionHash": "b", "timestamp": 100, "conditionId": "c", "asset": "1", "side": "SELL", "outcome": "Up", "price": 0.50, "size": 5},
        book,
        observed_at=102,
    )
    assert round(sell["slippage_cost_per_share"], 6) == 0.02
    assert sell["still_executable_at_or_better"] is False


def test_l5_summary_detects_two_sided_dca_behavior():
    rows = [
        {"timestamp": 100, "conditionId": "m1", "side": "BUY", "outcome": "Up", "price": 0.40, "title": "Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET"},
        {"timestamp": 101, "conditionId": "m1", "side": "BUY", "outcome": "Up", "price": 0.35, "title": "Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET"},
        {"timestamp": 102, "conditionId": "m1", "side": "BUY", "outcome": "Down", "price": 0.55, "title": "Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET"},
        {"timestamp": 103, "conditionId": "m2", "side": "SELL", "outcome": "Up", "price": 0.70, "title": "Ethereum Up or Down - August 27, 12:40AM-12:45AM ET"},
    ]
    result = summarize(TRADERS["l5"], rows)
    assert result["trade_observations"] == 4
    assert result["unique_markets"] == 2
    assert result["two_sided_buy_markets"] == 1
    assert result["short_crypto_trade_observations"] == 4
    assert result["realized_pnl"] == 0.0
    assert result["order_entry"] is False
