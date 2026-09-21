from src.polymarket_wallet_forensics import (
    WalletTrade,
    classify_sports_market,
    classify_trade,
    infer_window_minutes,
    reconstruct_short_crypto,
    resolve_wallet,
    summarize_sports_forensics,
)


def trade(
    *,
    side="BUY",
    outcome="Up",
    price=0.25,
    size=10,
    ts=1,
    market="0x" + "1" * 64,
    title="Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET",
):
    return WalletTrade(
        proxy_wallet="0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68",
        side=side,
        condition_id=market,
        size=size,
        price=price,
        timestamp_s=ts,
        title=title,
        slug="btc",
        event_slug="btc",
        outcome=outcome,
        outcome_index=0,
        transaction_hash=f"0x{ts:064x}",
    )


def test_aliases_resolve_to_expected_wallets():
    assert resolve_wallet("@l5zn1bwom8etsk")[0] == (
        "0xb945945d5bcaf7b56834d4da8cdf8f8f94b2db68"
    )
    assert resolve_wallet("swisstony")[0] == (
        "0x204f72f35326db932158cba6adff0b9a1da95e14"
    )


def test_infer_5m_and_15m_windows():
    assert (
        infer_window_minutes(
            "Bitcoin Up or Down - August 27, 12:30AM-12:45AM ET"
        )
        == 15
    )
    assert (
        infer_window_minutes(
            "Ethereum Up or Down - August 27, 11:55PM-12:00AM ET"
        )
        == 5
    )


def test_classify_short_crypto():
    assert classify_trade(trade()) == {
        "family": "SHORT_CRYPTO_UP_DOWN",
        "asset": "BTC",
        "horizon_minutes": 15,
    }


def test_reconstructs_locked_pair():
    rows = [
        trade(outcome="Up", price=0.25, size=10, ts=100),
        trade(outcome="Down", price=0.60, size=10, ts=145),
    ]
    result = reconstruct_short_crypto(rows)
    summary = result["summary"]

    assert summary["short_crypto_markets"] == 1
    assert summary["locked_positive_markets"] == 1
    assert round(summary["locked_profit_usd_before_unobserved_fees"], 6) == 1.5
    assert result["markets"][0]["first_pair_gap_s"] == 45


def test_sell_market_excluded_from_locked_attribution():
    rows = [
        trade(outcome="Up", price=0.20, size=10, ts=100),
        trade(outcome="Down", price=0.60, size=10, ts=110),
        trade(side="SELL", outcome="Up", price=0.50, size=1, ts=120),
    ]
    result = reconstruct_short_crypto(rows)
    summary = result["summary"]

    assert summary["markets_with_sells"] == 1
    assert summary["attribution_eligible_markets"] == 0
    assert summary["locked_positive_markets"] == 0
    assert summary["locked_profit_usd_before_unobserved_fees"] == 0.0


def sports_trade(
    *,
    title="Atlanta Braves vs. Milwaukee Brewers: O/U 7.5",
    outcome="Under",
    price=0.39,
    size=100,
    event_slug="braves-brewers",
    market="0x" + "2" * 64,
    side="BUY",
    ts=10,
):
    return WalletTrade(
        proxy_wallet="0x204f72f35326db932158cba6adff0b9a1da95e14",
        side=side,
        condition_id=market,
        size=size,
        price=price,
        timestamp_s=ts,
        title=title,
        slug="sports",
        event_slug=event_slug,
        outcome=outcome,
        outcome_index=0,
        transaction_hash=f"0x{ts:064x}",
    )


def test_classifies_common_sports_market_types():
    assert classify_sports_market(sports_trade()) == "TOTAL"
    assert classify_sports_market(
        sports_trade(
            title="Will Malaga CF win on 2026-08-24?",
            outcome="No",
        )
    ) == "MONEYLINE"
    assert classify_sports_market(
        sports_trade(
            title="Club A vs. Club B: Both Teams to Score",
            outcome="No",
        )
    ) == "BTTS"


def test_sports_summary_flags_multi_market_events_without_calling_it_arb():
    rows = [
        sports_trade(market="0x" + "2" * 64, ts=10),
        sports_trade(
            title="Will Atlanta Braves win on 2026-08-27?",
            outcome="No",
            event_slug="braves-brewers",
            market="0x" + "3" * 64,
            ts=11,
        ),
    ]
    result = summarize_sports_forensics(rows)
    assert result["sports_trade_count"] == 2
    assert result["multi_market_event_count"] == 1
    assert result["trades_in_multi_market_events"] == 2
    assert result["warning"].startswith("DESCRIPTIVE_ONLY")
