"""Three durable budgets, by what the order DOES -- not one flat total.

WHY THE FLAT TOTAL WAS WRONG. `L5_LIVE_AUTH_MAX_SUBMISSIONS=8` treats every
order as the same act. L5 works inventory sequentially -- open, extend,
wait, complete, keep working -- so a flat total is consumed by the orders
that TAKE ON risk and has nothing left for the orders that UNWIND it. The
budget that runs out first is the one that completes a pair, which leaves
unmatched exposure stranded and the exposure ceiling powerless to help.

So consumption is split three ways, durably:

    OPEN      increases |prospective unmatched| -- the scarce one
    COMPLETE  strictly reduces it -- looser, and separately gated
    REPLACE   a re-price of a leg already placed (attempt > 0)

and a cancellation is never budgeted at all, because a bound that could
refuse a cancel is a bound that traps an order on the venue.

Nothing here touches a venue.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX

EXP = "class-budget-test"
UP, DOWN = "UP", "DOWN"


@pytest.fixture
def led(tmp_path, monkeypatch):
    path = str(tmp_path / "auth.jsonl")
    monkeypatch.setenv(AUTH.LEDGER_ENV, path)
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, EXP)
    for env in AUTH.CLASS_BUDGET_ENV.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    return path


# ======================================================== reading the budgets


def test_with_NO_class_budget_configured_nothing_is_spent(led):
    assert AUTH.any_class_budget_configured() is False
    assert AUTH.class_claim_or_refuse(led, EXP, None) is None
    assert AUTH.class_spent(led, EXP, "OPEN") == []


def test_an_UNREADABLE_budget_is_ZERO_never_unlimited(led, monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "lots")
    assert AUTH.max_of_class("OPEN") == 0
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_class_ticket(led, EXP, "OPEN")
    assert "OPEN_BUDGET_EXHAUSTED" in str(e.value.args[0])


def test_an_UNSTATED_class_is_REFUSED_once_budgets_exist(led, monkeypatch):
    """An order nobody can charge is an unbudgeted order."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "3")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.class_claim_or_refuse(led, EXP, None)
    assert e.value.args[0] == "SUBMISSION_CLASS_NOT_STATED"


def test_an_UNKNOWN_class_always_raises_even_with_no_budgets(led):
    """'I was told nothing' and 'I was told something I cannot parse' must
    not collapse: the first is allowed when nothing is configured."""
    with pytest.raises(AUTH.Refused) as e:
        AUTH.normalize_class("HEDGE")
    assert e.value.args[0] == "SUBMISSION_CLASS_UNKNOWN"


# ========================================================== spending them


def test_each_class_has_its_OWN_budget_and_one_cannot_spend_another(
        led, monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "2")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "4")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "3")
    for _ in range(2):
        assert AUTH.claim_class_ticket(led, EXP, "OPEN")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_class_ticket(led, EXP, "OPEN")
    assert e.value.args[0] == "OPEN_BUDGET_EXHAUSTED"
    # THE POINT OF THE SPLIT: completing inventory is still permitted with
    # the exposure-increasing budget entirely spent.
    for _ in range(4):
        assert AUTH.claim_class_ticket(led, EXP, "COMPLETE")
    for _ in range(3):
        assert AUTH.claim_class_ticket(led, EXP, "REPLACE")
    rep = AUTH.class_budget_report(led, EXP)
    assert rep["OPEN"] == {"max": 2, "spent": 2, "remaining": 0}
    assert rep["COMPLETE"] == {"max": 4, "spent": 4, "remaining": 0}
    assert rep["REPLACE"] == {"max": 3, "spent": 3, "remaining": 0}


def test_tickets_are_DURABLE_so_a_restart_cannot_hand_one_back(
        led, monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "2")
    AUTH.claim_class_ticket(led, EXP, "OPEN")
    # a redeploy: nothing in memory survives, the volume does
    assert len(AUTH.class_spent(led, EXP, "OPEN")) == 1
    AUTH.claim_class_ticket(led, EXP, "OPEN")
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(led, EXP, "OPEN")


