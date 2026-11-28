"""Recovering a foreign order's remaining quantity, read-only and fail-closed.

THE BLOCKER THIS CLEARS, measured 2026-11-19T02:42:55Z on l5-pmus-live:

    account_exposure: {ok: false,
                       reason: "FOREIGN_ORDER_UNREADABLE:DT0VCZR013ZK"}
    foreign_orders:   {"DT0VCZR013ZK": {origin: "UNKNOWN",
                                        market: "aec-nba-mil-mia-2026-11-18",
                                        remaining: ""}}

`/v1/orders/open` returned that row with ALL FIVE accepted quantity fields
absent. BTC5m's exposure reader therefore returned UNKNOWN and `_submit`
refused every order with ACCOUNT_STATE_UNKNOWN -- correctly, but
permanently: nothing was going to change that row, so no BTC5m window
could ever open while the owner's hand-placed order rested.

`/v1/order/{id}` is the authoritative single-order read and does carry
`quantity`, `cumQuantity` and `leavesQuantity`. It is a GET. The fix asks
it, for FOREIGN orders only, and only when the list left the quantity
unreadable.

WHAT IT CANNOT DO, which is most of this file:

  - it never reads a missing quantity as zero;
  - it never claims a foreign order as ours;
  - it never submits, cancels or amends anything;
  - a venue answer that contradicts itself is refused, not averaged --
    self-contradiction is worse evidence than silence;
  - every failure returns the SAME refusal the code had before, so the
    worst case of the fix is the status quo.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as X

FOREIGN_VID = "DT0VCZR013ZK"
MARKET = "aec-nba-mil-mia-2026-11-18"


class Venue:
    """Open-orders row with NO quantity, plus a per-order answer."""

    def __init__(self, order_row=None, *, raises=False, rows=None,
                 not_a_row=False):
        self._order_row = order_row or {}
        self._raises = raises
        self._rows = rows
        self._not_a_row = not_a_row
        self.asked = []
        self.submitted = []
        self.cancelled = []

    def open_orders(self):
        if self._rows is not None:
            return self._rows
        return [{"id": FOREIGN_VID, "marketSlug": MARKET}]

    def positions(self):
        return []

    def order(self, oid):
        self.asked.append(oid)
        if self._raises:
            raise RuntimeError("venue read failed")
        if self._not_a_row:
            return "not a mapping"
        return dict(self._order_row)

    #: present so a test can assert they are never called
    def submit(self, *a, **k):
        self.submitted.append((a, k))
        raise AssertionError("exposure reading must never submit")

    def cancel(self, *a, **k):
        self.cancelled.append((a, k))
        raise AssertionError("exposure reading must never cancel")


def executor(venue, *, our_vids=(), owns=()):
    ex = X.Btc5mExecutor.__new__(X.Btc5mExecutor)
    ex._ledger = {"orders": {("c%d" % i): {"venue_order_id": v}
                             for i, v in enumerate(our_vids)},
                  "settled": {}, "inventory": {}}
    ex.transport = venue
    ex.owns = lambda c: c in owns
    ex._own_exposure = lambda: {"resting": D(0), "filled": D(0)}
    ex.inventory = lambda m: {X.C.UP: {"qty": D(0), "cost": D(0)},
                              X.C.DOWN: {"qty": D(0), "cost": D(0)}}
    return ex


def read(venue, **kw):
    return X.Btc5mExecutor.account_exposure(executor(venue, **kw))


# ------------------------------------------- the valid fallback

def test_leaves_quantity_recovers_the_bound():
    v = Venue({"leavesQuantity": "5"})
    r = read(v)
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("5")
    assert r["other_usd"] == D("5.00")          # foreign $1 a contract
    assert v.asked == [FOREIGN_VID]


def test_quantity_minus_cum_recovers_the_bound_when_leaves_is_absent():
    r = read(Venue({"quantity": "5", "cumQuantity": "0"}))
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("5")


def test_a_partial_fill_charges_only_what_is_still_working():
    """5 ordered, 2 filled -> 3 still exposed, not 5 and not 0."""
    r = read(Venue({"quantity": "5", "cumQuantity": "2"}))
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("3")
    assert r["other_usd"] == D("3.00")


def test_leaves_and_derived_agreeing_is_accepted():
    r = read(Venue({"leavesQuantity": "3", "quantity": "5",
                    "cumQuantity": "2"}))
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("3")


def test_a_fully_filled_order_contributes_nothing_but_is_not_UNKNOWN():
    """leaves 0 is a READ zero, not a missing one."""
    r = read(Venue({"quantity": "5", "cumQuantity": "5"}))
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("0")


def test_the_recovery_is_recorded_for_audit():
    r = read(Venue({"leavesQuantity": "5"}))
    rec = r["foreign_qty_recovered"]
    assert rec[0]["venue_order_id"] == FOREIGN_VID
    assert rec[0]["remaining"] == "5"
    assert rec[0]["basis"] == "leavesQuantity"
    assert "/v1/order/{id}" in rec[0]["source"]


# ------------------------------------- missing fields stay UNKNOWN

@pytest.mark.parametrize("row,why", [
    ({}, "no quantity at all"),
    ({"state": "ORDER_STATE_OPEN"}, "state only"),
    ({"quantity": "5"}, "quantity without cum"),
    ({"cumQuantity": "2"}, "cum without quantity"),
])
def test_a_missing_quantity_is_never_read_as_zero(row, why):
    r = read(Venue(row))
    assert r["unknown"] is True, why
    assert r["unknown_reason"] == "OPEN_ORDER_QUANTITY_UNREADABLE"
    assert r["other_open_order_contracts"] == D("0")
    assert r["foreign_qty_lookup_failed"]["venue_order_id"] == FOREIGN_VID


# --------------------------------- inconsistent answers stay UNKNOWN

@pytest.mark.parametrize("row,label", [
    ({"leavesQuantity": "5", "quantity": "5", "cumQuantity": "2"},
     "leaves disagrees with quantity-cum"),
    ({"quantity": "2", "cumQuantity": "5"}, "cum exceeds quantity"),
    ({"quantity": "-5", "cumQuantity": "0"}, "negative quantity"),
    ({"quantity": "5", "cumQuantity": "-1"}, "negative cum"),
    ({"leavesQuantity": "-1"}, "negative leaves"),
])
def test_a_self_contradictory_venue_answer_is_refused(row, label):
    """Self-contradiction is worse evidence than silence."""
    r = read(Venue(row))
    assert r["unknown"] is True, label
    assert r["unknown_reason"] == "OPEN_ORDER_QUANTITY_UNREADABLE"


@pytest.mark.parametrize("row", [
    {"leavesQuantity": "abc"}, {"leavesQuantity": None},
    {"quantity": "x", "cumQuantity": "0"},
])
def test_an_unparseable_quantity_is_refused(row):
    assert read(Venue(row))["unknown"] is True


# ------------------------------------- venue read failures

def test_a_raising_venue_read_stays_UNKNOWN():
    r = read(Venue({"leavesQuantity": "5"}, raises=True))
    assert r["unknown"] is True
    assert r["unknown_reason"] == "OPEN_ORDER_QUANTITY_UNREADABLE"
    assert "ORDER_READ_FAILED" in r["foreign_qty_lookup_failed"]["why"]


def test_a_non_mapping_answer_stays_UNKNOWN():
    r = read(Venue(not_a_row=True))
    assert r["unknown"] is True
    assert r["foreign_qty_lookup_failed"]["why"] == "ORDER_READ_NOT_A_ROW"


def test_an_empty_answer_stays_UNKNOWN():
    r = read(Venue({}))
    assert r["unknown"] is True


def test_a_transport_without_the_read_stays_UNKNOWN():
    class NoOrderRead:
        def open_orders(self):
            return [{"id": FOREIGN_VID, "marketSlug": MARKET}]

        def positions(self):
            return []
    r = read(NoOrderRead())
    assert r["unknown"] is True
    assert r["foreign_qty_lookup_failed"]["why"] == "NO_ORDER_READ_AVAILABLE"


def test_a_row_with_no_id_cannot_be_looked_up_and_stays_UNKNOWN():
    r = read(Venue(rows=[{"marketSlug": MARKET}]))
    assert r["unknown"] is True
    assert r["foreign_qty_lookup_failed"]["why"] == "NO_VENUE_ORDER_ID"


# ------------------------------------- it asks only when it must

def test_a_readable_row_triggers_no_lookup_at_all():
    v = Venue(rows=[{"id": FOREIGN_VID, "marketSlug": MARKET,
                     "remainingQuantity": "4"}])
    r = read(v)
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("4")
    assert v.asked == [], "no venue read is needed when the list carries it"


def test_our_own_order_is_never_looked_up_or_double_counted():
    """Already counted exactly by _own_exposure; and asking about it would
    be asking the venue about ownership we already know."""
    v = Venue(rows=[{"id": "V_OURS", "marketSlug": MARKET}])
    r = read(v, our_vids=("V_OURS",))
    assert r["unknown"] is False
    assert r["other_open_order_contracts"] == D("0")
    assert v.asked == []


def test_the_lookup_count_is_bounded_per_read():
    """A venue that omits every quantity must not become a request storm
    on the submit path."""
    rows = [{"id": "F%d" % i, "marketSlug": MARKET} for i in range(40)]
    v = Venue({}, rows=rows)
    r = read(v)
    assert r["unknown"] is True
    assert len(v.asked) <= X.Btc5mExecutor.FOREIGN_QTY_LOOKUP_MAX


def test_the_budget_resets_between_reads():
    """Cumulative it would be a slow permanent halt, not a bound."""
    v = Venue({"leavesQuantity": "1"})
    ex = executor(v)
    for _ in range(20):
        assert X.Btc5mExecutor.account_exposure(ex)["unknown"] is False
    assert len(v.asked) == 20


# ------------------------------------- it changes nothing else

def test_reading_exposure_never_submits_or_cancels():
    v = Venue({"leavesQuantity": "5"})
    read(v)
    assert v.submitted == [] and v.cancelled == []


def test_it_claims_no_ownership():
    """The recovered quantity is charged at the FOREIGN basis, so it lands
    in other_usd and never in own_usd."""
    r = read(Venue({"leavesQuantity": "5"}))
    assert r["own_usd"] == D("0")
    assert r["other_usd"] == D("5.00")
    assert r["other_basis"] == X.FOREIGN_BASIS


def test_the_helper_touches_no_mutating_route():
    """THE CODE is scanned, not the prose. The docstring itself says the
    helper "submits nothing, cancels nothing", so grepping raw source
    matched its own explanation -- the fifth time a doc comment has
    tripped a string-matching test in this project. Dropping the
    docstring first is the honest check."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(X.Btc5mExecutor._foreign_remaining)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]                 # drop the docstring
    code = ast.unparse(fn)
    for forbidden in ("submit", "cancel", "amend", "replace", "POST"):
        assert forbidden not in code, forbidden
    assert "t.order(" in code


def test_every_other_refusal_still_fires():
    class Broken:
        def open_orders(self):
            raise RuntimeError("down")

        def positions(self):
            return []
    r = read(Broken())
    assert r["unknown"] is True
    assert r["unknown_reason"].startswith("ACCOUNT_READ_FAILED")
    r2 = read(Venue(rows="not a list"))
    assert r2["unknown"] is True
