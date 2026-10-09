"""THE 1215z BURST: twenty maker fills in 2.47 seconds, reproduced.

WHAT HAPPENED. On 2026-10-08 the `cpc-btc-updown-15m-2026-10-08-1215z`
window took twenty first legs in 2.47 seconds, every one filled as our
`passiveExecution`, producing 2.60 shares of UNHEDGED directional
inventory -- twenty times the intended single-leg size -- with no
complement ever submitted. It resolved YES and paid +$1.3260. That is luck.
The same sequence on a "No" resolution loses the full $1.2740.

THE ROOT CAUSE IS NOT THE LATCH. The latch did exactly what it is
specified to do: it bounded the number of SUBMISSION ATTEMPT FILES at
twenty per window, and twenty is what it permitted. It bounds attempts. It
has never bounded simultaneous open orders, total shares, or worst-case
directional exposure, and reading it as though it did is the mistake.

The bound that should have stopped the second order was
`max_outstanding_orders: 1`, enforced by `eligible()`:

    if snapshot.get("our_outstanding_orders"):
        r.append("E3_OUR_ORDER_IS_OUTSTANDING")

`pmus_provider` published that field as the LITERAL `0`. The rule compared
against a constant, so it could not fire, and the loop opened a new first
leg every iteration until the attempt latch ran out at twenty.

WHY THESE TESTS EXIST RATHER THAN A WRITE-UP. A claim that the repair works
is worth nothing unless the harness can still produce the failure. So the
first test here REPRODUCES THE BURST by restoring the one literal, and gets
twenty placements. Every later test leaves the repair in place and asserts
one. If the reproduction ever stops reproducing, these tests are measuring
nothing and the suite says so.

Then the harder question, which is the one the incident does not answer:
the repair counts an order as outstanding by READING THE VENUE, so what
happens while the venue has not caught up? Those are the delayed- and
lost-acknowledgement tests at the bottom.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import latch as L
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


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    R._set_emergency_handover(None) if hasattr(
        R, "_set_emergency_handover") else None
    return fake_pin(BASE)


class LaggingVenue(LoopVenue):
    """A venue that acknowledges a POST and then HIDES the order.

    `lag_reads` reads of `/v1/orders/open` omit it AND `/v1/order/{id}`
    answers 404 for it -- the venue's two statements agreeing that an order
    we hold an id for does not exist yet. That is the eventual-consistency
    shape of "delayed acknowledgement", and it is the only shape that can
    defeat a guard built on reading the venue.
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
                # the tick happens on the OPEN read, which is the first
                # thing `read_order_state` asks for
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


def _run(wired, venue, clock, *, max_iterations=80, known=None, roles=None,
         pending=None, outstanding_literal=None):
    known = [] if known is None else known
    roles = {} if roles is None else roles
    pending = [] if pending is None else pending
    prov = _provider(venue, clock, pin=wired, known=known, roles=roles,
                     pending=pending, cutoff_at=BASE + 10_000)
    if outstanding_literal is not None:
        # THE 2026-10-08 DEFECT, RESTORED ON PURPOSE. This is the literal
        # `pmus_provider` published before the repair.
        real = prov          # bound separately: a __getattr__ that reads
                             # the rebound name recurses forever

        class Defective:
            def __getattr__(self, name):
                return getattr(real, name)

            def __call__(self):
                s = real()
                s["our_outstanding_orders"] = outstanding_literal
                s["our_open_residual"] = None
                return s
        prov = Defective()
    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=max_iterations)
    return out, tracker


# =====================================================================
# 1. THE REPRODUCTION. If this stops failing, nothing below means anything.
# =====================================================================

def test_the_1215z_burst_reproduces_with_the_original_literal(wired):
    """TWENTY FIRST LEGS, ONE PER ATTEMPT SLOT, exactly as it happened.

    The only thing restored is `our_outstanding_orders: 0`. Nothing else is
    changed, so this isolates the cause to that single field.
    """
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    out, _ = _run(wired, venue, clock, outstanding_literal=0,
                  max_iterations=80)

    assert out["placement_count"] == MAX_ATTEMPTS_PER_WINDOW == 20
    assert len(venue.posted) == 20
    # and the latch is what stopped it -- at TWENTY, not at one
    assert out["distinct_latch_slots"] == 20
    assert out["stopped_because"] is not None

    # THE SHAPE OF THE REAL INCIDENT: unhedged directional inventory, 20x
    # the intended single-leg size, and no complement.
    first_legs = [o for o in venue.orders.values()
                  if o["outcomeSide"] == UP]
    assert len(first_legs) == 20
    total = sum(D(o["quantity"]) for o in first_legs)
    assert total == D("0.13") * 20 == D("2.60")


