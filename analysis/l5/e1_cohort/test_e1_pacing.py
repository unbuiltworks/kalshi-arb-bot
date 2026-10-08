"""THE PACING REPAIR, PROVEN AGAINST A BOOK THAT MOVES.

On 2026-10-08 the fresh cohort armed, opened
`cpc-btc-updown-15m-2026-10-08-0100z`, and spent all 960 of its iterations in
about one second. Every refusal was `E9_PRICE_ABOVE_CEILING:0.62`: the best bid
was 0.62 against the frozen $0.60 ceiling, so the bound did its job and nothing
was ever priced. The fault was that the loop did not WAIT. It consumed one of
twelve authorized windows and exited.

These tests are the owner's five groups. The decisive one is
`test_062_is_refused_then_059_becomes_eligible`: the same frozen $0.60 ceiling
refuses 0.62, the clock advances, the book improves to 0.59, and the SAME
unmodified loop places. The repair is patience, not a relaxed bound.
"""
from __future__ import annotations

import pytest

from analysis.l5.e1_cohort import latch as L
from analysis.l5.e1_cohort import pacing as PACING
from analysis.l5.e1_cohort import pmus_provider as PROV
from analysis.l5.e1_cohort import runner as R
from analysis.l5.e1_cohort import spec as SPC
from analysis.l5.e1_cohort import stopping as SP

from analysis.l5.e1_cohort.test_e1_live_path import (        # noqa: F401
    FakeTransport, _isolate, arm, fake_pin, _snapshot_from, BASE)


W = 900


# --------------------------------------------------------------- fixtures

class Clock:
    """A clock the test moves, and a sleep that MOVES IT. This is the whole
    seam: pacing that sleeps is pacing that lets the book change."""

    def __init__(self, t0):
        self.t = float(t0)
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        assert s > 0, "a paced wait must never be zero -- that is a busy loop"
        self.sleeps.append(float(s))
        self.t += float(s)


def book_at(bid, ask, *, window_start=None, window=0, now=None):
    """A snapshot whose first-leg book is exactly `bid`/`ask`.

    `now` sets `book_unix`. It matters: the shared helper hard-codes
    `BASE + 10`, so a snapshot reused across a MOVING clock goes stale
    against the 2.0 s AuthBook bound after two seconds and E7 refuses it --
    correctly. A real provider reads a fresh book on every call, so the fake
    has to as well, or these tests would measure a frozen fake instead of
    the pacing.
    """
    ws = int(BASE if window_start is None else window_start)
    s = _snapshot_from(fake_pin(ws), window=window)
    s["first_leg"] = dict(s["first_leg"], best_bid=bid, best_ask=ask)
    s["first_leg_price"] = bid
    s["ws_state"] = dict(s["ws_state"],
                         first_leg={"best_bid": bid, "best_ask": ask})
    if now is not None:
        s["ws_state"] = dict(s["ws_state"], book_unix=float(now))
    return s


# ====================== GROUP 1: THE BOOK COMES TO US ====================

