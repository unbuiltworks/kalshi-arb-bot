"""The $2.16 durable-outstanding carry, and the accounting that produced it.

RUN 35641166387 stopped with:

    filled 1.47 + resting 0.11 + durable outstanding 2.16 + this rung 0.64
    = 4.38 committed, past 4.00

and 2.16 was 0.19 + 0.72 + 0.56 (three orders the VENUE HAD FILLED, whose
cash was already inside the 1.47) plus 0.69 (an order the VENUE HAD CANCELED
under maker-or-cancel). NONE of it was live. The one order that WAS live,
YES 0.11, was counted correctly and separately as `resting`.

THE INVARIANT:

    committed-order capital = authoritative currently-live orders
                            + newly proposed order commitments

with filled inventory accounted separately under the inventory/residual
limits. Provenance stays append-only: a release is a NEW row, never a delete.
"""
import os
import tempfile
from decimal import Decimal as D

import pytest

from src.gemini_execution import outstanding_ledger as OL


def _ledger():
    d = tempfile.mkdtemp()
    return OL.OutstandingLedger(os.path.join(d, "o.jsonl"),
                                auth_id="A", symbol="S")


def _place(L, coid, leg, px, qty="1"):
    L.intent(coid, leg=leg, price=px, qty=qty)
    L.accepted(coid, "oid-" + coid)


#: the run's five placements, in order
PLACED = (("yes19", "YES", "0.19"), ("no72", "NO", "0.72"),
          ("yes11", "YES", "0.11"), ("no69", "NO", "0.69"),
          ("no56", "NO", "0.56"))


def test_the_2_16_is_reproduced_exactly_by_the_old_behaviour():
    """Pin the DEFECT, so the fix below is measured against the real number."""
    L = _ledger()
    for coid, leg, px in PLACED:
        _place(L, coid, leg, px)
    # the live loop never reconciled, so every order sat at its PLACEMENT
    # state; `resting` already counted the one live order, so it is excluded
    got = L.committed_usd(exclude={"yes11"})
    assert got["usd"] == D("2.16"), got
    # and that 2.16 is precisely filled(1.47) + venue-cancelled(0.69)
    assert D("0.19") + D("0.72") + D("0.56") + D("0.69") == D("2.16")


def test_an_authoritative_read_releases_filled_and_cancelled_orders():
    """THE FIX. The venue lists only the one order that is actually live, so
    everything else is released and the charge collapses to that order."""
    L = _ledger()
    for coid, leg, px in PLACED:
        _place(L, coid, leg, px)

    active = [{"clientOrderId": "yes11", "orderId": "oid-yes11",
               "price": "0.11", "quantity": "1", "filledQuantity": "0"}]
    rec = L.reconcile(active)

    assert set(rec["released"]) == {"yes19", "no72", "no69", "no56"}
    for coid in ("yes19", "no72", "no69", "no56"):
        assert L.orders[coid]["state"] == OL.VENUE_ABSENT
        assert L.orders[coid]["state"] in OL.TERMINAL
    # the only committed-ORDER capital left is the live order itself.
    # `reconcile` reports the total as a STRING; `committed_usd` returns a
    # Decimal. Compared as Decimals here so the assertion is about the value.
    assert D(str(rec["committed_usd"])) == D("0.11")
    # and excluding it (the working set already counts it) leaves ZERO
    assert L.committed_usd(exclude={"yes11"})["usd"] == D("0")


def test_the_corrected_total_at_the_moment_of_the_stop():
    """What the CAP-4 window would have seen at its final action.

    filled 1.47 (inventory, a SEPARATE bucket) + resting 0.11 + durable 0.00
    + this rung 0.64 = 2.22, comfortably inside 4.00 -- so the rung the risk
    gate refused would have been ALLOWED.
    """
    L = _ledger()
    for coid, leg, px in PLACED:
        _place(L, coid, leg, px)
    L.reconcile([{"clientOrderId": "yes11", "orderId": "oid-yes11",
                  "price": "0.11", "quantity": "1", "filledQuantity": "0"}])
    durable = L.committed_usd(exclude={"yes11"})["usd"]
    total = D("1.47") + D("0.11") + durable + D("0.64")
    assert durable == D("0")
    assert total == D("2.22")
    assert total <= D("4.00")


# ------------------------------------------------ the three required proofs

