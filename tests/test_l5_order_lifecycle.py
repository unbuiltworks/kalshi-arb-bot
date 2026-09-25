"""An ACK is a receipt, not a final state.

Probe 04, 2026-09-25, real money. The ACK for CQ5NPBADMYC3 said filled_qty 0.
The venue's own record says it was ORDER_STATE_FILLED with cumQuantity 2.25,
leavesQuantity 0, avgPx 0.2900 and commission 0.0300, at lastTransactTime
13:01:46.847706680Z -- two milliseconds after insert.

Nothing ever asked again. So for the rest of the window the books carried a
resting order that did not exist: 29 later L5 actions were refused
MAX_LIVE_ORDERS against it, $0.72 stayed reserved against a position we
already held, and close-out tried to cancel it and was rejected.

These tests are that sequence.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as EX

MARKET = "cpc-btc-updown-15m-2026-09-25-1300z"

#: the venue's own row for CQ5NPBADMYC3, 2026-09-25T13:40Z, verbatim
PROBE_04_ORDER = {
    "id": "CQ5NPBADMYC3", "marketSlug": MARKET,
    "side": "ORDER_SIDE_BUY", "type": "ORDER_TYPE_LIMIT",
    "price": {"value": "0.32", "currency": "USD"},
    "quantity": 2.25, "cumQuantity": 2.25, "leavesQuantity": 0,
    "tif": "TIME_IN_FORCE_GOOD_TILL_CANCEL",
    "intent": "ORDER_INTENT_BUY_LONG", "outcomeSide": "OUTCOME_SIDE_YES",
    "state": "ORDER_STATE_FILLED",
    "avgPx": {"value": "0.2900", "currency": "USD"},
    "commissionNotionalTotalCollected": {"value": "0.0300",
                                         "currency": "USD"},
    "createTime": "2026-09-25T13:01:46.845851602Z",
    "insertTime": "2026-09-25T13:01:46.847706680Z",
    "lastTransactTime": "2026-09-25T13:01:46.847706680Z",
}


def test_the_venue_row_reads_as_a_completed_fill():
    p = EX.order_progress(PROBE_04_ORDER)
    assert p["terminal"] is True
    assert p["cum_qty"] == D("2.25")
    assert p["leaves_qty"] == D("0")
    assert p["avg_px"] == D("0.2900")
    assert p["commission"] == D("0.0300")
    assert p["state"] == "ORDER_STATE_FILLED"
    assert p["venue_order_id"] == "CQ5NPBADMYC3"


def test_leaves_quantity_is_the_authority_not_the_state_string():
    """Only ORDER_STATE_FILLED has been seen; the rest are not invented."""
    unknown = dict(PROBE_04_ORDER, state="ORDER_STATE_SOMETHING_NEW",
                   leavesQuantity=0)
    assert EX.order_progress(unknown)["terminal"] is True
    still_live = dict(PROBE_04_ORDER, state="", leavesQuantity="1.25",
                      cumQuantity="1.00")
    assert EX.order_progress(still_live)["terminal"] is False


def store_with_live_order(tmp_path, qty="2.25", price="0.32"):
    s = EX.InventoryStore(str(tmp_path / "inv.json"))
    s.record_submit("l5f-probe04", {"client_order_id": "l5f-probe04",
                                    "market": MARKET, "side": "UP",
                                    "price": D(price), "qty": D(qty)})
    s.attach_venue_id("l5f-probe04", "CQ5NPBADMYC3")
    return s


def test_probe_04_booked_from_the_venue_releases_the_order_and_the_envelope(
        tmp_path):
    """The whole defect, end to end."""
    s = store_with_live_order(tmp_path)
    assert s.live_order_count() == 1
    assert s.outstanding_by_market()[MARKET] == D("0.72")   # 0.32 * 2.25

    res = s.apply_order_progress("l5f-probe04",
                                 EX.order_progress(PROBE_04_ORDER),
                                 market=MARKET, side="UP")

    assert res["delta"] == "2.25", "the whole fill is booked"
    assert res.get("released") is True
    assert s.live_order_count() == 0, "MAX_LIVE_ORDERS capacity is freed"
    assert s.outstanding_by_market().get(MARKET, D(0)) == D(0)
    assert s.position(MARKET, "UP") == D("2.25"), "inventory reflects it"
    # committed at the ACTUAL average execution price, not the limit
    assert s.committed()[MARKET] == D("0.2900") * D("2.25")
    assert s.state["fees"]["l5f-probe04"] == "0.0300"


def test_polling_again_books_nothing_more(tmp_path):
    """Idempotence: the venue's cumQuantity is cumulative, not a delta."""
    s = store_with_live_order(tmp_path)
    prog = EX.order_progress(PROBE_04_ORDER)
    first = s.apply_order_progress("l5f-probe04", prog,
                                   market=MARKET, side="UP")
    pos_after_first = s.position(MARKET, "UP")

    for _ in range(4):
        again = s.apply_order_progress("l5f-probe04", prog,
                                       market=MARKET, side="UP")
        assert D(again["delta"]) == 0
    assert s.position(MARKET, "UP") == pos_after_first == D("2.25")
    assert first["delta"] == "2.25"