def test_062_is_refused_then_059_becomes_eligible(monkeypatch, tmp_path):
    """$0.62 refused -> time advances -> $0.59 eligible, SAME ceiling.

    This is the 2026-10-08 window, replayed with a book that moves. The
    $0.60 ceiling is never relaxed: 0.62 is refused every time it is seen,
    and the placement happens only once the market offers 0.59.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    seen = []

    def provider():
        # above the ceiling for the first 5 seconds of observation, then the
        # bid drops to 0.59 and the attempt becomes eligible.
        bid, ask = (("0.62", "0.64") if clock.t < BASE + 15
                    else ("0.59", "0.61"))
        seen.append(bid)
        return book_at(bid, ask, now=clock.t)

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep,
                       pace_s=1.0, max_iterations=40)

    assert "0.62" in seen and "0.59" in seen
    # it waited rather than spun
    assert out["paced_wait_count"] >= 4, out["paced_waits"]
    assert out["paced_total_sleep_s"] >= 4.0
    # and it placed, at the improved price, once the book allowed it
    assert out["placement_count"] >= 1
    assert FakeTransport.posts
    assert all(p["price"] == "0.59" for p in FakeTransport.posts), \
        FakeTransport.posts
    # every refusal before the improvement was the ceiling, and it was paced
    ceiling = [r for r in out["refusals"]
               if any(str(x).startswith("E9_PRICE_ABOVE_CEILING")
                      for x in r["refusals"])]
    assert ceiling, out["refusals"]
    assert all(r["pacing"] == "PACE" for r in ceiling)
    assert all(r["terminal_refusals"] == [] for r in ceiling)


def test_the_ceiling_itself_is_untouched_by_pacing():
    """Pacing changed no bound. Asserted on the frozen values directly."""
    assert str(SPC.MAX_FIRST_LEG_PRICE) == "0.60"
    assert str(SPC.PAIR_COST_CEILING) == "0.96"
    assert str(SPC.FIRST_LEG_QTY) == "0.13"
    assert SPC.MAX_ATTEMPTS_PER_WINDOW == 20
    assert SPC.WINDOWS == 12
    assert str(SPC.MAX_EXPOSURE_PER_WINDOW_USD) == "1.56"
    assert str(SPC.AUTHORIZED_WINDOW_CEILING_USD) == "5.00"


# ============== GROUP 2: PERSISTENT REFUSAL UNTIL THE CUTOFF =============

def test_persistent_refusal_paces_to_the_cutoff_and_places_nothing(
        monkeypatch, tmp_path):
    """A book that NEVER improves: observed patiently, nothing placed, and
    no iteration-cap termination."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    cutoff = float(BASE + W) - PROV.HANDOVER_TTE_S

    def provider():
        s = book_at("0.62", "0.64", now=clock.t)
        # the provider's own rule, evaluated against the moving clock
        s["within_handover_tte"] = (float(BASE + W) - clock.t) > \
            PROV.HANDOVER_TTE_S
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=2000)

    assert out["placement_count"] == 0
    assert FakeTransport.posts == []
    # it observed across the window rather than dying in one second
    assert out["paced_total_sleep_s"] > 600.0, out["paced_total_sleep_s"]
    # NEVER past the trading cutoff while still before it
    for w in out["paced_waits"]:
        assert w["sleep_s"] > 0
    assert clock.t >= cutoff
    # no latch slot, no attempt, no exposure
    assert out["distinct_latch_slots"] == 0
    d = tmp_path / "e1_cohort"
    assert not d.exists() or list(d.glob("*.submitted")) == []


def test_pacing_never_sleeps_past_the_trading_cutoff():
    """The deadline rule, unit-level: before the cutoff the wait is clamped
    to it, so the last tradeable instant is used and not slept through."""
    cutoff, end = 1000.0, 1060.0
    # plenty of room -> the full pace
    assert PACING.pace_seconds(now=900.0, trading_cutoff_unix=cutoff,
                               window_end_unix=end, pace_s=1.0) == 1.0
    # 0.3 s left before the cutoff -> clamped to 0.3, not 1.0
    assert PACING.pace_seconds(now=999.7, trading_cutoff_unix=cutoff,
                               window_end_unix=end,
                               pace_s=1.0) == pytest.approx(0.3)
    # past the cutoff -> wait out the window, still a real wait
    assert PACING.pace_seconds(now=1030.0, trading_cutoff_unix=cutoff,
                               window_end_unix=end, pace_s=1.0) == 1.0


def test_no_paced_wait_is_ever_zero_so_there_is_no_busy_loop():
    """Every reachable combination still yields a positive wait."""
    for now in (0.0, 999.9999, 1000.0, 1000.0001, 1059.9999, 1060.0, 2000.0):
        s = PACING.pace_seconds(now=now, trading_cutoff_unix=1000.0,
                                window_end_unix=1060.0, pace_s=1.0)
        assert s > 0.0, (now, s)
    assert PACING.pace_seconds(now=0.0, pace_s=1.0) > 0.0


# ============ GROUP 3: TRANSIENT vs TERMINAL, AND NEVER A RETRY ==========

@pytest.mark.parametrize("code", [
    "E9_PRICE_ABOVE_CEILING:0.62", "E9_WOULD_CROSS:0.5>=0.5",
    "E9_PRICE_ABSENT", "E9_OFF_TICK:0.615",
    "E8_FIRST_LEG_BID_NOT_POSITIVE:0.00", "E7_BOOK_IS_STALE",
    "E10_COMPLEMENT_INFEASIBLE:x", "E3_OUR_ORDER_IS_OUTSTANDING",
    "E4_PAST_HANDOVER_TTE", "E11_WINDOW_LATCH_EXHAUSTED",
    "PROJECTED_WINDOW_EXPOSURE_1.56_WOULD_REACH_1.56",
    "WINDOW_0_ATTEMPT_21_REFUSED_MAX_20",
])
def test_market_state_refusals_are_paced(code):
    assert PACING.is_paced(code) is True
    assert PACING.classify([code])["all_paced"] is True


