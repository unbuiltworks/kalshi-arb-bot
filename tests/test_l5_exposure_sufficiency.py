"""Are the first-round and unmatched limits sufficient under the three
states that actually break exposure accounting: simultaneous fills,
cancellation races, and restarts?

Driven against the real `FaithfulExecutor` and the real `InventoryStore`.
The answer is that no demonstrable gap remains in these three paths, and
each test names WHICH control refuses -- because "it is safe" is not a
finding, "this gate refuses, here, with this reason" is.

WHERE THE AUTHORITY SITS, and why nothing is duplicated into the reopening
gate to cover these. An unresolved cancel is caught by
`ValidationGates.gate`'s own `book_uncertain` test, which refuses every
submission while the ledger is unauthoritative. Re-deriving that inside
`probe_reopen_decision` would be a second opinion that can drift from the
one actually enforced -- the hazard `probe_budget_headroom`'s own docstring
warns about -- so the reopening gate is deliberately NOT the authority here
and these tests pin the real one instead.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

import tests.test_l5_two_sided_wiring as W
from tests.test_l5_two_sided_wiring import probe  # noqa: F401

PLAN = {"pair_cost": "0.95",
        "legs": [{"side": "UP", "price": "0.58", "qty": "1"},
                 {"side": "DOWN", "price": "0.37", "qty": "1"}]}


def rig(H, E, tmp_path, orders=6):
    ex, t = W.mk(H, E, tmp_path)
    ex.limits = H.limits(max_live_orders=orders)
    return ex, t


def gate(ex, rnd=1, plan=None, **over):
    kw = dict(bid=D("0.60"), ask=D("0.61"), tick=D("0.01"),
              depth_ticks=2, now_s=0.0, window_end_s=900.0)
    kw.update(over)
    return ex.probe_reopen_decision("m", rnd, plan or PLAN, **kw)


# =========================================== 1. SIMULTANEOUS FILLS

def test_both_legs_filling_at_once_leaves_no_residual_to_mistake(probe,
                                                                 tmp_path):
    """They match each other, so the worst case is zero, not double."""
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.apply_fill("m", "UP", D("1"), D("0.58"), basis="OWN")
    ex.store.apply_fill("m", "DOWN", D("1"), D("0.37"), basis="OWN")
    assert ex.store.residual("m") == D("0")
    r = gate(ex)
    # flat after a simultaneous pair: no gap, so the ECONOMICS decide, and
    # on real-fill priors another round is justified. What matters here is
    # that the pair is not double-counted as exposure.
    assert r["reopen"] is True
    assert D(r["outstanding_worst_case_usd"]) == D("0")


def test_the_reservation_takes_the_max_not_the_sum_of_live_sides(probe,
                                                                tmp_path):
    """If both resting sides fill they pair; only one filling ALONE hurts.

    So reserving `up_live + dn_live` would double-charge a pair and refuse
    rounds that are affordable; reserving the signed net would charge
    nothing and is the 1415z error. `max` is the worst case over
    simultaneous fills.
    """
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.outstanding_on_side = lambda m, side: D("2")   # balanced
    r = gate(ex)
    assert D(r["outstanding_worst_case_usd"]) == D("2") * D("0.58")


def test_an_unequal_pair_of_fills_leaves_a_gap_that_is_reduced(probe,
                                                              tmp_path):
    """A partial on one side only is still a reduction opportunity."""
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.apply_fill("m", "UP", D("1"), D("0.58"), basis="OWN")
    ex.store.apply_fill("m", "DOWN", D("0.4"), D("0.37"), basis="OWN")
    r = gate(ex)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert r["reducing_side"] == "DOWN"
    assert D(r["imbalance_gap"]) == D("0.6")


# ============================================ 2. CANCELLATION RACES

def test_an_unresolved_cancel_refuses_every_submission(probe, tmp_path):
    """THE AUTHORITY, named. `gate`'s own book_uncertain test, not mine.

    0145z/0200z produced ten requotes and not one clean cancel: seven came
    back `verify_error: HTTPError` and three `still_open` with the full
    `leaves_qty` outstanding. While the ledger cannot say whether an order
    is live, every figure computed from it is unauthoritative.
    """
    H, E = probe
    ex, t = rig(H, E, tmp_path)
    ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == []
    assert "BOOK_UNCERTAIN" in str(rec.get("refused_reason"))
    assert "CANCEL_VERIFY_HTTPError" in str(rec.get("refused_reason"))


def test_a_cancel_that_filled_anyway_is_counted_as_inventory(probe,
                                                             tmp_path):
    """The race itself: we cancel, the venue fills. Position wins.

    A cancelled order does not flatten filled residual inventory, so the
    fill must appear in the residual and drive a REDUCTION -- never be
    netted away by the vanished order.
    """
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.apply_fill("m", "UP", D("1"), D("0.58"), basis="OWN")
    ex.store.outstanding_on_side = lambda m, side: D("0")   # order gone
    r = gate(ex)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"
    assert D(r["held_residual"]) == D("1")


def test_an_uncertain_store_does_not_make_the_gate_claim_authority(probe,
                                                                   tmp_path):
    """It is not the gate's job, and duplicating it would invite drift."""
    import inspect
    from src import l5_live_execution as E2
    src = inspect.getsource(E2.FaithfulExecutor.probe_reopen_decision)
    assert "orders_uncertain" not in src
    # and the real authority is reached first, proven by the test above


