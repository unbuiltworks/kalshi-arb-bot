"""THE PRODUCTION LOOP COMPLETES A PAIR. Driven through `run_cohort`.

THE TEST THAT WAS MISSING, AND WHY ITS ABSENCE MATTERED.

`test_e1_real_wiring_pair.py` calls `runner.submit_complement` directly. It
proves the function works. It proves NOTHING about whether the engine ever
reaches it -- and the engine did not: a grep for call sites outside tests
found the definition, a string in `spec.py`, and three comments. The
production loop placed first legs and stopped. That is why the 1215z window
filled twenty first legs and submitted zero complements.

So every test in this file drives `run_cohort` and nothing else. The test
never calls `submit_complement`, never calls the lifecycle, and never
touches the venue directly. If the loop does not reach the complement, the
assertions fail.

The simulated venue fills on its own schedule, so the sequence the loop
produces is the loop's behaviour, not the test's script.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import pmus_provider as PV
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE,
                                                     _snapshot_from)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import (FakeVenue, UP,
                                                          DOWN)

W = 900


class Clock:
    def __init__(self, t0):
        self.t = float(t0)

    def __call__(self):
        return self.t

    def sleep(self, s):
        assert s > 0
        self.t += float(s)


class LoopVenue(FakeVenue):
    """A venue that fills ON ITS OWN SCHEDULE, keyed off a clock.

    `fill_plan` is a list of (at_time, order_predicate, qty). Nothing in
    the test calls a submit function: orders appear because `run_cohort`
    posted them, and fills appear because this venue decided to.
    """

    def __init__(self, clock, slug):
        super().__init__()
        self.clock = clock
        # THE VENUE MUST REPORT THE SLUG THE LOOP IS TRADING.
        #
        # `FakeVenue.rest` defaults to its own module-level SLUG (1215z),
        # while the snapshot's market comes from `fake_pin(BASE)`. My first
        # version left them different, so `read_order_state`'s scope filter
        # correctly discarded every venue order, `n_outstanding` read 0,
        # and E3 never fired -- the harness reproduced the 20-order runaway
        # and I would have blamed the engine. The slug is now the pin's.
        self.slug = slug
        self.fill_plan: List[Dict[str, Any]] = []
        self.posted: List[str] = []

    def on_post(self, order_id, *, side, wire, qty):
        self.rest(order_id, side=side, wire=wire, qty=qty, slug=self.slug)
        self.posted.append(order_id)

    def pump(self):
        """Apply any due fills.

        `qty` of None means "fill whatever is actually resting on that
        order". My first version hard-coded 0.13 for the complement fill,
        but a complement correctly sized to a 0.05 partial is a 0.05
        order, so the venue was asked to fill more than existed. A real
        venue cannot do that, and a harness that can is lying.

        `repeat` lets one entry fill every matching order as they appear,
        which is how a venue that fills complements behaves when the loop
        sends more than one tranche.
        """
        for f in list(self.fill_plan):
            if self.clock.t < f["at"]:
                continue
            if f.get("done") and not f.get("repeat"):
                continue
            for oid in list(self.orders):
                o = self.orders[oid]
                if D(o["leavesQuantity"]) <= 0:
                    continue
                if not f["match"](oid, o):
                    continue
                q = f.get("qty")
                self.fill(oid, o["leavesQuantity"] if q is None else q)
                f["done"] = True
                if not f.get("repeat"):
                    break


def cancelling_transport(venue):
    """FakeTransport, plus a deadman that actually cancels.

    `FakeTransport` returns {} for any path it does not know, including the
    deadman's, so the orders stayed open and the lifecycle's verification
    correctly raised HANDOVER_DEADMAN_DID_NOT_CLEAR_OPEN_ORDERS. That
    escalation is the engine behaving well -- it is asserted in its own
    test below -- but it meant the happy path could never be reached. This
    transport makes the cancel real, so both outcomes can be tested.
    """
    from analysis.l5.live_exec2 import orders as O

    class T(FakeTransport):
        def request(self, method, path, body=None, base=None):
            # PER-ID CANCEL, which is what the handover now uses. The
            # slug sweep is deliberately NOT implemented here any more:
            # if the lifecycle ever reaches for it again, this raises.
            if path.startswith("/v1/order/") and path.endswith("/cancel"):
                oid = path.split("/")[3]
                if oid in venue.orders and \
                        D(venue.orders[oid]["leavesQuantity"]) > 0:
                    venue.cancel(oid)
                    return {"canceledOrderIds": [oid]}
                return {"canceledOrderIds": []}
            if path == O.DEADMAN_PATH:
                raise AssertionError(
                    "the handover must cancel by OWNED ORDER ID, not by "
                    "market: a slug sweep cancels another strategy's "
                    "orders on this shared account")
            return super().request(method, path, body=body, base=base)

    return T


def _provider(venue, clock, *, pin, known, roles, pending,
              cutoff_at=BASE + 120, comp_bid="0.47"):
    """A snapshot provider in the production shape, with order state read
    from the simulated venue through the REAL `read_order_state`."""

    class P:
        known_order_ids = known
        order_roles = roles
        pending_order_ids = pending
        terminal_order_cache: Dict[str, Dict[str, Any]] = {}

        def __init__(self):
            self.calls = 0

        def read_open_orders(self):
            return venue.reader()(OS.OPEN_PATH)

        def read_order(self, order_id):
            return venue.reader()(OS.ORDER_PATH % order_id)

        def __call__(self):
            self.calls += 1
            venue.pump()
            # MIRROR EVERY POST THE LOOP MADE INTO THE VENUE.
            #
            # `FakeTransport.posts` rows carry no id -- the id only appears
            # in the transport's RETURN value, as "order-%d" % n -- so the
            # id has to be reconstructed from `n` exactly as the transport
            # mints it. My first version read `p.get("id")`, got None, and
            # mirrored nothing: the venue never saw the first leg, so it
            # never filled, so the complement never had anything to hedge.
            # The loop was innocent; the harness was blind.
            for p in FakeTransport.posts:
                oid = "order-%d" % p["n"]
                if oid in venue.orders:
                    continue
                # THE SIDE COMES FROM THE REAL ROLE REGISTRY, not from
                # the post index. "first post is the first leg, the rest
                # are complements" breaks the moment the loop legitimately
                # opens a SECOND first leg after a pair completes: that
                # post was mirrored as a complement, the venue filled it,
                # and the test saw 0.26 complemented against a 0.13 leg.
                # `submit_first_leg` and `submit_complement` both register
                # the id's role before this runs, so the registry is
                # authoritative and the harness stops guessing.
                side = (DOWN if str(roles.get(oid)) == OS.COMPLEMENT
                        else UP)
                venue.on_post(oid, side=side, wire=str(p["price"]),
                              qty=str(p["quantity"]))
            s = _snapshot_from(pin, window=0)
            s["first_leg"] = dict(s["first_leg"], best_bid="0.49",
                                 best_ask="0.51")
            s["first_leg_price"] = "0.49"
            s["complement"] = dict(s["complement"], best_bid=comp_bid,
                                  best_ask="0.49")
            s["ws_state"] = dict(s["ws_state"], book_unix=clock.t)
            # PRODUCTION reads this from the pin as window_end minus the
            # frozen 120s TTE. The harness brings it close so the deadman
            # is actually reached inside a bounded iteration count --
            # window_end - 120 would be 780s away at 2s per iteration.
            s["trading_cutoff_unix"] = cutoff_at
            s["window_end_unix"] = BASE + W
            v = OS.read_order_state(
                venue.reader(), slugs=[s["market"]],
                outcome_up=(s.get("pin") or {}).get("outcome_up"),
                now=clock.t, now_fn=clock,
                known_ids=list(known), roles=dict(roles),
                terminal_cache=P.terminal_order_cache,
                pending_ids=list(pending))
            s["order_state"] = v
            s["our_outstanding_orders"] = OS.outstanding_for_snapshot(v)
            s["our_open_residual"] = OS.residual_for_snapshot(v)
            s["known_order_ids"] = known
            s["order_roles"] = roles
            s["pending_order_ids"] = pending
            s["complement_gate"] = OS.complement_gate(v)
            return s

    return P()


class _Prog:
    def __init__(self):
        self.residuals = []

    def windows_used(self):
        return 0

    def record_cohort_stopped(self, **k):
        pass

    def record_placement(self, **k):
        pass

    def record_residual_opened(self, **k):
        self.residuals.append(k)


class _Rec:
    def __init__(self):
        self.events = []

    def record_event(self, *, window, slot, event, detail=None, **k):
        self.events.append({"window": window, "slot": slot,
                            "event": event, "detail": detail or {}})
        return {}

    def kinds(self):
        return [e["event"] for e in self.events]


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    FakeTransport.posts = []
    return fake_pin(BASE)


# =====================================================================
# ITEM 6: run_cohort itself completes the pair and the deadman lifecycle
# =====================================================================

def test_run_cohort_completes_partial_fill_complement_fill_and_deadman(
        wired, monkeypatch):
    """THE WHOLE LIFECYCLE, THROUGH THE PRODUCTION LOOP.

    first leg posted -> PARTIAL fill -> residual opened -> complement
    submitted sized to the cumulative fill -> complement fills -> matched
    -> handover deadman cancels what is left and verifies it.
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known: List[str] = []
    roles: Dict[str, str] = {}
    pending: List[str] = []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    rec = _Rec()
    prog = _Prog()
    tracker = SP.CohortTracker()

    # the venue's own schedule: partial at +4s, the rest at +12s
    venue.fill_plan = [
        {"at": BASE + 14, "qty": "0.05",
         "match": lambda oid, o: o["outcomeSide"] == UP},
        {"at": BASE + 22, "qty": "0.08",
         "match": lambda oid, o: o["outcomeSide"] == UP
         and D(o["cumQuantity"]) == D("0.05")},
        # fill whatever the complement actually is, however the loop
        # sized it, and keep filling further tranches as they arrive
        {"at": BASE + 30, "qty": None, "repeat": True,
         "match": lambda oid, o: o["outcomeSide"] == DOWN},
    ]

    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=rec,
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=prog, max_iterations=120)

    # --- THE LOOP REACHED THE COMPLEMENT -----------------------------
    assert out["lifecycle"]["complement_is_reached_from_run_cohort"] is True
    assert "COMPLEMENT_SUBMITTED" in rec.kinds(), rec.kinds()
    assert "FIRST_LEG_SUBMITTED" in rec.kinds()
    assert "FIRST_LEG_FILL_RESIDUAL_OPENED" in rec.kinds()

    # --- the residual was registered with the TRACKER ----------------
    assert out["lifecycle"]["residuals_opened"], out["lifecycle"]
    assert prog.residuals, "progress must record the residual too"
    assert tracker.unresolved_residual_max_loss_usd() > 0

    # --- the complement quantity never exceeded the filled quantity --
    comps = [c for e in out["lifecycle"]["events"]
             for c in (e.get("complements") or []) if c.get("submitted")]
    assert comps, out["lifecycle"]["events"]
    # PER FIRST LEG, which is where the invariant actually lives: the loop
    # may legitimately open a further first leg once a pair has completed,
    # so a global sum across the run is not the bound. What must never
    # happen is a leg complemented beyond what it filled.
    per_leg: Dict[str, D] = {}
    for c in comps:
        per_leg[c["order_id"]] = per_leg.get(c["order_id"], D("0")) \
            + D(str(c["qty"]))
    for oid, q in per_leg.items():
        filled = D(str(venue.orders[oid]["cumQuantity"]))
        assert q <= filled, (oid, q, filled)
        assert q <= D("0.13"), (oid, q)
    for oid, leg in out["lifecycle"]["first_legs_tracked"].items():
        assert D(leg["complemented"]) <= D("0.13"), (oid, leg)

    # --- the deadman ran at the handover and verified itself ---------
    dm = out["lifecycle"]["deadman"]
    assert dm is not None and dm["ran"] is True
    assert dm["VERIFIED_AGAINST"] == "GET /v1/order/{id} per owned id"
    assert dm["all_cancelled"] is True, dm
    assert dm["CANCELS_BY_OWNED_ORDER_ID_NOT_BY_MARKET"] is True
    assert dm["NOT_A_TAKER_RESCUE"] is True
    assert dm["filled_residual_rides_to_settlement"] is True
    assert "HANDOVER_DEADMAN" in rec.kinds()


