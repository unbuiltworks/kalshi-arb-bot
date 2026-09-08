"""If the venue says we own X, our risk engine must know X
(OWNER GO #167 5584132560 item 2). These tests are written from the
aec-setkameua-vervas-kohboh-2026-09-08 incident, not from theory."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_fill_ledger as fl


def _order(cum, state="ORDER_STATE_OPEN"):
    return {"cumQuantity": str(cum), "state": state}


def test_the_vervas_kohboh_sequence_is_booked_in_full():
    """Three partial fills of ONE resting order inside three seconds --
    0.20 at 05:57:09.23, 4.34 at 05:57:10.97, 4.54 at 05:57:12.14 -- must
    end with every unit booked exactly once, whatever order the
    observations arrive in."""
    led = fl.FillLedger()
    led.track("CC1E3Y7TPQ45")
    assert led.ingest("CC1E3Y7TPQ45", _order("0.20")) == D("0.20")
    assert led.ingest("CC1E3Y7TPQ45", _order("4.54")) == D("4.34")
    assert led.ingest("CC1E3Y7TPQ45", _order("9.08")) == D("4.54")
    assert led.total_booked() == D("9.08")
    assert [p["delta"] for p in led.provenance] == ["0.20", "4.34", "4.54"]


def test_re_reading_the_same_order_never_double_counts():
    """Reconnect, replay and duplicate delivery all re-present the same
    cumulative quantity. Booking it twice would inflate inventory."""
    led = fl.FillLedger()
    assert led.ingest("o", _order("4.34")) == D("4.34")
    for _ in range(5):
        assert led.ingest("o", _order("4.34")) == D("0")
    assert led.total_booked() == D("4.34")


def test_a_cumulative_quantity_that_goes_backwards_fails_closed():
    """A venue record that shrinks contradicts what we have already acted
    on. It is never silently accepted and never un-books inventory."""
    led = fl.FillLedger()
    led.ingest("o", _order("4.54"))
    assert led.ingest("o", _order("0.20")) == D("0")
    assert led.total_booked() == D("4.54")
    assert led.contradictions and led.contradictions[0]["order_id"] == "o"
    assert led.contradictions[0]["venue_cum"] == "0.20"


def test_an_order_with_no_quantity_field_books_nothing():
    """An unreadable order is not an order with zero fills."""
    led = fl.FillLedger()
    assert led.ingest("o", {"state": "ORDER_STATE_OPEN"}) == D("0")
    assert led.provenance == []


def test_only_the_venue_closes_an_order():
    """The live defect: we treated 'we asked for a cancel' as 'the order is
    over'. An order stays OPEN until the venue reports a terminal state,
    and a partially filled open order is NOT terminal."""
    led = fl.FillLedger()
    led.track("o")
    assert led.close_if_terminal("o", _order("0.20", "ORDER_STATE_OPEN")) is False
    assert led.close_if_terminal(
        "o", _order("0.20", "ORDER_STATE_PARTIALLY_FILLED")) is False
    assert led.open_orders() == ["o"]
    assert led.close_if_terminal("o", _order("4.54", "ORDER_STATE_CANCELLED")) is True
    assert led.open_orders() == []


def test_reconcile_recovers_the_fill_that_landed_after_we_stopped_watching():
    """The incident itself: CC1E3Y7TPQ45 was left for dead at 05:57:05 with
    nothing filled, and the venue executed 4.34 against it at 05:57:10.97.
    A later reconcile must find those units."""
    led = fl.FillLedger()
    led.track("CC1E3Y7TPQ45")
    led.ingest("CC1E3Y7TPQ45", _order("0"))
    out = led.reconcile(lambda oid: _order("4.34", "ORDER_STATE_CANCELLED"))
    assert out["recovered_fills"] == {"CC1E3Y7TPQ45": "4.34"}
    assert out["recovered_qty"] == "4.34"
    assert out["closed"] == ["CC1E3Y7TPQ45"] and out["still_open"] == []
    assert led.total_booked() == D("4.34")


def test_an_order_the_venue_will_not_serve_stays_open():
    """Unreadable is never 'finished': a finished order is exactly what we
    would need proof of."""
    led = fl.FillLedger()
    led.track("o")

    def boom(_oid):
        raise RuntimeError("venue 503")

    out = led.reconcile(boom)
    assert out["unreadable"] == ["o"] and out["still_open"] == ["o"]
    assert out["recovered_qty"] == "0"


def test_reconcile_is_safe_to_run_repeatedly():
    led = fl.FillLedger()
    led.track("o")
    reads = [_order("4.54", "ORDER_STATE_OPEN")] * 3
    totals = [led.reconcile(lambda oid, r=r: r)["recovered_qty"] for r in reads]
    assert totals == ["4.54", "0", "0"]
    assert led.total_booked() == D("4.54")


@pytest.mark.parametrize("venue,ledger,agrees", [
    ("13.8200", "9.48", False),      # the incident
    ("9.48", "9.48", True),
    ("9.4800", "9.48", True),
    (None, "9.48", None),            # the venue could not be asked
    ("9.48", None, None),
])
def test_venue_against_ledger(venue, ledger, agrees):
    out = fl.venue_vs_ledger(venue, ledger)
    assert out["agrees"] is agrees


def test_unknown_is_not_agreement():
    """The whole failure mode: a flat/locked claim made without venue
    evidence. Missing evidence must block the claim exactly as a
    disagreement does."""
    assert fl.may_assert_flat(fl.venue_vs_ledger("9.48", "9.48")) is True
    assert fl.may_assert_flat(fl.venue_vs_ledger("13.82", "9.48")) is False
    assert fl.may_assert_flat(fl.venue_vs_ledger(None, "9.48")) is False
    assert fl.may_assert_flat({}) is False
