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
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "2")
    AUTH.claim_accumulation_leg("mkt-a", 0, UP, {}, path=led, klass="OPEN")
    AUTH.claim_accumulation_leg("mkt-a", 0, DOWN, {}, path=led,
                               klass="COMPLETE")
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg("mkt-a", 1, UP, {}, path=led,
                                    klass="OPEN")
    assert e.value.args[0] == "SUBMISSION_BUDGET_EXHAUSTED"


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