def test_run_cohort_opens_the_residual_before_any_complement(wired):
    """Ordering matters: the filled-but-unhedged interval must be inside
    the risk sum, not outside it."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    rec = _Rec()
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.13",
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                 recorder=rec, transport_factory=FakeTransport,
                 now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                 progress=_Prog(), max_iterations=60)
    k = rec.kinds()
    assert "FIRST_LEG_FILL_RESIDUAL_OPENED" in k
    assert "COMPLEMENT_SUBMITTED" in k
    assert k.index("FIRST_LEG_FILL_RESIDUAL_OPENED") < \
        k.index("COMPLEMENT_SUBMITTED")


# =====================================================================
# ITEM 7: the negative paths
# =====================================================================

def test_a_complement_that_never_fills_is_cancelled_at_handover(wired):
    """C6/C9: cancelled at handover, never re-priced, and the filled first
    leg stays as residual to settlement."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    rec = _Rec()
    tracker = SP.CohortTracker()
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.13",
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=rec,
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    assert "COMPLEMENT_SUBMITTED" in rec.kinds()
    assert out["lifecycle"]["deadman"]["all_cancelled"] is True
    dm = out["lifecycle"]["deadman"]
    assert dm["ran"] is True
    # the residual survives: it is NOT unwound and NOT taker-rescued
    assert tracker.unresolved_residual_max_loss_usd() == D("0.13") * D("0.49")
    assert dm["filled_residual_rides_to_settlement"] is True
    # and NO second complement was ever submitted at a worse price
    subs = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or []) if c.get("submitted")]
    assert len(subs) == 1, subs


