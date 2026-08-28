from decimal import Decimal

import httpx

from src.l5_polymarket_replay import (
    Trade,
    fetch_wallet_trades,
    group_replays,
    hypothesis_summary,
    normalize_trade,
    parse_window_slug,
)


def test_parse_window_slug_uses_start_epoch_and_horizon():
    assert parse_window_slug("btc-updown-5m-1787850600") == (
        "btc",
        "5m",
        1787850600,
        1787850900,
    )
    assert parse_window_slug("eth-updown-15m-1787874300") == (
        "eth",
        "15m",
        1787874300,
        1787875200,
    )


def test_normalize_trade_filters_non_short_crypto():
    good = normalize_trade(
        {
            "timestamp": 1787874500,
            "conditionId": "0xabc",
            "asset": "123",
            "side": "BUY",
            "outcome": "Up",
            "price": 0.62,
            "size": 10,
            "title": "Bitcoin Up or Down",
            "slug": "btc-updown-15m-1787874300",
            "eventSlug": "btc-updown-15m-1787874300",
            "transactionHash": "0xtx",
        }
    )
    assert good is not None
    assert good.coin == "btc"
    assert good.horizon == "15m"
    assert good.expiry_ts == 1787875200

    assert normalize_trade({"slug": "some-election", "side": "BUY", "outcome": "Yes", "price": 0.5, "size": 1}) is None


def test_fetch_wallet_trades_explicitly_includes_maker_fills():
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["user"] == "0xwallet"
        assert params["takerOnly"] == "false"
        assert params["start"] == "123"
        assert params["end"] == "456"
        return httpx.Response(200, json=[])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        trades = fetch_wallet_trades("0xwallet", start=123, end=456, client=client)
    assert trades == []


def _trade(ts: int, outcome: str, price: str, size: str) -> Trade:
    return Trade(
        timestamp=ts,
        condition_id="0xcond",
        asset_id="asset",
        side="BUY",
        outcome=outcome,
        price=Decimal(price),
        size=Decimal(size),
        title="Bitcoin Up or Down",
        slug="btc-updown-15m-1787874300",
        event_slug="btc-updown-15m-1787874300",
        tx_hash=f"0x{ts}{outcome}",
        coin="btc",
        horizon="15m",
        window_start_ts=1787874300,
        expiry_ts=1787875200,
    )


def test_replay_detects_scaling_switch_and_sub_dollar_matched_inventory():
    trades = [
        _trade(1787874400, "up", "0.60", "10"),
        _trade(1787874460, "up", "0.64", "10"),
        _trade(1787874700, "down", "0.30", "10"),
        _trade(1787874760, "down", "0.28", "10"),
    ]
    replay = group_replays(trades)[0]

    assert replay["buy_count"] == 4
    assert replay["side_switches_between_buys"] == 1
    assert replay["sub_dollar_matched_steps"] >= 1
    assert replay["final"]["up_qty"] == "20"
    assert replay["final"]["down_qty"] == "20"
    assert replay["final"]["up_vwap"] == "0.62"
    assert replay["final"]["down_vwap"] == "0.29"
    assert replay["final"]["matched_pair_cost"] == "0.91"
    assert replay["final"]["locked_gross_per_pair"] == "0.09"
    assert replay["final"]["seconds_to_expiry"] == 440

    summary = hypothesis_summary([replay])
    assert summary["markets_with_3plus_buys"] == 1
    assert summary["markets_with_buy_side_switch"] == 1
    assert summary["markets_with_sub_dollar_matched_inventory"] == 1
    assert summary["markets_with_buy_at_or_above_60c"] == 1


def test_replay_keeps_directional_residual_visible():
    trades = [
        _trade(1787874400, "up", "0.65", "30"),
        _trade(1787874700, "down", "0.25", "10"),
    ]
    replay = group_replays(trades)[0]
    assert replay["final"]["matched_qty"] == "10"
    assert replay["final"]["residual_side"] == "up"
    assert replay["final"]["residual_qty"] == "20"
    assert replay["final"]["matched_pair_cost"] == "0.9"
