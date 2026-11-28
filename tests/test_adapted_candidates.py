"""FROZEN ADAPTED candidates, validated against the 2026-09-17 live cohort.

These are not invented scenarios. Every book state below is one the venue
actually served during the cohort, and every order is one we actually sent, so
a candidate that gets these wrong would have got the real day wrong.

The two facts the candidates exist to fix:
  * 13 rejections, ALL extend, 11 of them the SAME order (wire 0.72, qty 0.14).
    It rested 6 times while the YES bid was ~0.49-0.51 and was rejected 11
    times after the bid ran through 0.72 between 13:05 and 13:11.
  * 14 COMPLETEs all rested at 0.49-0.50 while the market went to 0.99 -- a
    median 34.5 ticks behind the touch -- and none filled.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.sweep import candidates as C

# the live book, early and late in the same market
EARLY = (D("0.51"), D("0.52"))        # yes_bid, yes_ask -- extends rested here
LATE = (D("0.99"), None)              # 49 bid levels, offers: [] -- stranded


# --------------------------------------------------------------------------
# candidate 1 against the real rejections

def test_the_repeated_extend_rests_early_and_crosses_late():
    """The whole finding, in one assertion pair: identical order, opposite
    outcome, and the only thing that changed is the book."""
    wire = D("0.72")                  # NO buy at 0.28 -> wire 0.72
    assert C.would_cross(False, wire, *EARLY) is False   # rested, 6 times
    assert C.would_cross(False, wire, D("0.99"), D("1.00")) is True  # rejected


def test_pair_target_placement_reproduces_the_live_failure():
    """The control must fail the way production failed, or the comparison is
    meaningless."""
    r = C.extend_placement("pair_target", False, D("0.72"),
                           D("0.99"), D("1.00"))
    assert r["outcome"] == C.CROSS


@pytest.mark.parametrize("mode", ["nearest_maker", "one_behind"])
def test_book_aware_placement_never_crosses_where_a_legal_price_exists(mode):
    """The point of candidate 1: on a moved book that rejected the stale
    price, a book-aware price is legal instead. Book here is 0.80/0.82, i.e.
    well past the 0.72 the controller kept re-sending."""
    r = C.extend_placement(mode, False, D("0.72"), D("0.80"), D("0.82"))
    assert r["outcome"] == C.RESTABLE
    assert C.would_cross(False, D(r["px"]), D("0.80"), D("0.82")) is False


@pytest.mark.parametrize("mode", ["nearest_maker", "one_behind"])
def test_at_the_extreme_there_is_no_legal_maker_price_and_it_says_so(mode):
    """At bid 0.99 / ask 1.00 a NO maker buy would need a wire price at or
    above 1.00, which is not a price. The candidate must WAIT rather than
    invent one -- inventing it is how a sweep manufactures opportunity that
    the venue would never have accepted."""
    r = C.extend_placement(mode, False, D("0.72"), D("0.99"), D("1.00"))
    assert r["outcome"] == C.WAIT
    assert r["px"] is None


def test_one_behind_is_strictly_less_aggressive_than_nearest():
    a = C.extend_placement("nearest_maker", True, D("0.50"), *EARLY)
    b = C.extend_placement("one_behind", True, D("0.50"), *EARLY)
    assert D(b["px"]) < D(a["px"])


def test_a_one_sided_book_produces_WAIT_not_a_guess():
    r = C.extend_placement("nearest_maker", True, D("0.50"), *LATE)
    assert r["outcome"] == C.WAIT
    assert "one-sided" in r["why"]


# --------------------------------------------------------------------------
# candidate 2 -- the owner's highest priority

def test_complete_is_waited_not_crossed_when_the_book_is_one_sided():
    """The live stranding: 0.19 NO residual, no offers, nothing to buy."""
    r = C.complete_plan(True, D("0.41"), D("0.19"), *LATE, D("0.98"))
    assert r["outcome"] == C.WAIT
    assert r["pair_cost"] is None


def test_a_worse_pair_ceiling_turns_a_WAIT_into_an_EXECUTABLE():
    """The sweep's actual question. Held NO at 0.41; completing at the touch
    costs 0.51, so the pair is 0.92 -- inside 0.98. Push the held cost up and
    0.98 refuses while 1.02 allows."""
    ok = C.complete_plan(True, D("0.41"), D("0.19"), *EARLY, D("0.98"))
    assert ok["outcome"] == C.EXECUTABLE and D(ok["pair_cost"]) == D("0.92")
    # held 0.55 + best maker complement 0.51 = 1.06
    tight = C.complete_plan(True, D("0.55"), D("0.19"), *EARLY, D("0.98"))
    assert tight["outcome"] == C.WAIT and D(tight["pair_cost"]) == D("1.06")
    still_tight = C.complete_plan(True, D("0.55"), D("0.19"), *EARLY, D("1.02"))
    assert still_tight["outcome"] == C.WAIT      # 1.06 > 1.02, correctly
    loose = C.complete_plan(True, D("0.55"), D("0.19"), *EARLY, D("1.08"))
    assert loose["outcome"] == C.EXECUTABLE
    assert D(loose["pair_cost"]) == D("1.06")
    assert D(loose["residual_reduction"]) == D("0.19")


def test_pair_below_one_is_not_asserted_to_be_profitable():
    r = C.complete_plan(True, D("0.41"), D("0.19"), *EARLY, D("0.98"))
    assert "fees NOT netted" in r["labels"]
    assert "not automatically profitable" in r["labels"]


def test_every_grid_ceiling_is_labelled_adapted():
    for mx in C.GRID["complete_max_pair_cost"]:
        r = C.complete_plan(True, D("0.41"), D("0.19"), *EARLY, D(mx))
        assert r["max_pair_cost"] == mx
        assert "ADAPTED" in r["labels"]


# --------------------------------------------------------------------------
# candidate 3 -- residual-aware suppression

def test_control_mode_suppresses_nothing():
    r = C.extend_suppression(D("2"), D("3"), 9, D("0.4"), D("0.4"), True,
                             mode="off")
    assert D(r["suppress_p"]) == 0


def test_a_one_sided_book_fully_suppresses_extend():
    """Extending into a book that cannot complete is how the residual got
    stranded; this is the single clearest suppression condition."""
    r = C.extend_suppression(D("0.19"), D("3"), 1, None, None, True)
    assert D(r["suppress_p"]) == 1
    assert "one-sided" in r["reason"]


def test_suppression_rises_with_residual():
    lo = C.extend_suppression(D("0.3"), D("3"), 0, None, None, False)
    hi = C.extend_suppression(D("2.7"), D("3"), 0, None, None, False)
    assert D(hi["suppress_p"]) > D(lo["suppress_p"])


def test_hard_mode_is_all_or_nothing():
    r = C.extend_suppression(D("0.3"), D("3"), 0, None, None, False, mode="hard")
    assert D(r["suppress_p"]) == 1
    n = C.extend_suppression(D("0"), D("3"), 0, None, None, False, mode="hard")
    assert D(n["suppress_p"]) == 0


# --------------------------------------------------------------------------
# candidate 4 -- matched-inventory preservation

def test_the_live_pattern_is_what_this_gate_blocks():
    """2026-09-17: matched 0.40/0.40 with residual 0.00 -- a completed, safe
    position -- then a further NO leg rebuilt exposure to 0.59/0.40 that could
    not be unwound."""
    off = C.acquisition_gate(D("0.40"), D("0"), D("0.30"), mode="off")
    assert off["allow"] is True                      # what we did
    wait = C.acquisition_gate(D("0.40"), D("0"), D("0.30"), mode="wait")
    assert wait["allow"] is False                    # what the candidate does


def test_small_matched_inventory_is_not_protected():
    r = C.acquisition_gate(D("0.05"), D("0"), D("0.30"), mode="wait")
    assert r["allow"] is True


def test_require_flat_allows_a_fresh_sequence_but_not_a_rebuild():
    assert C.acquisition_gate(D("0.40"), D("0"), D("0.30"),
                              mode="require_flat")["allow"] is True
    assert C.acquisition_gate(D("0.40"), D("0.19"), D("0.30"),
                              mode="require_flat")["allow"] is False


# --------------------------------------------------------------------------
# candidate 5 -- proposed-order residual cap

def test_the_live_overshoot_would_have_been_refused():
    """Lifecycle 2 reached residual 3.8636 against a cap of 3 because the cap
    tested the CURRENT residual, not the one the order would create."""
    cur = D("2.8636")
    assert C.proposed_residual_ok(cur, D("1.0"), True, D("3"))["allow"] is False
    r = C.proposed_residual_ok(cur, D("1.0"), True, D("3"))
    assert D(r["residual_after_if_filled"]) == D("3.8636")


def test_an_order_that_reduces_residual_is_always_allowed():
    r = C.proposed_residual_ok(D("2.9"), D("1.0"), False, D("3"))
    assert r["allow"] is True
    assert D(r["residual_after_if_filled"]) == D("1.9")


def test_the_cap_is_never_relaxed_by_this_candidate():
    """It may only refuse more, never less."""
    for units in ("0.1", "1.0", "2.9"):
        cur = D("0")
        strict = C.proposed_residual_ok(cur, D(units), True, D("3"))
        assert strict["allow"] is (D(units) <= D("3"))


# --------------------------------------------------------------------------
# the vocabulary itself

def test_there_is_no_such_thing_as_a_filled_outcome():
    """A tape of book states cannot prove a fill. If FILLED ever appears in
    this module, the sweep has started inventing evidence."""
    import inspect
    src = inspect.getsource(C)
    assert '"FILLED"' not in src and "'FILLED'" not in src
    assert set([C.CROSS, C.RESTABLE, C.EXECUTABLE, C.WAIT]) == {
        "CROSS", "RESTABLE", "EXECUTABLE", "WAIT"}


def test_the_grid_is_frozen_and_includes_the_current_behaviour_as_control():
    assert C.GRID["extend_placement"][0] == "pair_target"
    assert C.GRID["extend_suppression"][0] == "off"
    assert C.GRID["acquisition_gate"][0] == "off"
    assert "0.98" in C.GRID["complete_max_pair_cost"]
