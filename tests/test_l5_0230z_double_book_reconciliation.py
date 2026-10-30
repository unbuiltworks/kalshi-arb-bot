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
        assert QTY * (D(1) - D("1.0000")) == 0

    def test_the_venue_confirmed_result_is_the_purchase_cost(self):
        assert D(0) - ONE_FILL_COST == D("-0.7258")

    def test_the_balance_never_moved_again_so_nothing_was_redeemed(self):
        """Settlement at 02:46:23; cash read 342.49035 at 02:46:57 and
        02:49:00. A redemption would have shown up here."""
        assert CASH_AFTER == D("342.49035")


class TestTheHistoricalFiguresThatMustBeRestated:
    """Arithmetic only -- what the defect produced, for the correction."""

    def test_the_double_book_was_two_correct_bookings(self):
        assert DOUBLE_COST == 2 * ONE_FILL_COST

    def test_the_placeholder_is_the_quantity_at_a_dollar_a_share(self):
        assert UNKNOWN_BOUND == QTY * LX.UNKNOWN_PRICE_WORST_CASE

    def test_the_booked_loss_was_the_whole_notional(self):
        assert D(0) - UNKNOWN_BOUND == D("-1.9100")

    def test_the_overstatement_is_one_dollar_eighteen_forty_two(self):
        assert (UNKNOWN_BOUND - ONE_FILL_COST).quantize(D("0.0001")) \
            == D("1.1842")


