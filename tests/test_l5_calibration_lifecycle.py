"""The money path's calibration lifecycle, at the one idempotent choke point.

WHY THESE EXIST. `pre_submit` was the only recorder call the money path ever
made, so `terminal_state` could never be PRESENT and every one of the 10,741
recorded rows read `venue_actual_status: PENDING`. A genuine resting order
would have produced an unusable record even after it filled -- the one
measurement the maker-only question depends on.

`apply_order_progress` is now where `on_fill`, `on_fees_actual` and the
venue-confirmed `on_terminal` fire. It is the right place because it is the
ONLY idempotent choke point for a venue fill, reached by four call sites, and
because the hooks then inherit its idempotency: they sit inside `delta > 0`,
so a repeated poll of the same cumQuantity records nothing, exactly as it
books nothing.

A MODELLED OBSERVATION IS NEVER PROMOTED TO A VENUE-CONFIRMED FILL. The
shadow path still passes venue_confirmed=False, and these tests assert that
the only route to PRESENT is the venue saying terminal.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_fill_calibration as CAL
from src import l5_live_execution as E
from src import l5_maker_fill_model as FM

MARKET = "cpc-btc-updown-15m-2026-10-26-1545z"
COID = "l5f-lifecycle-test"


class Spy:
    """Records hook calls. Deliberately not the real recorder: these tests
    are about WHAT the money path calls, not about what the recorder does
    with it."""

    def __init__(self, raise_on=None):
        self.calls = []
        self.raise_on = raise_on

    def _rec(self, name, coid, **kw):
        if self.raise_on == name:
            raise RuntimeError("recorder fault")
        self.calls.append((name, coid, kw))

    def on_ack(self, coid, vid, now=None):
        self._rec("on_ack", coid, vid=vid)

    def on_fill(self, coid, **kw):
        self._rec("on_fill", coid, **kw)

    def on_fees_actual(self, coid, **kw):
        self._rec("on_fees_actual", coid, **kw)

    def on_terminal(self, coid, **kw):
        self._rec("on_terminal", coid, **kw)

    def on_settlement(self, coid, **kw):
        self._rec("on_settlement", coid, **kw)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture()
def store(tmp_path):
    s = E.InventoryStore(str(tmp_path / "inv.json"))
    s.fill_calibration = Spy()
    return s


def prog(cum, *, avg="0.48", commission="0.03", terminal=False,
         leaves="0", state="WORKING"):
    return {"cum_qty": cum, "avg_px": avg, "commission": commission,
            "terminal": terminal, "leaves_qty": leaves, "state": state}


# ============================================================ the wiring

def test_a_venue_fill_records_a_fill_and_its_ACTUAL_fee(store):
    store.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")
    spy = store.fill_calibration
    assert "on_fill" in spy.names()
    assert "on_fees_actual" in spy.names()
    fill = [c for c in spy.calls if c[0] == "on_fill"][0][2]
    assert D(str(fill["qty"])) == D("1.00")
    fees = [c for c in spy.calls if c[0] == "on_fees_actual"][0][2]
    assert D(str(fees["fee_usd"])) == D("0.03")
    assert "venue" in fees["source"]


def test_the_store_has_no_recorder_by_default(tmp_path):
    """Normal for every non-money caller; nothing changes for them."""
    s = E.InventoryStore(str(tmp_path / "i.json"))
    assert getattr(s, "fill_calibration", "missing") is None
    s.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")


# ====================================================== idempotency, partials

def test_a_repeated_poll_of_the_same_cum_qty_records_NOTHING(store):
    """Inherited idempotency: no delta, no booking, no record."""
    store.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")
    n = len([c for c in store.fill_calibration.calls if c[0] == "on_fill"])
    for _ in range(3):
        store.apply_order_progress(COID, prog("1.00"), market=MARKET,
                                   side="UP")
    assert len([c for c in store.fill_calibration.calls
                if c[0] == "on_fill"]) == n == 1


def test_partial_fills_are_recorded_as_successive_DELTAS(store):
    store.apply_order_progress(COID, prog("0.40", leaves="0.60"),
                               market=MARKET, side="UP")
    store.apply_order_progress(COID, prog("1.00", leaves="0.00"),
                               market=MARKET, side="UP")
    fills = [c[2] for c in store.fill_calibration.calls if c[0] == "on_fill"]
    assert len(fills) == 2
    assert D(str(fills[0]["qty"])) == D("0.40")
    assert D(str(fills[1]["qty"])) == D("0.60")       # the DELTA, not the cum
    assert D(str(fills[1]["cumulative_qty"])) == D("1.00")


def test_a_backwards_cum_qty_records_no_fill_and_refuses(store):
    store.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")
    before = len(store.fill_calibration.calls)
    out = store.apply_order_progress(COID, prog("0.50"), market=MARKET,
                                     side="UP")
    assert out.get("refused") == "CUM_QTY_WENT_BACKWARDS"
    assert len(store.fill_calibration.calls) == before


# ================================================ venue-confirmed terminal

def test_terminal_is_recorded_as_VENUE_CONFIRMED(store):
    store.apply_order_progress(
        COID, prog("1.00", terminal=True, state="FILLED"),
        market=MARKET, side="UP")
    term = [c[2] for c in store.fill_calibration.calls
            if c[0] == "on_terminal"]
    assert term and term[0]["venue_confirmed"] is True
    assert term[0]["state"] == "FILLED"


def test_a_cancellation_is_a_venue_confirmed_terminal_with_no_fill(store):
    store.apply_order_progress(
        COID, prog("0", terminal=True, state="CANCELLED", commission=None),
        market=MARKET, side="UP")
    names = store.fill_calibration.names()
    assert "on_terminal" in names
    assert "on_fill" not in names        # nothing filled, nothing recorded
    term = [c[2] for c in store.fill_calibration.calls
            if c[0] == "on_terminal"][0]
    assert term["venue_confirmed"] is True and term["state"] == "CANCELLED"


def test_only_the_venue_can_make_terminal_state_PRESENT(tmp_path):
    """The shadow path's own terminal must stay unusable, by design."""
    rec = CAL.CalibrationRecorder(str(tmp_path / "fc.jsonl"))
    rec.pre_submit("shadow|x", slug=MARKET, side=FM.DOWN, own_px="0.29",
                   qty="1", book={"bid": "0.70", "ask": "0.71"},
                   ladder=None, now=1.0)
    rec.on_terminal("shadow|x", state="SHADOW_QUOTE_WITHDRAWN",
                    reason="modelled_only", venue_confirmed=False, now=2.0)
    assert rec.completeness("shadow|x")["capture"][
        "terminal_state"] != CAL.PRESENT
    rec.on_terminal("shadow|x", state="FILLED", reason="venue",
                    venue_confirmed=True, now=3.0)
    assert rec.completeness("shadow|x")["capture"][
        "terminal_state"] == CAL.PRESENT


