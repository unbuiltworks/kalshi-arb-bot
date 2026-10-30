"""0230z: the same venue order booked twice, and how that became -$1.91.

THE FACTS, from the venue's own records (Railway service `l5-pmus-live`,
deployment 8db131c5, 2026-10-30):

  02:29:43  L5_LIVE_CASH          available_cash_usd 343.21615
  02:30:23  L5_LIVE_RECONCILE     open_orders_at_venue 1
                                  live_orders_adopted ["DDDXP54G12TT"]
                                  live_orders_pending_terminal
                                      ["l5f-b420c9403ecdba8ddb35c489"]
  02:30:27.385 L5_LIVE_LIFECYCLE  ORDER_PROGRESS
                                  coid DDDXP54G12TT, already 0, delta 1.91
                                  venue_order_id DDDXP54G12TT
  02:30:27.763 L5_LIVE_LIFECYCLE  PENDING_TERMINAL_RESOLVED
                                  coid l5f-b420c9403ecdba8ddb35c489
                                  already 0, delta 1.91
                                  venue_order_id DDDXP54G12TT   <-- THE SAME
  02:30:38  L5_LIVE_RECONCILE     drift 0230z|DOWN ours 3.82 venue 1.9100
                                  gross_quarantine leg_cost 1.451600
                                                   fill_count 2
  02:30:41  L5_LIVE_CASH          available_cash_usd 342.49035
  02:46:23  L5_LIVE_SETTLED       yes_value 1.0000, dn_qty 1.9100,
                                  dn_cost 1.9100, redemption_total 0,
                                  settlement_pnl -1.91000000

ONE venue order, `DDDXP54G12TT`, booked TWICE -- once as an adopted order
under the venue's own id, once as our own order under the client id we sent
it with. Both records carry that same `venue_order_id`; both read
`already: 0`, because `applied_fill_qty` is keyed on the CLIENT order id.
`apply_order_progress` is idempotent per coid, and the comment at
l5_live_execution.py:4152 relies on exactly that -- "Double-booking is not
the risk here" -- which holds only while one venue order has one coid.

The $1.1842 overstatement then arrives in a SECOND step, and it is not
itself a defect: our 3.82 contradicted the venue's 1.91, so
`adopt_venue_position` did what it is built to do -- quarantined the gross
book, took the venue's net as the position of record, and priced the basis
it could no longer decompose at UNKNOWN_PRICE_WORST_CASE, $1 a share. The
double-book DESTROYED a cost basis that was individually correct (each
booking used own 0.38); the conservative bound is what the destroyed basis
was replaced with.

The venue charged us once: 343.21615 - 0.7258 = 342.49035, and the balance
never moved again, so the DOWN shares redeemed nothing. Venue-confirmed
result -$0.7258; engine-booked -$1.9100.

Nothing here changes production code. These tests PIN the defect so the fix
has something to flip.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MARKET = "cpc-btc-updown-15m-2026-10-30-0230z"
OUR_COID = "l5f-b420c9403ecdba8ddb35c489"
VENUE_ID = "DDDXP54G12TT"

QTY = D("1.91")
WIRE_PX = D("0.6200")          # the venue's avgPx, YES-convention
OWN_PX = D("0.3800")           # what a DOWN share cost us: 1 - 0.62
ONE_FILL_COST = D("0.7258")    # 1.91 x 0.38
DOUBLE_COST = D("1.451600")    # what the quarantine recorded
UNKNOWN_BOUND = D("1.9100")    # 1.91 x $1.00, after adoption

CASH_BEFORE = D("343.21615")
CASH_AFTER = D("342.49035")

#: the venue's open-orders row for OUR order. It carries the venue's own id
#: and NO client_order_id -- which is the whole defect, because
#: reconcile_from_venue then keys it by `order_id` and calls it adopted.
VENUE_OPEN_ROW = {
    "id": VENUE_ID,
    "marketSlug": MARKET,
    "outcomeSide": "OUTCOME_SIDE_NO",
    "price": "0.6200",
    "qty": "1.91",
    "remainingQuantity": "1.91",
    "createTime": "2026-10-30T02:29:50.000000Z",
}

RESOLUTION = {"source": "VENUE_SETTLEMENT_PX", "market": MARKET,
              "yes_value": "1.0000"}


def _progress(cum: D) -> dict:
    return {"cum_qty": str(cum), "avg_px": str(WIRE_PX),
            "commission": "0.0000", "leaves_qty": "0",
            "terminal": True, "state": "ORDER_STATE_FILLED"}


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["live_orders"] = {OUR_COID: {
            "t": 1793327390.0,
            "venue_order_id": VENUE_ID,
            "client_order_id": OUR_COID,
            "session_id": "campaign10",
            "market": MARKET,
            "side": "DOWN",
            "price": str(OWN_PX),       # record_submit stores OWN terms
            "qty": "1.91",
            "remaining": "1.91",
            "state": "LIVE_AT_VENUE",
        }}
        s.state["committed"] = {MARKET: "0"}
        yield s


class TestWhatTheVenueActuallyDid:
    """The economic result, independent of anything our ledger says."""

    def test_the_account_was_charged_exactly_once(self):
        assert CASH_BEFORE - ONE_FILL_COST == CASH_AFTER

    def test_one_fill_costs_qty_times_the_own_price(self):
        assert (QTY * OWN_PX).quantize(D("0.0001")) == ONE_FILL_COST

    def test_the_own_price_is_the_wire_price_complemented(self):
        assert OWN_PX == D(1) - WIRE_PX

    def test_up_won_so_the_down_shares_redeemed_nothing(self):
        """yes_value 1.0000: a DOWN share pays 1 - 1 = 0."""
        redeem = QTY * (D(1) - D("1.0000"))
        assert redeem == 0

    def test_the_venue_confirmed_result_is_the_purchase_cost(self):
        assert D(0) - ONE_FILL_COST == -ONE_FILL_COST == D("-0.7258")

    def test_the_balance_never_moved_again_so_nothing_was_redeemed(self):
        """Settlement at 02:46:23; cash read 342.49035 at 02:46:57 and
        02:49:00. A redemption would have shown up here."""
        assert CASH_AFTER == D("342.49035")


class TestTheVenueRowCannotBeMatchedToOurOrder:
    """Step one: why one order became two records."""

    def test_the_row_carries_no_client_order_id(self):
        for k in ("client_order_id", "clientOrderId"):
            assert k not in VENUE_OPEN_ROW

    def test_so_reconcile_keys_it_by_the_venue_id(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert VENUE_ID in store.state["live_orders"]
        assert OUR_COID not in store.state["live_orders"]

    def test_and_calls_it_adopted_although_it_is_ours(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert store.state["live_orders"][VENUE_ID]["adopted"] is True

    def test_the_adopted_record_names_the_order_we_already_track(self, store):
        """The evidence that would have prevented this is IN the record."""
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        adopted = store.state["live_orders"][VENUE_ID]
        assert adopted["venue_order_id"] == VENUE_ID == VENUE_OPEN_ROW["id"]

    def test_our_own_order_is_parked_as_vanished(self, store):
        """It did not vanish. It is the row directly above."""
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        pend = store.state["pending_terminal"]
        assert OUR_COID in pend
        assert pend[OUR_COID]["venue_order_id"] == VENUE_ID
        assert pend[OUR_COID]["reason"] == "ABSENT_FROM_VENUE_OPEN_ORDERS"

    def test_both_records_point_at_one_venue_order(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        ids = {store.state["live_orders"][VENUE_ID]["venue_order_id"],
               store.state["pending_terminal"][OUR_COID]["venue_order_id"]}
        assert ids == {VENUE_ID}


class TestTheIdempotencyKeyIsTheClientIdNotTheVenueOrder:

    def test_each_coid_starts_from_zero_applied(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert store.applied_fill_qty(VENUE_ID) == 0
        assert store.applied_fill_qty(OUR_COID) == 0

    def test_booking_one_coid_does_not_mark_the_other_booked(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        store.apply_order_progress(VENUE_ID, _progress(QTY),
                                   market=MARKET, side="DOWN")
        assert store.applied_fill_qty(VENUE_ID) == QTY
        assert store.applied_fill_qty(OUR_COID) == 0      # <-- books again


class TestTheDoubleBookReproduces:
    """Step two: 1.91 + 1.91 = 3.82, at the correct price, twice."""

    @pytest.fixture()
    def booked(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        a = store.apply_order_progress(VENUE_ID, _progress(QTY),
                                       market=MARKET, side="DOWN")
        b = store.resolve_pending_terminal(OUR_COID, _progress(QTY))
        return store, a, b

    def test_both_bookings_report_a_full_delta(self, booked):
        _s, a, b = booked
        assert D(a["delta"]) == QTY
        assert D(b["delta"]) == QTY
        assert D(a["already"]) == D(b["already"]) == 0

    def test_the_position_is_double_the_venues(self, booked):
        s, _a, _b = booked
        assert s.position(MARKET, "DOWN") == D("3.82")

    def test_the_cost_basis_is_double_and_otherwise_correct(self, booked):
        s, _a, _b = booked
        assert s.leg_cost(MARKET)["DOWN"] == DOUBLE_COST
        assert DOUBLE_COST == 2 * ONE_FILL_COST

    def test_each_individual_booking_used_the_right_own_price(self, booked):
        s, _a, _b = booked
        per_share = s.leg_cost(MARKET)["DOWN"] / s.position(MARKET, "DOWN")
        assert per_share.quantize(D("0.01")) == OWN_PX

    def test_the_fill_count_records_two(self, booked):
        s, _a, _b = booked
        assert s.fill_count(MARKET, "DOWN") == 2


class TestAdoptionReplacesTheRuinedBasisWithTheWorstCase:
    """Step three, and this part is the safety machinery working."""

    @pytest.fixture()
    def adopted(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        store.apply_order_progress(VENUE_ID, _progress(QTY),
                                   market=MARKET, side="DOWN")
        store.resolve_pending_terminal(OUR_COID, _progress(QTY))
        # the venue now reports its net: 1.91 sold, i.e. -1.91
        store.reconcile_from_venue(
            [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
              "qtySoldDecimal": "1.9100", "netPositionDecimal": "-1.9100"}],
            [])
        return store

    def test_the_venue_net_becomes_the_position_of_record(self, adopted):
        assert adopted.position(MARKET, "DOWN") == QTY

    def test_the_gross_book_is_quarantined_not_deleted(self, adopted):
        q = adopted.state["gross_quarantine"][MARKET]
        assert q["positions"]["%s|DOWN" % MARKET] == "3.82"
        assert D(q["leg_cost"]["%s|DOWN" % MARKET]) == DOUBLE_COST
        assert q["fill_count"]["%s|DOWN" % MARKET] == 2

    def test_the_basis_is_rebooked_at_one_dollar_a_share(self, adopted):
        assert adopted.leg_cost(MARKET)["DOWN"] == UNKNOWN_BOUND
        assert UNKNOWN_BOUND == QTY * LX.UNKNOWN_PRICE_WORST_CASE

    def test_the_placeholder_basis_is_declared_not_hidden(self, adopted):
        assert "%s|DOWN" % MARKET in adopted.state["leg_cost_basis_unknown"]

    def test_a_risk_floor_is_pinned_at_the_worst_case(self, adopted):
        assert D(adopted.state["risk_floor_usd"][MARKET]) == UNKNOWN_BOUND


class TestSettlementThenProducesTheBookedMinusOneNinetyOne:
    """Step four: the number the owner was shown."""

    @pytest.fixture()
    def settled(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        store.apply_order_progress(VENUE_ID, _progress(QTY),
                                   market=MARKET, side="DOWN")
        store.resolve_pending_terminal(OUR_COID, _progress(QTY))
        store.reconcile_from_venue(
            [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
              "qtySoldDecimal": "1.9100", "netPositionDecimal": "-1.9100"}],
            [])
        return store, store.retire_settled_market(
            MARKET, yes_value=D("1.0000"), resolution=RESOLUTION)

    def test_it_retires(self, settled):
        _s, out = settled
        assert out["retired"] is True

    def test_the_quantities_match_the_live_record(self, settled):
        _s, out = settled
        assert D(out["dn_qty"]) == QTY
        assert D(out["up_qty"]) == 0

    def test_the_redemption_is_zero_because_up_won(self, settled):
        _s, out = settled
        assert D(out["redemption_total"]) == 0

    def test_the_cost_is_the_placeholder_not_the_cash(self, settled):
        _s, out = settled
        assert D(out["dn_cost"]) == UNKNOWN_BOUND
        assert D(out["cost_total"]) == UNKNOWN_BOUND

    def test_the_settlement_pnl_is_the_figure_that_was_reported(self, settled):
        _s, out = settled
        assert D(out["settlement_pnl"]) == D("-1.91000000")

    def test_it_is_labelled_an_unknown_cost_upper_bound(self, settled):
        s, _out = settled
        row = s.state["retired_markets"][MARKET]
        assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
        assert row["unverified_cost_basis_keys"] == ["%s|DOWN" % MARKET]
        assert row["l5_owner_attribution_verified"] is False

    def test_the_overstatement_against_the_venue_is_one_dollar_eighteen(
            self, settled):
        _s, out = settled
        booked = D(out["settlement_pnl"])
        true = -ONE_FILL_COST
        assert (true - booked).quantize(D("0.0001")) == D("1.1842")


@pytest.mark.xfail(strict=True, reason=(
    "THE DEFECT. One venue order must be booked once, whatever client id "
    "each record carries. Remove this marker with the fix -- strict=True "
    "makes an unexpected pass fail the suite, so it cannot rot."))
class TestTheInvariantTheFixMustEstablish:

    @pytest.fixture()
    def fixed(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        store.apply_order_progress(VENUE_ID, _progress(QTY),
                                   market=MARKET, side="DOWN")
        store.resolve_pending_terminal(OUR_COID, _progress(QTY))
        return store

    def test_one_venue_order_books_one_quantity(self, fixed):
        assert fixed.position(MARKET, "DOWN") == QTY

    def test_and_one_cost(self, fixed):
        assert fixed.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST

    def test_so_the_basis_survives_and_settlement_is_the_truth(self, fixed):
        out = fixed.retire_settled_market(
            MARKET, yes_value=D("1.0000"), resolution=RESOLUTION)
        assert D(out["settlement_pnl"]) == -ONE_FILL_COST
