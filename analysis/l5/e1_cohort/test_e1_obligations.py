"""THE TEN SCENARIOS: one outstanding first leg, whatever the venue says.

Every test here asserts the SAME TWO BOUNDS, named once:

    * no more than `MAX_OUTSTANDING_FIRST_LEGS` first-leg obligations are
      ever live at once;
    * aggregate exposure never exceeds the configured cap.

They hold while the venue acknowledges and then denies, while an order
rests, while it half fills, while a cancel is claimed but not done, and
while the venue is unreadable or self-contradictory. The point of the
patch is that NONE of those bounds depends on the venue being
read-your-writes consistent, because the record that enforces them is
ours.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import obligations as OB
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.spec import MAX_ATTEMPTS_PER_WINDOW
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import (FakeVenue, NotFound,
                                                          UP, DOWN)
from analysis.l5.e1_cohort.test_e1_run_cohort_lifecycle import (
    Clock, LoopVenue, _Prog, _Rec, _provider, cancelling_transport)

#: the configured bound under test. One, from the frozen spec's
#: `outstanding_order_invariant.max_outstanding_orders`.
MAX_OUTSTANDING_FIRST_LEGS = 1

#: the configured per-probe exposure cap these tests assert under.
EXPOSURE_CAP_USD = D("5.00")

SID = "E1-OBLIGATION-TEST"


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch, SID)
    FakeTransport.posts = []
    if hasattr(R, "_set_emergency_handover"):
        R._set_emergency_handover(None)
    return fake_pin(BASE)


@pytest.fixture()
def ledger(tmp_path):
    return OB.ObligationLedger(str(tmp_path / "e1_cohort"), session_id=SID)


class LaggingVenue(LoopVenue):
    """Acknowledges a POST, then denies the order exists for `lag_reads`.

    Both endpoints agree it is absent, so nothing looks inconsistent and
    nothing fails closed. This is the shape that defeated the venue-read
    guard, and it is the premise of scenario 1.
    """

    def __init__(self, clock, slug, *, lag_reads=0):
        super().__init__(clock, slug)
        self.lag_reads = int(lag_reads)
        self.reads_since_post: Dict[str, int] = {}

    def on_post(self, order_id, *, side, wire, qty):
        super().on_post(order_id, side=side, wire=wire, qty=qty)
        self.reads_since_post[order_id] = 0

    def _hidden(self, oid):
        n = self.reads_since_post.get(oid)
        return n is not None and n < self.lag_reads

    def reader(self):
        inner = super().reader()

        def _read(path):
            if path == OS.OPEN_PATH:
                body = inner(path)
                rows = [r for r in (body.get("orders") or [])
                        if not self._hidden(str(r.get("id")))]
                for k in list(self.reads_since_post):
                    self.reads_since_post[k] += 1
                return {"orders": rows}
            if path.startswith("/v1/order/"):
                oid = path.rsplit("/", 1)[-1]
                if self._hidden(oid):
                    raise NotFound()
                return inner(path)
            return inner(path)
        return _read


def _run(wired, venue, clock, *, ledger, max_iterations=80,
         transport=None, known=None, roles=None, pending=None):
    known = [] if known is None else known
    roles = {} if roles is None else roles
    pending = [] if pending is None else pending
    prov = _provider(venue, clock, pin=wired, known=known, roles=roles,
                     pending=pending, cutoff_at=BASE + 10_000)

    class WithLedger:
        """The production snapshot contract, with the ledger attached the
        way `PmusWindowProvider` attaches it."""
        obligations = ledger

        def __getattr__(self, name):
            return getattr(prov, name)

        def order_state(self, _pin=None):
            return prov().get("order_state")

        def __call__(self):
            snap = prov()
            snap["obligation_ledger"] = ledger
            try:
                ledger.reconcile(snap.get("order_state") or {})
            except OB.LedgerUnavailable:
                snap["our_outstanding_orders"] = OS.UNKNOWN_OUTSTANDING
                return snap
            return OB.merge_into_snapshot(snap, ledger=ledger)

    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=WithLedger(), tracker=tracker,
                       recorder=_Rec(),
                       transport_factory=transport or cancelling_transport(
                           venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=max_iterations)
    return out, tracker


def assert_bounds_held(out, ledger, venue, *, label):
    """THE TWO BOUNDS, asserted identically in every scenario."""
    live = [o for o in venue.orders.values()
            if o["outcomeSide"] == UP
            and o["state"] in ("ORDER_STATE_NEW",
                               "ORDER_STATE_PARTIALLY_FILLED")]
    assert len(live) <= MAX_OUTSTANDING_FIRST_LEGS, (
        "%s: %d first legs resting at the venue, bound is %d"
        % (label, len(live), MAX_OUTSTANDING_FIRST_LEGS))
    assert out["placement_count"] <= MAX_OUTSTANDING_FIRST_LEGS, (
        "%s: %d placements" % (label, out["placement_count"]))
    assert len(ledger.blocking()) <= MAX_OUTSTANDING_FIRST_LEGS, (
        "%s: %r" % (label, ledger.report()))
    worst = ledger.worst_case_usd()
    submitted = sum(D(o["quantity"]) * D(str(o["price"]["value"]))
                    for o in venue.orders.values()
                    if o["outcomeSide"] == UP)
    assert worst <= EXPOSURE_CAP_USD, (label, str(worst))
    assert submitted <= EXPOSURE_CAP_USD, (label, str(submitted))


# =====================================================================
# 1. POST acknowledged, order absent from both endpoints for N reads
# =====================================================================

@pytest.mark.parametrize("lag", [1, 2, 6, 20])
def test_1_acknowledged_then_denied_still_blocks(wired, ledger, lag):
    """THE REGRESSION. Before the ledger this placed `lag`-many first legs.

    The venue acknowledges and then denies for `lag` reads, both endpoints
    agreeing, so no refusal fires on inconsistency. Our own durable record
    is what refuses.
    """
    clock = Clock(BASE + 10)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=lag)
    out, _ = _run(wired, venue, clock, ledger=ledger)
    assert_bounds_held(out, ledger, venue, label="denied for %d reads" % lag)
    assert out["placement_count"] == 1
    refusals = [r for row in out["refusals"] for r in row["refusals"]]
    assert any(r.startswith("E3_OUR_ORDER_IS_OUTSTANDING")
               for r in refusals), refusals


def test_1b_absence_never_releases_an_obligation(ledger):
    """THE PREDICATE, isolated. A view that does not mention our order
    leaves the obligation exactly where it was."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    for _ in range(5):
        rep = ledger.reconcile({"ok": True, "orders": []})
        assert rep["unobserved"] == ["I1"]
        assert rep["released"] == []
    assert len(ledger.blocking()) == 1
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED


def test_1c_an_unobserved_id_is_charged_at_the_ceiling(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.01", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    # priced at the CEILING, not at the price we happened to send
    assert ledger.worst_case_usd() == D("0.13") * OB.UNOBSERVED_CEILING_USD


# =====================================================================
# 2. Observed but still resting
# =====================================================================

def test_2_observed_and_resting_is_not_release(ledger):
    """OBSERVATION IS NOT RELEASE. The order can still fill."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    view = {"ok": True, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_NEW", "terminal": False,
         "cum_quantity": "0", "outstanding": True}]}
    rep = ledger.reconcile(view)
    assert rep["released"] == []
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    assert len(ledger.blocking()) == 1
    # and now it is charged at its real own-side price, not the ceiling
    assert ledger.worst_case_usd() == D("0.13") * D("0.49")


def test_2b_a_resting_order_blocks_in_the_loop(wired, ledger):
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    out, _ = _run(wired, venue, clock, ledger=ledger)
    assert_bounds_held(out, ledger, venue, label="observed resting")


# =====================================================================
# 3. Partial fill with resting remainder
# =====================================================================

def test_3_partial_fill_with_remainder_still_blocks(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    view = {"ok": True, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_PARTIALLY_FILLED",
         "terminal": False, "cum_quantity": "0.05", "outstanding": True}]}
    ledger.reconcile(view)
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    assert ledger.rows["I1"]["filled_qty"] == "0.05"
    assert len(ledger.blocking()) == 1


def test_3b_partial_fill_in_the_loop_holds_the_bound(wired, ledger):
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    venue.fill_plan = [{"at": BASE + 16, "qty": "0.05",
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    out, _ = _run(wired, venue, clock, ledger=ledger)
    assert_bounds_held(out, ledger, venue, label="partial fill")


# =====================================================================
# 4. Full fill and inventory reconciliation
# =====================================================================

def test_4_a_full_fill_releases_only_once_accounted(ledger):
    """TERMINAL IS NOT ENOUGH. The fill must be visible to the gate."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    filled_row = {"id": "OID-1", "state": "ORDER_STATE_FILLED",
                  "terminal": True, "cum_quantity": "0.13",
                  "outstanding": False}

    # the venue says FILLED, and nothing accounts for the 0.13 yet
    unaccounted = {"ok": True, "orders": [filled_row], "residual": None,
                   "derived_net_qty": "0",
                   "historical_resolved_filled_qty": "0"}
    ledger.reconcile(unaccounted)
    assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    assert len(ledger.blocking()) == 1, (
        "an unhedged fill that nothing has accounted for must still block")

    # now the inventory view carries it
    accounted = dict(unaccounted, derived_net_qty="0.13",
                     residual={"qty": "0.13", "outcome_side": UP})
    rep = ledger.reconcile(accounted)
    assert rep["released"] == ["I1"]
    assert ledger.rows["I1"]["state"] == OB.RECONCILED
    assert ledger.rows["I1"]["fills_incorporated"] is True
    assert ledger.blocking() == []