class TestTheVenueRowIsResolvedToOurOwnOrder:
    """The first half of the fix: identity."""

    def test_the_row_still_carries_no_client_order_id(self):
        """The venue has not changed; our reading of it has."""
        for k in ("client_order_id", "clientOrderId"):
            assert k not in VENUE_OPEN_ROW

    def test_the_helper_finds_the_coid_we_sent_it_with(self, store):
        assert LX.tracked_coid_for_venue_order(
            VENUE_ID, store.state) == OUR_COID

    def test_reconcile_keys_the_row_under_our_coid(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert OUR_COID in store.state["live_orders"]
        assert VENUE_ID not in store.state["live_orders"]

    def test_nothing_is_adopted(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert store.state["live_orders"][OUR_COID]["adopted"] is False

    def test_our_order_is_not_parked_as_vanished(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert not (store.state.get("pending_terminal") or {})

    def test_the_venue_order_id_is_preserved_for_cancellation(self, store):
        """`owns_venue_order_id` and the deadman cancel both key on this."""
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        assert store.state["live_orders"][OUR_COID]["venue_order_id"] \
            == VENUE_ID
        assert store.owns_venue_order_id(VENUE_ID) is True

    def test_a_row_that_does_name_our_client_id_is_unaffected(self, store):
        row = dict(VENUE_OPEN_ROW, client_order_id=OUR_COID)
        store.reconcile_from_venue([], [row])
        assert OUR_COID in store.state["live_orders"]


class TestOneVenueOrderBooksOnce:
    """The invariant that was xfail before the fix."""

    @pytest.fixture()
    def booked(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        out = store.apply_order_progress(OUR_COID, _progress(QTY),
                                         market=MARKET, side="DOWN")
        return store, out

    def test_one_venue_order_books_one_quantity(self, booked):
        s, _o = booked
        assert s.position(MARKET, "DOWN") == QTY

    def test_and_one_cost(self, booked):
        s, _o = booked
        assert s.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST

    def test_and_one_fill(self, booked):
        s, _o = booked
        assert s.fill_count(MARKET, "DOWN") == 1

    def test_the_gross_book_now_agrees_with_the_venue_net(self, booked):
        """Which is what stops adoption firing and destroying the basis."""
        s, _o = booked
        s.reconcile_from_venue(
            [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
              "qtySoldDecimal": "1.9100", "netPositionDecimal": "-1.9100"}],
            [])
        assert s.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST
        assert MARKET not in (s.state.get("gross_quarantine") or {})
        assert "%s|DOWN" % MARKET not in (
            s.state.get("leg_cost_basis_unknown") or [])

    def test_so_the_basis_survives_and_settlement_is_the_truth(self, booked):
        s, _o = booked
        s.reconcile_from_venue(
            [{"marketSlug": MARKET, "qtyBoughtDecimal": "0",
              "qtySoldDecimal": "1.9100", "netPositionDecimal": "-1.9100"}],
            [])
        out = s.retire_settled_market(MARKET, yes_value=D("1.0000"),
                                      resolution=RESOLUTION)
        assert out["retired"] is True
        assert D(out["dn_cost"]) == ONE_FILL_COST
        assert D(out["redemption_total"]) == 0
        assert D(out["settlement_pnl"]) == -ONE_FILL_COST

    def test_the_settled_pnl_is_no_longer_an_upper_bound(self, booked):
        s, _o = booked
        s.retire_settled_market(MARKET, yes_value=D("1.0000"),
                                resolution=RESOLUTION)
        row = s.state["retired_markets"][MARKET]
        assert row["unverified_cost_basis_keys"] == []
        assert row["pnl_provenance"] != "UNKNOWN_COST_UPPER_BOUND"


class TestTheDuplicateGuardFailsClosed:
    """The second half: the consequence is unreachable however identity
    goes wrong next time. The defect state is forced directly here, because
    the identity fix means reconcile can no longer produce it."""

    @pytest.fixture()
    def two_records(self, store):
        """The ledger exactly as it stood at 02:30:27 on 2026-10-30."""
        store.state["live_orders"][VENUE_ID] = {
            "t": 1793327390.0, "venue_order_id": VENUE_ID,
            "client_order_id": VENUE_ID, "adopted": True,
            "market": MARKET, "side": "DOWN", "price": str(OWN_PX),
            "qty": "1.91", "remaining": "1.91", "state": "LIVE_AT_VENUE"}
        return store

    def test_the_first_booking_is_allowed(self, two_records):
        out = two_records.apply_order_progress(
            VENUE_ID, _progress(QTY), market=MARKET, side="DOWN")
        assert "refused" not in out
        assert two_records.position(MARKET, "DOWN") == QTY

    def test_the_guard_names_the_coid_that_already_booked(self, two_records):
        two_records.apply_order_progress(VENUE_ID, _progress(QTY),
                                         market=MARKET, side="DOWN")
        assert LX.duplicate_venue_order_coid(
            two_records.state, OUR_COID) == VENUE_ID

    def test_the_second_booking_is_refused(self, two_records):
        two_records.apply_order_progress(VENUE_ID, _progress(QTY),
                                         market=MARKET, side="DOWN")
        out = two_records.apply_order_progress(
            OUR_COID, _progress(QTY), market=MARKET, side="DOWN")
        assert out["refused"] == "DUPLICATE_VENUE_ORDER"
        assert out["already_booked_under"] == VENUE_ID
        assert out["venue_order_id"] == VENUE_ID

    def test_and_books_nothing(self, two_records):
        two_records.apply_order_progress(VENUE_ID, _progress(QTY),
                                        market=MARKET, side="DOWN")
        two_records.apply_order_progress(OUR_COID, _progress(QTY),
                                        market=MARKET, side="DOWN")
        assert two_records.position(MARKET, "DOWN") == QTY
        assert two_records.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST
        assert two_records.fill_count(MARKET, "DOWN") == 1

    def test_and_fails_closed(self, two_records):
        """Uncertain refuses submissions until a human resolves it."""
        two_records.apply_order_progress(VENUE_ID, _progress(QTY),
                                        market=MARKET, side="DOWN")
        two_records.apply_order_progress(OUR_COID, _progress(QTY),
                                        market=MARKET, side="DOWN")
        assert two_records.state.get("orders_uncertain")

    def test_the_pending_terminal_route_is_guarded_too(self, two_records):
        """The second 0230z booking came through this path, not the first."""
        two_records.apply_order_progress(VENUE_ID, _progress(QTY),
                                        market=MARKET, side="DOWN")
        two_records.state.setdefault("pending_terminal", {})[OUR_COID] = {
            "coid": OUR_COID, "venue_order_id": VENUE_ID, "market": MARKET,
            "side": "DOWN", "applied": "0", "resolvable": True}
        two_records.resolve_pending_terminal(OUR_COID, _progress(QTY))
        assert two_records.position(MARKET, "DOWN") == QTY
        assert two_records.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST


class TestWhatTheFixMustNotDisturb:
    """Explicitly preserved: partial fills, genuinely unknown orders, and
    the per-coid idempotency the ordinary path relies on."""

    def test_successive_partials_under_one_coid_still_book_deltas(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        a = store.apply_order_progress(
            OUR_COID, dict(_progress(D("0.91")), terminal=False,
                           leaves_qty="1.00",
                           state="ORDER_STATE_PARTIALLY_FILLED"),
            market=MARKET, side="DOWN")
        b = store.apply_order_progress(OUR_COID, _progress(QTY),
                                       market=MARKET, side="DOWN")
        assert D(a["delta"]) == D("0.91")
        assert D(b["delta"]) == D("1.00")
        assert store.position(MARKET, "DOWN") == QTY
        assert store.leg_cost(MARKET)["DOWN"] == ONE_FILL_COST

    def test_a_repolled_cumulative_still_books_nothing(self, store):
        store.reconcile_from_venue([], [VENUE_OPEN_ROW])
        store.apply_order_progress(OUR_COID, _progress(QTY),
                                   market=MARKET, side="DOWN")
        again = store.apply_order_progress(OUR_COID, _progress(QTY),
                                           market=MARKET, side="DOWN")
        assert D(again["delta"]) == 0
        assert "refused" not in again
        assert store.position(MARKET, "DOWN") == QTY

    def test_an_order_we_never_sent_is_still_adopted(self, store):
        stranger = dict(VENUE_OPEN_ROW, id="ZZZSTRANGER999")
        store.reconcile_from_venue([], [stranger])
        assert "ZZZSTRANGER999" in store.state["live_orders"]
        assert store.state["live_orders"]["ZZZSTRANGER999"]["adopted"] is True

    def test_an_adopted_stranger_can_still_book_its_fill(self, store):
        stranger = dict(VENUE_OPEN_ROW, id="ZZZSTRANGER999")
        store.reconcile_from_venue([], [stranger])
        out = store.apply_order_progress("ZZZSTRANGER999", _progress(QTY),
                                         market=MARKET, side="DOWN")
        assert "refused" not in out
        assert store.position(MARKET, "DOWN") == QTY

    def test_an_order_that_truly_vanished_is_still_parked(self, store):
        """No venue row at all: the 2045z protection must survive."""
        store.reconcile_from_venue([], [])
        pend = store.state["pending_terminal"]
        assert OUR_COID in pend
        assert pend[OUR_COID]["reason"] == "ABSENT_FROM_VENUE_OPEN_ORDERS"

    def test_a_coid_we_do_not_track_has_no_venue_order_id(self, store):
        assert LX.venue_order_id_for_coid(store.state, "unknown-coid") is None

    def test_an_unknown_venue_order_matches_no_coid(self, store):
        assert LX.tracked_coid_for_venue_order("NOSUCHID", store.state) is None

    def test_an_empty_venue_order_id_matches_nothing(self, store):
        for empty in (None, "", "   "):
            assert LX.tracked_coid_for_venue_order(empty, store.state) is None

    def test_requoting_is_unaffected_because_each_order_has_its_own_id(
            self, store):
        """A REPLACE cancels and re-sends: a new coid with a new venue id,
        which must never look like a duplicate of the order it replaced."""
        store.state["filled_qty"] = {OUR_COID: "1.91"}
        store.state["live_orders"]["l5f-replacement"] = {
            "venue_order_id": "DIFFERENTID123", "market": MARKET,
            "side": "DOWN", "price": str(OWN_PX), "remaining": "1.00"}
        assert LX.duplicate_venue_order_coid(
            store.state, "l5f-replacement") is None