def test_a_genuinely_live_pre_existing_order_still_consumes_budget():
    """A release must never become a blanket amnesty."""
    L = _ledger()
    _place(L, "old", "YES", "0.40")
    _place(L, "gone", "NO", "0.30")
    rec = L.reconcile([{"clientOrderId": "old", "orderId": "oid-old",
                        "price": "0.40", "quantity": "1",
                        "filledQuantity": "0"}])
    assert rec["released"] == ["gone"]
    assert L.orders["old"]["state"] not in OL.TERMINAL
    assert L.committed_usd()["usd"] == D("0.40")
    assert rec["verdict"] == OL.BLOCKED, "something IS live; say so"


def test_a_partial_fill_keeps_only_its_remainder_committed():
    L = _ledger()
    _place(L, "p", "YES", "0.50", qty="4")
    L.reconcile([{"clientOrderId": "p", "orderId": "oid-p", "price": "0.50",
                  "quantity": "4", "filledQuantity": "3"}])
    # one of four still working: 1 x 0.50
    assert L.committed_usd()["usd"] == D("0.50")
    assert L.orders["p"]["state"] == OL.PARTIAL


def test_a_restart_does_not_resurrect_a_released_reservation():
    """Released, then re-read from disk: it stays released, because the
    release is a durable APPEND and the venue has not said otherwise."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "o.jsonl")
    L = OL.OutstandingLedger(path, auth_id="A", symbol="S")
    _place(L, "a", "YES", "0.25")
    L.reconcile([])                       # venue lists nothing
    assert L.orders["a"]["state"] == OL.VENUE_ABSENT

    L2 = OL.OutstandingLedger(path, auth_id="A", symbol="S")
    assert L2.orders["a"]["state"] == OL.VENUE_ABSENT
    assert L2.committed_usd()["usd"] == D("0")
    assert L2.blocks_restart() == []


def test_but_the_venue_saying_it_is_live_again_DOES_resurrect_the_EXPOSURE():
    """The release is a READING, not a promise. If the venue lists the order
    again it is exposure again -- and the charge comes back.

    THROUGH A DIFFERENT DOOR THAN I FIRST ASSERTED, and the difference
    matters. `reconcile` skips entries already in TERMINAL, so the order's
    own row stays VENUE_ABSENT; the venue's active row then matches nothing
    we know and is counted as FOREIGN, whose notional is added to the total
    and which makes the verdict BLOCKED and tradeable False. The exposure is
    recovered either way, and the foreign path is the more conservative of
    the two: it also refuses to trade.
    """
    L = _ledger()
    _place(L, "a", "YES", "0.25")
    L.reconcile([])
    assert L.committed_usd()["usd"] == D("0")

    rec = L.reconcile([{"clientOrderId": "a", "orderId": "oid-a",
                        "price": "0.25", "quantity": "1",
                        "filledQuantity": "0"}])
    assert D(str(rec["committed_usd"])) == D("0.25"), "the cash is charged again"
    assert rec["verdict"] == OL.BLOCKED
    assert rec["tradeable"] is False
    assert [f["client_order_id"] for f in rec["foreign"]] == ["a"]


def test_provenance_is_append_only():
    """Every transition is a NEW row. Nothing is rewritten or deleted."""
    import json
    d = tempfile.mkdtemp()
    path = os.path.join(d, "o.jsonl")
    L = OL.OutstandingLedger(path, auth_id="A", symbol="S")
    _place(L, "a", "YES", "0.25")
    L.reconcile([])
    rows = [json.loads(x) for x in open(path) if x.strip()]
    states = [r.get("state") for r in rows]
    assert states == [OL.INTENT, OL.ACCEPTED, OL.VENUE_ABSENT], states
    assert len(rows) == 3, "a transition must APPEND, never overwrite"


def test_an_unreadable_read_releases_nothing():
    L = _ledger()
    _place(L, "a", "YES", "0.25")
    rec = L.reconcile(None)
    assert rec["verdict"] == OL.UNREADABLE
    assert L.orders["a"]["state"] not in OL.TERMINAL
    assert L.committed_usd()["usd"] == D("0.25")


def test_the_live_loop_reconciles_the_durable_ledger():
    """The fix must be ON THE LIVE PATH. `reconcile` ran once, at recovery;
    _refresh rebuilt inventory every cycle and never touched the ledger."""
    import inspect

    from src.gemini_execution import ws_continuous as WC
    src = inspect.getsource(WC.Driver._refresh)
    assert "self.outstanding.reconcile(" in src, (
        "the durable ledger must be reconciled against the same authoritative "
        "read the inventory is rebuilt from")
    assert "OL.UNREADABLE" in src, "and an unreadable read must stop the window"
