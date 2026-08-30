from src.l5_market_redeem_audit import audit_market_activity, choose_markets, summarize


def market(closed=2.0, cid="c1", cell="BTC_15m", ts=10):
    return {
        "condition_id": cid,
        "cell": cell,
        "title": "Bitcoin Up or Down - test",
        "timestamp": ts,
        "closed_realized_pnl": closed,
        "closed_rows": [{"outcome": "Up"}, {"outcome": "Down"}],
    }


def trade(cid, outcome, side, size, usdc, price=0.5):
    return {
        "type": "TRADE",
        "conditionId": cid,
        "outcome": outcome,
        "side": side,
        "size": size,
        "usdcSize": usdc,
        "price": price,
    }


def redeem(cid, outcome, size, usdc=None):
    return {
        "type": "REDEEM",
        "conditionId": cid,
        "outcome": outcome,
        "size": size,
        "usdcSize": size if usdc is None else usdc,
    }


def test_redeem_derived_pnl_and_quantity_proof():
    m = market(closed=2.0)
    rows = [
        trade("c1", "Up", "BUY", 10, 4.0, 0.4),
        trade("c1", "Down", "BUY", 10, 4.0, 0.4),
        redeem("c1", "Up", 10),
    ]
    a = audit_market_activity(m, rows)
    assert a.valid
    assert a.net_trade_spend == 8.0
    assert a.redeem_proceeds == 10.0
    assert a.redeem_derived_pnl == 2.0
    assert a.delta_closed_minus_redeem == 0.0
    assert a.sign_agrees is True


def test_sells_reduce_net_spend():
    m = market(closed=3.0)
    rows = [
        trade("c1", "Up", "BUY", 12, 6.0),
        trade("c1", "Down", "BUY", 10, 4.0),
        trade("c1", "Up", "SELL", 2, 1.0),
        redeem("c1", "Up", 10),
    ]
    a = audit_market_activity(m, rows)
    assert a.valid
    assert a.reconstructed_winner_qty == 10.0
    assert a.net_trade_spend == 9.0
    assert a.redeem_derived_pnl == 1.0
    assert a.delta_closed_minus_redeem == 2.0


def test_inventory_mutator_excluded():
    m = market()
    rows = [
        trade("c1", "Up", "BUY", 10, 4.0),
        {"type": "MERGE", "conditionId": "c1", "size": 2, "usdcSize": 2},
        redeem("c1", "Up", 10),
    ]
    a = audit_market_activity(m, rows)
    assert not a.valid
    assert "inventory-mutating" in a.reason


def test_redeem_quantity_mismatch_excluded():
    m = market()
    rows = [trade("c1", "Up", "BUY", 10, 4.0), redeem("c1", "Up", 5)]
    a = audit_market_activity(m, rows)
    assert not a.valid
    assert "redeem quantity mismatch" in a.reason


def test_incomplete_fetch_excluded():
    a = audit_market_activity(market(), [], complete=False, note="offset cap")
    assert not a.valid
    assert a.reason == "offset cap"


def test_choose_markets_balances_cells_deterministically():
    markets = [
        market(cid="b1", cell="BTC_15m", ts=40),
        market(cid="b2", cell="BTC_15m", ts=30),
        market(cid="e1", cell="ETH_15m", ts=20),
        market(cid="e2", cell="ETH_15m", ts=10),
    ]
    chosen = choose_markets(markets, 3)
    assert [m["condition_id"] for m in chosen] == ["b1", "e1", "b2"]


def test_summary_compares_same_validated_set_only():
    good = audit_market_activity(
        market(closed=2.1),
        [trade("c1", "Up", "BUY", 10, 4), trade("c1", "Down", "BUY", 10, 4), redeem("c1", "Up", 10)],
    )
    bad = audit_market_activity(market(closed=-99, cid="c2"), [], complete=False, note="missing")
    s = summarize([good, bad], discovered=100, selected=2)
    assert s["validated_markets"] == 1
    assert s["closed_realized_pnl_same_validated_set"] == 2.1
    assert s["redeem_derived_pnl_same_validated_set"] == 2.0
    assert s["closed_minus_redeem_delta"] == 0.1