@pytest.mark.parametrize("code", [
    "E1_DISARMED", "E1_FINGERPRINT_NOT_PINNED_TO_THIS_BUILD",
    "E12_SAFETY_INVARIANT_VIOLATION", "QTY_NOT_THE_FROZEN_0.13:0.5",
    "QTY_NOT_FINITE:None", "E11_LATCH_UNAVAILABLE:OSError",
    "COHORT_PLACEMENT_CAP_240", "FORWARD_LOOKING_LOSS_BOUND: realized 5",
    "VERDICT_IS_FAIL", "E4_WINDOW_OUT_OF_RANGE:None",
])
def test_safety_refusals_are_terminal(code):
    assert PACING.is_paced(code) is False
    assert PACING.classify([code])["all_paced"] is False


def test_an_unrecognised_refusal_is_terminal_so_it_fails_closed():
    """The direction a mistake here must fail: a refusal this code has never
    seen stops the cohort rather than being spun on."""
    assert PACING.is_paced("SOME_FUTURE_SAFETY_RULE") is False
    assert PACING.is_paced(None) is False
    assert PACING.is_paced(object()) is False


def test_one_terminal_refusal_poisons_an_otherwise_paced_list():
    c = PACING.classify(["E9_PRICE_ABOVE_CEILING:0.62", "E1_DISARMED"])
    assert c["all_paced"] is False
    assert c["terminal"] == ["E1_DISARMED"]


def test_an_empty_refusal_list_is_not_all_paced():
    assert PACING.classify([])["all_paced"] is False


def test_a_terminal_refusal_stops_the_cohort_rather_than_pacing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)

    def provider():
        s = book_at("0.50", "0.52", now=clock.t)
        s["qty"] = "0.50"          # QTY_NOT_THE_FROZEN_0.13 -> terminal
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=500)
    assert out["placement_count"] == 0
    assert str(out["stopped_because"]).startswith("TERMINAL_REFUSAL")
    assert out["iterations"] == 1, "it must stop on the FIRST one"
    assert out["paced_wait_count"] == 0
    assert clock.sleeps == []


def test_an_ambiguous_submission_is_never_retried(monkeypatch, tmp_path):
    """Unchanged by pacing, and asserted so it stays that way."""
    import analysis.l5.live_exec2.orders as O
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise O.SubmissionAmbiguous("no reply", detail={"phase": "POST"})

    monkeypatch.setattr(R, "submit_first_leg", boom)
    out = R.run_cohort(snapshot_provider=lambda: book_at("0.50", "0.52",
                                                        now=clock.t),
                       tracker=SP.CohortTracker(), now_fn=clock,
                       sleep_fn=clock.sleep, pace_s=1.0, max_iterations=500)
    assert len(calls) == 1, "an ambiguous submission was retried"
    assert out["stopped_because"] == \
        "AMBIGUOUS_SUBMISSION_RECONCILE_VENUE_STATE"
    assert out["paced_wait_count"] == 0


# ======= GROUP 4: NO ARTIFICIAL 960 TERMINATION, AND THE CAP IS A NET ====

def test_the_old_960_cap_could_not_hold_one_paced_window():
    """The arithmetic that made 2026-10-08 inevitable, stated as a test."""
    old_cap = SPC.MAX_PLACEMENTS * 4
    assert old_cap == 960
    observations_in_one_window = W / PACING.PRODUCTION_PACE_S
    assert observations_in_one_window == 900
    # twelve windows of patient observation is far more than the old cap
    assert SPC.WINDOWS * observations_in_one_window > old_cap


def test_the_cap_admits_a_complete_paced_cohort():
    cap = PACING.iteration_cap(windows=SPC.WINDOWS, window_len_s=float(W),
                               max_placements=SPC.MAX_PLACEMENTS)
    needed = SPC.WINDOWS * (W / PACING.PRODUCTION_PACE_S)
    assert cap > needed
    assert cap == 11760


def test_the_cap_does_not_shrink_when_the_suite_shrinks_the_pace():
    """The bug that hung the suite, pinned: a 0.001 s test pace once
    inflated the guard to ten million. The guard uses the PRODUCTION pace."""
    assert PACING.PACE_S != PACING.PRODUCTION_PACE_S  # conftest shrank it
    assert PACING.iteration_cap(windows=12, window_len_s=900.0,
                                max_placements=240) == 11760


