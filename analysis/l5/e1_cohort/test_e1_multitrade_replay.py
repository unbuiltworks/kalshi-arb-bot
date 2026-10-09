"""REPLAY AND SHADOW: THREE SEQUENTIAL PAIRS IN ONE WINDOW.

Driven through `run_cohort` and nothing else. The test never calls
`submit_first_leg`, never calls `submit_complement`, never touches the
lifecycle and never writes to the venue directly. Orders exist because the
production loop posted them; fills exist because the simulated venue
decided to, on its own clock. If the loop cannot sequence three
OPEN -> FILL -> COMPLETE cycles against real venue state, these fail.

WHAT IS BEING VALIDATED, beyond "it places three orders":

  * attempt N+1 reads the inventory attempt N actually created -- from the
    venue and from the durable latch, not from a counter;
  * a partial fill is complemented for what filled and no more, and the
    uncovered remainder is not double-covered on the next iteration;
  * a complement that is slow to fill does not release the one-outstanding
    block, and does not become a second complement for the same shares;
  * the venue contradicting itself about our own order -- resting in the
    open list, 404 on its own record, which is what PMUS actually did to
    `CZS79ZXA4Z84` at 22:00z -- stops the engine from acting rather than
    being resolved in the convenient direction;
  * the four envelope bounds refuse the FOURTH attempt, and the cumulative
    figures they refuse on are the ones the venue confirmed;
  * the attempt tape exists afterwards and `reconstruct_chain` reads it.

SHADOW is the same loop with the envelope stated and the transport refusing
every mutation, so the decision sequence is produced without an order
existing anywhere.
"""
from __future__ import annotations

from decimal import Decimal as D
from typing import Any, Dict, List

import pytest

from analysis.l5.e1_cohort import attempt_rows as AR
from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import multitrade as MT
from analysis.l5.e1_cohort import order_state as OS
from analysis.l5.e1_cohort import recorder as RC
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import stopping as SP
from analysis.l5.e1_cohort.test_e1_live_path import (FakeTransport, arm,
                                                     fake_pin, BASE)
from analysis.l5.e1_cohort.test_e1_pair_lifecycle import (FakeVenue, UP,
                                                          DOWN)
from analysis.l5.e1_cohort.test_e1_run_cohort_lifecycle import (Clock,
                                                                LoopVenue,
                                                                _provider,
                                                                _Prog, _Rec)

W = 900

#: three attempts, the frozen 0.13, and the $0.96 pair ceiling.
ENV3 = None


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Armed environment, own state dir, own latch, clean POST log."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    FakeTransport.cancels = []
    arm(monkeypatch)
    return fake_pin(BASE)


def envelope():
    return MT.MultiTradeEnvelope.for_attempts(3)


def run(prov, clock, *, recorder=None, iterations=400, envelope_=None):
    return R.run_cohort(
        snapshot_provider=prov, tracker=SP.CohortTracker(),
        recorder=recorder if recorder is not None else _Rec(),
        transport_factory=FakeTransport, now_fn=clock,
        sleep_fn=clock.sleep, pace_s=2.0, progress=_Prog(),
        max_iterations=iterations,
        envelope=envelope_ if envelope_ is not None else envelope())


# ============ 1. THREE SEQUENTIAL PAIRS, EACH READING THE LAST ==========

def test_three_pairs_sequence_and_each_attempt_reads_real_inventory(wired):
    """OPEN -> FILL -> COMPLETE, three times, in one window."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    # the whole window is available, so the three cycles are limited by
    # the engine's own sequencing rather than by the harness's clock.
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending, cutoff_at=BASE + 800)
    # the venue fills whatever is resting, repeatedly, so the loop has to
    # open each next first leg itself.
    venue.fill_plan = [{"at": BASE + 12 + 4 * i, "qty": None,
                        "match": (lambda oid, o: True)}
                       for i in range(12)]
    out = run(prov, clock, iterations=600)

    # EXACTLY THREE first legs. Not two, not four.
    assert out["placement_count"] == 3, out["refusals"][:4]
    assert out["distinct_latch_slots"] == 3
    legs = out["lifecycle"]["first_legs_tracked"]
    assert len({k for k, v in legs.items() if v["window"] == 0}) == 3

    # and every one of them was complemented, by the loop
    comps = [c for e in out["lifecycle"]["events"]
             for c in (e.get("complements") or [])]
    assert len(comps) >= 3, comps
    assert all(c["submitted"] for c in comps)
    # every first leg that FILLED was complemented, and each exactly once
    assert len({c["order_id"] for c in comps}) == 3, comps

    # NO DUPLICATE COVER: each first leg is covered exactly once for 0.13
    covered: Dict[str, D] = {}
    for c in comps:
        covered[c["order_id"]] = covered.get(c["order_id"], D("0")) \
            + D(str(c["qty"]))
    assert all(v <= D("0.13") for v in covered.values()), covered


def test_the_fourth_attempt_is_refused_by_the_envelope(wired):
    """Three slots spent; the envelope refuses a fourth on the cumulative
    figures the venue confirmed, not on a counter."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [{"at": BASE + 10 + 6 * i, "qty": None,
                        "match": (lambda oid, o: True)}
                       for i in range(8)]
    out = run(prov, clock, iterations=600)
    assert out["placement_count"] <= 3, out["placement_count"]
    codes = [c for r in out["refusals"] for c in r.get("refusals", [])]
    assert any(str(c).startswith("MT_") for c in codes), codes[:8]


