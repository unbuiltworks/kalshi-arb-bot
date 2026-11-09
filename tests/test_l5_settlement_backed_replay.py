from src.l5_settlement_backed_replay import reconcile_settled_market, aggregate_confirmed_markets


def fill(side, action, q, price, fee="0", rebate="0"):
    return dict(side=side, action=action, quantity=q, price=price,
                fee=fee, rebate=rebate)


def settled(fills, side="UP", redemption="0", up="0", down="0", **kw):
    return reconcile_settled_market(fills=fills, settlement_side=side,
        redemption=redemption, expected_up_qty=up, expected_down_qty=down,
        cost_basis_attested=True, **kw)


def test_actual_stranded_loss_not_flat_proxy():
    row = settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0", "0.7")
    assert row["eligible"] and row["stranded"]
    assert row["settled_net"] == "-0.308"


def test_sale_recovers_proceeds_but_never_assumes_fill():
    row = settled([fill("DOWN", "BUY", "0.7", "0.44"),
                   fill("DOWN", "SELL", "0.7", "0.35", fee="0.01")],
                  "UP", "0", "0", "0")
    assert row["settled_net"] == "-0.0730"


def test_completed_pair_settlement_and_fees():
    row = settled([fill("UP", "BUY", "1", "0.51", fee="0.01"),
                   fill("DOWN", "BUY", "1", "0.44", fee="0.01")],
                  "DOWN", "1", "1", "1")
    assert row["settled_net"] == "0.03"


def test_refuse_unattested_or_unresolved():
    a = dict(fills=[], settlement_side="UP", redemption="0",
             expected_up_qty="0", expected_down_qty="0")
    assert not reconcile_settled_market(**a)["eligible"]
    assert not reconcile_settled_market(**a, cost_basis_attested=True, unresolved_orders=1)["eligible"]


def test_refuse_inventory_or_redemption_mismatch():
    f = [fill("UP", "BUY", "1", "0.2")]
    assert settled(f, "UP", "1", "0", "0")["reason"] == "INVENTORY_MISMATCH"
    assert settled(f, "UP", "0", "1", "0")["reason"] == "REDEMPTION_MISMATCH"


def test_refuse_uncovered_sale_and_missing_fee():
    assert not settled([fill("UP", "SELL", "1", "0.2")])["eligible"]
    f = fill("UP", "BUY", "1", "0.2")
    del f["fee"]
    assert not settled([f])["eligible"]


def test_aggregate_requires_all_settled():
    a = settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0", "0.7")
    assert aggregate_confirmed_markets([a])["settled_net"] == "-0.308"
    assert not aggregate_confirmed_markets([a, {"eligible": False}])["eligible"]