def test_run_cohort_reports_a_cap_that_is_not_960(monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    out = R.run_cohort(snapshot_provider=lambda: None,
                       tracker=SP.CohortTracker(),
                       now_fn=lambda: BASE + 10, sleep_fn=lambda s: None)
    assert out["iteration_cap"] == 11760
    assert out["iteration_cap"] != 960
    assert out["PACING_CHANGES_NO_BOUND"] is True


# ===== GROUP 5: AN INELIGIBLE MARKET COSTS NO ATTEMPT AND NO BUDGET =====

def test_pacing_consumes_no_attempt_no_latch_and_no_risk_budget(
        monkeypatch, tmp_path):
    """The owner's item 6, measured on the tracker itself."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    tracker = SP.CohortTracker()

    out = R.run_cohort(snapshot_provider=lambda: book_at("0.62", "0.64",
                                                         now=clock.t),
                       tracker=tracker, transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=120)

    assert out["paced_wait_count"] >= 100          # it really did observe
    assert out["placement_count"] == 0
    assert tracker.placements == 0
    # the tracker pre-initialises all twelve windows, so the claim is that
    # every counter is still ZERO -- not that the dict is empty.
    assert set(tracker.window_attempts.values()) <= {0}, \
        tracker.window_attempts
    assert all(str(v) in ("0", "0.00") for v in
               tracker.window_exposure_usd.values()), \
        tracker.window_exposure_usd
    assert tracker.venue_errors == 0               # not a venue error
    from decimal import Decimal as _D
    assert _D(str(tracker.forward_looking_report(
        prospective_attempts=0)["forward_looking_total_usd"])) == _D("0")
    d = tmp_path / "e1_cohort"
    assert not d.exists() or list(d.glob("*.submitted")) == []
    assert FakeTransport.posts == []


def test_a_refusal_is_not_counted_as_a_venue_error(monkeypatch, tmp_path):
    """The 2026-10-07 storm was venue errors. A paced market refusal is
    neither an error nor a step toward the five-error safety stop."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    tracker = SP.CohortTracker()
    R.run_cohort(snapshot_provider=lambda: book_at("0.62", "0.64",
                                                   now=clock.t),
                 tracker=tracker, transport_factory=FakeTransport,
                 now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                 max_iterations=30)
    assert tracker.venue_errors == 0
    assert tracker.verdict()["stopped"] is False


# ================= GROUP 6: EMPTY SNAPSHOTS AND RESTART ==================

def test_a_transient_empty_snapshot_is_paced_not_fatal(monkeypatch,
                                                       tmp_path):
    """A momentary book gap must not end a live cohort."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)
    n = {"i": 0}

    class Prog:
        def windows_used(self):
            return 0
        def record_cohort_stopped(self, **k):
            pass
        def record_placement(self, **k):
            pass

    def provider():
        n["i"] += 1
        return None if n["i"] <= 3 else book_at("0.50", "0.52",
                                               now=clock.t)

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       progress=Prog(), max_iterations=20)
    assert out["stopped_because"] != "SNAPSHOT_PROVIDER_EXHAUSTED"
    assert out["placement_count"] >= 1


def test_a_permanently_empty_provider_stops_after_a_bounded_wait(
        monkeypatch, tmp_path):
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)
    clock = Clock(BASE + 10)

    class Prog:
        def windows_used(self):
            return 0
        def record_cohort_stopped(self, **k):
            pass

    out = R.run_cohort(snapshot_provider=lambda: None,
                       tracker=SP.CohortTracker(), now_fn=clock,
                       sleep_fn=clock.sleep, pace_s=1.0, progress=Prog(),
                       max_iterations=5000)
    assert str(out["stopped_because"]).startswith(
        "SNAPSHOT_PROVIDER_EMPTY_")
    assert out["iterations"] <= PACING.MAX_CONSECUTIVE_EMPTY_SNAPSHOTS + 2


def test_a_spent_window_budget_still_reads_as_exhausted(monkeypatch,
                                                        tmp_path):
    """Pacing must not hide the genuine end of the cohort."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    arm(monkeypatch)

    class Prog:
        def windows_used(self):
            return SPC.WINDOWS
        def record_cohort_stopped(self, **k):
            pass

    out = R.run_cohort(snapshot_provider=lambda: None,
                       tracker=SP.CohortTracker(),
                       now_fn=lambda: BASE + 10, sleep_fn=lambda s: None,
                       progress=Prog(), max_iterations=50)
    assert out["stopped_because"] == "SNAPSHOT_PROVIDER_EXHAUSTED"
    assert out["iterations"] == 1


# ============== GROUP 7: THE PRODUCTION LITERALS, ACTUALLY RUN ===========

def test_the_production_pacing_literals_are_what_ships():
    """Read from the SOURCE, because the suite patches the module values.

    This is the lesson from the earlier readiness guard: an identical check
    was written inside `conftest.py`, where pytest never collects it, so it
    has never run. These are asserted here, where they do.
    """
    import re
    src = open(PACING.__file__).read()
    assert re.search(r"^PRODUCTION_PACE_S = 1\.0$", src, re.M)
    assert re.search(r"^MIN_PACE_S = 0\.05$", src, re.M)
    assert re.search(r"^MAX_CONSECUTIVE_EMPTY_SNAPSHOTS = 30$", src, re.M)


def test_the_production_readiness_bound_is_what_ships():
    """The guard that was stranded in conftest.py, now collected."""
    import re
    src = open(PROV.__file__).read()
    assert re.search(r"^WS_READY_TIMEOUT_S = 20\.0$", src, re.M)
    assert re.search(r"^WS_READY_POLL_S = 0\.25$", src, re.M)
    assert re.search(r"^HANDOVER_TTE_S = 60\.0$", src, re.M)


def test_the_provider_publishes_both_pacing_deadlines():
    """The snapshot must carry them, or pacing silently loses its deadline."""
    import inspect
    src = inspect.getsource(PROV.PmusWindowProvider.__call__)
    assert '"trading_cutoff_unix"' in src
    assert '"window_end_unix"' in src


# ========= GROUP 8: OUTSTANDING ORDERS AND THE CUTOFF, UNDER PACING ======
#
# SCOPE, STATED HONESTLY. E1's runner has no deadman of its own: there is no
# `handover` or `withdraw` function in this package, and cancellation lives in
# XR2W's order modules, with the fill-races-a-cancel accounting already tested
# in `test_e1_cohort.py`. What pacing CAN affect is whether a paced loop ever
# places a second order while one is resting, and whether it ever places after
# the trading cutoff. Those are what these tests prove.

def test_pacing_waits_out_our_own_resting_order_and_never_doubles_up(
        monkeypatch, tmp_path):
    """One outstanding order at a time, preserved under pacing.

    `E3_OUR_ORDER_IS_OUTSTANDING` is PACED rather than terminal on purpose: a
    maker order rests and then fills or is cancelled, so waiting is the
    correct behaviour. What must never happen is a second order.
    """
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    clock = Clock(BASE + 10)

    seen = []

    def provider():
        s = book_at("0.50", "0.52", now=clock.t)
        # our order rests for the first 10 s, then resolves
        live = 1 if clock.t < BASE + 20 else 0
        s["our_outstanding_orders"] = live
        # what the loop had placed BEFORE this observation, and whether an
        # order of ours was live at that moment
        seen.append((clock.t, live, len(FakeTransport.posts)))
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=25)

    # it waited while the order was live, instead of spinning or stopping
    # the code now carries the COUNT ("...:1"), because the provider derives
    # it from venue state instead of publishing a hard-coded 0.
    outstanding = [r for r in out["refusals"]
                   if any(str(g).startswith("E3_OUR_ORDER_IS_OUTSTANDING")
                          for g in r["refusals"])]
    assert outstanding, out["refusals"]
    assert all(r["pacing"] == "PACE" for r in outstanding)
    # THE CLAIM: not one placement happened while an order of ours was live.
    # Every observation that saw a live order saw zero posts so far.
    while_live = [(t, n) for (t, live, n) in seen if live]
    assert while_live, seen
    assert all(n == 0 for (t, n) in while_live), while_live
    # and it did eventually place, once nothing of ours was outstanding --
    # otherwise this would pass by never trading at all.
    assert out["placement_count"] >= 1


