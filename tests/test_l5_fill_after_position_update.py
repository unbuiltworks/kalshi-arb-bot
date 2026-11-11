"""0415z, 2026-11-11: the position update arrived before the fill.

THE FACTS, from the service's own log (deployment ddfa5c0d):

  04:15:19.533 L5_LIVE_LIFECYCLE  ORDER_PROGRESS  DN6KVVR0144P NEW, cum 0
  04:15:20.831 L5_LIVE_RECONCILE  drift 0415z|UP  ours 0, venue 0.4400
                                  -> adopted at the $1/share bound
  04:15:22.507 L5_LIVE_LIFECYCLE  PENDING_TERMINAL_RESOLVED  DN6KVVR0144P
                                  FILLED, cum 0.44, avg_px 0.3200, already 0
                                  -> ADDED to the adopted 0.44: book 0.88
  04:15:33.527 L5_LIVE_RECONCILE  drift 0415z|UP  ours 0.8800, venue 0.4400
                                  -> adopted again, at the bound
  04:30:32.690 L5_LIVE_SETTLED    up_qty 0.4400, up_cost 0.4400,
                                  settlement_pnl -0.44000000,
                                  committed_released 0.140800

The venue charged $0.1408 (cash 341.07295 - 0.1408 = 340.93215, unchanged
after settlement). The ledger booked -$0.4400.

TWO PROTECTIONS, either of which is enough on its own:

  1. a difference that our own tracked, unbooked orders can explain is not
     adopted -- the order read books it at its real price;
  2. if the position WAS adopted, the fill that the adopted quantity already
     contains replaces that much of the bound instead of being added to it.

Anything our own orders cannot account for exactly is still adopted at the
bound, as before.
"""
from __future__ import annotations

import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX

MARKET = "cpc-btc-updown-15m-2026-11-11-0415z"
UP_COID, UP_VID = "l5f-4c50136c4b83c3c102312ed1", "DN6KVVR0144P"
DN_COID, DN_VID = "l5f-681862551a5e4d4b5afdfa02", "DN6KW0WG144P"
QTY = D("0.44")
PAID = D("0.1408")                 # 0.44 x 0.32
BOUND = D("0.4400")                # 0.44 x $1.00
KEY = "%s|UP" % MARKET
RESOLUTION = {"source": "VENUE_SETTLEMENT_PX", "market": MARKET,
              "yes_value": "0.0000"}

DN_OPEN_ROW = {"id": DN_VID, "marketSlug": MARKET,
               "outcomeSide": "OUTCOME_SIDE_NO", "price": "0.3700",
               "qty": "0.44", "remainingQuantity": "0.44",
               "createTime": "2026-11-11T04:15:18.000000Z"}


def up_open_row(remaining="0.44"):
    return {"id": UP_VID, "marketSlug": MARKET,
            "outcomeSide": "OUTCOME_SIDE_YES", "price": "0.3200",
            "qty": "0.44", "remainingQuantity": remaining,
            "createTime": "2026-11-11T04:15:17.000000Z"}


def venue_pos(net, market=MARKET):
    n = D(net)
    return {"marketSlug": market,
            "qtyBoughtDecimal": str(max(n, D(0))),
            "qtySoldDecimal": str(max(-n, D(0))),
            "netPositionDecimal": str(n)}


def filled(cum="0.44", px="0.3200", terminal=True):
    return {"cum_qty": cum, "avg_px": px, "commission": "0.0000",
            "leaves_qty": str(QTY - D(cum)), "terminal": terminal,
            "state": ("ORDER_STATE_FILLED" if terminal
                      else "ORDER_STATE_PARTIALLY_FILLED")}


def live(coid, vid, side, own_px, qty="0.44"):
    return {"t": 1794370517.0, "venue_order_id": vid,
            "client_order_id": coid, "session_id": "camp",
            "market": MARKET, "side": side, "price": own_px, "qty": qty,
            "remaining": qty, "state": "LIVE_AT_VENUE"}


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        s = LX.InventoryStore(os.path.join(d, "inv.json"))
        s.state["live_orders"] = {
            UP_COID: live(UP_COID, UP_VID, "UP", "0.32"),
            DN_COID: live(DN_COID, DN_VID, "DOWN", "0.63")}
        s.state["committed"] = {MARKET: "0"}
        yield s


