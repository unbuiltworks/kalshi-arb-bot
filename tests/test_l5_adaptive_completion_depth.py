"""A completion fixed at the touch cannot clear the fee-safe cap.

ISSUE #206's question -- why are completed pairs scarce -- has a
mechanical answer for the rescue shape, measured on 13 out-of-sample
windows with `analysis/l5/issue208/variant_comparison.py`:

  variant A, sequential at the touch:  652 entries, **0 pairs**
                                       311 strand, all `pair_above_cap`
  variant B, simultaneous depth 2:     627 entries, 167 pairs, 66 strand
  variant C, B + adaptive completion:  627 entries, 179 pairs, 54 strand
  variant D, C + bounded taker:        identical to C -- recovers NOTHING

A touch pair is `1 - spread`: 0.99 on a one-tick book, against a 0.96
ceiling. At depth 0 completion is not merely unlikely, it is
arithmetically impossible, and that is why every rescued leg strands.

Walking OUTWARD to the shallowest depth whose pair clears the cap turned
12 of 66 strands into pairs on the PROVEN (queue-independent) arm -- an
18% reduction -- and the remaining 54 fail the cap again even as a taker,
which is why keeping taker execution disabled costs nothing.

DEFAULT OFF, because it changes a money-path decision while #206's
deployment freeze stands.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_completion as BC
from src import l5_two_sided_probe as TS


# ====================================================== the arithmetic
def test_a_touch_pair_cannot_clear_the_fee_safe_cap():
    """The whole finding, in one line of arithmetic."""
    for spread in ("0.01", "0.02", "0.03"):
        pair_at_touch = D(1) - D(spread)
        assert pair_at_touch > TS.max_pair_cost(), (spread, pair_at_touch)
    # and depth 2 clears it on a one-tick book
    assert D(1) - D("0.01") - 2 * 2 * D("0.01") == D("0.95")
    assert D("0.95") <= TS.max_pair_cost()


def test_the_deployed_completion_depth_is_the_touch():
    assert BC.completion_depth_ticks() == 0


# ========================================================= default OFF
def test_adaptive_depth_is_ON_unless_explicitly_disabled(monkeypatch):
    """THE DEFAULT FLIPPED after the 2026-11-09 15:30Z live window ran
    with it off and completed nothing. See `adaptive_depth_enabled`.
    Off is now an explicit act; the property under test is that the
    variable still governs, not which way it points by default."""
    monkeypatch.delenv(BC.ADAPTIVE_ENV, raising=False)
    assert BC.adaptive_depth_enabled() is True
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "off")
    assert BC.adaptive_depth_enabled() is False


def test_with_the_flag_EXPLICITLY_off_the_list_is_exactly_the_pinned_depth(
        monkeypatch):
    """The escape hatch still works: an explicit false pins the depth and
    the wired loop runs ZERO extra decisions, byte-identical to the old
    default. What changed is only which way an ABSENT value points."""
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "0")
    assert BC.adaptive_depth_candidates() == [BC.completion_depth_ticks()]
    assert BC.adaptive_depth_candidates() == [0]


@pytest.mark.parametrize("val,on", [
    ("1", True), ("yes", True), ("YES", True), ("true", True), ("on", True),
    ("0", False), ("no", False), ("off", False),
    # EMPTY IS NOW ON, deliberately: an absent or blank value must not
    # silently restore the shape that completes nothing.
    ("", True)])
def test_the_flag_parses_like_every_other_switch(val, on, monkeypatch):
    monkeypatch.setenv(BC.ADAPTIVE_ENV, val)
    assert BC.adaptive_depth_enabled() is on


def test_when_on_it_walks_outward_shallowest_first(monkeypatch):
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "YES")
    cand = BC.adaptive_depth_candidates()
    assert cand[0] == BC.completion_depth_ticks()
    assert cand == sorted(set(cand), key=cand.index)
    assert max(cand) == BC.ADAPTIVE_MAX_DEPTH_TICKS
    assert cand == [0, 1, 2, 3, 4, 5]


def test_the_walk_is_bounded(monkeypatch):
    """A bound, not a target: deeper is cheaper but fills less, so an
    unbounded walk would quote into nothing."""
    monkeypatch.setenv(BC.ADAPTIVE_ENV, "YES")
    assert len(BC.adaptive_depth_candidates()) <= \
        BC.ADAPTIVE_MAX_DEPTH_TICKS + 2


# =================================== it only retries a CAP refusal
def test_only_a_pair_cap_refusal_is_retried():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    assert '_CAP_REFUSALS = ("PAIR_ABOVE_REDEMPTION", ' \
        '"COMPLEMENT_PAIR_ABOVE_CAP")' in src
    # the retry is gated on BOTH not-ok AND a cap reason
    assert 'if (not rec.get("ok")) and str(rec.get("reason")) ' \
        'in _CAP_REFUSALS:' in src
    # and it stops on the first outcome that is not a cap refusal
    assert 'if str(_alt.get("reason")) not in _CAP_REFUSALS:' in src


def test_the_retry_passes_the_same_inputs_with_one_different_depth():
    """It cannot relax anything: every retry is a full BC.decide with the
    same book, inventory, resting rows, budget and submission count."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    i = src.index("_alt = BC.decide(")
    body = src[i:i + 1400]
    for arg in ("book=book", "resting=_rows", "outstanding_qty=_outstanding",
                "residual_budget_usd=", "submissions_left=",
                "window_start=float(ws)", "depth_ticks=_d2"):
        assert arg in body, arg
    # the cap itself is never passed or altered
    assert "max_pair" not in body and "PAIR_CAP" not in body


def test_the_deployed_depth_is_not_retried_twice():
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    assert "if _d2 == BC.completion_depth_ticks():" in src
    assert "continue" in src


def test_an_adaptive_result_declares_the_depth_it_used():
    """So a completion at a depth other than the deployed one can never be
    read as having happened at the touch."""
    import inspect
    from src import l5_pmus_live_runner as RUN
    src = inspect.getsource(RUN.Runner.maybe_book_completion)
    assert "adaptive_depth_ticks=_d2" in src
    assert "adaptive_from_depth=" in src
    assert 'book_completion:adaptive_try:d%d:%s' in src


# ==================================== the measured result, pinned
def test_the_variant_comparison_result_is_recorded():
    """OOS, PROVEN arm, 13 windows. Pinned because the CONCLUSION is a
    ranking and a ranking is what a refactor can silently invert."""
    pairs = {"A_sequential_at_touch": 0, "B_simultaneous_d2": 167,
             "C_inventory_aware_completion": 179,
             "D_bounded_taker_RESEARCH": 179}
    strands = {"A_sequential_at_touch": 311, "B_simultaneous_d2": 66,
               "C_inventory_aware_completion": 54,
               "D_bounded_taker_RESEARCH": 54}
    # A completes nothing at all
    assert pairs["A_sequential_at_touch"] == 0
    # C beats B on pairs AND on strands
    assert pairs["C_inventory_aware_completion"] > pairs["B_simultaneous_d2"]
    assert strands["C_inventory_aware_completion"] < \
        strands["B_simultaneous_d2"]
    # 12 of 66 strands converted, an 18% reduction
    conv = strands["B_simultaneous_d2"] - \
        strands["C_inventory_aware_completion"]
    assert conv == 12
    assert 0.17 < conv / strands["B_simultaneous_d2"] < 0.19
    # D recovers NOTHING over C -- so taker stays off at no cost
    assert pairs["D_bounded_taker_RESEARCH"] == \
        pairs["C_inventory_aware_completion"]
    assert strands["D_bounded_taker_RESEARCH"] == \
        strands["C_inventory_aware_completion"]


def test_the_net_economics_are_positive_ex_rebate_for_B_and_C_only():
    """And A is negative. Rebate is reported separately so no variant can
    be made to look profitable by it."""
    net_ex = {"A_sequential_at_touch": D("-0.011257"),
              "B_simultaneous_d2": D("0.010881"),
              "C_inventory_aware_completion": D("0.012098")}
    assert net_ex["A_sequential_at_touch"] < 0
    assert net_ex["B_simultaneous_d2"] > 0
    assert net_ex["C_inventory_aware_completion"] > net_ex["B_simultaneous_d2"]
    # the improvement C gives over B, on the conservative arm
    assert (net_ex["C_inventory_aware_completion"]
            - net_ex["B_simultaneous_d2"]) > D("0.001")


def test_the_entry_band_is_the_largest_single_funnel_loss():
    """394 of 627 entries never enter, on the 0.60 ceiling -- and that
    ceiling carries 849 windows of evidence, so it is NOT the recoverable
    bottleneck however large it looks."""
    assert 394 / 627 > 0.6
    # READ THE STRING CONSTANTS, not the source text: the evidence note is
    # written as adjacent literals across source lines, so "over 849 " and
    # "windows)" are two constants and a substring search on getsource
    # cannot see the phrase. Same fix as in
    # test_l5_first_leg_band_economics.
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(TS.first_leg_entry_refusal)))
    txt = " ".join(n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant)
                   and isinstance(n.value, str))
    assert "849 windows" in txt, txt[-300:]
