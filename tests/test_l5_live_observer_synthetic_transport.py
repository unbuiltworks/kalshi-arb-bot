"""THE WHOLE CHAIN, driven by a synthetic transport (#208 PRIORITY 1).

"Prove durable live ACK / rest / partial / full / cancel / settle / restart
observation with synthetic transports." The other files drive the recorder
directly; this one drives the MONEY PATH'S OWN HOOKS -- `record_submit` and
`apply_order_progress` on a real `InventoryStore` -- and interleaves the
independent observer between them, exactly as a tick would.

NO TRANSPORT IS REAL. `FakeVenue` returns canned progress dicts; nothing
opens a socket, holds a credential, or submits. The point is that the
record a live order would produce is assembled by the deployed code, not by
a test calling the recorder's setters in a convenient order.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from src import l5_fill_calibration as CALIB          # noqa: E402
from src import l5_live_execution as LX               # noqa: E402
from src import l5_pmus_live_runner as RUN            # noqa: E402

SLUG = "cpc-btc-updown-15m-2026-11-08-0300z"
COID = "l5f-synthchain01"


class FakeLadder:
    def __init__(self, t=1000.0):
        self.books = {SLUG: {"t": t, "updates": 7}}

    def ahead_of_us(self, slug, side, wire):
        return D("250")

    def depth_at(self, slug, side, wire):
        return D("400")


class FakeBook:
    """`snapshot` and `trades`, which is all the observer reads."""

    def __init__(self, t=1000.0, prints=()):
        self.t, self._prints = t, list(prints)

    def snapshot(self, slug):
        return {"bid": "0.4400", "ask": "0.4500", "t": self.t,
                "observed_t": self.t, "last_update_t": self.t, "updates": 7}

    def trades(self, slug):
        return list(self._prints)


class FakeTransport:
    def __init__(self):
        self.market_slug = SLUG


class Rig:
    """A runner-shaped object with only what the observer touches."""

    calibration_live_tick = RUN.Runner.calibration_live_tick

    def __init__(self, tmp_path, t=1000.0, prints=()):
        self.cal = CALIB.CalibrationRecorder(str(tmp_path),
                                             clock=lambda: t)
        self.store = LX.InventoryStore(os.path.join(str(tmp_path),
                                                    "store.json"))
        self.store.fill_calibration = self.cal
        self.fill_calibration = self.cal
        self.executor = type("Ex", (), {"store": self.store})()
        self.shadow_ladder = FakeLadder(t=t)
        self.transport = FakeTransport()
        self.book = FakeBook(t=t, prints=prints)
        self.stats = __import__("collections").Counter()

    def observe_tick(self, now, prints=()):
        self.book = FakeBook(t=now, prints=prints)
        self.shadow_ladder = FakeLadder(t=now)
        self.calibration_live_tick(now)


def submit(rig, *, coid=COID, side="UP", price="0.4400", qty="1"):
    """The pre-submission half, through the paths the executor uses."""
    intent = {"market": SLUG, "side": side, "price": price, "qty": qty}
    rig.store.record_submit(coid, intent, session_id="s1", mode="live")
    rig.cal.pre_submit(coid, slug=SLUG, side=side, own_px=price, qty=qty,
                       book=rig.book.snapshot(SLUG), ladder=rig.shadow_ladder,
                       now=1000.0)
    rig.cal.on_ack(coid, "VENUE-1")
    return intent


def progress(rig, *, coid=COID, side="UP", cum, avg="0.4400", leaves="0",
             terminal=False, state="ORDER_STATE_NEW", commission="0"):
    """One venue poll, through `apply_order_progress` -- the real hook."""
    return rig.store.apply_order_progress(
        coid, {"cum_qty": cum, "avg_px": avg, "leaves_qty": leaves,
               "terminal": terminal, "state": state,
               "commission": commission, "venue_order_id": "VENUE-1"},
        market=SLUG, side=side)


# =====================================================================
# 1. THE FULL CHAIN, END TO END.
# =====================================================================

def test_ack_rest_partial_full_and_terminal_make_a_usable_record(tmp_path):
    rig = Rig(tmp_path)
    submit(rig, qty="2")
    assert COID in rig.store.live_orders()

    rig.observe_tick(1002.0, prints=[{"t": 1001.0, "px": "0.4400",
                                      "qty": "4"}])
    progress(rig, cum="1", leaves="1")                  # PARTIAL
    rig.observe_tick(1004.0, prints=[{"t": 1003.0, "px": "0.4400",
                                      "qty": "1"}])
    progress(rig, cum="2", leaves="0", terminal=True,
             state="ORDER_STATE_FILLED")                # FULL + terminal

    rec = rig.cal.record(COID)
    assert len(rec["fills"]) == 2                       # successive deltas
    assert [str(D(f["qty"])) for f in rec["fills"]] == ["1", "1"]
    assert len(rec["observations"]) == 2
    assert rec["capture"]["terminal_state"] == CALIB.PRESENT
    assert rec["capture"]["lifetime_observation"] == CALIB.PRESENT
    assert rec["completeness"]["usable_for_calibration"] is True
    # the stat is CUMULATIVE across ticks, one per observed order per tick
    assert rig.stats["calib_live_observations"] == 2
    assert rig.stats["calib_live_orders_seen"] == 1     # last tick's count


def test_the_venue_commission_fills_the_actual_fee_column(tmp_path):
    """A zero is a FACT and is recorded as zero; this venue pays rebates
    separately, so a zero commission is not a rebate."""
    rig = Rig(tmp_path)
    submit(rig)
    rig.observe_tick(1002.0)
    progress(rig, cum="1", leaves="0", terminal=True,
             state="ORDER_STATE_FILLED", commission="0")
    rec = rig.cal.record(COID)
    assert rec["capture"]["fee_accounting"] == CALIB.PRESENT
    assert rec.get("fees_actual") is not None
    assert D(rec["fees_actual"]["fee_usd"]) == 0


def test_a_cancelled_order_is_venue_confirmed_and_still_usable(tmp_path):
    """It is evidence that the queue did NOT clear."""
    rig = Rig(tmp_path)
    submit(rig)
    rig.observe_tick(1002.0)
    rig.observe_tick(1004.0)
    progress(rig, cum="0", leaves="0", terminal=True,
             state="ORDER_STATE_CANCELED")
    rec = rig.cal.record(COID)
    assert rec["fills"] == []
    assert rec["terminal"]["state"] == "ORDER_STATE_CANCELED"
    assert rec["terminal"]["venue_confirmed"] is True
    assert rec["completeness"]["usable_for_calibration"] is True


def test_settlement_closes_the_record_when_driven(tmp_path):
    rig = Rig(tmp_path)
    submit(rig)
    rig.observe_tick(1002.0)
    progress(rig, cum="1", leaves="0", terminal=True,
             state="ORDER_STATE_FILLED")
    rig.cal.on_settlement(COID, slug=SLUG, settlement_px="1")
    assert rig.cal.record(COID)["settlement"]["settlement_px"] == "1"


# =====================================================================
# 2. IDEMPOTENCE AND THE AWKWARD CASES.
# =====================================================================

def test_polling_the_same_cumulative_twice_books_and_records_nothing(tmp_path):
    rig = Rig(tmp_path)
    submit(rig, qty="2")
    progress(rig, cum="1", leaves="1")
    progress(rig, cum="1", leaves="1")                  # the same poll again
    assert len(rig.cal.record(COID)["fills"]) == 1


def test_a_cumulative_that_goes_backwards_is_refused_not_guessed(tmp_path):
    rig = Rig(tmp_path)
    submit(rig, qty="2")
    progress(rig, cum="2", leaves="0")
    out = progress(rig, cum="1", leaves="1")
    assert out.get("refused") == "CUM_QTY_WENT_BACKWARDS"
    assert len(rig.cal.record(COID)["fills"]) == 1


def test_the_observer_stops_once_the_order_is_no_longer_live(tmp_path):
    """A terminal order leaves `live_orders`, so it stops being observed --
    and the observations it already has are kept."""
    rig = Rig(tmp_path)
    submit(rig)
    rig.observe_tick(1002.0)
    progress(rig, cum="1", leaves="0", terminal=True,
             state="ORDER_STATE_FILLED")
    assert COID not in rig.store.live_orders()
    before = len(rig.cal.record(COID)["observations"])
    rig.observe_tick(1006.0)
    assert len(rig.cal.record(COID)["observations"]) == before


# =====================================================================
# 3. RESTART, across the real store and the real ledger.
# =====================================================================

def test_the_chain_survives_a_restart_and_the_boundary_is_marked(tmp_path):
    rig = Rig(tmp_path)
    submit(rig, qty="2")
    rig.observe_tick(1002.0)
    progress(rig, cum="1", leaves="1")

    # a new process: both the ledger and the store are reloaded from disk
    rig2 = Rig(tmp_path)
    assert COID in rig2.store.live_orders()             # still outstanding
    rec = rig2.cal.record(COID)
    assert len(rec["fills"]) == 1                       # the partial survived
    assert rec["restart_boundaries"] == 0               # nothing live yet

    rig2.observe_tick(1010.0)
    progress(rig2, cum="2", leaves="0", terminal=True,
             state="ORDER_STATE_FILLED")
    rec2 = rig2.cal.record(COID)
    assert rec2["restart_boundaries"] == 1              # events span two
    assert len(rec2["fills"]) == 2
    assert rec2["completeness"]["usable_for_calibration"] is True
    assert rig2.cal.report()["orders_straddling_a_restart"] == 1
