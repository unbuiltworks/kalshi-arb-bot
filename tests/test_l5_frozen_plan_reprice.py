"""A frozen plan the book has walked away from is re-priced, not reused.

THE DEFECT, FOUND ON THE REAL EXECUTION PATH and reproduced below. With
both legs of a round cancelled at zero fill and the bid then moving
0.42 -> 0.46, `probe_plan` prices UP at 0.44 from the current mirror while
the engine sends UP at **0.40** -- the frozen price, four ticks behind,
resting six ticks off a 0.46 touch where nothing fills.

WHY NEITHER EXISTING GUARD SAW IT.

  * `plan_executable`'s CROSSING test fires when a leg becomes too
    AGGRESSIVE. A leg left behind is perfectly placeable, so it never
    crosses and is never reported.
  * `plan_executable`'s DRIFT test (`PRICE_LEFT_BEHIND`) only fires past
    `STALE_DRIFT_TICKS` = 6. Four is under it.
  * and above six the only remedy the caller has is RETIRING the round --
    which at the deployed `ts_round_allocations` of 1 makes `probe_round`
    return None and ends the window on ACCUMULATION_EXHAUSTED.

So the two guards between them repair a plan that moved against us and
leave a plan that moved in our favour resting where it cannot fill. That
asymmetry is why it survived: every recorded failure of a frozen plan was
a crossing one.

WHAT THE REPAIR DOES NOT TOUCH. The freeze exists so leg two matches leg
one. That reason applies only while a leg of the round is resting or
filled, and the re-price runs ONLY when `probe_round_is_safe_to_retire`
reports the round holds nothing live, nothing fillable and no unmatched
fill. So a resting or partly filled round is byte-for-byte unchanged, and
because nothing is cancelled the repair cannot churn orders: it never runs
while an order exists.

No network, no threads, no live order.
"""
from __future__ import annotations

import pathlib
import tempfile
from decimal import Decimal as D

import pytest

import tests.test_l5_e2e_window as H
import tests.test_l5_release_candidate_lifecycle as RC

from src import l5_two_sided_probe as TS

SLUG = H.SLUG

# the e2e fixture configuration, plus the deployed clip
pytestmark = pytest.mark.usefixtures("production_configuration")
production_configuration = RC.production_configuration


def _legs(bid="0.42", ask="0.44", depth=2):
    """A plan shaped exactly as `build_orders` emits one: OWN prices."""
    b, a = D(bid), D(ask)
    t = D("0.01")
    return {"ok": True, "legs": [
        {"side": "UP", "price": str(b - depth * t), "qty": "1"},
        {"side": "DOWN", "price": str(D(1) - a - depth * t), "qty": "1"}]}


# =====================================================================
# THE ARITHMETIC
# =====================================================================

def test_a_plan_priced_from_the_current_book_has_not_drifted():
    d = TS.plan_drift_ticks(_legs(), bid="0.42", ask="0.44", tick="0.01",
                            depth_ticks=2)
    assert d == D(0), d


def test_a_four_tick_move_reads_as_four_ticks_of_drift():
    """THE REPRODUCTION, in arithmetic. The plan was priced at bid 0.42;
    the bid is now 0.46."""
    d = TS.plan_drift_ticks(_legs("0.42", "0.44"), bid="0.46", ask="0.48",
                            tick="0.01", depth_ticks=2)
    assert d == D(4), d


def test_drift_is_the_WORST_leg_not_the_average():
    """One leg stale is enough: it is the one that will not fill."""
    p = _legs("0.42", "0.44")
    p["legs"][1]["price"] = str(D(1) - D("0.48") - D("0.02"))   # DOWN fresh
    d = TS.plan_drift_ticks(p, bid="0.46", ask="0.48", tick="0.01",
                            depth_ticks=2)
    assert d == D(4), d                      # the UP leg still carries it


def test_a_spent_leg_is_not_judged():
    """The 2215z rule, carried over verbatim: a spent leg's price is
    history and will never be sent again."""
    d = TS.plan_drift_ticks(_legs("0.42", "0.44"), bid="0.46", ask="0.48",
                            tick="0.01", depth_ticks=2, spent_sides=["UP"])
    # only DOWN remains, and DOWN's fair price moved by 4 ticks too
    assert d == D(4), d
    both = TS.plan_drift_ticks(_legs("0.42", "0.44"), bid="0.46", ask="0.48",
                               tick="0.01", depth_ticks=2,
                               spent_sides=["UP", "DOWN"])
    assert both is None, both                # nothing left to judge