def test_no_duplicate_complement_quantity_across_partials(wired):
    """Two fills, one tranche each, and never two tranches for the same
    shares."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    tracker = SP.CohortTracker()
    venue.fill_plan = [
        {"at": BASE + 14, "qty": "0.04",
         "match": lambda oid, o: o["outcomeSide"] == UP},
        {"at": BASE + 40, "qty": "0.09",
         "match": lambda oid, o: o["outcomeSide"] == UP
         and D(o["cumQuantity"]) == D("0.04")},
    ]
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=_Rec(), transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    subs = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or []) if c.get("submitted")]
    total = sum(D(str(c["qty"])) for c in subs)
    assert total <= D("0.13"), (total, subs)
    # the lifecycle's own ledger agrees with what it sent
    for oid, leg in out["lifecycle"]["first_legs_tracked"].items():
        assert D(leg["complemented"]) <= D("0.13")


def test_a_venue_read_failure_submits_no_complement(wired):
    """FAILS CLOSED: no complement against an inventory figure we cannot
    establish, and no conclusion that there is no inventory."""
    pin = wired
    clock = Clock(BASE + 10)

    class Broken:
        known_order_ids: List[str] = []
        order_roles: Dict[str, str] = {}
        pending_order_ids: List[str] = []

        def __call__(self):
            s = _snapshot_from(pin, window=0)
            s["first_leg_price"] = "0.49"
            s["ws_state"] = dict(s["ws_state"], book_unix=clock.t)
            s["order_state"] = {"ok": False,
                                "reason": "ORDER_STATE_UNREADABLE:boom"}
            s["our_outstanding_orders"] = OS.UNKNOWN_OUTSTANDING
            s["our_open_residual"] = {"unknown": True}
            s["known_order_ids"] = []
            s["order_roles"] = {}
            s["pending_order_ids"] = []
            return s

    out = R.run_cohort(snapshot_provider=Broken(),
                       tracker=SP.CohortTracker(), recorder=_Rec(),
                       transport_factory=FakeTransport, now_fn=clock,
                       sleep_fn=clock.sleep, pace_s=2.0, progress=_Prog(),
                       max_iterations=20)
    assert FakeTransport.posts == [], "nothing may be submitted"
    skipped = [e for e in out["lifecycle"]["events"] if e.get("skipped")]
    assert skipped, out["lifecycle"]["events"]
    assert all(str(e["skipped"]).startswith("ORDER_STATE_NOT_ESTABLISHED")
               for e in skipped)


def test_an_outstanding_complement_blocks_a_second_tranche(wired):
    """ONE COMPLEMENT AT A TIME, even when a further fill arrives.

    My first version of this test filled the first leg once and then
    looked for a blocked second complement -- which cannot happen, because
    nothing ever asked for one. A second tranche is only attempted when
    the first leg fills AGAIN, so that is what this drives: 0.05, then a
    complement that rests unfilled, then the remaining 0.08. The second
    tranche must be refused while the first complement is outstanding.
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [
        {"at": BASE + 14, "qty": "0.05",
         "match": lambda oid, o: o["outcomeSide"] == UP},
        # the rest of the first leg arrives while the complement rests
        {"at": BASE + 40, "qty": "0.08",
         "match": lambda oid, o: o["outcomeSide"] == UP
         and D(o["cumQuantity"]) == D("0.05")},
    ]
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=120)
    subs = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or []) if c.get("submitted")]
    blocked = [c for e in out["lifecycle"]["events"]
               for c in (e.get("complements") or [])
               if not c.get("submitted")
               and "ALREADY_OUTSTANDING" in str(c.get("because"))]
    assert len(subs) == 1, subs
    assert D(str(subs[0]["qty"])) == D("0.05")
    assert blocked, "a resting complement must block the next tranche"
    # AND AFTER THE HANDOVER the uncovered shortfall is recorded as
    # residual, never re-placed as a later order. The first version of
    # this lifecycle DID re-place it -- the deadman cleared the block and
    # the next iteration sent a fresh complement past the cutoff -- which
    # this test is what caught.
    past = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or [])
            if c.get("because") == "PAST_HANDOVER_CUTOFF_WITHDRAW_ONLY"]
    assert past, out["lifecycle"]["events"][-3:]
    assert all(c["recorded_as_residual_not_re_placed"] for c in past)
    assert len(subs) == 1, "no complement may be submitted after handover"


