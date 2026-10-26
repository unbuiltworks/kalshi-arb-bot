"""A re-quote that reposts the same price is pure loss.

The resting loop cancels when the TOUCH moves away from our price. For OPEN
and EXTEND that implies a different price, because they join the touch. A
COMPLETE does not: its price comes from the pair bound, so the repost
recomputes the identical number and we have cancelled only to re-queue behind
everyone.

Measured on the 2026-09-13/14 cohort -- 472 real orders with venue
order_ids -- of 301 re-quote transitions, 283 (94.0%) reposted the SAME
price, with runs of 115, 96 and 52 consecutive identical reposts in a single
market.

The fix asks what the repost would price, using the same call the repost
makes, and keeps resting when that equals the resting price. It changes no
price, no bound and no size.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

from src import pmus_spring_l5_session as S


SRC = inspect.getsource(S)


def _guard_block():
    """The whole guard, to and including the bare cancel.

    This was a fixed 2600-character slice. The guard's comment grew when the
    no-legal-replacement branch landed and the CODE fell outside the window,
    so three assertions failed while the thing they assert was present. A
    slice that ends at the construct it is bounded by cannot drift that way.
    """
    i = SRC.index('if relation_ in requote_relations() and touch != px_:')
    tail = 'return f"touch_moved:{touch}"'
    return SRC[i:SRC.index(tail, i) + len(tail)]


def test_the_suppression_only_applies_to_complete():
    """OPEN and EXTEND join the touch, so a moved touch IS a new price."""
    g = _guard_block()
    assert 'if relation_ == "complete":' in g
    assert g.index('if relation_ == "complete":') < g.index('touch_moved')


def test_it_prices_the_repost_with_the_same_call_the_repost_uses():
    """Not a re-implementation: the same function, same touch argument."""
    g = _guard_block()
    assert "spring_complement_price(" in g
    assert 'complement_pricing() == "l5_touch"' in g, \
        "the touch argument must follow the live pricing regime"


def test_it_keeps_resting_when_the_price_is_identical():
    """Split in two when the None case stopped sharing this branch.

    It read `if nxt is not None and D(str(nxt)) == px_:`, which bundled two
    unlike cases: "priced the same" and "could not price at all". They now
    have separate branches because they need opposite reasons in the tape,
    and only the second was ever a defect.
    """
    g = _guard_block()
    assert "if D(str(nxt)) == px_:" in g
    assert "requotes_suppressed" in g
    assert "return None" in g


def test_a_failure_to_price_now_FAILS_CLOSED_and_keeps_resting():
    """CHANGED DELIBERATELY. This test used to require the opposite.

    It read "An exception must not silently pin an order in place" and
    asserted the guard fell through to the cancel whenever pricing failed.
    That is the cancel-before-replacement defect: `nxt` is None both when
    pricing RAISED and when no fee-safe complement EXISTS at the current
    book, and the fall-through cancelled in both cases -- destroying the only
    order working inventory we had already paid for, with nothing to replace
    it. OWNER: "A complement must never be cancelled for repricing unless its
    replacement passes all applicable preflight checks."

    THE OLD CONCERN WAS REAL AND IS ANSWERED ELSEWHERE, which is why the
    inversion is safe. A held order is not held indefinitely: `_await_order`
    rests "until filled, until the deadline, or until still_wanted() names a
    reason", and STOP_BEFORE_SETTLEMENT_S (120 s) and the market-scoped
    deadman bound it independently. So the deadline retires the order, not a
    cancel issued with no replacement in hand.

    The two causes are also distinguishable in the tape rather than merged.
    """
    g = _guard_block()
    assert "except Exception as exc:" in g
    assert "priced_failed = type(exc).__name__" in g
    # the exception does NOT return; it falls into the shared None branch
    between = g[g.index("except Exception as exc:"):g.index("if nxt is None:")]
    assert "touch_moved" not in between
    # and that branch keeps resting, naming which cause it was
    after = g[g.index("if nxt is None:"):]
    assert "return None" in after
    assert "no_legal_replacement" in after and "price_failed:%s" in after


def test_the_suppression_is_counted_so_it_is_observable():
    assert '"requotes_suppressed"' in _guard_block()


def test_the_measured_evidence_is_recorded_beside_the_guard():
    g = _guard_block()
    assert "94.0%" in g and "283" in g


def test_the_complement_price_function_is_unchanged_in_behaviour():
    """The fix must not alter any price the engine chooses.

    Same inputs, same outputs as the bound arithmetic has always given:
    min(pair bound, fee-safe bound, opposite touch - 1 tick).
    """
    tick = D("0.01")
    for avg, opp_ask, want in ((D("0.38"), D("0.62"), D("0.60")),
                               (D("0.38"), D("0.89"), D("0.60")),
                               (D("0.50"), D("0.52"), D("0.48"))):
        px, econ = S.spring_complement_price(avg, opp_ask, tick)
        assert px == want, (avg, opp_ask, px, econ.get("regime"))
        assert avg + px <= S.pair_cost_max()


def test_the_far_from_touch_bound_case_still_prices():
    """The case a distance cap would have broken, kept working.

    A sub-par pair REQUIRES bidding below the touch: at avg 0.50 against a
    0.99 opposite touch the complement is 0.48, which is 50 ticks away and
    entirely correct. An earlier attempt at this fix capped distance from the
    touch and broke 26 existing tests for exactly this reason.
    """
    px, econ = S.spring_complement_price(D("0.50"), D("0.99"), D("0.005"))
    assert px is not None
    assert px <= D(econ["hard_bound_px"])
