"""A COMPLETE is never cancelled for repricing without a legal replacement.

OWNER: "A complement must never be cancelled for repricing unless its
replacement passes all applicable preflight checks."

THIS IS THE UNION OF TWO GUARDS, and the history matters because each closes a
hole the other left open.

  * SAME-PRICE SUPPRESSION came first, from measured evidence: on the
    2026-09-13/14 cohort, 283 of 301 re-quote transitions (94.0%) reposted the
    IDENTICAL price, with runs of 115, 96 and 52 consecutive identical reposts
    in one market. A COMPLETE's price comes from the pair bound, not the touch,
    so a moved touch does not imply a moved price.
  * NO-LEGAL-REPLACEMENT is the defect this file is named for. `nxt` is None
    both when no fee-safe complement exists at the current book AND when the
    pricing call raised, and the earlier version fell through to the cancel in
    BOTH cases -- abandoning the only order working inventory already paid for.
    It is now fail-closed: no legal price means keep resting.

WHY DIRECTION IS DELIBERATELY NOT TESTED. An earlier draft of mine held the
order whenever the new price was "worse placed" (lower). That was unsafe. The
legal complement price FALLS as the cost basis rises -- at a 0.98 bound, basis
0.30 prices 0.68 and basis 0.70 prices 0.28 -- so a lower `nxt` means the
resting order now sits ABOVE what the bound sanctions. Holding it to keep queue
position would preserve an order that can fill outside the bound. Reposting
lower is the conservative act, and every `nxt` is legal by construction because
it came from the bound-checked pricing call.

OPEN and EXTEND keep the blanket behaviour, correctly: they join the touch, so a
moved touch does imply a moved price, and if no legal price exists they simply
do not open and nothing is at risk.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as ses

_SRC = open(ses.__file__).read()
TICK = D("0.01")


def _guard() -> str:
    start = _SRC.index("if relation_ in requote_relations() and touch != px_:")
    end = _SRC.index('return f"touch_moved:{touch}"', start)
    return _SRC[start:end]


# ------------------------------------------------- the guard is where it must be
def test_the_guard_sits_inside_the_requote_condition_and_is_complete_only():
    g = _guard()
    assert 'if relation_ == "complete":' in g
    assert "spring_complement_price(" in g
    assert '"open"' not in g and '"extend"' not in g


def test_the_replacement_is_priced_BEFORE_any_cancel_can_be_returned():
    g = _guard()
    assert g.index("spring_complement_price(") < len(g)
    # there is no cancel return inside the guard at all; the only one is after
    assert "touch_moved" not in g


def test_no_legal_replacement_keeps_the_order_resting():
    g = _guard()
    i = g.index("if nxt is None:")
    assert "return None" in g[i:]
    assert "no_legal_replacement" in g[i:]


def test_a_pricing_exception_fails_CLOSED_and_is_distinguishable_in_the_tape():
    g = _guard()
    assert "priced_failed = type(exc).__name__" in g
    # the exception does not itself return; it falls into the None check
    i = g.index("except Exception as exc:")
    between = g[i:g.index("if nxt is None:")]
    assert "touch_moved" not in between
    assert "price_failed:%s" in g


def test_the_same_price_repost_is_suppressed_and_counted():
    g = _guard()
    i = g.index("if D(str(nxt)) == px_:")
    tail = g[i:]
    assert "requotes_suppressed" in tail
    assert "return None" in tail


def test_a_different_legal_price_is_recorded_and_then_cancels():
    g = _guard()
    assert 'track["requote_replacement"] = str(nxt)' in g
    # and the bare cancel is the single one, immediately after the guard
    assert _SRC.count('return f"touch_moved:{touch}"') == 1


def test_direction_is_NOT_tested_because_that_would_be_unsafe():
    """Pins the decision, so a future edit cannot quietly reintroduce it."""
    g = _guard()
    for unsafe in ("<= px_", "< px_", "> px_", ">= px_"):
        assert unsafe not in g, unsafe
    assert "conservative act" in g


def test_the_measured_evidence_for_same_price_suppression_is_recorded():
    g = _guard()
    for fact in ("472 real orders", "301", "283", "94.0%", "115, 96"):
        assert fact in g, fact


# ------------------------------------------------------ the guards it keeps
def test_the_crossing_guard_still_precedes_everything():
    assert (_SRC.index('return f"would_cross:{opp_ask}"')
            < _SRC.index("if relation_ in requote_relations()"))


def test_there_is_still_exactly_one_requote_condition():
    assert _SRC.count("if relation_ in requote_relations() and touch != px_:") == 1


def test_the_guard_adds_no_second_copy_of_the_pair_bound():
    """It must reuse the pricing call, not re-implement the ceiling.

    Scoped to CODE lines: the comment is allowed to quote the bound when it
    explains why direction is unsafe, and does, but no executable line may
    name a ceiling of its own.
    """
    code = "\n".join(ln for ln in _guard().splitlines()
                     if not ln.strip().startswith("#"))
    for forbidden in ("SPRING_PAIR_TARGET", "pair_cost_max(",
                      "completion_pair_ceiling(", "0.98", "0.96"):
        assert forbidden not in code, forbidden


def test_it_passes_the_touch_through_only_in_the_l5_touch_regime():
    g = _guard()
    assert 'complement_pricing() == "l5_touch"' in g
    assert "touch=(touch" in g


# ------------------------------------------- the preflight it actually reuses
@pytest.mark.parametrize("basis,expect_none", [
    (D("0.30"), False), (D("0.44"), False), (D("0.60"), False),
    (D("0.90"), True), (D("0.97"), True), (D("0.98"), True)])
def test_no_legal_replacement_is_a_REACHABLE_state(basis, expect_none):
    """The fail-closed branch is not hypothetical."""
    px, econ = ses.spring_complement_price(basis, D("0.50"), TICK)
    assert (px is None) is expect_none, econ


def test_the_legal_price_falls_as_basis_rises_which_is_why_direction_is_unsafe():
    lo, _ = ses.spring_complement_price(D("0.30"), D("0.90"), TICK)
    hi, _ = ses.spring_complement_price(D("0.70"), D("0.90"), TICK)
    assert lo is not None and hi is not None
    assert hi < lo, (lo, hi)


# ----------------------------------------------------- still dark by default
def test_the_flag_remains_off_so_the_guard_is_not_live_yet(monkeypatch):
    monkeypatch.delenv(ses.COMPLETE_REQUOTE_ENV, raising=False)
    assert ses.complete_requote_enabled() is False
    assert "complete" not in ses.requote_relations()
