from decimal import Decimal as D

from src import gemini_low_volume_reward_allocator as alloc
from src import gemini_mixed_reward_shadow as mixed


def c(ticker, asset, yes, no, pool="100", pair="1.00"):
    return alloc.Candidate(
        ticker=ticker,
        symbol=f"GEMI-{ticker}-UP",
        asset=asset,
        window_minutes=5,
        yes_quote=D(yes),
        no_quote=D(no),
        daily_pool_usd=D(pool),
        qualifying_makers=0,
        modeled_pair_profit=D(pair),
        planned_uptime=D("0.60"),
        seconds_to_expiry=D("300"),
    )


def baseline(selected, reserved, risk):
    return {
        "selected": selected,
        "reserved_usd": str(reserved),
        "portfolio_one_side_risk_usd": str(risk),
    }


def test_one_sided_fallback_uses_cheaper_leg_only():
    action = mixed.one_sided_fallback(c("XRP05M", "XRP", "0.38", "0.45"))
    assert action["side"] == "YES"
    assert D(action["price"]) == D("0.38")
    assert D(action["reserve_usd"]) == D("3.80")
    assert action["mode"] == "ONE_SIDED_FALLBACK"


def test_mixed_extension_fills_leftover_risk_without_breaking_cap():
    eth = c("ETH05M", "ETH", "0.23", "0.58", pool="200", pair="1.81")
    sol = c("SOL05M", "SOL", "0.32", "0.48", pool="100", pair="1.91")
    xrp = c("XRP05M", "XRP", "0.38", "0.45", pool="100", pair="1.60")
    base_selected = [
        {"ticker": eth.ticker, "asset": eth.asset, "reward_proxy_usd": "117", "reserve_usd": "8.10", "max_one_side_usd": "5.80"},
        {"ticker": sol.ticker, "asset": sol.asset, "reward_proxy_usd": "60", "reserve_usd": "8.00", "max_one_side_usd": "4.80"},
    ]
    out = mixed.extend_baseline_with_one_sided_fallback(
        [eth, sol, xrp],
        baseline(base_selected, "16.10", "10.60"),
        available_usd=D("57.1455"),
        safety_cash=D("12"),
        one_side_cap=D("15"),
    )
    assert out["baseline_market_coverage_count"] == 2
    assert out["market_coverage_count"] == 3
    assert len(out["one_sided_fallbacks"]) == 1
    assert out["one_sided_fallbacks"][0]["ticker"] == "XRP05M"
    assert D(out["portfolio_one_side_risk_usd"]) == D("14.40")
    assert D(out["reserved_usd"]) == D("19.90")


def test_fallback_is_rejected_if_incremental_risk_breaks_cap():
    xrp = c("XRP05M", "XRP", "0.45", "0.50")
    out = mixed.extend_baseline_with_one_sided_fallback(
        [xrp],
        baseline([], "0", "14.8"),
        available_usd=D("57.1455"),
        safety_cash=D("12"),
        one_side_cap=D("15"),
    )
    assert out["one_sided_fallbacks"] == []
    assert D(out["portfolio_one_side_risk_usd"]) == D("14.8")


def test_same_asset_fallback_does_not_stack_on_two_sided_asset():
    btc5 = c("BTC05M", "BTC", "0.30", "0.50", pool="250")
    btc15 = c("BTC15M", "BTC", "0.25", "0.55", pool="250")
    base_selected = [{"ticker": btc5.ticker, "asset": "BTC", "reward_proxy_usd": "100"}]
    out = mixed.extend_baseline_with_one_sided_fallback(
        [btc5, btc15],
        baseline(base_selected, "8", "5"),
        available_usd=D("57.1455"),
        safety_cash=D("12"),
        one_side_cap=D("15"),
    )
    assert out["one_sided_fallbacks"] == []


def test_candidate_reconstruction_requires_safe_evaluation():
    row = {
        "safe": True,
        "ticker": "XRP05M",
        "symbol": "GEMI-XRP05M-UP",
        "asset": "XRP",
        "window_minutes": 5,
        "seconds_to_expiry": "300",
        "daily_pool_usd": "100",
        "qualifying_maker_count": 0,
        "model": {"yes_quote": "0.38", "no_quote": "0.45", "modeled_pair_profit_after_fees": "1.60"},
    }
    assert mixed.candidate_from_evaluation(row) is not None
    row["safe"] = False
    assert mixed.candidate_from_evaluation(row) is None