def test_a_second_first_leg_is_refused_while_one_is_resting(wired):
    """One outstanding first leg at a time, from venue state."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []                        # nothing ever fills
    out = run(prov, clock, iterations=60)
    assert out["placement_count"] == 1, out["placement_count"]
    codes = [c for r in out["refusals"] for c in r.get("refusals", [])]
    assert any("E3_OUR_ORDER_IS_OUTSTANDING" in str(c) for c in codes)


# =================== 2. PARTIAL FILLS AND SLOW COMPLEMENTS ==============

def test_a_partial_fill_is_complemented_for_what_filled_only(wired):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [
        {"at": BASE + 14, "qty": "0.05",
         "match": lambda oid, o: o["outcomeSide"] == UP},
    ]
    out = run(prov, clock, iterations=80)
    comps = [c for e in out["lifecycle"]["events"]
             for c in (e.get("complements") or [])]
    assert comps, "a partial fill must still be complemented"
    # the tranche covers 0.05, never the unfilled 0.13
    assert D(str(comps[0]["qty"])) == D("0.05")
    total = sum(D(str(c["qty"])) for c in comps)
    assert total <= D("0.05"), comps


def test_a_slow_complement_does_not_become_a_second_complement(wired):
    """The complement rests unfilled. The shortfall is already covered, so
    no second tranche may be submitted for the same shares."""
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [
        {"at": BASE + 14, "qty": None,
         "match": lambda oid, o: o["outcomeSide"] == UP},
    ]
    out = run(prov, clock, iterations=120)
    comps = [c for e in out["lifecycle"]["events"]
             for c in (e.get("complements") or [])]
    per_leg: Dict[str, D] = {}
    for c in comps:
        per_leg[c["order_id"]] = per_leg.get(c["order_id"], D("0")) \
            + D(str(c["qty"]))
    assert all(v <= D("0.13") for v in per_leg.values()), per_leg


# ============== 3. THE VENUE CONTRADICTING ITSELF =======================

def test_resting_but_404_on_its_own_record_stops_the_engine_acting(wired):
    """THE 22:00z INCONSISTENCY, reproduced.

    PMUS listed `CZS79ZXA4Z84` in the open-order list and answered 404 on
    `GET /v1/order/CZS79ZXA4Z84`. Two endpoints disagreeing about an order
    of ours is a refusal, not a coin flip -- the engine must neither treat
    it as absent (and open another first leg) nor as filled (and hedge
    inventory it may not hold).
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []

    real_reader = venue.reader

    def contradicting_reader():
        base = real_reader()

        class _NotFound(Exception):
            def __init__(self):
                super().__init__("no such order")
                self.code = 404

        def _read(path):
            if path.startswith("/v1/order/") and venue.posted:
                oid = path.rsplit("/", 1)[-1]
                # it IS in the open list, and its own record 404s
                if oid in venue.orders:
                    raise _NotFound()
            return base(path)
        return _read

    venue.reader = contradicting_reader
    out = run(prov, clock, iterations=40)
    codes = [c for r in out["refusals"] for c in r.get("refusals", [])]
    joined = " ".join(str(c) for c in codes)
    assert "IS_RESTING_BUT_404_ON_ITS_OWN_RECORD" in joined, codes[:6]
    # and NO second first leg was opened on the strength of the 404
    assert out["placement_count"] == 1, out["placement_count"]


# =================== 4. THE TAPE THE RUN LEAVES BEHIND ==================

def test_the_run_writes_an_attempt_tape_that_reconstructs(wired, tmp_path):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = [{"at": BASE + 10 + 6 * i, "qty": None,
                        "match": (lambda oid, o: True)}
                       for i in range(8)]
    rec = RC.Recorder(str(tmp_path / "tape"))
    out = run(prov, clock, recorder=rec, iterations=600)

    tape = out["attempt_tape"]
    assert tape is not None, "the production path wrote no attempt tape"
    assert tape["errors"] == [], tape["errors"]
    assert tape["n_written"] == out["placement_count"]

    rows = rec.read(RC.ATTEMPTS)
    assert len(rows) == out["placement_count"]
    # every row carries the decision-time book state, so the reconstructor
    # can order the tape
    assert all(r.get("ws_state_at_decision") for r in rows)
    # inventory before and after, from the venue
    assert all(r["extra"]["inventory_after"]["source"]
               == "VENUE_AUTHORITATIVE" for r in rows)
    # nothing invented
    assert all(r["queue_state"]["observed"] is False for r in rows)
    assert all(r["markouts"]["observed"] is False for r in rows)
    assert all(r["REALIZED_CLAIM"] is False for r in rows)

    chain = RC.reconstruct_chain(rec)
    s1 = chain["step_1_first_leg_maker_fill_rate"]
    assert s1["placements"] == out["placement_count"]