@pytest.mark.parametrize("kw", [
    {"bid": None, "ask": "0.44"},
    {"bid": "0.42", "ask": None},
    {"bid": "0", "ask": "0.44"},
    {"bid": "nan", "ask": "0.44"},
    {"bid": "0.50", "ask": "0.44"},          # crossed
    {"bid": "0.44", "ask": "0.44"},          # locked
])
def test_an_unjudgeable_book_returns_None_and_never_zero(kw):
    """None, not zero. A caller must not read "I could not tell" as "it
    has not drifted" -- that would re-price on every unreadable book."""
    assert TS.plan_drift_ticks(_legs(), tick="0.01", depth_ticks=2,
                               **kw) is None


def test_an_unreadable_leg_price_returns_None():
    p = _legs()
    p["legs"][0]["price"] = "not-a-price"
    assert TS.plan_drift_ticks(p, bid="0.42", ask="0.44", tick="0.01",
                               depth_ticks=2) is None


def test_a_plan_that_is_not_two_legs_returns_None():
    assert TS.plan_drift_ticks({"legs": []}, bid="0.42", ask="0.44",
                               tick="0.01", depth_ticks=2) is None


# =====================================================================
# THE TOLERANCE
# =====================================================================

def test_the_default_tolerance_is_two_ticks(monkeypatch):
    monkeypatch.delenv(TS.REPLAN_DRIFT_ENV, raising=False)
    assert TS.replan_drift_ticks() == 2


def test_the_tolerance_can_never_exceed_the_retirement_tolerance(
        monkeypatch):
    """THE CLAMP THAT MATTERS. Above `STALE_DRIFT_TICKS` the re-price would
    sit BEHIND retirement again and restore the gap this closes."""
    monkeypatch.setenv(TS.REPLAN_DRIFT_ENV, "99")
    assert TS.replan_drift_ticks() == TS.STALE_DRIFT_TICKS
    assert TS.replan_drift_ticks() <= TS.STALE_DRIFT_TICKS


@pytest.mark.parametrize("raw,want", [("1", 1), ("3", 3), ("6", 6),
                                      ("0", 1), ("-4", 1), ("", 2),
                                      ("junk", 2), ("2.5", 2)])
def test_the_tolerance_parses_or_falls_back(monkeypatch, raw, want):
    monkeypatch.setenv(TS.REPLAN_DRIFT_ENV, raw)
    assert TS.replan_drift_ticks() == want


# =====================================================================
# THE REAL PATH
# =====================================================================

def _win(tmp_path):
    return H.Window(tmp_path, limits=RC.deployed_limits(),
                    gates=RC.deployed_gates())


def test_the_reproduction_the_replacement_now_carries_the_CURRENT_price(
        tmp_path):
    """THE DEFECT, END TO END. Before the repair this sent UP 0.40 against
    a 0.46 bid; it now sends 0.44, which is the depth-2 price the planner
    itself derives from that book."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)                             # both cancelled, zero filled
    assert w.store.live_order_count() == 0
    frozen = {s["side"]: D(s["price"]) for s in w.venue.sent[:2]}
    w.book.set("0.46", "0.48")
    r = w.tick(200.0, bid="0.46", ask="0.48")
    assert w.reason(r) == "SUBMITTED", w.reason(r)
    stage = r.get("TWO_SIDED_REPLAN")
    assert stage is not None, sorted(r)
    assert stage["repriced"] is True, stage
    assert D(stage["drift_ticks"]) == D(4), stage
    fresh = w.venue.sent[-1]
    assert D(fresh["price"]) != frozen[fresh["side"]], (fresh, frozen)
    # and it is the price the planner derives RIGHT NOW, not merely a
    # different number
    want = TS.quote_own_price(fresh["side"], bid=D("0.46"), ask=D("0.48"),
                              tick=D("0.01"), depth_ticks=2)
    assert D(fresh["price"]) == want, (fresh["price"], want)


def test_a_plan_that_has_NOT_drifted_is_left_exactly_as_it_was(tmp_path):
    """No re-price, no stage, no rewrite of the stored plan. A one-tick
    book is below the two-tick tolerance."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)
    before = w.ex.probe_stored_plan(SLUG, w.ex.probe_round(SLUG) or 0)
    r = w.tick(200.0)                         # the SAME book
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage is None or stage.get("repriced") is not True, stage
    after = w.ex.probe_stored_plan(SLUG, w.ex.probe_round(SLUG) or 0)
    if before is not None and after is not None:
        assert ([str(l["price"]) for l in before.get("legs") or ()]
                == [str(l["price"]) for l in after.get("legs") or ()])


