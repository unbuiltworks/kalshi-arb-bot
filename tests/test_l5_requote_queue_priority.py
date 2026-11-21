"""The requote must not spend queue priority it has not measured.

WHAT THESE PIN. `completion_requote` documented `queue_ahead` as "REPORTED,
NOT ACTED ON", and the live caller never supplied it, so all ten requotes of
the 2026-11-02 0145z/0200z campaign logged `queue_ahead_at_target: null` and
cancelled anyway -- paying a dearer own price AND surrendering their place in
the queue, on no evidence. Zero of the ten produced a fill.

NOTHING HERE CLAIMS A FILL. The depths are aggregate displayed size from the
authenticated ladder; the venue publishes no per-order position. The gate
compares a MEASURED depth against our own MEASURED need and refuses the one
case that needs no model: strictly worse on both axes.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_two_sided_probe as TSP


def _kw(**over):
    """A requote that ACTS under the pre-patch rules, so each test changes
    exactly one thing. DOWN completion against a held UP leg of 2.05 at
    0.46, the shape the 0145z window actually produced.
    """
    base = dict(
        side="DOWN",
        resting_own_px="0.48",
        resting_qty="2.05",
        resting_filled_qty="0",
        bid="0.48", ask="0.49",
        tick="0.01", min_qty="0.01",
        held_qty="2.05", held_cost_usd="0.943",
        deficient_qty="0", deficient_cost_usd="0",
        residual_budget_usd="1.50",
        now_s=100.0, window_start_s=0.0, window_end_s=900.0,
        book_age_s=0.05,
    )
    base.update(over)
    return base


def test_the_baseline_acts_so_every_other_test_isolates_one_change():
    res = TSP.completion_requote(**_kw())
    assert res.get("act") is True, res


# ------------------------------------------------- fail closed on no evidence

def test_requiring_queue_evidence_refuses_when_there_is_none():
    """The live defect exactly: no accessor supplied, so no depth is known."""
    res = TSP.completion_requote(**_kw(require_queue_evidence=True))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_EVIDENCE_ABSENT"


def test_a_raising_accessor_is_unknown_not_zero():
    def boom(_px):
        raise RuntimeError("ladder read failed")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=boom))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_EVIDENCE_ABSENT"


def test_an_accessor_returning_none_is_unknown_not_zero():
    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=lambda _px: None))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_EVIDENCE_ABSENT"


def test_keyboard_interrupt_from_the_accessor_is_not_swallowed():
    def interrupted(_px):
        raise KeyboardInterrupt

    try:
        TSP.completion_requote(**_kw(require_queue_evidence=True,
                                     queue_ahead_fn=interrupted))
    except KeyboardInterrupt:
        return
    raise AssertionError("KeyboardInterrupt must propagate")


# ------------------------------------------------------- the measured verdict

def test_the_real_0145z_queue_refuses_the_move():
    """The window's own numbers: 120.01 ahead for a 2.05 need is 58.5x.

    This is the decision that, taken ten times, produced no fills.
    """
    def ahead(px):
        return D("12.0") if D(str(px)) == D("0.48") else D("120.01")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_COST_EXCEEDS_GAIN"
    assert res["queue_ahead_at_target"] == "120.01"
    assert res["qty_needed"] == "2.05"
    # 120.01 / 2.05 = 58.54
    assert res["queue_multiple_of_need"] == "58.54"
    # AND IT STATES THE CASH THE MOVE WOULD HAVE SPENT: 2 ticks x 2.05.
    # Was 3 ticks x 2.05 = 0.0615. The pair ceiling now caps the target one
    # tick cheaper -- `completion_max_pair()` defaults to the opening
    # ceiling rather than to redemption -- so the move this gate refuses is
    # a cheaper one than before. The gate itself is unchanged and still
    # refuses on the same 58.54x queue multiple.
    assert res["extra_cash_usd"] == "0.0410"


def test_a_shallower_target_queue_is_an_improvement_on_both_axes():
    """Better price AND less size ahead needs no tolerance argument."""
    def ahead(px):
        return D("500") if D(str(px)) == D("0.48") else D("3.0")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is True, res
    assert res["queue_ahead_at_target_measured"] == "3.0"
    assert res["queue_ahead_at_resting_measured"] == "500"


def test_a_deeper_target_queue_inside_the_tolerance_still_acts():
    """Deeper is tolerated while it stays a plausible multiple of our need.

    2.05 x 10 = 20.5 tolerated; 15 is inside it.
    """
    def ahead(px):
        return D("1.0") if D(str(px)) == D("0.48") else D("15")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is True, res


def test_the_tolerance_boundary_is_not_a_refusal():
    """Exactly 10x our need is tolerated; the refusal is strictly greater."""
    def ahead(px):
        return D("1.0") if D(str(px)) == D("0.48") else D("20.5")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is True, res


def test_just_past_the_boundary_refuses():
    def ahead(px):
        return D("1.0") if D(str(px)) == D("0.48") else D("20.51")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_COST_EXCEEDS_GAIN"


def test_a_deep_target_that_is_still_better_than_resting_acts():
    """The gate needs BOTH axes worse. A huge queue we are already behind
    is not made worse by moving closer to the touch within it.
    """
    def ahead(px):
        return D("9000") if D(str(px)) == D("0.48") else D("8000")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is True, res


# -------------------------------------------------- unresolved cancel blocking

def test_an_unresolved_cancel_outranks_everything():
    """Seven HTTPErrors and three still_open across the campaign, and the
    loop requoted again every time.
    """
    def ahead(px):
        return D("1.0") if D(str(px)) == D("0.48") else D("2.0")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead,
                                       cancel_unresolved="STILL_OPEN_AT_VENUE"))
    assert res["act"] is False
    assert res["reason"] == "CANCEL_UNRESOLVED"


def test_unresolved_cancel_is_checked_before_the_book_is_even_read():
    """It must not depend on a readable book to refuse."""
    res = TSP.completion_requote(**_kw(bid=None, ask=None,
                                       cancel_unresolved="CANCEL_UNVERIFIED"))
    assert res["act"] is False
    assert res["reason"] == "CANCEL_UNRESOLVED"


def test_orders_uncertain_still_outranks_an_unresolved_cancel():
    res = TSP.completion_requote(**_kw(orders_uncertain="x",
                                       cancel_unresolved="y"))
    assert res["act"] is False
    assert res["reason"] == "ORDERS_UNCERTAIN"


# ------------------------------------------------------- backward compatibility

def test_the_gate_is_off_by_default_so_existing_callers_are_unchanged():
    """`require_queue_evidence` defaults False: a caller that supplies no
    accessor behaves exactly as before the patch. The LIVE caller passes
    True; this keeps the change to the one path that was measured.
    """
    res = TSP.completion_requote(**_kw())
    assert res["act"] is True
    assert res["queue_evidence_required"] is False
    assert res["queue_ahead_at_target_measured"] is None


def test_evidence_is_graded_when_supplied_even_without_requiring_it():
    """Supplying the accessor opts in to the verdict; the flag only governs
    what happens when the depth is UNKNOWN.
    """
    def ahead(px):
        return D("1.0") if D(str(px)) == D("0.48") else D("120.01")

    res = TSP.completion_requote(**_kw(queue_ahead_fn=ahead))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_COST_EXCEEDS_GAIN"


def test_a_refusal_never_implies_the_order_was_touched():
    """Every refusal leaves the existing quote resting. Nothing in the
    result may read as a cancellation or a submission.
    """
    res = TSP.completion_requote(**_kw(require_queue_evidence=True))
    assert res["act"] is False
    assert "to_price" not in res
    assert "qty" not in res


# ---------------------------------------------- observe-only calibration mode

def test_observe_only_mode_measures_but_never_refuses():
    """THE FIRST DEPLOYMENT MUST NOT DECIDE. The 0145z requote that moved
    own 0.50 -> 0.52 produced the campaign's ONLY completed pair, and that
    build recorded no queue depth at 0.52 -- so whether this gate would have
    blocked the one profitable action is unknown. Observe first.
    """
    def ahead(px):
        return D("12.0") if D(str(px)) == D("0.48") else D("120.01")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead,
                                       queue_gate_binds=False))
    assert res["act"] is True, res
    # the verdict it WOULD have reached is recorded, not applied
    assert res["queue_gate_would_refuse"] is True
    assert res["queue_gate_binds"] is False
    assert res["queue_ahead_at_target_measured"] == "120.01"


def test_observe_only_still_fails_closed_on_absent_evidence():
    """Observe-only relaxes the VERDICT, not the requirement to measure.
    An absent depth is still unknown and still refuses when required.
    """
    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_gate_binds=False))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_EVIDENCE_ABSENT"


def test_would_refuse_is_false_when_the_queue_is_acceptable():
    def ahead(px):
        return D("500") if D(str(px)) == D("0.48") else D("3.0")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead,
                                       queue_gate_binds=False))
    assert res["act"] is True
    assert res["queue_gate_would_refuse"] is False


def test_the_gate_binds_by_default_so_opting_out_is_explicit():
    def ahead(px):
        return D("12.0") if D(str(px)) == D("0.48") else D("120.01")

    res = TSP.completion_requote(**_kw(require_queue_evidence=True,
                                       queue_ahead_fn=ahead))
    assert res["act"] is False
    assert res["reason"] == "QUEUE_COST_EXCEEDS_GAIN"
