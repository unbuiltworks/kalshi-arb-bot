"""PROOF that every outstanding L5 order is cancelled by EXACT venue id.

Four triggers, a restart and a network interruption, all against the real
`l5_live_execution.deadman` and the real store. The claim being proved is not
"a cancel was sent" -- it is that the VENUE reported a terminal state on
re-read, that an unproven cancel marks the book UNCERTAIN rather than
reporting success, and that nothing is cancelled by client order id.
"""
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E


class Venue:
    """Records what it was asked, by which id, and can fail on demand."""

    def __init__(self, states=None, cancel_raises=(), order_raises=()):
        self.cancel_calls, self.order_calls = [], []
        self.states = states or {}
        self.cancel_raises = set(cancel_raises)
        self.order_raises = set(order_raises)
        self.armed = True

    def cancel(self, venue_order_id):
        self.cancel_calls.append(venue_order_id)
        if venue_order_id in self.cancel_raises:
            raise ConnectionError("network interruption")
        return {"ok": True, "venue_order_id": venue_order_id}

    def order(self, venue_order_id):
        self.order_calls.append(venue_order_id)
        if venue_order_id in self.order_raises:
            raise TimeoutError("read timed out")
        st = self.states.get(venue_order_id, "ORDER_STATE_CANCELED")
        return {"id": venue_order_id, "state": st,
                "cumQuantity": 0, "leavesQuantity": 0 if "CANCEL" in st else 5}


def store_with(tmp_path, orders):
    st = E.InventoryStore(str(tmp_path / "s.json"))
    st.state["live_orders"] = dict(orders)
    st.save()
    return st


def vids(rows):
    """The venue ids out of a deadman bucket. The buckets hold RECORDS --
    client id, venue id, market, side, the venue's own state and any cancel
    error -- not bare ids, which is richer than an assertion on ids alone."""
    out = []
    for r in rows:
        out.append(r.get("venue_order_id") if isinstance(r, dict) else r)
    return sorted(x for x in out if x is not None)


def coids(rows):
    out = []
    for r in rows:
        out.append(r.get("client_order_id") if isinstance(r, dict) else r)
    return sorted(x for x in out if x is not None)


def live(coid, vid, market="cpc-btc-updown-15m-2026-10-16-2100z", side="UP"):
    return (coid, {"client_order_id": coid, "venue_order_id": vid,
                   "market": market, "side": side, "qty": "0.01",
                   "price": "0.59", "remaining": "0.01"})


# ------------------------------------- BOTH legs, by EXACT venue id

def test_every_outstanding_order_is_cancelled_by_EXACT_venue_id(tmp_path):
    st = store_with(tmp_path, dict([live("l5f-aaa", "V-1", side="UP"),
                                    live("l5f-bbb", "V-2", side="DOWN")]))
    t = Venue()
    out = E.deadman(st, t, reason="operator_halt")
    # the VENUE ids were sent, and the CLIENT ids never were
    assert sorted(t.cancel_calls) == ["V-1", "V-2"]
    for coid in ("l5f-aaa", "l5f-bbb"):
        assert coid not in t.cancel_calls
    # and every one was re-read, because an ack is not proof
    assert sorted(t.order_calls) == ["V-1", "V-2"]
    assert vids(out["cancelled"]) == ["V-1", "V-2"]
    assert out["still_open"] == [] and out["unverified"] == []
    # and each record carries the VENUE's own state, which is what decides
    for rec in out["cancelled"]:
        assert rec["venue_state"] == "ORDER_STATE_CANCELED"
        assert rec["leaves_qty"] == "0"


@pytest.mark.parametrize("reason", [
    "operator_halt",            # 1. HALT asserted
    "ts_terminal_deadline",     # 2. market about to expire
    "window_end",               # 2b. window rolled
    "uncertain_reconcile",      # 3. reconciliation went uncertain
    "shutdown",                 # 4. process going down
])
def test_every_trigger_reaches_the_same_exact_id_cancellation(tmp_path,
                                                              reason):
    """One cancellation route, reached by every emergency. The reason is
    recorded; it does not change what happens."""
    st = store_with(tmp_path, dict([live("l5f-a", "V-9")]))
    t = Venue()
    out = E.deadman(st, t, reason=reason)
    assert t.cancel_calls == ["V-9"]
    assert vids(out["cancelled"]) == ["V-9"]
    assert out["reason"] == reason


# ----------------------------------------- an unproven cancel is NOT success

def test_a_cancel_the_venue_still_shows_WORKING_is_not_success(tmp_path):
    """The dangerous case: 200 on the cancel, still working on re-read."""
    st = store_with(tmp_path, dict([live("l5f-a", "V-1")]))
    t = Venue(states={"V-1": "ORDER_STATE_WORKING"})
    out = E.deadman(st, t, reason="operator_halt")
    assert out["cancelled"] == []
    assert vids(out["still_open"]) == ["V-1"]
    assert st.orders_uncertain() is not None


