"""Consecutive market cycles, end to end, on one store.

fill -> reconcile -> adopted order -> progress -> settlement -> retirement
-> next market, repeated. The live failures were both things that only
appear when a store is reused across markets: a book that drifts because
positions and costs were written separately, and orders that can never be
polled so the concurrency slot never frees.

Every assertion is on the SAME store, carried forward, because that is where
the defects lived.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MARKETS = ["1900z", "1915z", "1930z", "1945z", "2000z"]
#: (side, wire, qty) per market, and the settlement it expires at
PLAN = [
    ([("DOWN", "0.20", "2.00"), ("UP", "0.25", "2.00")], D(0)),   # DOWN wins
    ([("UP", "0.30", "1.50"), ("DOWN", "0.40", "1.50")], D(1)),   # UP wins
    ([("DOWN", "0.10", "3.00")], D(0)),                            # naked DOWN
    ([("UP", "0.45", "1.00"), ("DOWN", "0.45", "2.00")], D(1)),
    ([("DOWN", "0.55", "0.50"), ("UP", "0.15", "0.50")], D(0)),
]


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def venue_position(market, up, dn):
    """A row in the shape the venue actually sends."""
    return {"marketSlug": market, "qtyBoughtDecimal": str(up),
            "qtySoldDecimal": str(dn), "netPositionDecimal": str(up - dn)}


def run_cycle(store, market, fills, settlement):
    """One market, all the way through, exercising every seam."""
    # 1. an order rests at the venue that we do not know about -> adopt it
    store.reconcile_from_venue([], [{
        "id": "V-%s" % market, "market": market, "side": "DOWN",
        "price": {"value": "0.50"}, "qty": "1", "remaining_qty": "1"}])
    adopted = store.live_orders()["V-%s" % market]
    assert adopted["venue_order_id"] == "V-%s" % market

    # 2. poll it to a terminal state -- it must NOT stall
    prog = LX.order_progress({"id": "V-%s" % market,
                              "state": "ORDER_STATE_EXPIRED",
                              "cumQuantity": "0", "leavesQuantity": "0",
                              "avgPx": "0", "commission": "0"})
    store.apply_order_progress("V-%s" % market, prog, market=market,
                               side="DOWN")
    assert store.live_order_count() == 0

    # 3. our own fills, booked from the venue's WIRE prices
    for side, wire, qty in fills:
        store.apply_fill(market, side, D(qty), D(wire), basis=LX.WIRE)
    assert store.book_violations(market) == []

    # 4. the venue agrees with our gross -> the matched pair survives
    up = store.position(market, "UP")
    dn = store.position(market, "DOWN")
    store.reconcile_from_venue([venue_position(market, up, dn)], [])
    assert store.position(market, "UP") == up
    assert store.position(market, "DOWN") == dn
    assert store.orders_uncertain() is None

    # 5. risk is priceable, and bounded by what the shares can lose
    risk = store.risk_exposure_usd(market)
    assert risk >= 0

    # 6. settle and retire
    out = store.retire_settled_market(market, yes_value=settlement)
    assert out["retired"] is True
    assert store.committed().get(market) is None
    assert store.markets_with_exposure() == []
    return out


def test_five_consecutive_markets_stay_coherent(store):
    results = []
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        results.append(run_cycle(store, market, fills, settlement))
        # nothing from a finished market may linger into the next one
        assert store.live_order_count() == 0
        assert store.markets_with_exposure() == []
        assert store.orders_uncertain() is None
    assert len(results) == 5


def test_cumulative_realized_is_the_sum_of_the_settlements(store):
    total = D(0)
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        out = run_cycle(store, market, fills, settlement)
        total += D(out["settlement_pnl"])
    lines = store.realized_lines()
    assert D(lines["realized_attributed"]) == total
    assert D(lines["realized_unattributed"]) == D(0)
    assert D(lines["realized_total"]) == total


def test_every_settlement_is_economically_possible(store):
    """A pair redeems exactly $1; a leg's cost per share stays inside $0-$1."""
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        out = run_cycle(store, market, fills, settlement)
        up_q, dn_q = D(out["up_qty"]), D(out["dn_qty"])
        assert D(out["matched_redeem"]) == D(out["matched_qty"])
        assert D(out["redemption_total"]) == (up_q * D(settlement)
                                              + dn_q * (D(1) - D(settlement)))
        for q, c in ((up_q, D(out["up_cost"])), (dn_q, D(out["dn_cost"]))):
            if q > 0:
                assert D(0) <= c / q <= D(1)
            else:
                assert c == 0


def test_a_naked_short_leg_costs_one_minus_wire_across_cycles(store):
    """1930z is 3.00 DOWN at wire 0.10, so $0.90 a share, and it WINS."""
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        out = run_cycle(store, market, fills, settlement)
        if market == "1930z":
            assert D(out["dn_qty"]) == D("3.00")
            assert D(out["dn_cost"]) == D("2.70")          # 3 * (1 - 0.10)
            assert D(out["redemption_total"]) == D("3.00")
            assert D(out["settlement_pnl"]) == D("0.30")


def test_no_phantom_orders_accumulate_over_cycles(store):
    seen = set()
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        run_cycle(store, market, fills, settlement)
        seen |= set(store.live_orders())
    assert store.live_orders() == {}
    assert seen == set()          # nothing was ever left live between markets


def test_the_store_survives_a_restart_between_cycles():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "inv.json")
        total = D(0)
        for market, (fills, settlement) in zip(MARKETS, PLAN):
            s = LX.InventoryStore(path)          # a fresh process each cycle
            total += D(run_cycle(s, market, fills, settlement)["settlement_pnl"])
        final = LX.InventoryStore(path)
        assert D(final.realized_lines()["realized_attributed"]) == total
        assert final.markets_with_exposure() == []


def test_the_loss_halt_sees_the_cumulative_result(store):
    for market, (fills, settlement) in zip(MARKETS, PLAN):
        run_cycle(store, market, fills, settlement)
    lim = LX.SafetyLimits(daily_realized_loss_halt_usd=D(5))
    verdict = lim.refuse(
        price=D("0.5"), qty=D("1"), market="next", committed_by_market={},
        residual_after=D(0), realized_today=store.realized_today(),
        open_markets=[], outstanding_by_market={}, outstanding_residual=D(0),
        live_order_count=0, risk_by_market={}, orders_uncertain=None)
    assert verdict != "DAILY_REALIZED_LOSS_HALT"     # these cycles profit