def unknown(s):
    return list(s.state.get("leg_cost_basis_unknown") or [])


def settle(s):
    # the unfilled DOWN order was cancelled at the terminal, as it was live
    s.state["live_orders"].pop(DN_COID, None)
    return s.retire_settled_market(MARKET, yes_value=D("0.0000"),
                                   resolution=RESOLUTION)


# ======================================================================
# 1. THE NIGHT, REPLAYED: POSITION FIRST, FILL SECOND
# ======================================================================
class TestTheNightReplayed:

    def test_the_position_that_our_own_order_explains_is_not_adopted(
            self, store):
        out = store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        assert MARKET in out["fills_in_flight_deferred"]
        d = out["fills_in_flight_deferred"][MARKET]
        assert d["difference"] == "0.44" and d["deferrals"] == 1
        assert [o["venue_order_id"] for o in d["orders"]] == [UP_VID]
        assert unknown(store) == []
        assert MARKET not in (store.state.get("gross_quarantine") or {})
        assert out["ALERT"] is None
        # the order is off the live map and owes its final read
        assert out["live_orders_pending_terminal"] == [UP_COID]
        # and new exposure is refused until that read lands
        assert store.orders_uncertain() is not None

    def test_the_fill_is_then_booked_at_what_it_cost(self, store):
        store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        store.resolve_pending_terminal(UP_COID, filled())
        assert store.position(MARKET, "UP") == QTY
        assert store.leg_cost(MARKET)["UP"] == PAID
        assert store.fill_count(MARKET, "UP") == 1

    def test_the_next_reconciliation_agrees_and_adopts_nothing(self, store):
        store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        store.resolve_pending_terminal(UP_COID, filled())
        out = store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        assert out["gross_net_agrees"][MARKET] is True
        assert out["fills_in_flight_deferred"] == {}
        assert store.position(MARKET, "UP") == QTY       # not 0.88
        assert store.leg_cost(MARKET)["UP"] == PAID
        assert unknown(store) == []
        assert (store.state.get("fill_in_flight") or {}) == {}

    def test_settlement_books_what_the_venue_charged(self, store):
        store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        store.resolve_pending_terminal(UP_COID, filled())
        store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        out = settle(store)
        assert out["retired"] is True
        assert D(out["up_cost"]) == PAID
        assert D(out["settlement_pnl"]) == -PAID          # not -0.4400
        row = store.state["retired_markets"][MARKET]
        assert row["pnl_provenance"] != "UNKNOWN_COST_UPPER_BOUND"
        assert row["unverified_cost_basis_keys"] == []