def test_an_order_with_NO_venue_id_cannot_be_cancelled_and_says_so(tmp_path):
    st = store_with(tmp_path, {"l5f-x": {
        "client_order_id": "l5f-x", "venue_order_id": None,
        "market": "m", "side": "UP", "qty": "0.01", "remaining": "0.01"}})
    t = Venue()
    out = E.deadman(st, t, reason="operator_halt")
    assert t.cancel_calls == []
    assert coids(out["no_venue_id"]) == ["l5f-x"]
    assert st.orders_uncertain() is not None


# --------------------------------------------- network interruption

def test_a_cancel_that_raises_but_reads_back_terminal_IS_cancelled(tmp_path):
    """The network dropped the response, not the request."""
    st = store_with(tmp_path, dict([live("l5f-a", "V-1")]))
    t = Venue(cancel_raises={"V-1"}, states={"V-1": "ORDER_STATE_CANCELED"})
    out = E.deadman(st, t, reason="operator_halt")
    assert vids(out["cancelled"]) == ["V-1"]   # the venue's state decides
    # the cancel error is RECORDED, not hidden, even on success
    assert out["cancelled"][0]["cancel_error"] is not None
    assert st.orders_uncertain() is None


def test_a_cancel_AND_a_read_that_both_fail_leaves_the_book_UNCERTAIN(
        tmp_path):
    """Total interruption. The order's fate is unknown, so the next
    submission must be blocked."""
    st = store_with(tmp_path, dict([live("l5f-a", "V-1")]))
    t = Venue(cancel_raises={"V-1"}, order_raises={"V-1"})
    out = E.deadman(st, t, reason="operator_halt")
    assert out["cancelled"] == []
    assert "V-1" in (vids(out["unverified"]) + vids(out["errors"]))
    assert st.orders_uncertain() is not None


def test_an_uncertain_book_blocks_the_next_submission(tmp_path):
    """UNCERTAIN is only useful if it actually stops the next order."""
    from src.l5_live_execution import SafetyLimits
    st = store_with(tmp_path, dict([live("l5f-a", "V-1")]))
    t = Venue(cancel_raises={"V-1"}, order_raises={"V-1"})
    E.deadman(st, t, reason="operator_halt")
    why = st.orders_uncertain()
    assert why is not None
    lim = SafetyLimits(max_usd_per_market=D("5"), max_abs_residual=D("10"),
                       max_total_exposure_usd=D("5"),
                       max_concurrent_markets=1, max_live_orders=2,
                       daily_realized_loss_halt_usd=D("5"))
    r = lim.refuse(price=D("0.59"), qty=D("0.01"), market="m",
                   committed_by_market={}, residual_after=D(0),
                   realized_today=D(0), open_markets=["m"],
                   outstanding_by_market={}, live_order_count=0,
                   orders_uncertain=why)
    assert r is not None and "UNCERTAIN" in r


# --------------------------------------------------------- restart

def test_a_RESTART_recovers_the_orders_from_DISK_and_cancels_them(tmp_path):
    """The live-order record is durable, so a fresh process cancels what the
    dead one left resting -- which is the state a crash actually leaves."""
    st = store_with(tmp_path, dict([live("l5f-a", "V-1"),
                                    live("l5f-b", "V-2")]))
    del st                                  # the process dies
    st2 = E.InventoryStore(str(tmp_path / "s.json"))   # a new one starts
    assert sorted(st2.live_orders()) == ["l5f-a", "l5f-b"]
    t = Venue()
    out = E.deadman(st2, t, reason="orphan_sweep_after_restart")
    assert sorted(t.cancel_calls) == ["V-1", "V-2"]
    assert vids(out["cancelled"]) == ["V-1", "V-2"]


def test_nothing_resting_sends_nothing(tmp_path):
    """Safe when unarmed and flat: no reads, no sends, empty lists."""
    st = store_with(tmp_path, {})
    t = Venue()
    out = E.deadman(st, t, reason="operator_halt")
    assert t.cancel_calls == [] and t.order_calls == []
    assert out["cancelled"] == [] and out["still_open"] == []


# ------------------------------------- cancellation survives the shadow lock

def test_the_shadow_lock_blocks_submit_but_NEVER_cancel():
    """The emergency route must stay open while trading is locked."""
    from src.l5_pmus_live_runner import ShadowOnlyTransport
    assert ShadowOnlyTransport.BLOCKED == ("submit",)
    assert "cancel" not in ShadowOnlyTransport.BLOCKED