def test_refusals_are_logged_with_inventory_and_reason(wired, tmp_path):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []
    rec = RC.Recorder(str(tmp_path / "tape"))
    run(prov, clock, recorder=rec, iterations=40)

    events = [e for e in rec.read(RC.EVENTS)
              if e.get("event") == "DECISION_REFUSED"]
    assert events, "no refusal was logged as a decision"
    d = events[0]["detail"]
    assert d["reason"], d
    assert "inventory_before" in d
    assert "market_state_observed" in d
    assert d["market_state_observed"]["first_leg_price"] is not None


def test_a_submission_is_logged_as_a_decision(wired, tmp_path):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []
    rec = RC.Recorder(str(tmp_path / "tape"))
    run(prov, clock, recorder=rec, iterations=40)
    subs = [e for e in rec.read(RC.EVENTS)
            if e.get("event") == "DECISION_SUBMITTED"]
    assert len(subs) == 1
    assert subs[0]["detail"]["reason"] == "ELIGIBLE_ON_EVERY_BOUND"
    assert subs[0]["detail"]["order_id"]


# ========================= 5. SHADOW ====================================

class RefusingTransport:
    """Every mutation refused. The decision sequence is produced; no order
    can exist anywhere."""

    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, base=None):
        self.calls.append((method, path))
        from analysis.l5.live_exec2 import orders as O
        if path == O.PREVIEW_PATH:
            return {"order": dict(body["request"])}
        raise O.MutationRefused("SHADOW: no mutation may leave this run")


def test_shadow_produces_decisions_and_places_nothing(wired):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []
    out = R.run_cohort(
        snapshot_provider=prov, tracker=SP.CohortTracker(), recorder=_Rec(),
        transport_factory=RefusingTransport, now_fn=clock,
        sleep_fn=clock.sleep, pace_s=2.0, progress=_Prog(),
        max_iterations=40, envelope=envelope())
    assert out["placement_count"] == 0
    assert venue.posted == []
    # it did evaluate and refuse, rather than doing nothing at all
    assert out["refusals"], "shadow produced no decisions"


def test_the_envelope_is_reported_in_the_result(wired):
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending)
    venue.fill_plan = []
    out = run(prov, clock, iterations=10)
    e = out["envelope"]
    assert e["max_cum_first_leg_shares"] == "0.39"
    assert e["max_gross_notional_usd"] == "0.3744"
    assert e["max_outstanding_usd"] == "0.2015"
    assert e["max_residual_shares"] == "0.13"
    assert e["first_leg_purchases_are_not_the_gross_outlay"] is True


# ========== 6. A SPENT ENVELOPE MUST NOT STRAND FILLED INVENTORY ========

def test_a_spent_gross_envelope_still_complements_a_filled_leg(wired):
    """THE REGRESSION, found in replay rather than in production.

    Three first legs placed, two complemented, the third FILLED -- then
    `MT_GROSS_NOTIONAL_*` refused a fourth attempt. The refusal was
    correct. Classifying it as a terminal refusal was not: the loop broke
    out of the window with 0.13 shares unhedged and the handover deadman
    never run, which is precisely the stranded-residual failure every
    other bound in this engine is shaped to avoid.

    A spent cash envelope blocks NEW FIRST LEGS. It must never block the
    complement that closes inventory already held, nor the exact-id
    cancellation at the handover.
    """
    pin = wired
    clock = Clock(BASE + 10)
    venue = LoopVenue(clock, pin["slug"])
    known, roles, pending = [], {}, []
    prov = _provider(venue, clock, pin=pin, known=known, roles=roles,
                     pending=pending, cutoff_at=BASE + 800)
    venue.fill_plan = [{"at": BASE + 12 + 4 * i, "qty": None,
                        "match": (lambda oid, o: True)}
                       for i in range(12)]
    out = run(prov, clock, iterations=600)

    legs = out["lifecycle"]["first_legs_tracked"]
    mine = {k: v for k, v in legs.items() if v["window"] == 0}
    assert len(mine) == 3
    # EVERY filled leg carries cover. None is left at "0".
    assert all(D(str(v["complemented"])) > 0 for v in mine.values()), mine
    # the envelope did refuse a fourth attempt
    codes = [c for r in out["refusals"] for c in r.get("refusals", [])]
    assert any(str(c).startswith("MT_GROSS_NOTIONAL_") for c in codes)
    # and it stopped for the envelope, not by running out of iterations
    assert out["iteration_cap_reached"] is False