def test_a_ticket_is_NEVER_RETURNED_by_a_cancel_or_a_rejection(
        led, monkeypatch):
    """Identical semantics to every other claim here: an attempt the venue
    refused has still spent the budget."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    AUTH.claim_class_ticket(led, EXP, "OPEN")
    files = AUTH.class_spent(led, EXP, "OPEN")
    assert len(files) == 1
    with pytest.raises(AUTH.Refused):
        AUTH.claim_class_ticket(led, EXP, "OPEN")
    assert AUTH.class_spent(led, EXP, "OPEN") == files


def test_a_DIFFERENT_experiment_gets_its_own_budget(led, monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    AUTH.claim_class_ticket(led, EXP, "OPEN")
    assert AUTH.claim_class_ticket(led, "other-experiment", "OPEN")


# ==================================================== through the claim paths


def test_the_class_budget_binds_on_the_ACCUMULATION_path(led, monkeypatch):
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "2")
    AUTH.claim_accumulation_leg("mkt-a", 0, UP, {}, path=led, klass="OPEN")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("mkt-a", 1, UP, {}, path=led,
                                    klass="OPEN")
    assert e.value.args[0] == "OPEN_BUDGET_EXHAUSTED"
    # ...and the complement still goes, which is the whole point
    rec = AUTH.claim_accumulation_leg("mkt-a", 0, DOWN, {}, path=led,
                                      klass="COMPLETE")
    assert rec["klass"] == "COMPLETE"
    assert rec["class_ticket"]


def test_the_class_budget_binds_on_the_L5_FOLLOWING_path_TOO(led, monkeypatch):
    """A class bound that one strategy could sidestep is not a bound."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "0")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission("mkt-a", {}, path=led, klass="OPEN")
    assert e.value.args[0] == "OPEN_BUDGET_EXHAUSTED"


def test_an_exhausted_class_does_NOT_spend_the_legs_own_slot(led, monkeypatch):
    """Order of operations. If the class refuses, the (market, round, leg)
    slot must still be free, so the same intent can be placed later under a
    replenished budget instead of being lost twice over."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "0")
    with pytest.raises(AUTH.Refused):
        AUTH.claim_accumulation_leg("mkt-a", 0, UP, {}, path=led,
                                    klass="OPEN")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    rec = AUTH.claim_accumulation_leg("mkt-a", 0, UP, {}, path=led,
                                      klass="OPEN")
    assert rec["sid"] == AUTH.accumulation_sid(EXP, "mkt-a", 0, UP, 0)


def test_the_OVERALL_safety_bound_still_binds_across_all_classes(
        led, monkeypatch):
    """The three budgets are the operative limits; the flat total stays as a
    finite backstop below their sum, so no two classes can both run out."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "5")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "5")
    # 4, not 2: with a total of 2 the COMPLETION RESERVE refuses the very
    # first OPEN, which is correct but is a different property (it has its
    # own tests below). 4 leaves room for two non-reducing orders plus the
    # two-submission reserve.
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "4")
    AUTH.claim_accumulation_leg("mkt-a", 0, UP, {}, path=led, klass="OPEN")
    AUTH.claim_accumulation_leg("mkt-a", 1, UP, {}, path=led, klass="OPEN")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("mkt-a", 2, UP, {}, path=led,
                                    klass="OPEN")
    # the reserve speaks first here, and it is the stricter of the two
    assert e.value.args[0] in ("SUBMISSION_BUDGET_EXHAUSTED",
                               "NO_COMPLETION_CAPACITY_RESERVED")


# ======================================= classifying, and the safeguard


class _Store:
    def __init__(self, residual=D(0), outstanding=D(0)) -> None:
        self._r = residual
        self._o = outstanding

    def residual(self, market):
        return self._r

    def outstanding_residual(self, market):
        return self._o


class _Ex:
    prospective_residual = LX.FaithfulExecutor.prospective_residual
    submission_class = LX.FaithfulExecutor.submission_class
    balancing_refusal = LX.FaithfulExecutor.balancing_refusal

    def __init__(self, residual=D(0), outstanding=D(0)) -> None:
        self.store = _Store(residual, outstanding)


def _tr(qty="2.46"):
    return {"qty": D(qty), "price": D("0.90")}


def test_a_FIRST_LEG_is_OPEN(led):
    ex = _Ex()
    assert ex.submission_class("m", {"side": UP}, _tr()) == "OPEN"


def test_a_SAME_SIDE_EXTEND_is_OPEN(led):
    ex = _Ex(residual=D("2.46"))
    assert ex.submission_class("m", {"side": UP}, _tr()) == "OPEN"


def test_the_COMPLEMENT_of_a_long_UP_position_is_COMPLETE(led):
    ex = _Ex(residual=D("2.46"))
    assert ex.submission_class("m", {"side": DOWN}, _tr()) == "COMPLETE"


def test_a_leg_whose_COMPLEMENT_IS_ALREADY_RESTING_is_not_COMPLETE_again(led):
    """The prospective book, not the realized one. 1130z: four legs each
    calling itself the opening one filled inside seven seconds."""
    ex = _Ex(residual=D("2.46"), outstanding=D("-2.46"))
    # flat prospectively, so a further DOWN does not reduce -- it opens
    assert ex.submission_class("m", {"side": DOWN}, _tr()) == "OPEN"


