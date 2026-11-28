"""Owner 2026-11-22: every fill and position needs durable ownership
attribution, so the account-wide ledger stops booking this bot's positions
at $1 a contract. The bot publishes its own filled orders, by venue order
id, in the shape the account-wide ledger already reads from the BTC5m
lane; that ledger checks each figure against the venue before using it."""
import json
import urllib.request
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    monkeypatch.delenv(M.SUBMISSION_LOCK_ENV, raising=False)
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e.b.m("aec-x")
    e.step_market("aec-x")
    return e, v


def by_id(doc):
    return {o["venue_order_id"]: o for o in doc["orders"]}


def test_unfilled_orders_are_not_listed(eng):
    e, v = eng
    doc = M.fills_doc(e.b)
    assert doc["readable"] is True and doc["strategy"] == "SPORTS"
    assert doc["orders"] == [] and doc["as_of_utc"]
    assert len(e.b.d["markets"]["aec-x"]["orders"]) == 2


def test_a_fill_is_listed_with_the_venue_reported_cost(eng):
    e, v = eng
    orders = e.b.d["markets"]["aec-x"]["orders"]
    oid, o = next((i, x) for i, x in orders.items() if x["side"] == "YES")
    # the venue reports an average price better than the limit
    v.orders[oid]["filled"] = D("2")
    v.orders[oid]["px"] = D(o["px"]) - D("0.01")
    e.refresh_fills("aec-x")
    row = by_id(M.fills_doc(e.b))[oid]
    assert row["market"] == "aec-x" and row["side"] == "YES"
    assert D(row["filled_qty"]) == 2
    assert D(row["filled_cost"]) == 2 * (D(o["px"]) - D("0.01"))
    assert D(row["price"]) == D(o["px"]) - D("0.01")
    assert row["cost_from"] == "VENUE_REPORTED"
    assert D(row["limit_price"]) == D(o["px"])
    # the per-market cost the bot always kept agrees with the per-order one
    assert D(e.b.d["markets"]["aec-x"]["cost"]["YES"]) == D(row["filled_cost"])


def test_partial_fills_accumulate_cost(eng):
    e, v = eng
    orders = e.b.d["markets"]["aec-x"]["orders"]
    oid, o = next((i, x) for i, x in orders.items() if x["side"] == "NO")
    v.orders[oid]["filled"] = D("1")
    e.refresh_fills("aec-x")
    v.orders[oid]["filled"] = D("2")
    e.refresh_fills("aec-x")
    row = by_id(M.fills_doc(e.b))[oid]
    assert D(row["filled_qty"]) == 2
    assert D(row["filled_cost"]) == 2 * D(o["px"])
    assert row["side"] == "NO" and row["state"] == "OPEN"


def test_an_order_filled_before_this_field_existed_uses_its_limit(eng):
    """Orders already in the production book carry no per-order cost. They
    are published at quantity x limit and say so; the reader verifies."""
    e, v = eng
    orders = e.b.d["markets"]["aec-x"]["orders"]
    oid, o = next(iter(orders.items()))
    o["filled"], o["open"] = "2", False            # as an old book holds it
    assert "fill_cost" not in o
    row = by_id(M.fills_doc(e.b))[oid]
    assert row["cost_from"] == "LIMIT_PRICE" and row["state"] == "CLOSED"
    assert D(row["filled_cost"]) == 2 * D(o["px"])
    # ...and a later fill of such an order adds to that base
    o["qty"], o["filled"], o["open"] = "4", "2", True
    v.orders[oid]["qty"], v.orders[oid]["filled"] = D("4"), D("4")
    e.refresh_fills("aec-x")
    row = by_id(M.fills_doc(e.b))[oid]
    assert D(row["filled_cost"]) == 4 * D(o["px"])
    assert row["cost_from"] == "VENUE_REPORTED"


def test_the_feed_survives_a_restart(eng, tmp_path):
    e, v = eng
    oid = next(iter(e.b.d["markets"]["aec-x"]["orders"]))
    v.orders[oid]["filled"] = D("2")
    e.refresh_fills("aec-x")
    e.b.save()
    again = M.fills_doc(M.Book(tmp_path / "s.json"))
    assert by_id(again)[oid]["filled_qty"] == "2"


def test_an_unreadable_order_makes_the_whole_feed_unreadable(eng):
    e, v = eng
    oid, o = next(iter(e.b.d["markets"]["aec-x"]["orders"].items()))
    o["filled"], o["side"] = "2", "MAYBE"
    doc = M.fills_doc(e.b)
    assert doc["readable"] is False and doc["orders"] == []


def test_nothing_but_orders_is_published(eng):
    e, v = eng
    oid = next(iter(e.b.d["markets"]["aec-x"]["orders"]))
    v.orders[oid]["filled"] = D("2")
    e.refresh_fills("aec-x")
    doc = M.fills_doc(e.b)
    assert set(doc) == {"readable", "strategy", "lane", "as_of_utc",
                        "orders", "labels"}
    assert set(doc["orders"][0]) == {
        "venue_order_id", "market", "side", "price", "limit_price",
        "filled_qty", "filled_cost", "cost_from", "state"}


def test_it_is_served_beside_the_registry(eng, monkeypatch):
    e, v = eng
    oid = next(iter(e.b.d["markets"]["aec-x"]["orders"]))
    v.orders[oid]["filled"] = D("2")
    e.refresh_fills("aec-x")
    monkeypatch.delenv("RAILWAY_PRIVATE_DOMAIN", raising=False)
    port = 18233
    M.serve_registry(e.b, port)
    with urllib.request.urlopen(
            "http://localhost:%d/%s" % (port, M.FILLS_KEY), timeout=3) as r:
        doc = json.loads(r.read())
    assert doc["strategy"] == "SPORTS" and by_id(doc)[oid]["filled_qty"] == "2"
    with urllib.request.urlopen(
            "http://localhost:%d/%s" % (port, M.REGISTRY_KEY), timeout=3) as r:
        assert json.loads(r.read())["lane"] == M.LANE
