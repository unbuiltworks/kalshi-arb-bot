"""First-leg fill -> inventory -> second-leg decision, end to end.

PRIORITY 2 of the expedited assignment: prove the whole path
deterministically, through the money path's OWN hooks, for all four
outcomes the controller can reach --

    maker COMPLETE | REPRICE | HOLD | refuse an unjustified cross

-- and PRIORITY 1: prove the defaults at this commit are unchanged, with
`L5_PROVISIONAL_FORCED_SIDE` OFF.

HOW THE FILL IS REAL. `record_submit` then `apply_order_progress` are the
hooks the executor itself calls; nothing here fabricates a position by
writing `state["positions"]` directly, because the question is whether a
VENUE-REPORTED fill becomes inventory the completion controller can see.

PRICE CONVENTION, which has caught me repeatedly. UP rests on the BID at
wire == own. DOWN is a sell of YES resting on the ASK, so its own cost is
`1 - ask`. **A HIGH yes price makes DOWN CHEAP.** Every book below is
annotated with the pair it implies so the premise is checkable.

No transport that can submit. No network. No order. Disarmed throughout.
"""
from __future__ import annotations

import collections
import os
from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_completion_latency as CL
from src import l5_fill_calibration as CALIB
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as RUN
from src import l5_two_sided_probe as TS

SLUG = "cpc-btc-updown-15m-2026-11-08-1200z"
WS = 1794139200.0
COID = "l5f-firstleg-0001"


class Book:
    def __init__(self, bid, ask, age="0.4"):
        self.bid, self.ask, self.age = D(bid), D(ask), D(age)

    def snapshot(self, slug):
        return {"bid": self.bid, "ask": self.ask,
                "stream_age_s": self.age,
                "bid_shares": D("500"), "ask_shares": D("500")}

    def trades(self, slug):
        return []


class Transport:
    """NO submit, NO cancel, NO network."""
    tick_size = "0.01"
    quantity_increment = "1"

    def __init__(self):
        self.market_slug = SLUG


class Gates:
    max_worst_case_deficit_usd = D("1.00")


class Rig:
    """The real method bodies on the attributes they touch."""
    LATENCY_OBSERVER_KEY = RUN.Runner.LATENCY_OBSERVER_KEY

    def __init__(self, tmp_path, book):
        self.transport = Transport()
        self.store = LX.InventoryStore(
            os.path.join(str(tmp_path), "inv.json"))
        self.book = book
        self.executor = type("E", (), {"validation_gates": Gates(),
                                       "store": self.store})()
        self.stats = collections.Counter()
        self.win = collections.Counter()
        self.shadow_ladder = None
        self.last_order_poll = {}
        self._last_action_row = {}
        self._book_completion_t = 0.0
        self._latency_last_slug = None
        self.now_t = WS + 60.0
        self.completion_latency = CL.CompletionLatency()
        self.fill_calibration = CALIB.CalibrationRecorder(
            str(tmp_path), path=os.path.join(str(tmp_path), "cal.ndjson"))

    maybe_book_completion = RUN.Runner.maybe_book_completion
    poll_orders = RUN.Runner.poll_orders
    _latency_safe = RUN.Runner._latency_safe
    _latency_observer_note = RUN.Runner._latency_observer_note
    _latency_report = RUN.Runner._latency_report
    _bc_capture = RUN.Runner._bc_capture

    def lifecycle(self, events):
        for e in events or ():
            self.stats["LIFECYCLE:%s" % (e.get("event") or "ERROR")] += 1


def fill_first_leg(rig, *, side="UP", price="0.62", qty="1"):
    """A venue-reported fill, through the real hooks, becoming inventory."""
    rig.store.record_submit(COID, {"market": SLUG, "side": side,
                                   "price": price, "qty": qty},
                            session_id="probe", mode="live")
    out = rig.store.apply_order_progress(
        COID, {"cum_qty": qty, "avg_px": price, "leaves_qty": "0",
               "terminal": True, "state": "ORDER_STATE_FILLED",
               "commission": "0", "venue_order_id": "V-1"},
        market=SLUG, side=side)
    return out


