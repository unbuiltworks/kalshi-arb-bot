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
    i = SRC.index('if relation_ in requote_relations() and touch != px_:')
    return SRC[i:i + 2600]


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


def test_it_keeps_resting_only_when_the_price_is_identical():
    g = _guard_block()
    assert "if nxt is not None and D(str(nxt)) == px_:" in g
    assert "return None" in g


def test_a_failure_to_price_falls_back_to_the_old_behaviour():
    """An exception must not silently pin an order in place."""
    g = _guard_block()
    assert "except Exception:" in g
    assert "nxt = None" in g.split("except Exception:")[1][:120]


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