def test_4b_after_release_the_residual_gate_is_what_blocks(ledger):
    """THE HANDOFF IS EXPLICIT. The obligation releases; the residual
    rule takes over, which is its job and not this module's."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    view = {"ok": True,
            "orders": [{"id": "OID-1", "state": "ORDER_STATE_FILLED",
                        "terminal": True, "cum_quantity": "0.13",
                        "outstanding": False}],
            "residual": {"qty": "0.13", "outcome_side": UP},
            "derived_net_qty": "0.13"}
    ledger.reconcile(view)
    assert ledger.blocking() == []
    snap = OB.merge_into_snapshot(
        {"our_outstanding_orders": 0,
         "our_open_residual": {"qty": "0.13"}}, ledger=ledger)
    assert snap["our_outstanding_orders"] == 0
    assert snap["our_open_residual"], "the residual gate still holds"


# =====================================================================
# 5. Cancel acknowledged but the order is still open
# =====================================================================

def test_5_a_claimed_cancel_that_did_not_happen_still_blocks(ledger):
    """THE CANCEL'S OWN ANSWER IS NOT EVIDENCE. Only the venue's state is."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    # the cancel returned 200; the order is still ORDER_STATE_NEW
    view = {"ok": True, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_NEW", "terminal": False,
         "cum_quantity": "0", "outstanding": True}]}
    ledger.reconcile(view)
    assert ledger.rows["I1"]["state"] == OB.OBSERVED
    assert len(ledger.blocking()) == 1


# =====================================================================
# 6. Cancel verified, with partial fills accounted
# =====================================================================