def test_a_REPRICE_is_charged_to_REPLACE_whatever_its_direction(led):
    ex = _Ex(residual=D("2.46"))
    assert ex.submission_class("m", {"side": DOWN}, _tr(),
                               attempt=1) == "REPLACE"
    assert ex.submission_class("m", {"side": UP}, _tr(),
                               attempt=2) == "REPLACE"
    assert ex.submission_class("m", {"side": DOWN}, _tr(),
                               attempt=0) == "COMPLETE"


def test_an_UNREADABLE_attempt_is_charged_to_REPLACE(led):
    ex = _Ex(residual=D("2.46"))
    assert ex.submission_class("m", {"side": DOWN}, _tr(),
                               attempt="??") == "REPLACE"


def test_the_safeguard_PASSES_a_complement_inside_the_unmatched_need(led):
    ex = _Ex(residual=D("2.46"))
    assert ex.balancing_refusal("m", {"side": DOWN}, _tr("2.46"),
                                "COMPLETE") is None
    assert ex.balancing_refusal("m", {"side": DOWN}, _tr("1.00"),
                                "COMPLETE") is None


def test_the_safeguard_REFUSES_a_complement_that_OVERSHOOTS(led):
    """"Balancing" that retires one imbalance and opens an equal one on the
    other side leaves the account exactly as exposed, with another pair of
    fees paid for it."""
    ex = _Ex(residual=D("2.46"))
    bad = ex.balancing_refusal("m", {"side": DOWN}, _tr("5.00"), "COMPLETE")
    assert bad and bad["reason"] == "BALANCING_WOULD_OVERSHOOT"
    assert bad["max_qty"] == "2.46"


def test_the_safeguard_refuses_an_overshoot_from_a_SHORT_position_too(led):
    ex = _Ex(residual=D("-2.46"))
    bad = ex.balancing_refusal("m", {"side": UP}, _tr("5.00"), "COMPLETE")
    assert bad and bad["reason"] == "BALANCING_WOULD_OVERSHOOT"
    assert bad["max_qty"] == "2.46"


def test_the_safeguard_counts_OUTSTANDING_orders_as_filled(led):
    """"Cannot create excess inventory if all outstanding orders fill" is
    the condition, so the resting complement counts."""
    ex = _Ex(residual=D("2.46"), outstanding=D("-2.00"))
    # 0.46 of need left; a 2.46 complement would overshoot to -2.00
    bad = ex.balancing_refusal("m", {"side": DOWN}, _tr("2.46"), "COMPLETE")
    assert bad and bad["reason"] == "BALANCING_WOULD_OVERSHOOT"
    assert ex.balancing_refusal("m", {"side": DOWN}, _tr("0.46"),
                                "COMPLETE") is None


def test_the_safeguard_does_not_apply_to_an_OPEN_order(led):
    """OPEN is the scarce budget precisely because it MAY add exposure. The
    ceilings that bound it are the exposure cap and the residual cap."""
    ex = _Ex()
    assert ex.balancing_refusal("m", {"side": UP}, _tr(), "OPEN") is None


def test_an_order_charged_to_COMPLETE_that_does_not_reduce_is_refused(led):
    """Defence in depth: if the classifier and the safeguard ever disagree,
    the safeguard wins and nothing goes to the wire."""
    ex = _Ex()
    bad = ex.balancing_refusal("m", {"side": UP}, _tr(), "COMPLETE")
    assert bad and bad["reason"] == "BALANCING_DOES_NOT_REDUCE"


# ============ THE BACKSTOP MUST NOT STRAND WHAT THE OTHER CLASSES CREATED
#
# The hole in the first design. With OPEN 8, REPLACE 16 and a flat backstop
# of 28, OPEN and REPLACE together (24) can leave only 4 of the backstop for
# COMPLETE -- while 8 opened legs can need up to 16 completion submissions.
# The account would then hold unmatched inventory with no submission left to
# close it, which is the one outcome every limit here exists to prevent.
#
# Two repairs, and both are needed: the backstop does not bind a
# risk-reducing order at all, and an OPEN is refused before the exposure
# exists if the completion capacity to close it is not reserved.