# ======================================================================
# 2. IF IT WAS ADOPTED ANYWAY, THE FILL REPLACES THE BOUND
# ======================================================================
class TestTheFillReplacesTheBound:

    @pytest.fixture()
    def adopted(self, store, monkeypatch):
        """No deferral at all: exactly what production did at 04:15:20."""
        monkeypatch.setattr(LX, "FILL_IN_FLIGHT_MAX_DEFERRALS", 0)
        store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        return store

    def test_the_adoption_is_at_the_bound_as_before(self, adopted):
        assert adopted.position(MARKET, "UP") == QTY
        assert adopted.leg_cost(MARKET)["UP"] == BOUND
        assert unknown(adopted) == [KEY]
        assert {k: D(v) for k, v in
                adopted.state["adopted_unattributed"].items()} == {KEY: QTY}

    def test_the_fill_is_not_added_on_top(self, adopted):
        adopted.resolve_pending_terminal(UP_COID, filled())
        assert adopted.position(MARKET, "UP") == QTY      # production: 0.88
        assert adopted.leg_cost(MARKET)["UP"] == PAID     # production: 0.44
        assert unknown(adopted) == []
        assert adopted.state["adopted_unattributed"] == {}

    def test_the_replacement_is_on_the_record(self, adopted):
        adopted.resolve_pending_terminal(UP_COID, filled())
        rows = adopted.state["adoption_attributions"]
        assert len(rows) == 1
        r = rows[0]
        assert r["key"] == KEY and r["coid"] == UP_COID
        assert D(r["qty"]) == QTY and D(r["own_price"]) == D("0.32")
        assert D(r["bound_replaced_usd"]) == BOUND
        assert D(r["measured_usd"]) == PAID
        assert D(r["still_at_the_bound"]) == 0

    def test_no_second_adoption_and_the_right_settlement(self, adopted,
                                                         monkeypatch):
        adopted.resolve_pending_terminal(UP_COID, filled())
        monkeypatch.setattr(LX, "FILL_IN_FLIGHT_MAX_DEFERRALS", 2)
        out = adopted.reconcile_from_venue([venue_pos("0.44")],
                                           [DN_OPEN_ROW])
        assert out["gross_net_agrees"][MARKET] is True
        assert unknown(adopted) == []
        assert D(settle(adopted)["settlement_pnl"]) == -PAID

    def test_a_partial_fill_replaces_only_its_own_share(self, adopted):
        adopted.state["pending_terminal"].pop(UP_COID)
        adopted.state["live_orders"][UP_COID] = live(
            UP_COID, UP_VID, "UP", "0.32")
        adopted.apply_order_progress(UP_COID, filled("0.20", terminal=False),
                                     market=MARKET, side="UP")
        assert adopted.position(MARKET, "UP") == QTY
        assert adopted.leg_cost(MARKET)["UP"] == \
            D("0.20") * D("0.32") + D("0.24") * D(1)
        assert unknown(adopted) == [KEY]                  # 0.24 still unknown
        assert D(adopted.state["adopted_unattributed"][KEY]) == D("0.24")

    def test_a_fill_with_no_order_of_ours_behind_it_replaces_nothing(
            self, adopted):
        adopted.apply_fill(MARKET, "UP", D("0.10"), D("0.30"), coid=None,
                           basis=LX.WIRE)
        assert adopted.position(MARKET, "UP") == QTY + D("0.10")
        assert adopted.leg_cost(MARKET)["UP"] == BOUND + D("0.03")
        assert unknown(adopted) == [KEY]

    def test_once_attributed_later_fills_are_added_normally(self, adopted):
        adopted.resolve_pending_terminal(UP_COID, filled())
        adopted.apply_fill(MARKET, "UP", D("0.40"), D("0.30"),
                           coid="l5f-another", basis=LX.WIRE)
        assert adopted.position(MARKET, "UP") == D("0.84")
        assert adopted.leg_cost(MARKET)["UP"] == PAID + D("0.12")


# ======================================================================
# 3. WHAT OUR ORDERS CANNOT EXPLAIN IS STILL ADOPTED AT THE BOUND
# ======================================================================
class TestStillConservative:

    def test_more_than_our_orders_could_have_filled(self, store):
        out = store.reconcile_from_venue([venue_pos("0.60")], [DN_OPEN_ROW])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == [KEY]
        assert store.leg_cost(MARKET)["UP"] == D("0.60")

    def test_the_wrong_side_for_the_order_that_is_missing(self, store):
        """The UP order vanished; a DOWN position bigger than the DOWN
        order could have made is not ours to explain."""
        out = store.reconcile_from_venue([venue_pos("-0.60")], [])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == ["%s|DOWN" % MARKET]

    def test_an_open_order_the_venue_says_is_untouched(self, store):
        """Both orders still open with everything remaining: neither can
        have filled, so the position is not theirs."""
        out = store.reconcile_from_venue(
            [venue_pos("0.44")], [DN_OPEN_ROW, up_open_row("0.44")])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == [KEY]

    def test_a_market_we_have_no_order_in(self, store):
        other = "cpc-btc-updown-15m-2026-11-11-0430z"
        out = store.reconcile_from_venue(
            [venue_pos("0.44", market=other)],
            [DN_OPEN_ROW, up_open_row("0.44")])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == ["%s|UP" % other]

    def test_an_order_with_no_venue_id_explains_nothing(self, store):
        store.state["live_orders"][UP_COID]["venue_order_id"] = None
        out = store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == [KEY]

    def test_an_order_already_booked_in_full_explains_nothing(self, store):
        store.apply_order_progress(UP_COID, filled(), market=MARKET,
                                   side="UP")
        out = store.reconcile_from_venue([venue_pos("0.88")], [DN_OPEN_ROW])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == [KEY]

    def test_the_wait_is_bounded(self, store):
        """The order read never comes: two reconciliations wait, the third
        adopts at the bound exactly as it always did."""
        for n in (1, 2):
            out = store.reconcile_from_venue([venue_pos("0.44")],
                                             [DN_OPEN_ROW])
            assert out["fills_in_flight_deferred"][MARKET]["deferrals"] == n
            assert unknown(store) == []
        out = store.reconcile_from_venue([venue_pos("0.44")], [DN_OPEN_ROW])
        assert out["fills_in_flight_deferred"] == {}
        assert unknown(store) == [KEY]
        assert store.leg_cost(MARKET)["UP"] == BOUND
        assert out["ALERT"] is not None
        # and the late fill still lands on the bound, not on top of it
        store.resolve_pending_terminal(UP_COID, filled())
        assert store.position(MARKET, "UP") == QTY
        assert store.leg_cost(MARKET)["UP"] == PAID