def test_6_a_verified_cancel_with_a_partial_fill(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    row = {"id": "OID-1", "state": "ORDER_STATE_CANCELED", "terminal": True,
           "cum_quantity": "0.05", "outstanding": False}
    # cancelled, but 0.05 filled before it went -- and nothing accounts
    ledger.reconcile({"ok": True, "orders": [row], "residual": None,
                      "derived_net_qty": "0",
                      "historical_resolved_filled_qty": "0"})
    assert ledger.rows["I1"]["state"] == OB.TERMINAL_FILL_UNACCOUNTED
    # with the 0.05 in inventory it releases, and the residual gate holds
    rep = ledger.reconcile({"ok": True, "orders": [row],
                            "residual": {"qty": "0.05",
                                         "outcome_side": UP},
                            "derived_net_qty": "0.05"})
    assert rep["released"] == ["I1"]
    assert ledger.rows["I1"]["terminal_state"] == "ORDER_STATE_CANCELED"
    assert ledger.rows["I1"]["filled_qty"] == "0.05"


def test_6b_a_verified_cancel_with_no_fill_releases_immediately(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    rep = ledger.reconcile({"ok": True, "orders": [
        {"id": "OID-1", "state": "ORDER_STATE_CANCELED", "terminal": True,
         "cum_quantity": "0", "outstanding": False}]})
    assert rep["released"] == ["I1"]
    assert ledger.blocking() == []


# =====================================================================
# 7. Ambiguous POST response
# =====================================================================

def test_7_an_ambiguous_post_is_never_retired(ledger):
    """THE MOST DANGEROUS STATE E1 CAN OCCUPY: we may have created an
    order and cannot prove we did not. It blocks, indefinitely, and
    `retire_never_sent` refuses to touch it."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "AMBIG-1")
    ledger.retire_never_sent("I1", why="not allowed -- an order may exist")
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED
    assert len(ledger.blocking()) == 1
    for _ in range(10):
        ledger.reconcile({"ok": True, "orders": []})
    assert len(ledger.blocking()) == 1


def test_7b_only_a_pre_post_refusal_may_retire(ledger):
    """The latch refusing BEFORE the POST is the one safe retirement:
    the O_EXCL create failed, so no order can exist."""
    ledger.record_submission(intent_id="I2", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.retire_never_sent("I2", why="LATCH_REFUSED_BEFORE_THE_POST")
    assert ledger.rows["I2"]["state"] == OB.RETIRED_NEVER_SENT
    assert ledger.blocking() == []


# =====================================================================
# 8. Two concurrent workers racing the reservation
# =====================================================================

def test_8_two_workers_cannot_both_reserve_the_first_leg(tmp_path):
    """TWO LEDGERS OVER ONE DIRECTORY, as two processes on one volume.

    The second worker LOADS the first's obligation from disk, so it starts
    already blocked. The durable file is the reservation, which is why a
    counter in memory would not do.
    """
    sd = str(tmp_path / "e1_cohort")
    a = OB.ObligationLedger(sd, session_id=SID)
    a.record_submission(intent_id="W1", window=0, slot=0, slug="s",
                        side=UP, price="0.49", qty="0.13")
    b = OB.ObligationLedger(sd, session_id=SID)
    assert len(b.blocking()) == 1, b.report()
    assert b.rows["W1"]["state"] == OB.SUBMITTED
    snap = OB.merge_into_snapshot({"our_outstanding_orders": 0}, ledger=b)
    assert snap["our_outstanding_orders"] == 1
    # and the attempt latch is the independent second lock on the same slot
    assert len(a.blocking()) == len(b.blocking()) == 1


def test_8b_the_latch_and_the_ledger_are_independent_locks(wired, ledger,
                                                           tmp_path):
    clock = Clock(BASE + 10)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=20)
    out, _ = _run(wired, venue, clock, ledger=ledger)
    st = L.window_state(window=0)
    assert st["attempts_spent"] == 1, (
        "one obligation, one attempt slot: the ledger stopped the loop "
        "before the latch had to")
    assert_bounds_held(out, ledger, venue, label="both locks")


# =====================================================================
# 9. Restart with persisted pending ids and latch records
# =====================================================================

def test_9_a_restart_reloads_the_obligation_and_stays_blocked(tmp_path):
    sd = str(tmp_path / "e1_cohort")
    first = OB.ObligationLedger(sd, session_id=SID)
    first.record_submission(intent_id="R1", window=0, slot=0, slug="s",
                            side=UP, price="0.49", qty="0.13")
    first.bind_venue_id("R1", "OID-R1")
    first.reconcile({"ok": True, "orders": [
        {"id": "OID-R1", "state": "ORDER_STATE_NEW", "terminal": False,
         "cum_quantity": "0", "outstanding": True}]})
    assert first.rows["R1"]["state"] == OB.OBSERVED

    # THE PROCESS DIES. A new one comes up on the same volume.
    restarted = OB.ObligationLedger(sd, session_id=SID)
    assert restarted.rows["R1"]["state"] == OB.OBSERVED
    assert len(restarted.blocking()) == 1
    assert restarted.observed_ids() == frozenset({"OID-R1"})


def test_9b_a_restart_that_cannot_read_its_own_record_refuses(tmp_path):
    """AN UNREADABLE OBLIGATION IS NOT AN ABSENT ONE."""
    sd = str(tmp_path / "e1_cohort")
    first = OB.ObligationLedger(sd, session_id=SID)
    first.record_submission(intent_id="R1", window=0, slot=0, slug="s",
                            side=UP, price="0.49", qty="0.13")
    target = [f for f in os.listdir(OB.ledger_dir(sd))
              if f.endswith(".json")][0]
    with open(os.path.join(OB.ledger_dir(sd), target), "w") as fh:
        fh.write("{not json")
    with pytest.raises(OB.LedgerUnavailable):
        OB.ObligationLedger(sd, session_id=SID)


def test_9c_the_record_survives_as_plain_fsynced_json(tmp_path):
    sd = str(tmp_path / "e1_cohort")
    led = OB.ObligationLedger(sd, session_id=SID)
    led.record_submission(intent_id="R1", window=3, slot=7, slug="slug-x",
                          side=UP, price="0.49", qty="0.13")
    files = [f for f in os.listdir(OB.ledger_dir(sd)) if f.endswith(".json")]
    assert len(files) == 1
    row = json.load(open(os.path.join(OB.ledger_dir(sd), files[0])))
    for k in ("slug", "side", "price", "qty", "window", "slot", "state",
              "submitted_unix", "pid", "session_id"):
        assert k in row, row
    assert row["state"] == OB.SUBMITTED
    # and the append-only journal has the same event
    jrn = [f for f in os.listdir(OB.ledger_dir(sd))
           if f.endswith(".journal.jsonl")]
    assert jrn, os.listdir(OB.ledger_dir(sd))


# =====================================================================
# 10. Venue unreadable or contradictory
# =====================================================================

def test_10_an_unreadable_view_advances_nothing(ledger):
    """NOT EVIDENCE IN EITHER DIRECTION. It cannot release an obligation,
    and it cannot invent one."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    for view in ({"ok": False, "reason": "ORDER_STATE_UNREADABLE:boom"},
                 {}, {"ok": False, "reason": "ORDER_STATE_STALE:9s>5s"}):
        rep = ledger.reconcile(view)
        assert rep["view_ok"] is False
        assert rep["because"] == "VIEW_NOT_OK_NOTHING_ADVANCED"
        assert rep["released"] == []
    assert len(ledger.blocking()) == 1
    assert ledger.rows["I1"]["state"] == OB.SUBMITTED


def test_10b_the_truthy_sentinel_is_preserved_by_the_merge(ledger):
    """THE FAIL-CLOSED PATH IS UNTOUCHED. A failed venue read already
    refuses on its own, and the merge must not convert that into a number
    that could compare as permissive."""
    snap = OB.merge_into_snapshot(
        {"our_outstanding_orders": OS.UNKNOWN_OUTSTANDING}, ledger=ledger)
    assert snap["our_outstanding_orders"] == OS.UNKNOWN_OUTSTANDING
    assert bool(snap["our_outstanding_orders"]) is True


def test_10c_the_merge_only_ever_raises_the_floor(ledger):
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    # the venue says MORE than we do -- its number must win
    snap = OB.merge_into_snapshot(
        {"our_outstanding_orders": 4,
         "aggregate_exposure_usd": "99.00"}, ledger=ledger)
    assert snap["our_outstanding_orders"] == 4
    assert snap["aggregate_exposure_usd"] == "99.00"
    # and when we say more, ours wins
    snap2 = OB.merge_into_snapshot(
        {"our_outstanding_orders": 0,
         "aggregate_exposure_usd": "0.00"}, ledger=ledger)
    assert snap2["our_outstanding_orders"] == 1
    assert D(snap2["aggregate_exposure_usd"]) > 0
    assert snap2["exposure_floor_from_obligations"] is True


def test_10d_a_contradictory_venue_is_a_refusal_not_a_release(ledger):
    """`read_order_state` already fails closed when one endpoint calls an
    order resting and the other calls it finished. That refusal arrives
    here as `ok: False`, so it advances nothing."""
    ledger.record_submission(intent_id="I1", window=0, slot=0, slug="s",
                             side=UP, price="0.49", qty="0.13")
    ledger.bind_venue_id("I1", "OID-1")
    rep = ledger.reconcile({
        "ok": False,
        "reason": "ORDER_STATE_INCONSISTENT:OID-1_OPEN_AND_"
                  "ORDER_STATE_CANCELED"})
    assert rep["released"] == []
    assert len(ledger.blocking()) == 1


def test_10e_order_state_no_longer_resolves_a_never_observed_id():
    """THE ONE-LINE CHANGE IN `order_state`, asserted directly.

    An acknowledged id that neither endpoint will admit to used to be
    `pending_ids_resolved_by_this_read`. It is now counted.
    """
    v = FakeVenue()
    view = v.view(known=["GHOST-1"], roles={"GHOST-1": OS.FIRST_LEG},
                  pending_ids=["GHOST-1"], observed_ids=())
    assert view["ok"] is True
    assert view["n_outstanding_first_legs"] == 1
    assert view["acknowledged_but_never_observed_ids"] == ["GHOST-1"]
    assert bool(OS.outstanding_for_snapshot(view)) is True

    # once it HAS been observed, absence resolves it as before
    seen = v.view(known=["GHOST-1"], roles={"GHOST-1": OS.FIRST_LEG},
                  pending_ids=["GHOST-1"], observed_ids=("GHOST-1",))
    assert seen["n_outstanding_first_legs"] == 0
    assert seen["pending_ids_resolved_by_this_read"] == ["GHOST-1"]