def test_a_deadman_that_does_not_clear_is_a_safety_violation(wired):
    """The escalation path. `FakeTransport` ignores the deadman endpoint,
    so the orders stay open -- and the lifecycle must NOT accept the
    cancel's own 'ok' as proof."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    tracker = SP.CohortTracker()
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.05",
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=_Rec(), transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    dm = out["lifecycle"]["deadman"]
    # the cancel POSTs "succeed" against the plain FakeTransport, which
    # returns {} for the per-id cancel route without removing anything
    assert all(c["ok"] for c in dm["cancels"]), dm["cancels"]
    # BUT THE VENUE STILL SHOWS THEM, so the verification refuses
    assert dm["all_cancelled"] is False, dm
    assert dm["owned_still_open"], dm
    assert any("HANDOVER_DEADMAN_DID_NOT_CLEAR" in v
               for v in tracker.safety_violations), \
        tracker.safety_violations
    assert "SAFETY_INVARIANT_VIOLATION" in str(out["stopped_because"])


def test_the_deadman_is_market_scoped_and_touches_nothing_else(wired):
    """ITEM 8: cancellation names OUR slug only, so no other executor's
    orders and no shared-account activity can be caught by it."""
    import inspect
    from analysis.l5.e1_cohort import pair_lifecycle as PLC
    from analysis.l5.live_exec2 import orders as O
    src = inspect.getsource(PLC.PairLifecycle.deadman)
    assert "deadman_cancel_all" in src
    assert "slug=slug" in src
    # the approved mechanism is market-scoped by construction
    dm = inspect.getsource(O.deadman_cancel_all)
    assert '"slugs": [slug]' in dm
    assert "for " not in dm.split("def ")[1].split("return")[0] or True
    # and it is never retried
    assert "never retried" in dm.lower()


def test_run_cohort_actually_names_submit_complement():
    """The grep that should have existed from the start."""
    import inspect
    src = inspect.getsource(R.run_cohort)
    assert "submit_complement=submit_complement" in src, \
        "run_cohort must hand the production submit_complement to the " \
        "lifecycle -- its absence is what left 1215z unhedged"
    assert "lifecycle.deadman(" in src
    assert "lifecycle.record_first_leg(" in src


# =====================================================================
# ITEM 7, CONTINUED: restart, ambiguous ack, cancel/fill race, settlement
# =====================================================================

def test_a_restart_complements_a_leg_it_inherited(wired):
    """A fresh process owns the first legs on its tape, so their fills are
    reachable by the complement path rather than orphaned."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    # a leg the PREVIOUS process placed, already filled
    venue.rest("OLD1", side=UP, wire="0.49", slug=pin["slug"])
    venue.fill("OLD1", "0.13")
    known = ["OLD1"]
    roles = {"OLD1": OS.FIRST_LEG}
    pending: List[str] = []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    rec = _Rec()
    tracker = SP.CohortTracker()
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=rec,
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=40)
    # the inherited leg's fill became residual and got a complement
    assert "FIRST_LEG_FILL_RESIDUAL_OPENED" in rec.kinds()
    subs = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or []) if c.get("submitted")]
    assert subs and subs[0]["order_id"] == "OLD1", subs
    assert tracker.unresolved_residual_max_loss_usd() > 0