def test_a_partial_fill_keeps_the_remainder_outstanding(tmp_path):
    s = store_with_live_order(tmp_path)
    partial = dict(PROBE_04_ORDER, cumQuantity="1.00", leavesQuantity="1.25",
                   state="ORDER_STATE_PARTIALLY_FILLED",
                   avgPx={"value": "0.3000"})
    res = s.apply_order_progress("l5f-probe04", EX.order_progress(partial),
                                 market=MARKET, side="UP")

    assert res["delta"] == "1.00" and res["terminal"] is False
    assert s.position(MARKET, "UP") == D("1.00")
    assert s.live_order_count() == 1, "the rest is still live"
    assert s.live_orders()["l5f-probe04"]["remaining"] == "1.25"
    assert s.outstanding_by_market()[MARKET] == D("0.32") * D("1.25")


def test_a_partial_then_the_rest_never_double_counts(tmp_path):
    s = store_with_live_order(tmp_path)
    partial = dict(PROBE_04_ORDER, cumQuantity="1.00", leavesQuantity="1.25",
                   state="ORDER_STATE_PARTIALLY_FILLED")
    s.apply_order_progress("l5f-probe04", EX.order_progress(partial),
                           market=MARKET, side="UP")
    s.apply_order_progress("l5f-probe04", EX.order_progress(partial),
                           market=MARKET, side="UP")     # a repeat poll
    res = s.apply_order_progress("l5f-probe04",
                                 EX.order_progress(PROBE_04_ORDER),
                                 market=MARKET, side="UP")

    assert res["delta"] == "1.25", "only the NEW quantity"
    assert s.position(MARKET, "UP") == D("2.25"), "2.25 total, not 3.25"
    assert s.live_order_count() == 0


def test_a_cumulative_quantity_that_goes_backwards_fails_closed(tmp_path):
    """The venue contradicting itself is uncertainty, not a correction."""
    s = store_with_live_order(tmp_path)
    s.apply_order_progress("l5f-probe04", EX.order_progress(PROBE_04_ORDER),
                           market=MARKET, side="UP")
    backwards = dict(PROBE_04_ORDER, cumQuantity="1.00", leavesQuantity="0")
    res = s.apply_order_progress("l5f-probe04",
                                 EX.order_progress(backwards),
                                 market=MARKET, side="UP")

    assert res.get("refused") == "CUM_QTY_WENT_BACKWARDS"
    assert s.orders_uncertain(), "exposure is not released on a contradiction"
    assert s.position(MARKET, "UP") == D("2.25"), "nothing was unbooked"


def test_a_terminal_order_with_no_fill_releases_without_inventory(tmp_path):
    """Cancelled or expired: the slot frees, the position does not move."""
    s = store_with_live_order(tmp_path)
    dead = dict(PROBE_04_ORDER, cumQuantity="0", leavesQuantity="0",
                state="ORDER_STATE_CANCELLED")
    res = s.apply_order_progress("l5f-probe04", EX.order_progress(dead),
                                 market=MARKET, side="UP")

    assert res["delta"] == "0" and res.get("released") is True
    assert s.live_order_count() == 0
    assert s.position(MARKET, "UP") == D(0)
    assert s.outstanding_by_market().get(MARKET, D(0)) == D(0)


