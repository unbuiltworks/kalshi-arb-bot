from decimal import Decimal as D

from src import gemini_multi_market_reward_shadow as shadow


def _row(asset="BTC", window=5, tte="300", ticker=None):
    ticker = ticker or f"{asset}{'05M' if window == 5 else '15M'}TEST"
    return {
        "ticker": ticker,
        "symbol": f"GEMI-{ticker}-UP",
        "asset": asset,
        "window_minutes": window,
        "seconds_to_expiry": tte,
        "daily_pool_usd": "250" if asset == "BTC" else "100",
        "qualifying_maker_count": 0,
    }


def _sample(asset="BTC", window=5, bid="0.48", ask="0.50", tte="300",
            pair_profit="0.10", makers=0):
    row = _row(asset, window, tte)
    row.update({
        "book_ok": True,
        "bid": bid,
        "bid_qty": "500",
        "ask": ask,
        "ask_qty": "500",
        "qualifying_maker_count": makers,
        "model": {
            "yes_quote": bid,
            "no_quote": str(D("1") - D(ask)),
            "modeled_pair_profit_after_fees": pair_profit,
            "reserve_usd": str(D("10") * (D(bid) + (D("1") - D(ask)))),
            "max_one_side_usd": str(D("10") * max(D(bid), D("1") - D(ask))),
        },
    })
    return row


def test_nearest_rows_keeps_one_per_asset_window():
    rows = [
        _row("BTC", 5, "500", "BTC05MFAR"),
        _row("BTC", 5, "200", "BTC05MNEAR"),
        _row("BTC", 15, "700", "BTC15MNEAR"),
        _row("ETH", 5, "250", "ETH05MNEAR"),
        _row("SOL", 5, "40", "TOO_CLOSE"),
    ]
    chosen = shadow.nearest_rows(rows)
    assert {(r["asset"], r["window_minutes"], r["ticker"]) for r in chosen} == {
        ("BTC", 5, "BTC05MNEAR"),
        ("BTC", 15, "BTC15MNEAR"),
        ("ETH", 5, "ETH05MNEAR"),
    }


def test_safe_sample_builds_candidate():
    result = shadow.evaluate_sample(_sample(), D("10"), previous_mid=D("0.49"))
    assert result["safe"] is True
    assert result["reasons"] == []
    assert result["candidate"].modeled_pair_profit == D("0.10")


def test_toxic_midpoint_move_goes_dark():
    result = shadow.evaluate_sample(_sample(bid="0.58", ask="0.60"), D("10"), previous_mid=D("0.49"))
    assert result["safe"] is False
    assert "TOXIC_MIDPOINT_MOVE" in result["reasons"]
    assert result["candidate"] is None


def test_negative_pair_profit_is_not_selected_for_reward_subsidy():
    result = shadow.evaluate_sample(_sample(pair_profit="-0.01"), D("10"))
    assert result["safe"] is False
    assert "NEGATIVE_MODELED_PAIR_PROFIT" in result["reasons"]


def test_rebate_band_and_lr_distance_are_required():
    result = shadow.evaluate_sample(_sample(bid="0.10", ask="0.40", pair_profit="2.90"), D("10"))
    assert result["safe"] is False
    assert "OUTSIDE_MAKER_REBATE_BAND" in result["reasons"]
    assert "OUTSIDE_LR_MAX_MIDPOINT_DISTANCE" in result["reasons"]


def test_summary_tracks_shadow_uptime_without_calling_it_realized():
    stat = shadow.EventStats("BTC05MTEST", "GEMI-BTC05MTEST-UP", "BTC", 5)
    stat.observed_snapshots = 5
    stat.safe_quote_snapshots = 4
    stat.selected_quote_snapshots = 3
    summary = shadow.summarize_stats({stat.symbol: stat})
    assert summary["portfolio_shadow_uptime"] == "0.6"
    assert summary["events"][0]["shadow_uptime_meets_50pct"] is True


def test_allocator_with_actual_bankroll_respects_portfolio_one_side_cap():
    candidates = []
    for asset, pool in (("BTC", "250"), ("ETH", "200"), ("SOL", "100"), ("XRP", "100")):
        sample = _sample(asset=asset)
        sample["daily_pool_usd"] = pool
        result = shadow.evaluate_sample(sample, D("10"))
        assert result["safe"]
        candidates.append(result["candidate"])

    allocation = shadow.allocator.allocate(
        candidates,
        available_usd=D("57.1455"),
        safety_cash=D("12"),
        max_same_asset_windows=1,
        max_portfolio_one_side_usd=D("15"),
    )
    assert D(allocation["reserved_usd"]) <= D("45.1455")
    assert D(allocation["portfolio_one_side_risk_usd"]) <= D("15")
    assert len(allocation["selected"]) == 3