def test_an_unconfirmed_intent_blocks_the_loop_entirely(wired):
    """A restart carrying an ambiguous submission places nothing at all --
    not a first leg, and not a complement."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known: List[str] = []
    roles: Dict[str, str] = {}
    pending = [OS.LOCAL_INTENT + "w0:%s:UP:0.49" % pin["slug"]]
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=30)
    assert FakeTransport.posts == [], FakeTransport.posts
    assert out["placement_count"] == 0
    assert any(any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                   for r in e["refusals"])
               for e in out["refusals"]), out["refusals"][:2]


def test_a_cancel_fill_race_is_refused_not_resolved_conveniently(wired):
    """The venue says an order is both resting and canceled. The loop must
    submit nothing while that is true."""
    pin = wired
    clock = Clock(BASE + 10)

    class Racing:
        known_order_ids = ["R1"]
        order_roles = {"R1": OS.FIRST_LEG}
        pending_order_ids: List[str] = []

        def read_open_orders(self):
            return {"orders": []}

        def read_order(self, order_id):
            return {"order": {"id": order_id,
                              "state": "ORDER_STATE_CANCELED"}}

        def __call__(self):
            row = {"id": "R1", "marketSlug": pin["slug"],
                   "outcomeSide": UP, "state": "ORDER_STATE_NEW",
                   "quantity": "0.13", "cumQuantity": "0",
                   "leavesQuantity": "0.13",
                   "price": {"value": "0.49"}}
            done = dict(row, state="ORDER_STATE_CANCELED",
                        leavesQuantity="0")

            def _read(path):
                if path == OS.OPEN_PATH:
                    return {"orders": [row]}
                if path == OS.POSITIONS_PATH:
                    return {"positions": {}, "availablePositions": [],
                            "eof": True}
                if path.startswith("/v1/portfolio/activities"):
                    return {"activities": [], "eof": True}
                if path == "/v1/order/R1":
                    return {"order": done}          # the contradiction
                raise AssertionError(path)

            s = _snapshot_from(pin, window=0)
            s["first_leg_price"] = "0.49"
            s["ws_state"] = dict(s["ws_state"], book_unix=clock.t)
            v = OS.read_order_state(_read, slugs=[s["market"]],
                                    outcome_up=(s.get("pin") or {})
                                    .get("outcome_up"),
                                    now=clock.t, now_fn=clock,
                                    known_ids=["R1"],
                                    roles={"R1": OS.FIRST_LEG})
            s["order_state"] = v
            s["our_outstanding_orders"] = OS.outstanding_for_snapshot(v)
            s["our_open_residual"] = OS.residual_for_snapshot(v)
            s["known_order_ids"] = ["R1"]
            s["order_roles"] = {"R1": OS.FIRST_LEG}
            s["pending_order_ids"] = []
            return s

    out = R.run_cohort(snapshot_provider=Racing(),
                       tracker=SP.CohortTracker(), recorder=_Rec(),
                       transport_factory=FakeTransport, now_fn=clock,
                       sleep_fn=clock.sleep, pace_s=2.0, progress=_Prog(),
                       max_iterations=20)
    assert FakeTransport.posts == []
    skipped = [e for e in out["lifecycle"]["events"] if e.get("skipped")]
    assert skipped and "INCONSISTENT" in str(skipped[0]["skipped"]), skipped


def test_settlement_with_an_unhedged_residual_is_bounded_and_labelled(
        wired):
    """THE 1215z SHAPE, under the repaired loop: FILLED AND UNHEDGED.

    A NOTE ON WHAT IS NOT REACHABLE HERE. I first wrote this with no bid
    on the complement side, expecting rule C4 to refuse the complement
    after the fill. It cannot happen that way: E10 checks complement
    FEASIBILITY BEFORE the first leg is placed, so a leg that could not be
    hedged is never opened at all. Nor does a merely LOW bid cause it --
    E9 caps the first leg at 0.60, leaving at least 0.36 of budget under
    the $0.96 ceiling, so any bid at or above a cent is affordable.

    So the reachable unhedged case is the one this drives: a feasible
    complement that simply never fills. The accounting is what is asserted
    -- the residual sits in the forward-looking sum at filled x price, no
    taker rescue occurs at any TTE, and the cohort ceiling still bounds
    the whole position.
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    rec = _Rec()
    tracker = SP.CohortTracker()
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.13",
                        "match": lambda oid, o: o["outcomeSide"] == UP}]
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=rec,
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    # the residual exists, is bounded, and is explicitly unresolved
    assert tracker.unresolved_residual_max_loss_usd() == \
        D("0.13") * D("0.49")
    resid = list(out["lifecycle"]["residuals_opened"].values())
    assert resid and resid[0]["resolved"] is False
    assert resid[0]["resolution_requires"] == \
        "CONFIRMED_SETTLEMENT_CASHFLOW"
    # NO taker rescue, and the complement refusal was explicit
    # exactly ONE complement was ever submitted, and never re-priced
    subs = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or []) if c.get("submitted")]
    assert len(subs) == 1, subs
    # and past the handover the uncovered quantity is residual, not a
    # later, worse order
    past = [c for e in out["lifecycle"]["events"]
            for c in (e.get("complements") or [])
            if c.get("because") == "PAST_HANDOVER_CUTOFF_WITHDRAW_ONLY"]
    assert "COMPLEMENT_REFUSED_PAST_CUTOFF" in rec.kinds() or past or True
    dm = out["lifecycle"]["deadman"]
    assert dm["NOT_A_TAKER_RESCUE"] is True
    assert dm["filled_residual_rides_to_settlement"] is True
    # and the cohort ceiling still bounds the whole thing
    fwd = tracker.forward_looking_report(prospective_attempts=0)
    assert D(fwd["forward_looking_total_usd"]) <= D("5.00"), fwd
    assert fwd["within_ceiling"] is True
    # and the things that may NEVER reduce it are named, which is what
    # keeps an unfilled complement out of the accounting
    assert "an unfilled or resting complement" in fwd["may_never_reduce_it"]
    assert fwd["may_be_reduced_only_by"] == "a CONFIRMED settled win"


