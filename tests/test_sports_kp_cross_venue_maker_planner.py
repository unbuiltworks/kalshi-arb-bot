from decimal import Decimal

from src.sports_kp_cross_venue_maker_planner import (
    BinaryVenueBook,
    CrossVenueGates,
    SideBook,
    plan_both_venues,
    plan_orientation,
)

D = Decimal
ZERO = lambda price, quantity: D("0")


def _side(bid: str, ask: str, bid_depth: str = "100", ask_depth: str = "100") -> SideBook:
    return SideBook(D(bid), D(bid_depth), D(ask), D(ask_depth))


def _book(venue: str, yes_bid="0.43", yes_ask="0.45", no_bid="0.53", no_ask="0.55", depth="100"):
    return BinaryVenueBook(
        venue=venue,
        market_id=f"{venue}-m",
        yes=_side(yes_bid, yes_ask, depth, depth),
        no=_side(no_bid, no_ask, depth, depth),
    )


def _gates(**overrides):
    values = dict(
        exact_counterpart=True,
        settlement_rules_certified=True,
        fee_schedule_certified=True,
        quote_fresh=True,
        quote_skew_ok=True,
        hedge_depth_exact=True,
        capital_snapshot_fresh=True,
        no_unresolved_exposure=True,
    )
    values.update(overrides)
    return CrossVenueGates(**values)


def test_kalshi_maker_polymarket_hedge_can_plan_q100_with_roomy_books():
    kalshi = _book("kalshi", yes_bid="0.40", yes_ask="0.42")
    poly = _book("polymarket", no_bid="0.49", no_ask="0.50")
    result = plan_orientation(
        maker_book=kalshi,
        maker_side="YES",
        hedge_book=poly,
        hedge_side="NO",
        maker_fee_fn=ZERO,
        hedge_fee_fn=ZERO,
        gates=_gates(),
        balances={"kalshi": D("1000"), "polymarket": D("1000")},
    )
    assert result["largest_plannable_tier"] == 100
    row = {r["quantity"]: r for r in result["tiers"]}[100]
    assert row["maker_price"] == "0.41"
    assert D(row["profit_per_contract"]) == D("0.09")
    assert row["execution_eligible"] is False
    assert result["order_entry"] is False


def test_planner_never_crosses_maker_ask_and_rejects_rich_bid():
    maker = _book("kalshi", yes_bid="0.48", yes_ask="0.49")
    hedge = _book("polymarket", no_bid="0.50", no_ask="0.51")
    result = plan_orientation(
        maker_book=maker,
        maker_side="YES",
        hedge_book=hedge,
        hedge_side="NO",
        maker_fee_fn=ZERO,
        hedge_fee_fn=ZERO,
        gates=_gates(),
    )
    assert result["largest_plannable_tier"] == 0
    assert all(r["status"] == "MAKER_MARKET_TOO_RICH" for r in result["tiers"])


def test_hedge_depth_caps_tiers_without_assuming_book_walking():
    maker = _book("kalshi", yes_bid="0.40", yes_ask="0.42", depth="100")
    hedge = _book("polymarket", no_bid="0.49", no_ask="0.50", depth="10")
    result = plan_orientation(
        maker_book=maker,
        maker_side="YES",
        hedge_book=hedge,
        hedge_side="NO",
        maker_fee_fn=ZERO,
        hedge_fee_fn=ZERO,
        gates=_gates(),
    )
    rows = {r["quantity"]: r for r in result["tiers"]}
    assert rows[10]["status"] == "PLANNABLE"
    assert rows[25]["status"] == "HEDGE_DEPTH_BLOCKED"
    assert result["largest_plannable_tier"] == 10


def test_capital_blocker_is_attributed_to_hedge_venue():
    maker = _book("kalshi", yes_bid="0.40", yes_ask="0.42")
    hedge = _book("polymarket", no_bid="0.49", no_ask="0.50")
    result = plan_orientation(
        maker_book=maker,
        maker_side="YES",
        hedge_book=hedge,
        hedge_side="NO",
        maker_fee_fn=ZERO,
        hedge_fee_fn=ZERO,
        gates=_gates(),
        balances={"kalshi": D("1000"), "polymarket": D("8")},
        free_buffers={"kalshi": D("0"), "polymarket": D("5")},
    )
    rows = {r["quantity"]: r for r in result["tiers"]}
    assert rows[5]["status"] == "PLANNABLE"
    assert rows[10]["status"] == "CAPITAL_BLOCKED"
    assert rows[10]["capital_blockers"] == ["polymarket"]


def test_unknown_rule_or_fee_gate_never_becomes_plannable():
    maker = _book("polymarket", yes_bid="0.40", yes_ask="0.42")
    hedge = _book("kalshi", no_bid="0.49", no_ask="0.50")
    result = plan_orientation(
        maker_book=maker,
        maker_side="YES",
        hedge_book=hedge,
        hedge_side="NO",
        maker_fee_fn=ZERO,
        hedge_fee_fn=ZERO,
        gates=_gates(fee_schedule_certified=False),
    )
    assert result["largest_plannable_tier"] == 0
    assert any(r["status"] == "SAFETY_GATE_BLOCKED" for r in result["tiers"])
    assert "fee_schedule_certified" in result["gate_failures"]


def test_fee_rounding_is_applied_at_each_full_quantity():
    def one_cent_per_order(price, quantity):
        return D("0.01")

    maker = _book("kalshi", yes_bid="0.40", yes_ask="0.42")
    hedge = _book("polymarket", no_bid="0.49", no_ask="0.50")
    result = plan_orientation(
        maker_book=maker,
        maker_side="YES",
        hedge_book=hedge,
        hedge_side="NO",
        maker_fee_fn=one_cent_per_order,
        hedge_fee_fn=one_cent_per_order,
        gates=_gates(),
        tiers=(1, 5),
    )
    rows = {r["quantity"]: r for r in result["tiers"]}
    assert D(rows[1]["profit_per_contract"]) == D("0.07")
    assert D(rows[5]["profit_per_contract"]) == D("0.086")


def test_plan_both_venues_emits_all_four_complementary_routes_read_only():
    results = plan_both_venues(
        kalshi_book=_book("kalshi"),
        polymarket_book=_book("polymarket"),
        kalshi_maker_fee_fn=ZERO,
        kalshi_taker_fee_fn=ZERO,
        polymarket_maker_fee_fn=ZERO,
        polymarket_taker_fee_fn=ZERO,
        gates=_gates(),
    )
    assert len(results) == 4
    assert {r["route"] for r in results} == {
        "kalshi_MAKER_TO_polymarket_HEDGE",
        "polymarket_MAKER_TO_kalshi_HEDGE",
    }
    assert all(r["order_entry"] is False for r in results)
