from decimal import Decimal as D

from src.polymarket_l5_market_cohort_economics import reconcile_market_cohorts, reward_totals_by_type


def trade(ts, cid, slug, usdc, *, side="BUY"):
    return {
        "type": "TRADE",
        "timestamp": ts,
        "conditionId": cid,
        "slug": slug,
        "eventSlug": slug,
        "side": side,
        "usdcSize": str(usdc),
    }


def redeem(ts, cid, slug, usdc):
    return {
        "type": "REDEEM",
        "timestamp": ts,
        "conditionId": cid,
        "slug": slug,
        "eventSlug": slug,
        "usdcSize": str(usdc),
    }


def test_reward_totals_keep_maker_and_taker_rebates_separate():
    rows = [
        {"type": "MAKER_REBATE", "usdc_size": "12.50"},
        {"type": "TAKER_REBATE", "usdc_size": "3.25"},
        {"type": "MAKER_REBATE", "usdc_size": "0.75"},
        {"type": "OTHER", "usdc_size": "999"},
    ]
    out = reward_totals_by_type(rows)
    assert D(out["MAKER_REBATE"]) == D("13.25")
    assert D(out["TAKER_REBATE"]) == D("3.25")


def test_cross_midnight_redeem_is_joined_to_market_cohort():
    start = 1_000_000
    end = start + 86_400
    slug = f"btc-updown-5m-{start + 86_100}"
    trades = [
        trade(start + 86_110, "c1", slug, "60"),
        trade(start + 86_200, "c1", slug, "39"),
    ]
    redeems = [redeem(end + 30, "c1", slug, "100")]

    out = reconcile_market_cohorts(trades, redeems, day_start=start, day_end=end)
    assert out["market_count"] == 1
    assert out["settled_market_count"] == 1
    assert out["unresolved_market_count"] == 0
    assert D(out["settled_buy_usdc"]) == D("99")
    assert D(out["settled_redeem_usdc"]) == D("100")
    assert D(out["observed_settled_lifecycle_cashflow"]) == D("1")
    assert D(out["conservative_cashflow_if_no_redeem_is_zero_payout"]) == D("1")
    assert out["positive_market_count"] == 1


def test_no_redeem_market_is_excluded_from_observed_but_counted_in_conservative_scenario():
    start = 2_000_000
    end = start + 86_400
    slug = f"eth-updown-15m-{start + 100}"
    trades = [trade(start + 110, "c2", slug, "75")]

    out = reconcile_market_cohorts(trades, [], day_start=start, day_end=end)
    assert out["market_count"] == 1
    assert out["settled_market_count"] == 0
    assert out["unresolved_market_count"] == 1
    assert out["no_redeem_observed_market_count"] == 1
    assert D(out["observed_settled_lifecycle_cashflow"]) == D("0")
    assert D(out["no_redeem_observed_buy_usdc"]) == D("75")
    assert D(out["conservative_cashflow_if_no_redeem_is_zero_payout"]) == D("-75")
    assert D(out["by_family"]["eth_15m"]["conservative_cashflow_if_no_redeem_is_zero_payout"]) == D("-75")


def test_no_redeem_sell_cashflow_is_preserved_in_conservative_scenario():
    start = 2_500_000
    end = start + 86_400
    slug = f"btc-updown-5m-{start + 100}"
    trades = [
        trade(start + 110, "c2b", slug, "75"),
        trade(start + 120, "c2b", slug, "20", side="SELL"),
    ]
    out = reconcile_market_cohorts(trades, [], day_start=start, day_end=end)
    assert D(out["no_redeem_observed_buy_usdc"]) == D("75")
    assert D(out["no_redeem_observed_sell_usdc"]) == D("20")
    assert D(out["conservative_cashflow_if_no_redeem_is_zero_payout"]) == D("-55")


def test_market_window_start_controls_cohort_not_activity_timestamp():
    start = 3_000_000
    end = start + 86_400
    prior_slug = f"btc-updown-5m-{start - 300}"
    current_slug = f"eth-updown-5m-{start + 300}"
    trades = [
        trade(start + 10, "old", prior_slug, "50"),
        trade(start + 310, "new", current_slug, "98"),
    ]
    redeems = [
        redeem(start + 20, "old", prior_slug, "100"),
        redeem(start + 500, "new", current_slug, "100"),
    ]

    out = reconcile_market_cohorts(trades, redeems, day_start=start, day_end=end)
    assert out["market_count"] == 1
    assert out["settled_market_count"] == 1
    assert out["markets"][0]["condition_id"] == "new"
    assert D(out["observed_settled_lifecycle_cashflow"]) == D("2")


def test_family_breakdown_keeps_btc_eth_and_horizons_separate():
    start = 4_000_000
    end = start + 86_400
    btc_slug = f"btc-updown-5m-{start + 100}"
    eth_slug = f"eth-updown-15m-{start + 200}"
    trades = [
        trade(start + 110, "btc", btc_slug, "99"),
        trade(start + 210, "eth", eth_slug, "101"),
    ]
    redeems = [
        redeem(start + 500, "btc", btc_slug, "100"),
        redeem(start + 1000, "eth", eth_slug, "100"),
    ]

    out = reconcile_market_cohorts(trades, redeems, day_start=start, day_end=end)
    assert D(out["by_family"]["btc_5m"]["cashflow"]) == D("1")
    assert D(out["by_family"]["eth_15m"]["cashflow"]) == D("-1")
    assert out["positive_market_count"] == 1
    assert out["negative_market_count"] == 1
