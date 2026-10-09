"""The five mechanisms in ONE sequence, as they would run live.

Separate suites already cover each in isolation. The question here is whether
they compose: durable persistence, authenticated pricing, fail-closed
submission, cancellation, and restart recovery, in the order a real window
hits them, across a simulated process boundary.
"""
import signal

import pytest

from src import pmus_guardian_gate as GATE
from src import pmus_order_guardian as G
from src import pmus_q1_canary as C
from src import pmus_q1_executor as X
from src import pmus_ws_live_state as WS


SLUG = "cpc-btc-updown-15m-2099-01-01-1200z"


class Venue:
    """One mutable fake venue shared across the simulated restart."""

    def __init__(self):
        self.orders = {}
        self.open = []
        self.cancels = []
        self.readable = True

    def list_open(self):
        if not self.readable:
            raise OSError("gateway 502")
        return list(self.open)

    def read(self, oid):
        if not self.readable:
            raise OSError("gateway 502")
        return self.orders.get(oid, {})

    def cancel(self, oid):
        self.cancels.append(oid)
        return {"ok": True}


def _state(bid="0.45", ask="0.46"):
    st = WS.LiveState()
    st.apply({"marketData": {"marketSlug": SLUG,
                             "bids": [{"px": {"value": bid}, "qty": "100"}],
                             "offers": [{"px": {"value": ask}, "qty": "100"}]}})
    return st


def test_the_five_mechanisms_compose_across_a_process_boundary(tmp_path):
    path = str(tmp_path / "own.json")
    venue = Venue()

    # ---- 1. AUTHENTICATED PRICING -----------------------------------
    # the boundary the executor prices through, served from the WS state
    feed = C._StateBackedFeed(_state(), max_age_s=1e9)
    X.attach_ws_feed(feed)
    assert feed.bbo(SLUG)["source"] == "authenticated_ws"

    # ---- 2. FAIL-CLOSED SUBMISSION, before anything exists ----------
    g = G.OwnershipStore(path)
    rep = GATE.open_for_business(g, list_open=venue.list_open,
                                 read_order=venue.read,
                                 list_recent=lambda: [])
    GATE.require_open_for_business(rep)        # clean venue: proceeds

    # ---- 3. DURABLE PERSISTENCE, written before the order exists ----
    g.intend("i-1", market=SLUG, side="down", price="0.69", qty="10")
    on_disk = G.OwnershipStore(path)
    assert on_disk.orders["i-1"]["state"] == "INTENT"
    g.acknowledged("i-1", "V1")
    venue.orders["V1"] = {"state": "ORDER_STATE_NEW", "filled_qty": 0}
    venue.open = [{"id": "V1"}]

    # a second window may NOT start while that order is outstanding
    blocked = GATE.open_for_business(g, list_open=venue.list_open,
                                     read_order=venue.read,
                                     list_recent=lambda: [])
    with pytest.raises(GATE.SubmissionBlocked):
        GATE.require_open_for_business(blocked)

    # ---- 4. CANCELLATION on a graceful stop -------------------------
    seen = []
    GATE.arm_shutdown(g, cancel=venue.cancel, read_order=venue.read,
                      on_report=seen.append)
    # the venue fills it in the cancel race -- the R4 shape
    venue.orders["V1"] = {"state": "ORDER_STATE_FILLED", "filled_qty": 10}
    venue.open = []
    import os
    os.kill(os.getpid(), signal.SIGTERM)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    assert venue.cancels == ["V1"]
    assert seen[0]["results"][0]["verdict"] == "FILLED_NOT_CANCELLED"

    # ---- 5. RESTART RECOVERY: a new process, same volume ------------
    g2 = G.OwnershipStore(path)
    assert g2.orders["V1" if "V1" in g2.orders else "i-1"]["filled_qty"] == "10"
    rec = GATE.open_for_business(g2, list_open=venue.list_open,
                                 read_order=venue.read,
                                 list_recent=lambda: [])
    # the fill is booked exactly once and business may resume
    assert rec["reconcile"]["late_fills_booked"] in (["i-1"], [])
    assert rec["may_submit"] is True
    again = GATE.open_for_business(g2, list_open=venue.list_open,
                                   read_order=venue.read,
                                   list_recent=lambda: [])
    assert again["reconcile"]["late_fills_booked"] == []


def test_an_unreadable_venue_at_restart_keeps_the_chain_closed(tmp_path):
    """Recovery that cannot read the venue must not conclude 'all clear'."""
    path = str(tmp_path / "own.json")
    venue = Venue()
    g = G.OwnershipStore(path)
    g.intend("i-1", market=SLUG, side="up", price="0.45", qty="10")
    g.acknowledged("i-1", "V1")
    venue.readable = False
    g2 = G.OwnershipStore(path)
    rep = GATE.open_for_business(g2, list_open=venue.list_open,
                                 read_order=venue.read,
                                 list_recent=lambda: [])
    assert rep["may_submit"] is False
    with pytest.raises(GATE.SubmissionBlocked):
        GATE.require_open_for_business(rep)


def test_a_stale_feed_stops_the_chain_before_the_gate_matters(tmp_path):
    """Pricing and ownership are independent guards; either one refusing is
    enough, and a stale feed refuses without falling back to REST."""
    feed = C._StateBackedFeed(_state(), max_age_s=0.0)
    X.attach_ws_feed(feed)
    with pytest.raises(WS.StaleFeed):
        feed.bbo(SLUG)