def test_pacing_places_nothing_after_the_trading_cutoff(monkeypatch,
                                                        tmp_path):
    """Past the handover TTE the loop keeps observing but cannot place."""
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "e1_cohort"))
    FakeTransport.posts = []
    arm(monkeypatch)
    # start AFTER the cutoff: window_end - 30 s, cutoff is window_end - 60 s
    clock = Clock(float(BASE + W) - 30.0)

    def provider():
        s = book_at("0.50", "0.52", now=clock.t)       # a perfectly good book
        s["within_handover_tte"] = (float(BASE + W) - clock.t) > \
            PROV.HANDOVER_TTE_S
        return s

    out = R.run_cohort(snapshot_provider=provider,
                       tracker=SP.CohortTracker(),
                       transport_factory=FakeTransport,
                       now_fn=clock, sleep_fn=clock.sleep, pace_s=1.0,
                       max_iterations=20)

    assert out["placement_count"] == 0, "placed past the trading cutoff"
    assert FakeTransport.posts == []
    past = [r for r in out["refusals"]
            if "E4_PAST_HANDOVER_TTE" in r["refusals"]]
    assert past, out["refusals"]
    assert all(r["pacing"] == "PACE" for r in past)
    d = tmp_path / "e1_cohort"
    assert not d.exists() or list(d.glob("*.submitted")) == []