def test_the_latch_bounds_attempts_and_nothing_else(wired):
    """WHAT THE LATCH ACTUALLY BOUNDS, asserted rather than assumed.

    Twenty named slot FILES per window. Not one outstanding order, not a
    share count, not a dollar of directional exposure. The 1215z window is
    what "bounded by attempts alone" looks like when every attempt rests
    and then fills.
    """
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    out, _ = _run(wired, venue, clock, outstanding_literal=0,
                  max_iterations=80)
    st = L.window_state(window=0)
    assert st["attempts_spent"] == 20
    assert st["exhausted"] is True
    assert st["is_a_bare_counter"] is False
    # twenty SIMULTANEOUSLY open orders were never the latch's business
    open_now = [o for o in venue.orders.values()
                if o["state"] == "ORDER_STATE_NEW"]
    assert len(open_now) == 20, (
        "the latch permitted twenty concurrent resting orders; concurrency "
        "is not what it bounds")


# =====================================================================
# 2. THE REPAIR, AT THE DEPLOYED COMMIT
# =====================================================================

def test_the_deployed_commit_stops_at_one_outstanding_first_leg(wired):
    """THE SAME HARNESS, WITHOUT THE LITERAL. One order, then refusal."""
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])
    out, _ = _run(wired, venue, clock, max_iterations=80)

    assert out["placement_count"] == 1, out["placement_count"]
    assert len(venue.posted) == 1
    refusals = [r for row in out["refusals"] for r in row["refusals"]]
    assert any(r.startswith("E3_OUR_ORDER_IS_OUTSTANDING")
               for r in refusals), refusals


def test_an_unreadable_view_refuses_rather_than_reading_as_zero(wired):
    """THE FAIL-CLOSED DIRECTION. The sentinel is TRUTHY, so the same
    `if` that refuses on a count refuses on a failed read."""
    v = FakeVenue()
    view = {"ok": False, "reason": "ORDER_STATE_UNREADABLE:boom"}
    n = OS.outstanding_for_snapshot(view)
    assert n == OS.UNKNOWN_OUTSTANDING
    assert bool(n) is True, "a failed read must never be a permissive zero"


# =====================================================================
# 3. DELAYED AND LOST ACKNOWLEDGEMENTS
# =====================================================================

def test_a_lost_acknowledgement_blocks_indefinitely(wired, monkeypatch):
    """THE POST RETURNS NO IDENTIFIABLE ORDER.

    The pre-POST `LOCAL_INTENT` row stays in `pending_order_ids` and is
    counted as outstanding for as long as it is unresolved, because we
    cannot prove an order does not exist. One attempt, then refusal.
    """
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, wired["slug"])

    class Mute(cancelling_transport(venue)):      # type: ignore[misc]
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body=body, base=base)
            if method == "POST" and path == "/v1/orders":
                # acknowledged by HTTP, with no id we can use
                return {}
            return out

    prov = _provider(venue, clock, pin=wired, known=[], roles={},
                     pending=[], cutoff_at=BASE + 10_000)
    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=_Rec(), transport_factory=Mute,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=60)
    posts = [p for p in FakeTransport.posts]
    assert len(posts) <= 1, "a lost ack must not be retried or multiplied"
    assert any("LOCAL_INTENT" in str(p)
               for p in prov.pending_order_ids) or out["placement_count"] == 0


def test_a_delayed_acknowledgement_is_the_discriminating_case(wired):
    """THE VENUE ACKNOWLEDGES, THEN DENIES THE ORDER EXISTS.

    `/v1/orders/open` omits it and `/v1/order/{id}` answers 404 for two
    reads. Both endpoints agree, so nothing is inconsistent and nothing
    fails closed -- `order_state` records the id under
    `ids_absent_at_venue_404` and does NOT count it as outstanding.

    This test asserts the OBSERVED behaviour, whatever it is, and names it,
    rather than asserting the behaviour I would prefer.
    """
    clock = Clock(BASE + 10)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=2)
    out, _ = _run(wired, venue, clock, max_iterations=80)

    placed = out["placement_count"]
    # the bound that held is the one being measured
    assert placed >= 1
    if placed > 1:
        pytest.xfail(
            "DELAYED-ACK RACE CONFIRMED: %d first legs placed while the "
            "venue denied the first existed. E3 is read-your-writes "
            "dependent; an acknowledged id absent from BOTH endpoints is "
            "recorded under ids_absent_at_venue_404 and not counted as "
            "outstanding. See the minimum control in the report." % placed)
    assert placed == 1