# ======================================================================
# 4. PARTIAL FILLS AND MATCHED PAIRS
# ======================================================================
class TestPartialsAndPairs:

    def test_a_partial_fill_in_flight_on_an_order_still_open(self, store):
        out = store.reconcile_from_venue(
            [venue_pos("0.20")], [DN_OPEN_ROW, up_open_row("0.24")])
        assert MARKET in out["fills_in_flight_deferred"]
        assert unknown(store) == []
        store.apply_order_progress(UP_COID, filled("0.20", terminal=False),
                                   market=MARKET, side="UP")
        assert store.position(MARKET, "UP") == D("0.20")
        assert store.leg_cost(MARKET)["UP"] == D("0.064")
        out = store.reconcile_from_venue(
            [venue_pos("0.20")], [DN_OPEN_ROW, up_open_row("0.24")])
        assert out["gross_net_agrees"][MARKET] is True

    def test_a_matched_pair_is_not_thrown_away_for_a_fill_in_flight(
            self, store):
        """Holding a completed pair, a third order fills. The venue's net
        moves first. Adoption would have replaced UP 0.44 / DOWN 0.44 and
        their costs with a net of 0.44 at $1 a share."""
        store.apply_order_progress(UP_COID, filled(), market=MARKET,
                                   side="UP")
        store.apply_order_progress(DN_COID, filled(px="0.3700"),
                                   market=MARKET, side="DOWN")
        third, vid = "l5f-third", "DN6THIRD"
        store.state["live_orders"][third] = live(third, vid, "UP", "0.30")
        out = store.reconcile_from_venue([venue_pos("0.44")], [])
        assert MARKET in out["fills_in_flight_deferred"]
        assert store.position(MARKET, "UP") == QTY
        assert store.position(MARKET, "DOWN") == QTY
        assert store.leg_cost(MARKET) == {"UP": PAID,
                                          "DOWN": D("0.44") * D("0.63")}
        assert MARKET not in (store.state.get("gross_quarantine") or {})
        store.resolve_pending_terminal(third, filled(px="0.3000"))
        assert store.position(MARKET, "UP") == D("0.88")
        out = store.reconcile_from_venue([venue_pos("0.44")], [])
        assert out["gross_net_agrees"][MARKET] is True
        assert unknown(store) == []

    def test_a_down_fill_in_flight_is_recognised_too(self, store):
        out = store.reconcile_from_venue([venue_pos("-0.44")],
                                         [up_open_row("0.44")])
        assert MARKET in out["fills_in_flight_deferred"]
        store.resolve_pending_terminal(DN_COID, filled(px="0.3700"))
        assert store.position(MARKET, "DOWN") == QTY
        assert store.leg_cost(MARKET)["DOWN"] == D("0.44") * D("0.63")