def test_the_flat_backstop_DOES_NOT_BIND_a_risk_reducing_order(
        led, monkeypatch):
    """The core repair. A global count that can refuse an order which
    provably reduces |unmatched| protects nothing and can only strand
    risk."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "4")
    AUTH.claim_accumulation_leg("m", 0, UP, {}, path=led, klass="OPEN")
    AUTH.claim_accumulation_leg("m", 1, UP, {}, path=led, klass="OPEN")
    # the flat total is now spent for anything that ADDS exposure
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 2, UP, {}, path=led, klass="OPEN")
    assert e.value.args[0] in ("SUBMISSION_BUDGET_EXHAUSTED",
                               "NO_COMPLETION_CAPACITY_RESERVED")
    # ...and the complement STILL GOES, because refusing it could only
    # strand the inventory the two OPEN legs just created
    rec = AUTH.claim_accumulation_leg("m", 0, DOWN, {}, path=led,
                                      klass="COMPLETE", reducing=True)
    assert rec["reducing"] is True
    assert rec["class_ticket"]


def test_a_REPRICED_complement_is_also_exempt_from_the_backstop(
        led, monkeypatch):
    """A re-price is CHARGED to REPLACE -- the split the owner approved --
    but its EFFECT is still risk-reducing. With one live-order slot the
    complement is always placed against a book that has moved, so repricing
    it is how a complement gets placed at all: a backstop that refused it
    would strand inventory just as surely."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "16")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "0")
    rec = AUTH.claim_accumulation_leg("m", 0, DOWN, {}, path=led, attempt=1,
                                      klass="REPLACE", reducing=True)
    assert rec["klass"] == "REPLACE"
    assert rec["reducing"] is True


def test_a_REPRICED_OPEN_leg_is_NOT_exempt(led, monkeypatch):
    """The exemption is on the EFFECT, not on the class. Repricing a leg
    that still adds exposure is exactly what the backstop is for."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "16")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "0")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 0, UP, {}, path=led, attempt=1,
                                    klass="REPLACE", reducing=False)
    assert e.value.args[0] == "SUBMISSION_BUDGET_EXHAUSTED"


def test_an_OPEN_is_REFUSED_when_the_COMPLETE_budget_could_not_close_it(
        led, monkeypatch):
    """Do not open what you cannot close. Reserving after the fact is
    impossible: once the budget is spent the inventory already exists."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "3")
    # spend the completion budget down to one, below the two-submission
    # reserve one obligation can need
    for r in range(2):
        AUTH.claim_accumulation_leg("m", r, DOWN, {}, path=led,
                                    klass="COMPLETE", reducing=True)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 5, UP, {}, path=led, klass="OPEN")
    assert e.value.args[0] == "NO_COMPLETION_CAPACITY_RESERVED"
    assert e.value.detail["complete_remaining"] == 1
    assert e.value.detail["reserve_required"] == 2


def test_the_reserve_is_checked_against_the_FLAT_TOTAL_too(led, monkeypatch):
    """A reserve only one of the two bounds respects is not a reserve."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "4")
    # two OPEN legs spend two of the four
    AUTH.claim_accumulation_leg("m", 0, UP, {}, path=led, klass="OPEN")
    AUTH.claim_accumulation_leg("m", 1, UP, {}, path=led, klass="OPEN")
    # a third would leave 1 < the 2 an obligation can need
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 2, UP, {}, path=led, klass="OPEN")
    assert e.value.args[0] == "NO_COMPLETION_CAPACITY_RESERVED"
    assert e.value.detail["total_spent"] == 2


def test_the_APPROVED_FIGURES_cannot_strand_inventory(led, monkeypatch):
    """The approved campaign, end to end: OPEN 8, COMPLETE 12, REPLACE 16,
    backstop 28. Spend OPEN and REPLACE to their ceilings and the
    completion budget must still be fully available."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["REPLACE"], "16")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "28")
    opens = 0
    for r in range(8):
        try:
            AUTH.claim_accumulation_leg("m", r, UP, {}, path=led,
                                        klass="OPEN")
            opens += 1
        except AUTH.Refused:
            break
    reps = 0
    for r in range(16):
        try:
            AUTH.claim_accumulation_leg("m2", r, UP, {}, path=led,
                                        attempt=1, klass="REPLACE",
                                        reducing=False)
            reps += 1
        except AUTH.Refused:
            break
    # OPEN and REPLACE are now spent against the flat backstop...
    with pytest.raises(AUTH.Refused):
        AUTH.claim_accumulation_leg("m3", 0, UP, {}, path=led, klass="OPEN")
    # ...and EVERY ONE of the twelve completion submissions is still
    # available, which is the property that cannot be allowed to fail.
    for r in range(12):
        AUTH.claim_accumulation_leg("m4", r, DOWN, {}, path=led,
                                    klass="COMPLETE", reducing=True)
    rep = AUTH.class_budget_report(led, EXP)
    assert rep["COMPLETE"]["spent"] == 12
    assert opens + reps <= 28
    assert opens >= 1 and reps >= 1


def test_an_UNREADABLE_reserve_refuses_the_OPEN(led, monkeypatch):
    """A reserve we cannot count is not a reserve we may assume exists."""
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "8")
    monkeypatch.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "12")
    import src.l5_live_authorization as A

    def boom(*a, **kw):
        raise OSError("volume gone")

    monkeypatch.setattr(A, "class_spent", boom)
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("m", 0, UP, {}, path=led, klass="OPEN")
    assert e.value.args[0] == "COMPLETION_CAPACITY_UNREADABLE"
