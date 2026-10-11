"""Contract tests for EVERY venue surface the CI15M live path consumes.

Each adapter is tested two ways:

  1. against the PRODUCER'S OWN SOURCE, so the contract cannot drift
     silently -- this is the check that would have caught all three live
     defects;
  2. across the five response classes: successful+populated,
     successful+empty, unsuccessful, malformed/unrecognised, and the
     relevant terminal/nonterminal states.

THE INVARIANT UNDER TEST, stated once: "the venue says nothing is there" is
a FACT; "we could not find out" is UNKNOWN. They must never normalise to
the same value, and UNKNOWN must fail closed.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

import pytest

from src.gemini_execution import metadata, transport
from src.gemini_execution.ci15m_adapters import (ActiveOrders, CANCELLED,
                                                 DiscoveredMarket,
                                                 ExecutableBook, FILLED,
                                                 OrderHistory, OrderState,
                                                 PlaceAck, PositionRead,
                                                 RESTING, UNRESOLVED,
                                                 markets_of)


# =====================================================================
# 1. metadata.positions()   -- defect #3
# =====================================================================
def test_positions_contract_matches_the_producer_source():
    src = inspect.getsource(metadata.positions)
    assert '"rows": None' in src and 'out["ok"] = True' in src
    assert 'out["rows"] = rows' in src
    for guessed in ('out["positions"]', 'out["result"]', 'out["data"]'):
        assert guessed not in src, guessed


def test_positions_successful_populated():
    rows = [{"symbol": "GEMI-BTC15M2610031845-UP", "outcome": "yes",
             "totalQuantity": "1"}]
    r = PositionRead.of({"path": "/v1/prediction-markets/positions",
                         "rows": rows, "status": 200, "ok": True, "total": 1})
    assert r.readable and r.rows == rows and not r.is_empty_account


def test_positions_successful_empty_is_a_fact_not_unknown():
    r = PositionRead.of({"rows": [], "status": 200, "ok": True, "total": 0})
    assert r.readable and r.is_empty_account


@pytest.mark.parametrize("got", [
    {"ok": False, "rows": None, "status": 500, "why": "boom"},
    {"ok": False, "rows": [], "status": 200, "why": "unrecognised shape"},
    {"rows": [{"x": 1}]},                       # no ok key at all
    {"ok": True, "rows": None},
    {"ok": True, "rows": {"not": "a list"}},
    None, [], "nope", 7,
])
def test_positions_unsuccessful_and_malformed_fail_closed(got):
    r = PositionRead.of(got)
    assert r.readable is False and r.is_empty_account is False
    assert r.why


# =====================================================================
# 2. the place ACK and order states   -- defect #1
# =====================================================================
def test_order_state_open_is_resting_not_a_non_fill():
    o = OrderState.of({"status": "open", "filledQuantity": "0",
                       "orderId": 145828836710194348})
    assert o.state == RESTING and o.terminal is False
    assert "not NOT_FILLED" in o.why
    assert o.order_id == "145828836710194348"


def test_order_state_covers_every_terminal_and_nonterminal_status():
    for st in ("filled",):
        assert OrderState.of({"status": st, "filledQuantity": "1"}
                             ).state == FILLED
    for st in ("cancelled", "canceled", "expired", "rejected", "closed"):
        assert OrderState.of({"status": st, "filledQuantity": "0"}
                             ).state == CANCELLED
    for st in ("open", "accepted", "pending", "partially_filled"):
        assert OrderState.of({"status": st, "filledQuantity": "0"}
                             ).state == RESTING
    # a dead status WITH a fill is a fill, not a cancellation
    assert OrderState.of({"status": "closed", "filledQuantity": "1"}
                         ).state == FILLED


@pytest.mark.parametrize("body", [
    {}, {"status": "weird"}, {"status": None},
    {"status": "open", "filledQuantity": "not a number"},
    None, [], "x",
])
def test_order_state_malformed_is_unresolved(body):
    o = OrderState.of(body)
    assert o.state == UNRESOLVED and o.terminal is False and o.why


def test_the_real_ci15m_1_ack_normalises_to_resting():
    """VERBATIM from the venue, order 145828836710194348 -- the ack CI15M-1
    recorded as NOT_FILLED. It later filled."""
    o = OrderState.of({
        "clientOrderId": "503c9952-54a0-4ce4-8223-7ac26917fc8c",
        "createdAt": "2026-10-03T17:04:52.117Z", "filledQuantity": "0",
        "orderId": 145828836710194348, "orderType": "limit",
        "outcome": "yes", "price": "0.7", "quantity": "1",
        "remainingQuantity": "1", "side": "buy", "status": "open",
        "timeInForce": "maker-or-cancel"})
    assert o.state == RESTING, "this is the defect that cost $2.86"


def test_the_real_ci15m_1_cancelled_ack_normalises_to_cancelled():
    o = OrderState.of({"cancelledAt": "2026-10-03T17:04:37.503Z",
                       "filledQuantity": "0", "orderId": 145828836710181412,
                       "price": "0.7", "status": "cancelled"})
    assert o.state == CANCELLED and o.terminal is True


def test_place_ack_contract_matches_the_transport_source():
    src = inspect.getsource(transport._post)
    for key in ('out["body"]', 'out["ok"]', 'out["classification"]'):
        assert key in src, key
    assert '"classification": AMBIGUOUS' in src


def test_place_ack_successful_and_nested_order_object():
    ack = PlaceAck.of({"ok": True, "status": 201, "classification":
                       "ACCEPTED",
                       "body": {"order": {"status": "filled",
                                          "filledQuantity": "1",
                                          "price": "0.70", "outcome": "yes",
                                          "orderId": 1}}})
    assert ack.ok and ack.order.state == FILLED and ack.order.filled == D("1")


def test_place_ack_unwrapped_order_object():
    ack = PlaceAck.of({"ok": True, "status": 201, "body":
                       {"status": "open", "filledQuantity": "0",
                        "orderId": 2}})
    assert ack.ok and ack.order.state == RESTING


@pytest.mark.parametrize("res,amb", [
    ({"ok": False, "classification": "AMBIGUOUS", "status": None,
      "body": None}, True),
    ({"ok": False, "classification": "REJECTED", "status": 400,
      "body": {"reason": "nope"}}, False),
    ({"ok": True, "status": 201, "body": None}, False),
    ({"ok": True, "status": 201, "body": "not json"}, False),
    (None, False),
])
def test_place_ack_unsuccessful_and_malformed(res, amb):
    ack = PlaceAck.of(res)
    assert ack.ok is False and ack.why
    assert ack.ambiguous is amb


# =====================================================================
# 3. active orders   -- defect #2
# =====================================================================
def test_active_orders_contract_matches_the_producer_source():
    src = inspect.getsource(transport._orders_of)
    for k in ('"orders"', '"results"', '"data"'):
        assert k in src, k
    assert "return []" in src, (
        "_orders_of returns [] for an unrecognised shape; the adapter must "
        "therefore require the shape to be RECOGNISED")


def test_active_orders_successful_populated_and_empty():
    full = ActiveOrders.of({"ok": True, "status": 200, "body": {"orders": [
        {"orderId": 1, "status": "open", "filledQuantity": "0"}]}})
    assert full.readable and full.count == 1 and full.any_unresolved
    empty = ActiveOrders.of({"ok": True, "status": 200,
                             "body": {"orders": []}})
    assert empty.readable and empty.count == 0 and not empty.any_unresolved
    as_list = ActiveOrders.of({"ok": True, "status": 200, "body": []})
    assert as_list.readable and as_list.count == 0


def test_active_orders_unsuccessful_is_unknown_not_empty():
    r = ActiveOrders.of({"ok": False, "status": 500,
                         "classification": "SERVER_ERROR",
                         "body": {"error": "boom"}})
    assert r.readable is False and r.count is None
    assert "never an empty one" in r.why


def test_active_orders_unrecognised_shape_is_unknown_not_empty():
    """The exact hole four resting orders went through: a body we cannot
    find the list in normalises to [] under `_orders_of`."""
    r = ActiveOrders.of({"ok": True, "status": 200, "body":
                         {"activeOrders": [{"orderId": 1,
                                            "status": "open"}]}})
    assert r.readable is False and r.count is None
    assert "not recognised" in r.why
    # and the producer really would have said "empty" here
    assert transport._orders_of({"body": {"activeOrders": [{"orderId": 1}]}}) \
        == []


@pytest.mark.parametrize("res", [None, [], "x", {"body": {"orders": []}}])
def test_active_orders_malformed_fails_closed(res):
    assert ActiveOrders.of(res).readable is False


# =====================================================================
# 4. the executable ladder
# =====================================================================
def test_ladder_contract_matches_the_producer_source():
    src = inspect.getsource(metadata.contract_depth)
    for k in ('"bids"', '"asks"', '"best"', '"best_qty"', '"readable"'):
        assert k in src, k


def test_ladder_successful_and_the_down_synthesis():
    b = ExecutableBook.of({"readable": True, "snapshot_age_s": 0.2,
                           "bids": {"best": "0.60", "best_qty": "500"},
                           "asks": {"best": "0.70", "best_qty": "400"}})
    assert b.readable and b.bid == D("0.60") and b.ask == D("0.70")
    assert b.bid_size == D("500") and b.ask_size == D("400")
    assert b.acquisition("UP") == D("0.70")
    assert b.acquisition("DOWN") == D("0.40")       # 1 - bid


@pytest.mark.parametrize("dep", [
    {"readable": False, "why": "503"},
    {"readable": True, "bids": {"best": None}, "asks": {"best": "0.70"}},
    {"readable": True, "bids": {"best": "0.60"}, "asks": {"best": None}},
    {"readable": True, "bids": {"best": "0.90"}, "asks": {"best": "0.80"}},
    {"readable": True},
    None, [], "x",
])
def test_ladder_unreadable_one_sided_and_crossed_fail_closed(dep):
    b = ExecutableBook.of(dep)
    assert b.readable is False and b.why
    assert b.acquisition("UP") is None


def test_an_unknown_side_is_a_programming_error_not_a_silent_none():
    b = ExecutableBook.of({"readable": True,
                           "bids": {"best": "0.60"},
                           "asks": {"best": "0.70"}})
    with pytest.raises(ValueError):
        b.acquisition("SIDEWAYS")


# =====================================================================
# 5. order history / terminal polling
# =====================================================================
def test_order_history_contract_matches_the_producer_source():
    src = inspect.getsource(metadata.order_history)
    for k in ('"readable"', '"per_status"', '"total_rows"', '"verdict"'):
        assert k in src, k
    assert 'entry["orders"]' in src and "include_rows" in src, (
        "`orders` is present only when include_rows=True; the adapter must "
        "detect its absence rather than read it as empty")


def test_order_history_successful_populated_and_findable():
    h = OrderHistory.of({"readable": True, "verdict": "ORDERS_FOUND",
                         "total_rows": 2, "per_status": [
                             {"status_filter": "filled", "orders": [
                                 {"orderId": 9, "status": "filled",
                                  "filledQuantity": "1", "price": "0.70",
                                  "outcome": "yes"}]},
                             {"status_filter": "cancelled", "orders": [
                                 {"orderId": 8, "status": "cancelled",
                                  "filledQuantity": "0"}]}]})
    assert h.readable and h.total_rows == 2
    assert h.find(9).state == FILLED and h.find(9).price == D("0.70")
    assert h.find(8).state == CANCELLED
    assert h.find(12345) is None


def test_order_history_successful_empty_is_a_fact():
    h = OrderHistory.of({"readable": True,
                         "verdict": "NO_ORDERS_ON_RECORD",
                         "total_rows": 0, "per_status": [
                             {"status_filter": "filled", "orders": []}]})
    assert h.readable and h.total_rows == 0 and h.orders == ()


def test_order_history_rows_reported_but_absent_fails_closed():
    """THE PRODUCER'S OWN DOCUMENTED TRAP: without include_rows=True the
    `orders` key is absent, and reading that as empty is what makes a
    restart assume flat when it is not."""
    h = OrderHistory.of({"readable": True, "verdict": "ORDERS_FOUND",
                         "total_rows": 3,
                         "per_status": [{"status_filter": "filled",
                                         "sample": {"orderId": 1}}]})
    assert h.readable is False
    assert "include_rows" in h.why


@pytest.mark.parametrize("hist", [
    {"readable": False, "verdict": "UNREADABLE",
     "unreadable_statuses": ["filled"], "total_rows": 0},
    {"readable": False, "verdict": "NO_CREDENTIALS"},
    {"readable": True, "per_status": "not a list", "total_rows": 0},
    None, [], "x",
])
def test_order_history_unreadable_and_malformed_fail_closed(hist):
    h = OrderHistory.of(hist)
    assert h.readable is False and h.why


# =====================================================================
# 6. discovery
# =====================================================================
def test_discovery_normalises_the_public_events_listing():
    got = markets_of([
        {"ticker": "BTC15M2610031845",
         "contracts": [{"strike": {"value": "84000",
                                   "availableAt": "2026-10-03T18:30:00Z"},
                        "expiryDate": "2026-10-03T18:45:00.000Z"}]},
        {"ticker": "BTC15M2610031900", "contracts": [
            {"strike": {}, "expiryDate": "2026-10-03T19:00:00.000Z"}]},
    ])
    assert [m.ticker for m in got] == ["BTC15M2610031845",
                                       "BTC15M2610031900"]
    assert got[0].symbol == "GEMI-BTC15M2610031845-UP"
    assert got[0].strike_published is True
    assert got[0].expiry_ts and got[0].strike_available_at
    assert got[1].strike_published is False, "no strike value published"


@pytest.mark.parametrize("events", [
    None, {}, "x", [None, 7, "y"], [{"no_ticker": 1}],
    [{"ticker": "T", "contracts": "not a list"}],
    [{"ticker": "T", "contracts": [{"expiryDate": "not a date"}]}],
])
def test_discovery_malformed_yields_nothing_rather_than_guessing(events):
    got = markets_of(events)
    assert all(isinstance(m, DiscoveredMarket) for m in got)
    for m in got:
        assert m.expiry_ts is None or isinstance(m.expiry_ts, float)
