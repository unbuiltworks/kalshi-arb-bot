from decimal import Decimal as D
from src.l5_settlement_backed_replay import reconcile_settled_market, aggregate_confirmed_markets, compare_settled_strategies


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
    assert row["settled_net"] == "-0.3080"


def test_sale_recovers_proceeds_but_never_assumes_fill():
    row = settled([fill("DOWN", "BUY", "0.7", "0.44"),
                   fill("DOWN", "SELL", "0.7", "0.35", fee="0.01")],
                  "UP", "0", "0", "0")
    assert D(row["settled_net"]) == D("-0.0730")


def test_completed_pair_settlement_and_fees():
    row = settled([fill("UP", "BUY", "1", "0.51", fee="0.01"),
                   fill("DOWN", "BUY", "1", "0.44", fee="0.01")],
                  "DOWN", "1", "1", "1")
    assert row["settled_net"] == "0.0300"


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
    assert aggregate_confirmed_markets([a])["settled_net"] == "-0.3080"
    assert not aggregate_confirmed_markets([a, {"eligible": False}])["eligible"]


def test_confirmed_same_population_vs_no_trade():
    loss = settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0", "0.7")
    rescued = settled([fill("DOWN", "BUY", "0.7", "0.44"),
                       fill("DOWN", "SELL", "0.7", "0.35", fee="0.01")],
                      "UP", "0", "0", "0")
    result = compare_settled_strategies(baseline={"m": loss},
                                        candidate={"m": rescued},
                                        identical_market_ids=["m"])
    assert result["eligible"]
    assert D(result["delta_settled_net"]) == D("0.2350")
    assert not result["candidate_beats_no_trade"]


def test_replay_refuses_missing_or_unsettled_market():
    loss = settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0", "0.7")
    assert compare_settled_strategies(baseline={"a": loss},
           candidate={}, identical_market_ids=["a"])["reason"] == "MARKET_POPULATION_MISMATCH"
    assert compare_settled_strategies(baseline={"a": loss},
           candidate={"a": {"eligible": False}},
           identical_market_ids=["a"])["reason"] == "UNCONFIRMED_SETTLEMENT"


# ---------------------------------------------------------------------
# THE PROPERTY THE INCONSISTENCY REVEALED (CI run 42669331096)
# ---------------------------------------------------------------------
def test_reported_money_does_not_depend_on_how_the_inputs_were_SPELLED():
    """The same economics must report the same string.

    `str(Decimal)` keeps the scale the arithmetic produced, so a fee of
    "0.01" and a fee of "0.010000" -- identical money -- reported
    "-0.073" and "-0.0730". Three of this file's assertions encoded the
    former and two the latter, which is why CI split.
    """
    terse = settled([fill("DOWN", "BUY", "0.7", "0.44"),
                     fill("DOWN", "SELL", "0.7", "0.35", fee="0.01")],
                    "UP", "0", "0", "0")
    padded = settled([fill("DOWN", "BUY", "0.70", "0.4400"),
                      fill("DOWN", "SELL", "0.700", "0.350000",
                           fee="0.010000")],
                     "UP", "0", "0", "0")
    assert terse["settled_net"] == padded["settled_net"] == "-0.0730"
    assert terse["cash_before_redemption"] == padded["cash_before_redemption"]


def test_every_reported_money_field_has_exactly_four_places():
    row = settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0", "0.7")
    for k in ("settled_net", "cash_before_redemption", "redemption"):
        assert row[k].split(".")[-1].__len__() == 4, (k, row[k])


def test_a_non_terminating_mean_is_still_a_four_place_string():
    """three markets at -0.308 average to -0.102666..., which str() would
    render at 28 significant digits."""
    rows = [settled([fill("DOWN", "BUY", "0.7", "0.44")], "UP", "0", "0",
                    "0.7") for _ in range(3)]
    agg = aggregate_confirmed_markets(rows)
    assert agg["net_per_market"] == "-0.3080"
    rows.append(settled([fill("UP", "BUY", "1", "0.51", fee="0.01"),
                         fill("DOWN", "BUY", "1", "0.44", fee="0.01")],
                        "DOWN", "1", "1", "1"))
    agg2 = aggregate_confirmed_markets(rows)
    assert len(agg2["net_per_market"].split(".")[-1]) == 4