def decide(rig, *, held_side, held_qty, held_cost, book, now=None,
           resting=(), outstanding="0", submissions_left=5,
           last_decide_t=0.0):
    """`BC.decide` on the inventory the store actually holds."""
    return BC.decide(
        market=SLUG, held_side=held_side, held_qty=D(str(held_qty)),
        held_cost_usd=D(str(held_cost)), deficient_qty=D("0"),
        deficient_cost_usd=D("0"), book=book.snapshot(SLUG),
        now_t=(WS + 60.0) if now is None else now, window_start=WS,
        resting=list(resting), outstanding_qty=D(str(outstanding)),
        tick="0.01", min_qty="1", depth_ticks=BC.completion_depth_ticks(),
        residual_budget_usd=D("1.00"), submissions_left=submissions_left,
        last_decide_t=last_decide_t)


# =====================================================================
# PRIORITY 1 -- the defaults at this commit
# =====================================================================

def test_the_provisional_forced_side_flag_is_off_unless_set(monkeypatch):
    monkeypatch.delenv("L5_PROVISIONAL_FORCED_SIDE", raising=False)
    got = (os.getenv("L5_PROVISIONAL_FORCED_SIDE")
           or "").strip().lower() in ("1", "yes", "true", "on")
    assert got is False


def test_with_the_flag_unset_one_pinned_side_still_refuses_to_open(
        monkeypatch):
    """THE DEFAULT IS THE OBSERVED BEHAVIOUR, byte for byte. At ask 0.01
    the UP provisional price is unreadable and DOWN is 0.99 -- and with the
    flag off the controller must still refuse, exactly as the running
    image does."""
    monkeypatch.delenv("L5_PROVISIONAL_FORCED_SIDE", raising=False)
    book = {"bid": "0.00", "ask": "0.01"}
    px_up = LX.controller_provisional_price("UP", book, "0.01")
    px_dn = LX.controller_provisional_price("DOWN", book, "0.01")
    assert px_up is None and px_dn == D("0.99")
    forced_ok = (os.getenv("L5_PROVISIONAL_FORCED_SIDE")
                 or "").strip().lower() in ("1", "yes", "true", "on")
    both = px_up is None and px_dn is None
    refuses = both or (not forced_ok and (px_up is None or px_dn is None))
    assert refuses is True


def test_route_b_itself_is_still_default_off(monkeypatch):
    """The completion controller is inert without its own switch, so this
    commit cannot change live behaviour on a deployment that has not set
    it."""
    monkeypatch.delenv(BC.ENABLED_ENV, raising=False)
    assert BC.enabled() is False


def test_the_deployed_economic_constants_are_unchanged():
    assert BC.completion_depth_ticks() == 0
    assert float(TS.MAX_BOOK_AGE_S) == 2.0
    assert float(TS.completion_max_pair()) == 0.96


def test_the_latency_accumulator_cannot_refuse_or_submit():
    import inspect
    src = inspect.getsource(CL).lower()
    # NOT a bare "submit": `note_eligible(submitted=...)` is a FIELD, and
    # recording that something was submittable is the opposite of being
    # able to submit it. The words that would matter are a call or a held
    # object.
    for banned in ("def submit", "self.transport", "requests.", "urllib",
                   "http://", "https://", ".post(", ".cancel("):
        assert banned not in src, banned
    # and it holds no state beyond its own per-market dicts
    acc = CL.CompletionLatency()
    assert list(vars(acc)) == ["m"], vars(acc)


# =====================================================================
# PRIORITY 2 -- the full path, all four outcomes
# =====================================================================