# ================================================== 3. RESTARTS

def test_a_restart_rereads_inventory_from_disk_and_reduces_it(probe,
                                                              tmp_path):
    """A fresh executor on the same volume must not open on top of it."""
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.apply_fill("m", "UP", D("1"), D("0.58"), basis="OWN")
    ex2, _ = rig(H, E, tmp_path)            # new process, same ledger dir
    assert ex2.store.position("m", "UP") == D("1")
    r = gate(ex2)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"


def test_a_restart_that_lost_its_claim_files_still_cannot_open_blind(probe,
                                                                     tmp_path):
    """REVIEW BLOCKER 1, under the state that actually produces it."""
    H, E = probe
    ex, _ = rig(H, E, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("claim dir unreadable after restart")
    ex.probe_legs_claimed = boom
    r = gate(ex)
    assert r["reopen"] is False
    assert r["reason"] == "CLAIMS_UNREADABLE_NO_UNVERIFIED_OPENING"


def test_a_restart_with_inventory_AND_no_claims_still_reduces(probe,
                                                              tmp_path):
    """Durable inventory is checked first, so the two combine correctly."""
    H, E = probe
    ex, _ = rig(H, E, tmp_path)
    ex.store.apply_fill("m", "UP", D("1"), D("0.58"), basis="OWN")

    def boom(*a, **k):
        raise RuntimeError("claim dir unreadable after restart")
    ex.probe_legs_claimed = boom
    r = gate(ex)
    assert r["reopen"] is True
    assert r["reason"] == "RESIDUAL_HELD_NEXT_LEG_REDUCES_NOT_OPENS"


# ============== 4. THE FIRST ROUND, under the same three states

@pytest.mark.parametrize("prep,why", [
    ("uncertain", "BOOK_UNCERTAIN"),
    ("live_order_cap", "MAX_LIVE_ORDERS"),
])
def test_the_first_round_is_refused_by_a_named_control(probe, tmp_path,
                                                       prep, why):
    """Round 0 skips the ECONOMICS; it does not skip these."""
    H, E = probe
    ex, t = rig(H, E, tmp_path, orders=(0 if prep == "live_order_cap" else 6))
    if prep == "uncertain":
        ex.store.mark_uncertain("CANCEL_VERIFY_HTTPError")
    rec = ex.handle(W._row(H), W._book(), market="m", window="21:00")
    assert t.sent == []
    assert why in str(rec.get("refused_reason"))
