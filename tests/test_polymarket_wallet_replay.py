from decimal import Decimal as D

from src.polymarket_wallet_replay import (
    analyze_wallet_trades,
    dedupe_trades,
    normalize_trade,
    parse_short_crypto_slug,
    replay_market,
)


def raw(ts, outcome, price, size, condition="0x" + "a" * 64, slug="btc-updown-15m-1787860800", side="BUY"):
    return {
        "timestamp": ts,
        "conditionId": condition,
        "asset": f"asset-{outcome}",
        "side": side,
        "outcome": outcome,
        "size": size,
        "price": price,
        "title": "Bitcoin Up or Down - August 27, 4:00PM-4:15PM ET",
        "slug": slug,
        "eventSlug": slug,
        "transactionHash": f"tx-{ts}-{outcome}-{price}-{size}",
    }


def test_parse_short_crypto_slug():
    assert parse_short_crypto_slug("btc-updown-15m-1787860800") == (
        "BTC", 15, 1787860800, 1787861700
    )
    assert parse_short_crypto_slug("eth-updown-5m-1787860800") == (
        "ETH", 5, 1787860800, 1787861100
    )
    assert parse_short_crypto_slug("other-market") == (None, None, None, None)


def test_normalize_trade_marks_short_crypto_and_time_to_window():
    t = normalize_trade(raw(1787861000, "Up", "0.60", "10"))
    assert t.is_short_crypto
    assert t.crypto_asset == "BTC"
    assert t.horizon_minutes == 15
    assert t.window_end == 1787861700


def test_dedupe_exact_public_activity_rows():
    t = normalize_trade(raw(1787861000, "Up", "0.60", "10"))
    assert dedupe_trades([t, t]) == [t]


def test_replay_tracks_side_switch_and_sub_one_complete_set():
    trades = [
        normalize_trade(raw(1787861000, "Up", "0.60", "100")),
        normalize_trade(raw(1787861100, "Up", "0.64", "100")),
        normalize_trade(raw(1787861200, "Down", "0.28", "150")),
    ]
    result = replay_market(trades)
    assert result["buy_side_switches"] == 1
    assert result["bought_qty_up"] == D("200")
    assert result["bought_qty_down"] == D("150")
    assert result["avg_up"] == D("0.62")
    assert result["avg_down"] == D("0.28")
    assert result["pair_avg_sum"] == D("0.90")
    assert result["paired_qty"] == D("150")
    assert result["gross_edge_per_pair"] == D("0.10")
    assert result["gross_complete_set_edge"] == D("15.00")
    assert result["sequence"][-1]["directional_outcome"] == "Up"
    assert result["sequence"][-1]["directional_qty"] == D("50")


def test_replay_does_not_call_pairing_profit_when_weighted_cost_above_one():
    trades = [
        normalize_trade(raw(1787861000, "Up", "0.62", "100")),
        normalize_trade(raw(1787861200, "Down", "0.45", "100")),
    ]
    result = replay_market(trades)
    assert result["pair_avg_sum"] == D("1.07")
    assert result["gross_edge_per_pair"] == D("-0.07")
    assert result["gross_complete_set_edge"] == D("0")


def test_wallet_summary_tests_scaling_threshold_hypothesis():
    c1 = "0x" + "a" * 64
    c2 = "0x" + "b" * 64
    trades = [
        normalize_trade(raw(1787861000, "Up", "0.60", "100", condition=c1)),
        normalize_trade(raw(1787861100, "Down", "0.30", "100", condition=c1)),
        normalize_trade(raw(1787862000, "Down", "0.75", "50", condition=c2, slug="eth-updown-5m-1787862000")),
    ]
    result = analyze_wallet_trades(trades)
    assert result["market_count"] == 2
    assert result["markets_with_both_sides"] == 1
    assert result["markets_with_side_switches"] == 1
    assert result["markets_with_sub_1_weighted_pair_cost"] == 1
    assert result["buy_count_at_or_above_price"]["0.60"] == 2
    assert result["buy_count_at_or_above_price"]["0.70"] == 1