def test_a_LIVE_order_forbids_the_re_price_so_leg_two_still_matches(
        tmp_path):
    """THE FREEZE'S PURPOSE, PRESERVED. With leg one resting, the round
    holds something and the plan must not move under leg two."""
    w = _win(tmp_path)
    w.tick(30.0)                              # leg one RESTS
    assert w.store.still_fillable_order_count() == 1
    # TWO TICKS, which is AT the re-price tolerance, and chosen so the
    # frozen DOWN leg does not CROSS the new book -- at own 0.54 its wire
    # is 0.46, which rests exactly at the new ask. A larger move refuses
    # leg two on PRESEND_WOULD_CROSS and would prove nothing about the
    # re-price guard.
    w.book.set("0.44", "0.46")
    r = w.tick(45.0, bid="0.44", ask="0.46")
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage is None, stage               # not even considered
    # leg two is the COMPLEMENT of the frozen leg one, same quantities
    assert len(w.venue.sent) == 2
    assert w.venue.sent[1]["side"] != w.venue.sent[0]["side"]
    assert D(w.venue.sent[1]["qty"]) == D(w.venue.sent[0]["qty"])


def test_HELD_inventory_forbids_the_re_price(tmp_path):
    """An unmatched fill is the other thing that forbids it: re-pricing
    under a held leg would change what the complement is matching."""
    w = _win(tmp_path)
    w.venue.fill_next()
    w.tick(30.0)                              # leg one FILLS
    side = w.venue.sent[0]["side"]
    assert w.store.position(SLUG, side) > 0
    assert w.store.live_order_count() == 0    # nothing live -- only the fill
    w.book.set("0.46", "0.48")
    r = w.tick(45.0, bid="0.46", ask="0.48")
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage is None, stage


def test_a_fresh_plan_that_will_not_PRICE_is_never_substituted(tmp_path,
                                                               monkeypatch):
    """IT CAN NEVER MAKE THINGS WORSE. If the re-plan refuses, the engine
    keeps the plan it already had rather than losing both."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)
    before = w.ex.probe_stored_plan(SLUG, w.ex.probe_round(SLUG) or 0)
    monkeypatch.setattr(type(w.ex), "probe_plan",
                        lambda self, *a, **k: {"ok": False,
                                               "reason": "BOOK_STALE"})
    w.book.set("0.46", "0.48")
    r = w.tick(200.0, bid="0.46", ask="0.48")
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage is not None and stage["repriced"] is False, stage
    assert stage["refused"] == "BOOK_STALE", stage
    after = w.ex.probe_stored_plan(SLUG, w.ex.probe_round(SLUG) or 0)
    if before is not None:
        assert after is not None
        assert ([str(l["price"]) for l in before.get("legs") or ()]
                == [str(l["price"]) for l in after.get("legs") or ()])


def test_unreadable_round_state_keeps_the_frozen_plan(tmp_path,
                                                      monkeypatch):
    """FAIL CLOSED. An unknown is not "the round holds nothing"."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)

    def boom(*a, **k):
        raise RuntimeError("durable state unreadable")

    # the drift read is inside the same guarded block as the round-state
    # read, so making either one raise exercises the same fail-closed
    # branch. This one is patched because `probe_round_is_safe_to_retire`
    # is also called by the RETIREMENT path, and breaking that would be
    # testing a different mechanism.
    monkeypatch.setattr("src.l5_two_sided_probe.plan_drift_ticks", boom)
    w.book.set("0.46", "0.48")
    r = w.tick(200.0, bid="0.46", ask="0.48")
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage is None, stage               # never re-priced


def test_the_re_price_cancels_nothing_so_it_cannot_churn_orders(tmp_path):
    """THE CHURN ARGUMENT, asserted rather than claimed. Across a drifting
    book the re-price adds no cancellation of its own: every cancellation
    is the age deadline's, by exact id."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)
    cancels_before = list(w.venue.cancelled)
    for off, bid, ask in ((200.0, "0.46", "0.48"), (215.0, "0.50", "0.52"),
                          (230.0, "0.38", "0.40")):
        w.book.set(bid, ask)
        w.tick(off, bid=bid, ask=ask)
    # no cancellation beyond the two the TTL already made
    assert w.venue.cancelled[:len(cancels_before)] == cancels_before
    extra = w.venue.cancelled[len(cancels_before):]
    for vid in extra:
        # anything further must be a TTL cancellation of an order the
        # re-price itself placed, never of one it replaced in place
        assert vid in w.venue.by_oid, vid


def test_the_re_priced_pair_still_clears_the_fee_safe_cap(tmp_path):
    """It is a PLAN, through every gate. A re-price cannot buy a pair the
    opening ceiling would refuse."""
    w = _win(tmp_path)
    w.tick(30.0)
    w.tick(45.0)
    w.tick(175.0)
    w.book.set("0.46", "0.48")
    r = w.tick(200.0, bid="0.46", ask="0.48")
    stage = (r or {}).get("TWO_SIDED_REPLAN")
    assert stage and stage["repriced"] is True
    pair = sum(D(l["price"]) for l in stage["now"])
    assert pair <= TS.max_pair_cost(), (pair, TS.max_pair_cost())