# ------------------------------------------------- positions, the real shape

def test_positions_reads_the_mapping_the_venue_actually_returns():
    """LIVE 2026-09-25T13:40Z: `positions` is an OBJECT keyed by slug."""
    from src.l5_pmus_transport import rows_of
    live_empty = {"positions": {}, "nextCursor": "", "eof": True,
                  "availablePositions": []}
    assert rows_of(live_empty, "positions") == []

    populated = {"positions": {MARKET: {"netPosition": "2.25",
                                        "side": "YES"}}}
    rows = rows_of(populated, "positions")
    assert rows == [{"marketSlug": MARKET, "netPosition": "2.25",
                     "side": "YES"}]


def test_reconciliation_survives_the_populated_mapping(tmp_path):
    """The exact TypeError Probe 04 died of, reproduced and fixed."""
    from src.l5_pmus_transport import rows_of
    s = store_with_live_order(tmp_path)
    # the venue's real populated shape: keyed by slug, no side field, the
    # direction in the sign of the net with unsigned halves beside it
    populated = {"positions": {MARKET: {"netPositionDecimal": "2.25",
                                        "qtyBoughtDecimal": "2.25",
                                        "qtySoldDecimal": "0"}}}
    rows = rows_of(populated, "positions")
    # before the fix this list was ["cpc-..."], strings, and p["market"]
    # raised TypeError: string indices must be integers
    for p in rows:
        assert isinstance(p, dict)
    out = s.reconcile_from_venue(rows, [])
    assert out["reconciled"] is True
    assert out["open_orders_at_venue"] == 0


def test_reconciliation_with_no_position_and_no_orders(tmp_path):
    s = store_with_live_order(tmp_path)
    out = s.reconcile_from_venue([], [])
    assert out["reconciled"] is True
    assert "l5f-probe04" in out["live_orders_released"]
    assert s.live_order_count() == 0


# --------------------------------------------- the probe, end to end

class AckZeroThenFilled:
    """The venue as it actually behaved on Probe 04.

    submit() ACKs with filled_qty 0. Two milliseconds later the order is
    FILLED. cancel() raises, as it did against a terminal order -- and the
    point of these tests is that it is never called.
    """

    name = "PROBE04"
    quantity_increment = D("0.01")

    def __init__(self):
        self.armed = True
        self.posts, self.cancels, self.order_reads = [], [], []
        self.preflight_report = {"preflight": "PASSED"}

    def preflight(self, *, expect_account=None, known_positions=None):
        return self.preflight_report

    def arm(self):
        return {"armed": True}

    def submit(self, intent):
        self.posts.append(dict(intent))
        return {"order_id": "CQ5NPBADMYC3", "filled_qty": "0"}

    def order(self, order_id):
        self.order_reads.append(order_id)
        return dict(PROBE_04_ORDER)

    def cancel(self, order_id):
        self.cancels.append(order_id)
        raise RuntimeError("cannot cancel a filled order")

    def positions(self):
        return []

    def open_orders(self):
        return []


def test_the_probe_learns_the_fill_and_stops_blocking_itself(tmp_path,
                                                             monkeypatch):
    """Probe 04's sequence, with the fix: ACK 0 -> venue FILLED -> free."""
    import scripts.l5_pmus_live_oneshot as P
    import src.l5_probe_manifest as PM
    import tests.test_l5_live_oneshot as O

    tp = AckZeroThenFilled()
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    probe = P.Probe(market=MARKET, store=store,
                    journal=__import__("src.l5_session_journal",
                                       fromlist=["Journal"]).Journal(
                        str(tmp_path / "j.jsonl")),
                    auth=None, transport=tp,
                    book_feed=O.Feed(lambda: 0.0), clock=lambda: 0.0,
                    sleep=lambda s: None, activity=None)
    probe.armed = True

    # the order exists and our books think it is resting, as they did live
    store.record_submit("l5f-probe04", {"client_order_id": "l5f-probe04",
                                        "market": MARKET, "side": "UP",
                                        "price": D("0.32"), "qty": D("2.25")})
    store.attach_venue_id("l5f-probe04", "CQ5NPBADMYC3")
    assert store.live_order_count() == 1

    out = probe.refresh_live_orders()

    assert tp.order_reads == ["CQ5NPBADMYC3"], "it asked the venue"
    assert out[0]["delta"] == "2.25" and out[0]["released"] is True
    assert store.live_order_count() == 0, (
        "MAX_LIVE_ORDERS no longer blocks the next L5 action")
    assert store.position(MARKET, "UP") == D("2.25")
    assert store.outstanding_by_market().get(MARKET, D(0)) == D(0)