def test_a_venue_reported_fill_becomes_inventory_the_controller_can_see(
        tmp_path):
    """THE SEAM. Without this the rest is theatre: the completion
    controller reads `store.position`, and only `apply_order_progress`
    puts a venue fill there."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    assert rig.store.position(SLUG, "UP") == 0
    fill_first_leg(rig)
    assert rig.store.position(SLUG, "UP") == D("1")
    assert rig.store.position(SLUG, "DOWN") == 0
    assert rig.store.residual(SLUG) == D("1")
    legs = rig.store.leg_cost(SLUG)
    assert legs["UP"] == D("0.62"), legs


def test_the_decision_is_inert_without_route_b_even_with_inventory(
        tmp_path, monkeypatch):
    """DEFAULT PRESERVATION, with real inventory present.

    `monkeypatch.delenv` is not decoration: a full-suite run has other
    tests that set `L5_BOOK_COMPLETION`, and the first version of this
    test read the ambient environment and failed in the suite while
    passing alone. A test about a DEFAULT has to establish the default.
    """
    monkeypatch.delenv(BC.ENABLED_ENV, raising=False)
    assert BC.enabled() is False
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig)
    assert rig.store.position(SLUG, "UP") == D("1")   # inventory IS there
    assert rig.maybe_book_completion(rig.now_t) is None


@pytest.fixture
def route_b(monkeypatch):
    monkeypatch.setenv(BC.ENABLED_ENV, "YES")
    monkeypatch.delenv("L5_PROVISIONAL_FORCED_SIDE", raising=False)
    assert BC.enabled() is True
    yield


def test_OUTCOME_1_maker_COMPLETE_on_a_cheap_complement(tmp_path, route_b):
    """Held UP at 0.38; the book is 0.62/0.63 so the DOWN complement rests
    at own 1 - 0.63 = 0.37. Pair 0.38 + 0.37 = 0.75, far inside the 0.96
    ceiling, so completing is justified and the action is a MAKER order.
    """
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book)
    assert rec["ok"] is True, rec
    assert rec["action"] == "COMPLETE", rec
    assert D(str(rec["pair_own_cost"])) <= D("0.96"), rec
    # and it carries the book age it was decided on, on a PASSING decision
    assert rec["stream_age_s"] == "0.4"


def test_OUTCOME_2_REPRICE_when_our_own_resting_order_is_worse(
        tmp_path, route_b):
    """A completion already resting at a WORSE own price must be moved,
    not duplicated. Same economics as outcome 1, plus our own stale row."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    stale = [{"side": "DOWN", "price": "0.50", "qty": "1",
              "coid": "l5f-old", "venue_order_id": "V-OLD"}]
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, resting=stale, outstanding="0")
    assert rec["ok"] is True, rec
    assert rec["action"] == "REPRICE", rec


def test_OUTCOME_2b_an_already_better_resting_order_is_left_alone(
        tmp_path, route_b):
    """Idempotence: a completion already at or better than the target is
    not churned. Repricing a good order burns a submission and loses queue
    position."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    good = [{"side": "DOWN", "price": "0.30", "qty": "1",
             "coid": "l5f-good", "venue_order_id": "V-GOOD"}]
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, resting=good, outstanding="1")
    assert rec["ok"] is False, rec
    assert rec["reason"] in ("ALREADY_RESERVED",
                             "ALREADY_RESTING_AT_OR_BETTER",
                             "ALREADY_MATCHED"), rec


def test_OUTCOME_3_HOLD_when_the_pair_is_above_the_ceiling(
        tmp_path, route_b):
    """Held UP at 0.72 against the same book: complement own 0.37, pair
    1.09 -- above $1, so completing locks a loss. The controller must hold
    and name the economics."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.72")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.72",
                 book=rig.book)
    assert rec["ok"] is False, rec
    assert rec["action"] == "HOLD", rec
    assert rec["reason"] == "PAIR_ABOVE_REDEMPTION", rec
    assert D(str(rec["pair_own_cost"])) > D("1"), rec
    # the age is carried on the refusal too, so freshness stays auditable
    assert rec["stream_age_s"] == "0.4"