# ============================================ fee attribution, missing data

def test_a_commission_the_venue_did_not_state_is_unknown_not_zero(store):
    store.apply_order_progress(COID, prog("1.00", commission=None),
                               market=MARKET, side="UP")
    fees = [c[2] for c in store.fill_calibration.calls
            if c[0] == "on_fees_actual"][0]
    assert fees["fee_usd"] is None


def test_a_zero_commission_is_a_fact_and_is_recorded_as_zero(store):
    """This venue credits rebates separately; a zero commission is not a
    rebate, and it is not unknown either."""
    store.apply_order_progress(COID, prog("1.00", commission="0"),
                               market=MARKET, side="UP")
    fees = [c[2] for c in store.fill_calibration.calls
            if c[0] == "on_fees_actual"][0]
    assert D(str(fees["fee_usd"])) == D("0")


# ================================== isolation: the recorder cannot matter

@pytest.mark.parametrize("hook", ["on_fill", "on_fees_actual", "on_terminal"])
def test_a_recorder_fault_does_not_affect_the_booking(tmp_path, hook):
    """A fault must not fail a reconciliation that already booked a fill."""
    s = E.InventoryStore(str(tmp_path / "i.json"))
    s.fill_calibration = Spy(raise_on=hook)
    out = s.apply_order_progress(
        COID, prog("1.00", terminal=True), market=MARKET, side="UP")
    assert out["delta"] == "1.00"
    assert s.position(MARKET, "UP") == D("1.00")
    assert out.get("refused") is None


def test_a_recorder_without_the_hooks_at_all_is_harmless(tmp_path):
    class Old:
        pass
    s = E.InventoryStore(str(tmp_path / "i.json"))
    s.fill_calibration = Old()
    out = s.apply_order_progress(COID, prog("1.00", terminal=True),
                                 market=MARKET, side="UP")
    assert out["delta"] == "1.00"


# ================================================= restart recovery parity

def test_a_restart_rereads_booked_fills_and_does_not_double_record(tmp_path):
    """The ledger is durable; the recorder is attached fresh. A reload must
    not re-record fills the store already booked."""
    p = str(tmp_path / "inv.json")
    s1 = E.InventoryStore(p)
    s1.fill_calibration = Spy()
    s1.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")
    assert len([c for c in s1.fill_calibration.calls
                if c[0] == "on_fill"]) == 1

    s2 = E.InventoryStore(p)                 # restart
    s2.fill_calibration = Spy()
    s2.apply_order_progress(COID, prog("1.00"), market=MARKET, side="UP")
    assert [c for c in s2.fill_calibration.calls if c[0] == "on_fill"] == []
    assert s2.applied_fill_qty(COID) == D("1.00")
