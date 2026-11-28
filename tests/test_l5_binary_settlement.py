"""Only an authoritative BINARY resolution may be booked as realized cash.

STAGE A, 2026-10-13. The 2230z BTC15m window acquired 2.90 UP shares for
$0.590000 and settled worthless. The venue's expired book still carried a
price of 0.01, `sweep_settlements` handed that to `retire_settled_market` as
`yes_value`, and the old guard admitted it because 0.01 is inside [0, 1]:

    redeem = 2.90 * 0.01 = 0.029          <- PHANTOM CASH. Nobody paid it.
    pnl    = 0.029 - 0.590 = -0.561000    <- booked as realized

True realized was -$0.590000. A binary contract pays exactly 0 or exactly 1;
every other value is a mark, and a mark is never settlement cash.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_pmus_transport as T


@pytest.fixture()
def store():
    with tempfile.TemporaryDirectory() as d:
        yield LX.InventoryStore(os.path.join(d, "inv.json"))


def stage_a_book(store, market="btc-15m-2230z"):
    """Stage A's book exactly: 2.90 UP, $0.590000, no DOWN leg."""
    store.state["positions"]["%s|UP" % market] = "2.90"
    store.state.setdefault("leg_cost", {})["%s|UP" % market] = "0.590000"
    store.state["committed"][market] = "0.590000"
    return market


def resolution(market, value, source="VENUE_SETTLEMENT_PX"):
    return {"source": source, "market": market, "yes_value": str(value)}


# ------------------------------------------------- 1. the Stage A defect

def test_the_stage_a_phantom_credit_is_refused(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D("0.01"))
    assert out["retired"] is False
    assert out["reason"] == "SETTLEMENT_NOT_BINARY"
    assert out["yes_value"] == "0.01"
    # nothing moved: not the book, not the cap, not realized.
    assert store.position(m, "UP") == D("2.90")
    assert store.committed().get(m) == D("0.590000")
    assert store.realized_today() == D(0)
    assert m not in (store.state.get("retired_markets") or {})


@pytest.mark.parametrize("px", ["0.01", "0.99", "0.5", "0.0001", "-1", "2"])
def test_no_value_between_or_beyond_the_two_is_a_settlement(store, px):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D(px))
    assert out["retired"] is False
    assert out["reason"] == "SETTLEMENT_NOT_BINARY"


def test_the_old_arithmetic_is_what_it_would_have_produced():
    """Pinned so the number in the report stays traceable: the refusal above
    is what stands between this and the ledger."""
    assert D("2.90") * D("0.01") == D("0.0290")
    assert D("0.0290") - D("0.590000") == D("-0.561000")


# --------------------------------------------- 2. a legitimate zero payout

def test_a_legitimate_zero_payout_books_the_whole_loss(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D("0.0000"),
                                      resolution=resolution(m, "0.0000"))
    assert out["retired"] is True
    assert D(out["redemption_total"]) == D(0)
    assert D(out["settlement_pnl"]) == D("-0.590000")
    assert store.realized_today() == D("-0.590000")
    assert out["resolution_source"] == "VENUE_SETTLEMENT_PX"
    assert store.committed().get(m) in (None, D(0))


# ------------------------------------------------- 3. a winning payout

def test_a_winning_payout_redeems_at_one(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D("1.0000"),
                                      resolution=resolution(m, "1.0000"))
    assert out["retired"] is True
    assert D(out["redemption_total"]) == D("2.90")
    assert D(out["settlement_pnl"]) == D("2.310000")
    assert store.realized_today() == D("2.310000")


# ------------------------- 4. missing, unreadable or non-binary resolution

def test_a_missing_settlement_value_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=None)
    assert out["retired"] is False
    assert out["reason"] == "SETTLEMENT_ABSENT"
    assert store.realized_today() == D(0)


def test_an_unreadable_settlement_value_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value="pending")
    assert out["retired"] is False
    assert out["reason"] == "SETTLEMENT_UNREADABLE"


def test_a_resolution_for_another_market_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(
        m, yes_value=D(1), resolution=resolution("btc-15m-2245z", "1"))
    assert out["retired"] is False
    assert out["reason"] == "RESOLUTION_MARKET_MISMATCH"
    assert store.position(m, "UP") == D("2.90")


def test_a_resolution_from_a_non_authoritative_source_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(
        m, yes_value=D(1),
        resolution=resolution(m, "1", source="LAST_BOOK_PRICE"))
    assert out["retired"] is False
    assert out["reason"] == "RESOLUTION_NOT_AUTHORITATIVE"


def test_a_resolution_that_disagrees_with_the_value_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D(1),
                                      resolution=resolution(m, "0"))
    assert out["retired"] is False
    assert out["reason"] == "RESOLUTION_VALUE_DISAGREES"


def test_an_unnamed_resolution_settles_nothing(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(m, yes_value=D(1),
                                      resolution={"source": "POSITION_RESOLUTION"})
    assert out["retired"] is False
    assert out["reason"] == "RESOLUTION_MARKET_UNNAMED"


def test_position_resolution_is_authoritative(store):
    m = stage_a_book(store)
    out = store.retire_settled_market(
        m, yes_value=D(0),
        resolution=resolution(m, "0", source="POSITION_RESOLUTION"))
    assert out["retired"] is True
    assert out["resolution_source"] == "POSITION_RESOLUTION"


# ------------------------------------------- the transport's own refusal

class _Doc:
    """PmusTransport.settlement with only the HTTP read stubbed."""

    def __init__(self, doc):
        self.doc = doc

    def __call__(self, path):
        return self.doc


def _settlement(doc, slug="btc-15m-2230z"):
    tp = T.PmusTransport.__new__(T.PmusTransport)
    tp._get = _Doc(doc)
    return T.PmusTransport.settlement(tp, slug)


def test_the_transport_calls_a_non_binary_price_not_terminal():
    rep = _settlement({"marketData": {"state": "MARKET_STATE_EXPIRED",
                                      "settlementPx": {"value": "0.0100"}}})
    assert rep["terminal"] is False
    assert rep["not_terminal_reason"] == "SETTLEMENT_NOT_BINARY"


def test_the_transport_passes_a_binary_price_with_its_source():
    rep = _settlement({"marketData": {"state": "MARKET_STATE_EXPIRED",
                                      "settlementPx": {"value": "0.0000"},
                                      "slug": "btc-15m-2230z"}})
    assert rep["terminal"] is True
    assert rep["settlement_px"] == D("0.0000")
    assert rep["resolution"]["source"] == "VENUE_SETTLEMENT_PX"
    assert rep["resolution"]["market"] == "btc-15m-2230z"


def test_the_transport_refuses_a_document_about_another_market():
    rep = _settlement({"marketData": {"state": "MARKET_STATE_EXPIRED",
                                      "settlementPx": {"value": "1.0000"},
                                      "slug": "btc-15m-2245z"}})
    assert rep["terminal"] is False
    assert rep["not_terminal_reason"] == "RESOLUTION_MARKET_MISMATCH"


def test_a_closed_market_is_still_not_terminal():
    rep = _settlement({"marketData": {"state": "MARKET_STATE_CLOSED",
                                      "settlementPx": {"value": "1.0000"}}})
    assert rep["terminal"] is False
    assert rep["not_terminal_reason"] == "STATE_NOT_TERMINAL"