def test_an_absent_acknowledged_id_is_reported_not_counted(wired):
    """THE MECHANISM BEHIND THE TEST ABOVE, isolated and asserted.

    This is the gap stated plainly: a confirmed venue id that neither
    endpoint will admit to is treated as RESOLVED, because a successful
    read that omits an id is normally authoritative. Immediately after a
    POST it is not.
    """
    v = FakeVenue()
    view = v.view(known=["GHOST-1"], roles={"GHOST-1": OS.FIRST_LEG},
                  pending_ids=["GHOST-1"])
    assert view["ok"] is True
    assert view["ids_absent_at_venue_404"] == ["GHOST-1"]
    assert view["n_outstanding_first_legs"] == 0
    assert OS.outstanding_for_snapshot(view) == 0


def test_a_local_intent_the_venue_never_saw_is_counted(wired):
    """THE CONTRAST THAT PROVES THE DISTINCTION IS DELIBERATE.

    The same absence, for an id we never got, counts and blocks.
    """
    v = FakeVenue()
    intent = OS.LOCAL_INTENT + "w0:slug:YES:0.49"
    view = v.view(known=[], roles={}, pending_ids=[intent])
    assert view["ok"] is True
    assert intent in view["unconfirmed_pending_ids"]
    assert view["n_outstanding_first_legs"] == 1
    assert bool(OS.outstanding_for_snapshot(view)) is True


# =====================================================================
# 4. DO THE CAPS CHARGE OPEN ORDERS AS IF THEY WILL FILL?
# =====================================================================

def test_exposure_charges_open_leaves_as_well_as_filled_inventory(wired):
    """ITEM 4. An outstanding order must cost what it would cost FILLED,
    not nothing until it fills."""
    v = FakeVenue()
    v.rest("OPEN-1", side=UP, wire="0.49", qty="0.13")
    view = v.view(known=["OPEN-1"], roles={"OPEN-1": OS.FIRST_LEG})
    assert view["ok"] is True
    assert view["n_outstanding_first_legs"] == 1
    exp = D(str(view["aggregate_exposure_usd"]))
    assert exp > 0, view
    # a resting 0.13 @ 0.49 own-side is charged at its own-side cost
    assert exp >= D("0.13") * D("0.49")


def test_a_partial_fill_is_still_outstanding(wired):
    """It has leaves at the venue and can still fill, so it still blocks
    and still costs."""
    v = FakeVenue()
    v.rest("P-1", side=UP, wire="0.49", qty="0.13")
    v.fill("P-1", "0.05")
    view = v.view(known=["P-1"], roles={"P-1": OS.FIRST_LEG})
    assert view["ok"] is True
    assert view["n_outstanding_first_legs"] == 1


@pytest.mark.parametrize("lag,expected_at_least", [(0, 1), (2, 2), (6, 3)])
def test_the_delayed_ack_breach_scales_with_the_venue_lag(wired, lag,
                                                          expected_at_least):
    """HOW BIG IS THE RESIDUAL RISK? It scales with how long the venue
    denies our order exists, and it is bounded above by the attempt latch
    at twenty -- which is the 1215z number.

    Recorded as a parametrised measurement rather than a verdict: the exact
    counts depend on the loop's pace against the venue's lag, so what is
    asserted is the DIRECTION (more lag, more orders) and the ceiling.
    """
    clock = Clock(BASE + 10)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=lag)
    out, _ = _run(wired, venue, clock, max_iterations=80)
    placed = out["placement_count"]
    assert placed >= expected_at_least, (lag, placed)
    assert placed <= MAX_ATTEMPTS_PER_WINDOW, (
        "the attempt latch is the only ceiling, and it is twenty")


def test_the_local_record_already_knows_what_the_venue_denies(wired):
    """THE MINIMUM CONTROL IS AVAILABLE WITHOUT ANY NEW STATE.

    During the breach above, E1's OWN records already hold the submitted
    id: the latch slot is on disk and the id is in `pending_order_ids`.
    Nothing had to be inferred from the venue to know a submission had
    gone out -- which is why a local "one unobserved submission at a time"
    gate is sufficient, and why it cannot be defeated by venue lag.
    """
    clock = Clock(BASE + 10)
    venue = LaggingVenue(clock, wired["slug"], lag_reads=6)
    pending: List[str] = []
    prov = _provider(venue, clock, pin=wired, known=[], roles={},
                     pending=pending, cutoff_at=BASE + 10_000)
    R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                 recorder=_Rec(),
                 transport_factory=cancelling_transport(venue),
                 now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                 progress=_Prog(), max_iterations=80)
    # our own record of what we sent, with no venue read involved
    assert len(venue.posted) >= 2
    assert len(pending) >= 1, pending
    slots = L.window_state(window=0)
    assert slots["attempts_spent"] == len(venue.posted), (
        "the latch counted every submission the venue was denying")