def test_close_out_does_not_cancel_an_order_the_venue_calls_filled(
        tmp_path, monkeypatch):
    """Probe 04 cancelled a filled order and was rejected. Not any more."""
    import scripts.l5_pmus_live_oneshot as P
    import tests.test_l5_live_oneshot as O
    from src.l5_session_journal import Journal

    tp = AckZeroThenFilled()
    store = EX.InventoryStore(str(tmp_path / "inv.json"))
    probe = P.Probe(market=MARKET, store=store,
                    journal=Journal(str(tmp_path / "j.jsonl")),
                    auth=None, transport=tp,
                    book_feed=O.Feed(lambda: 0.0), clock=lambda: 0.0,
                    sleep=lambda s: None, activity=None)
    probe.armed = True
    store.record_submit("l5f-probe04", {"client_order_id": "l5f-probe04",
                                        "market": MARKET, "side": "UP",
                                        "price": D("0.32"), "qty": D("2.25")})
    store.attach_venue_id("l5f-probe04", "CQ5NPBADMYC3")

    close = probe.close_out()

    assert tp.cancels == [], "no cancel was attempted against a filled order"
    assert close["cancel_errors"] == []
    assert close["order_progress"][0]["released"] is True
    assert close["reconciliation"]["reconciled"] is True
    assert not store.orders_uncertain(), "the state is resolved, not uncertain"


def test_the_transport_itself_parses_the_populated_positions_mapping():
    """Pin the CALL SITE, not just the helper.

    `rows_of` being right is not enough: `positions()` has to use it. The
    first version of this suite tested the helper and stayed green when
    positions() was reverted to the coercion that produced Probe 04's
    TypeError.
    """
    from src import l5_pmus_transport as TP

    class Canned:
        def __init__(self, payload):
            self.payload = payload

        def request(self, method, path, body=None, base=None):
            assert method == "GET", method
            return self.payload

    live_empty = {"positions": {}, "nextCursor": "", "eof": True,
                  "availablePositions": []}
    tp = TP.PmusTransport(market_slug=MARKET, http=Canned(live_empty))
    assert tp.positions() == []
    assert tp.available_positions() == []

    populated = {"positions": {MARKET: {"netPosition": "2.25",
                                        "side": "YES"}},
                 "availablePositions": [{"marketSlug": MARKET,
                                         "quantity": "2.25"}]}
    tp = TP.PmusTransport(market_slug=MARKET, http=Canned(populated))
    rows = tp.positions()
    assert rows == [{"marketSlug": MARKET, "netPosition": "2.25",
                     "side": "YES"}]
    # every row is a mapping, which is the property Probe 04's close-out
    # needed and did not get: `for p in positions: p["market"]` over a bare
    # dict iterates KEYS, and indexing a string is a TypeError
    for p in rows:
        assert isinstance(p, dict)
    assert tp.available_positions() == [{"marketSlug": MARKET,
                                         "quantity": "2.25"}]


def test_the_transports_order_read_unwraps_the_venue_envelope():
    from src import l5_pmus_transport as TP

    class Canned:
        def request(self, method, path, body=None, base=None):
            assert method == "GET" and path == "/v1/order/CQ5NPBADMYC3"
            return {"order": dict(PROBE_04_ORDER)}

    tp = TP.PmusTransport(market_slug=MARKET, http=Canned())
    row = tp.order("CQ5NPBADMYC3")
    assert row["state"] == "ORDER_STATE_FILLED"
    assert EX.order_progress(row)["cum_qty"] == D("2.25")
