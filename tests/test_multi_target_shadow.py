"""Several pair targets, one market state, one tape.

OWNER 2026-09-11: "post virtual complements at several simultaneous targets
against the same market state: 0.98, 0.985, 0.99, 0.995, optionally touch
itself... How much fill probability do we buy by giving up 0.5c, 1c, or 1.5c
of pair edge?"

The experiment exists because our policy PINS the target at 0.98 -- 120 of 121
live proposals carried exactly that -- so the variation has to be created
rather than found.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_execution_shadow as ex
from src import pmus_multi_target_shadow as mt

BOOK = {"bid": 0.605, "ask": 0.61, "slug": "aec-nfl-bal-ind-2026-09-13",
        "bid_qty": 203519.0,
        "book_levels": {"bids": [{"px": 0.605, "qty": 203519},
                                 {"px": 0.600, "qty": 40000},
                                 {"px": 0.590, "qty": 5000}],
                        "offers": [{"px": 0.610, "qty": 1000}]}}
PLAN = {"_side_yes": True, "side": "YES", "q1_size": 1, "max_pair_cost": "0.98"}


def _arms(**kw):
    return mt.arms_for("aec-nfl-bal-ind-2026-09-13", PLAN, 100.0, BOOK, -3,
                       "0.39", first_leg={"side": "NO", "price": "0.39"}, **kw)


# ------------------------------------ the ladder walks toward the queue front
def test_each_half_cent_of_target_buys_one_tick_toward_the_touch():
    """The whole point: 0.98 sits three ticks back on a half-cent book and
    0.995 joins the touch. If this ever stops holding, the experiment is no
    longer measuring what it claims to."""
    got = {a["arm"]: a["state_at_post"]["ticks_from_touch"] for a in _arms()}
    assert got["0.98"] == -3
    assert got["0.985"] == -2
    assert got["0.99"] == -1
    assert got["0.995"] == 0


def test_the_touch_arm_confirms_the_identity_rather_than_assuming_it():
    """pair at touch = 1 - spread. On a 0.005 book that is 0.995, so the touch
    arm and the 0.995 arm must land on the same price -- computed by two
    different routes."""
    got = {a["arm"]: a["px"] for a in _arms()}
    assert got["touch"] == got["0.995"]
    pair = {a["arm"]: a["implied_pair_at_arm"] for a in _arms()}
    assert str(pair["0.995"]) == "0.995"


def test_queue_ahead_falls_in_STEPS_at_populated_levels_not_per_tick():
    """A tick of relaxation is not a fixed amount of queue.

    On this book the levels are 0.605 / 0.600 / 0.590, so the 0.985 arm at
    0.595 lands in a GAP: it gains a tick over the 0.98 arm and reduces
    queue-ahead by NOTHING, because no size rests between them. Only crossing
    a populated level moves the queue. This surprised me when the test failed,
    and it matters for reading the experiment: if 0.985 shows no fill
    improvement, that may be the book's shape rather than a flat response to
    price."""
    from src import pmus_shadow_proposal_log as plog
    q = {a["arm"]: plog.queue_position(a) for a in _arms()}
    ahead = {k: v["queue_ahead_qty"] for k, v in q.items()}
    assert ahead["0.98"] == 243519.0, "behind 203519 + 40000"
    assert ahead["0.985"] == 243519.0, "a tick better, but into a GAP"
    assert q["0.985"]["size_at_our_price"] is None, "nothing rests at 0.595"
    assert ahead["0.99"] == 203519.0, "now past the 0.600 level"
    assert ahead["0.995"] == 0.0, "at the touch nothing is ahead"
    assert ahead["0.98"] >= ahead["0.985"] > ahead["0.99"] > ahead["0.995"]


# --------------------------------------------------- it must never cross
def test_an_arm_is_capped_below_the_opposing_ask_and_never_crosses():
    """A crossing arm would measure a TAKER, which is a different strategy and
    would flatter the relaxed targets for the wrong reason."""
    px = mt.target_price(D("1.20"), "0.39", 0.61, True)
    assert px is not None and float(px) <= 0.61 - float(ex.TICK)


def test_an_illegal_or_unknowable_price_is_dropped_not_clamped_to_something():
    assert mt.target_price(D("0.98"), None, 0.61, True) is None
    assert mt.target_price(D("0.20"), "0.39", 0.61, True) is None, (
        "a negative complement price is not an arm")


def test_the_touch_arm_frames_the_side_it_buys():
    """A NO buy joins 1 - yes_ask, not the yes bid. Getting this wrong would
    make the reference arm the most misleading row in the table."""
    assert mt.touch_price(BOOK, True) == D("0.605")
    assert mt.touch_price(BOOK, False) == D("0.39")


# ----------------------------------------- separate from the frozen policy arm
def test_arms_are_held_apart_from_the_policy_proposals():
    import inspect
    src = inspect.getsource(mt.MultiTargetArm)
    assert "SEPARATE ON PURPOSE" in src
    arm = mt.MultiTargetArm()
    arm.record(slug="s", plan=PLAN, t_post=1.0, ranked_row=BOOK,
               residual_before=-3, surplus_avg="0.39", first_leg={})
    assert len(arm.arms) == 5
    assert all("|" in k for k in arm.arms), "arm ids are suffixed, never reused"


def test_a_failing_arm_cannot_cost_the_policy_arm():
    import inspect
    from src import pmus_sports_shadow_breadth as SB
    src = inspect.getsource(SB)
    i = src.index("self.target_arms.record(")
    assert "except Exception" in src[i:i + 700]
    assert "must never cost the policy arm" in src


# -------------------------------------------------------------- the report
def test_the_report_states_the_half_of_the_gate_it_cannot_answer():
    """OWNER's bar includes 'additional residual losses caused by more
    first-side fills'. Every arm shares ONE first leg, so that half is not
    measurable here and the report has to say so on its face."""
    rep = mt.by_arm([])
    assert "says nothing about whether a relaxed policy would take more" \
        in rep["first_leg_note"]
    assert "TRADE_THROUGH_PROXY" in rep["semantics"]
    assert "not fills" in rep["semantics"].lower()


def test_rates_are_computed_over_resolved_arms_only():
    a = {"arm": "0.99", "status": ex.NO_FILL, "state_at_post": {},
         "latency_s": None}
    b = {"arm": "0.99", "status": ex.VIRTUAL_MATCHED, "state_at_post": {},
         "latency_s": 30.0}
    pend = {"arm": "0.99", "status": ex.PENDING, "state_at_post": {}}
    got = mt.by_arm([a, b, pend])["by_arm"]["0.99"]
    assert got["n"] == 3 and got["resolved"] == 2
    assert got["trade_through"] == 1 and got["trade_through_rate"] == 0.5