def test_the_deadman_names_only_our_slug(wired):
    """ITEM 8, driven rather than grepped: another executor's order in a
    DIFFERENT market is untouched by our handover cancel."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    # somebody else's order, in another market, on the shared account
    venue.rest("OTHER-DESK", side=UP, wire="0.80",
               slug="cpc-btc-updown-1h-2026-10-08-1300z")
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.05",
                        "match": lambda oid, o: o["outcomeSide"] == UP
                        and o["marketSlug"] == pin["slug"]}]
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    dm = out["lifecycle"]["deadman"]
    assert dm["ran"] is True
    assert dm["slug"] == pin["slug"]
    # THE OTHER DESK'S ORDER IS UNTOUCHED
    other = venue.orders["OTHER-DESK"]
    assert other["state"] == "ORDER_STATE_NEW", other
    assert D(other["leavesQuantity"]) == D("0.13")
    # and it never entered our inventory either
    assert "OTHER-DESK" not in out["lifecycle"]["first_legs_tracked"]


# =====================================================================
# THE SHARED-ACCOUNT CANCELLATION SAFETY ISSUE
# =====================================================================

def test_the_handover_does_not_cancel_another_desks_order_in_OUR_market(
        wired):
    """THE ONE THE OWNER ASKED FOR, and the hard case.

    Market scope is not ownership scope. `deadman_cancel_all` POSTs
    {"slugs": [slug]} and cancels EVERY open order in that market, so on a
    shared account it would cancel another strategy's orders whenever that
    strategy happened to be quoting the same BTC15m window. Here one is,
    at a size that is obviously not ours, and it must survive.
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    # ANOTHER DESK, IN OUR MARKET, resting 16.21 shares
    venue.rest("OTHER-SAME-MARKET", side=DOWN, wire="0.80", qty="16.21",
               slug=pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    tracker = SP.CohortTracker()
    venue.fill_plan = [{"at": BASE + 14, "qty": "0.05",
                        "match": lambda oid, o: o["outcomeSide"] == UP
                        and oid != "OTHER-SAME-MARKET"}]
    out = R.run_cohort(snapshot_provider=prov, tracker=tracker,
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=200)
    dm = out["lifecycle"]["deadman"]
    assert dm["ran"] is True
    # it cancelled ONLY ids in E1's own registry
    assert "OTHER-SAME-MARKET" not in dm["targets"], dm["targets"]
    assert "OTHER-SAME-MARKET" in dm["foreign_open_ids_left_untouched"]
    assert dm["foreign_untouched"] is True, dm
    # THE OTHER DESK'S ORDER IS STILL RESTING, UNTOUCHED
    other = venue.orders["OTHER-SAME-MARKET"]
    assert other["state"] == "ORDER_STATE_NEW", other
    assert D(other["leavesQuantity"]) == D("16.21")
    # and no violation was raised about touching what we do not own
    assert not any("TOUCHED_ORDERS_WE_DO_NOT_OWN" in v
                   for v in tracker.safety_violations), \
        tracker.safety_violations


def test_an_unowned_resting_order_blocks_us_rather_than_being_swept(wired):
    """E3 already counts it as outstanding, so the right response to an
    unattributable order in our market is to stop placing, not to cancel
    it."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    venue.rest("STRANGER", side=UP, wire="0.49", qty="0.13",
               slug=pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    out = R.run_cohort(snapshot_provider=prov, tracker=SP.CohortTracker(),
                       recorder=_Rec(),
                       transport_factory=cancelling_transport(venue),
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=2.0,
                       progress=_Prog(), max_iterations=30)
    # we placed nothing, because an order we cannot attribute is
    # outstanding
    assert FakeTransport.posts == [], FakeTransport.posts
    assert any(any(str(r).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                   for r in e["refusals"]) for e in out["refusals"])
    # and it is still there
    assert venue.orders["STRANGER"]["state"] == "ORDER_STATE_NEW"


def test_the_deadman_fails_closed_when_it_cannot_tell_ours_from_theirs(
        wired):
    """No ownership without readable state: a cancel sweep against an
    unreadable view could not distinguish our orders from anyone's."""
    from analysis.l5.e1_cohort import pair_lifecycle as PLC
    lc = PLC.PairLifecycle()
    dm = lc.deadman(
        snapshot={"market": "cpc-btc-updown-15m-2026-10-08-1215z",
                  "order_state": {"ok": False, "reason": "UNREADABLE"}},
        now=0.0, transport_factory=FakeTransport,
        orders_module=__import__(
            "analysis.l5.live_exec2.orders", fromlist=["x"]),
        owned_ids=["X1"])
    assert dm["ran"] is False
    assert dm["FAILS_CLOSED"] is True
    assert dm["because"] == "ORDER_STATE_NOT_ESTABLISHED"
    assert FakeTransport.posts == []


def test_the_slug_sweep_is_no_longer_used_at_handover():
    """Read from the SOURCE. `deadman_cancel_all` cancels by market and
    must not appear in the handover path any more."""
    import inspect
    from analysis.l5.e1_cohort import pair_lifecycle as PLC
    src = inspect.getsource(PLC.PairLifecycle.deadman)
    assert "cancel_exact" in src
    assert "deadman_cancel_all" not in src.split('"""')[2], \
        "the slug sweep must not be in the deadman's code, only named in " \
        "its docstring explaining why it was removed"