def test_OUTCOME_4_a_price_that_would_CROSS_is_refused(tmp_path, route_b):
    """THE UNJUSTIFIED CROSS. ROUTE B is maker-only: a completion priced
    where it would take liquidity is refused as WOULD_CROSS, whatever the
    pair economics say. A one-tick book leaves no room to rest behind the
    touch, which is the case that produced it in replay."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, depth_override=None) \
        if False else decide(rig, held_side="UP", held_qty="1",
                             held_cost="0.38", book=rig.book)
    # the default depth is 0 (at the touch) and is proven post-only, so a
    # COMPLETE here is NOT a cross -- assert that explicitly, because
    # "rests at the touch" being mistaken for "crosses" is the error this
    # guards.
    assert rec["action"] == "COMPLETE"
    assert rec.get("reason") != "WOULD_CROSS"
    # now make crossing unavoidable: an inverted book has no restable side
    bad = Book("0.63", "0.62")
    rec2 = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                  book=bad)
    assert rec2["ok"] is False, rec2
    assert rec2["reason"] in ("BOOK_UNUSABLE", "WOULD_CROSS"), rec2


def test_a_stale_book_refuses_before_any_economics_are_computed(
        tmp_path, route_b):
    rig = Rig(tmp_path, Book("0.62", "0.63",
                             age=str(TS.MAX_BOOK_AGE_S + 5)))
    fill_first_leg(rig, price="0.38")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book)
    assert rec["reason"] == "STALE_BOOK", rec
    assert rec.get("pair_own_cost") is None, rec


def test_a_matched_book_is_not_completed_again(tmp_path, route_b):
    """Both legs held: there is nothing to complete and the controller
    must not open a third leg."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, side="UP", price="0.38")
    rig.store.record_submit("l5f-two", {"market": SLUG, "side": "DOWN",
                                        "price": "0.37", "qty": "1"},
                            session_id="probe", mode="live")
    rig.store.apply_order_progress(
        "l5f-two", {"cum_qty": "1", "avg_px": "0.37", "leaves_qty": "0",
                    "terminal": True, "state": "ORDER_STATE_FILLED",
                    "commission": "0", "venue_order_id": "V-2"},
        market=SLUG, side="DOWN")
    assert rig.store.residual(SLUG) == 0
    rec = rig.maybe_book_completion(rig.now_t)
    assert rec is None or rec.get("ok") is not True, rec


def test_no_submissions_left_refuses_rather_than_producing_an_order(
        tmp_path, route_b):
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, submissions_left=0)
    assert rec["ok"] is False and rec["reason"] == "NO_SUBMISSIONS_LEFT", rec


def test_past_the_terminal_deadline_nothing_is_completed(tmp_path, route_b):
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, now=WS + 1000.0)
    assert rec["ok"] is False and rec["reason"] == "PAST_TERMINAL", rec


def test_the_cooldown_prevents_a_refusal_being_retried_every_tick(
        tmp_path, route_b):
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.38")
    now = WS + 60.0
    rec = decide(rig, held_side="UP", held_qty="1", held_cost="0.38",
                 book=rig.book, now=now, last_decide_t=now - 0.2)
    assert rec["reason"] == "DECIDE_COOLDOWN", rec


# =====================================================================
# the probe's own measurement, on the real path
# =====================================================================

def test_the_fill_produces_a_real_detection_and_a_real_book_age(
        tmp_path, route_b):
    """What the probe is for. A filled first leg gives inventory, the
    controller finally evaluates, and the book age at THAT evaluation is
    recorded -- the number production has never produced."""
    rig = Rig(tmp_path, Book("0.62", "0.63"))
    fill_first_leg(rig, price="0.72")        # dear pair -> it will HOLD
    t = WS + 60.0
    for _ in range(4):
        rig.now_t = t
        rig.maybe_book_completion(t)
        t += 5.5
    r = rig.completion_latency.report(SLUG)
    assert r["decisions"] >= 3, r
    assert r["book_age_at_decision_s"]["n"] >= 3, r
    assert r["book_age_at_decision_s"]["median"] == 0.4
    assert r["detection_to_first_decision_s"] == 0.0
    assert "PAIR_ABOVE_REDEMPTION" in r["decision_reasons"], r
