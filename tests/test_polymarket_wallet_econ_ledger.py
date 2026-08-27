from src.polymarket_wallet_econ_ledger import build_ledger


def test_resolved_short_crypto_adds_incentives_separately():
    trades = [
        {"conditionId": "c", "timestamp": 100, "title": "Bitcoin Up or Down - 1:00PM-1:05PM ET", "slug": "btc-updown-5m-1", "side": "BUY", "outcome": "Up", "price": 0.40, "size": 10},
        {"conditionId": "c", "timestamp": 101, "title": "Bitcoin Up or Down - 1:00PM-1:05PM ET", "slug": "btc-updown-5m-1", "side": "BUY", "outcome": "Down", "price": 0.50, "size": 10},
    ]
    redeems = [{"conditionId": "c", "timestamp": 200, "usdcSize": 10}]
    rewards = [{"timestamp": 210, "usdcSize": 2}]
    rebates = [{"timestamp": 211, "usdcSize": 1}]
    out = build_ledger(
        trades=trades,
        redeems=redeems,
        rewards=rewards,
        rebates=rebates,
        target_start=90,
        target_end=220,
    )
    assert out["resolved_short_crypto_markets"] == 1
    assert round(out["resolved_trading_pnl_usd"], 8) == 1.0
    assert round(out["resolved_plus_incentives_usd"], 8) == 4.0
