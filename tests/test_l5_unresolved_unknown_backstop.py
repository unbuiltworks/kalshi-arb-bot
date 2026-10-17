"""The independent account-wide backstop. ADDITIVE, never a substitution.

WHY IT EXISTS, stated as the factual answer to the owner's question. An
L5-scoped realized ceiling measures only ATTESTED L5 settlements. On this
account that figure is 0 -- not because L5 broke even, but because nothing has
ever been attested -- while $294.09 of the account's realized total rests on
$1/share upper-bound placeholders whose cost AND ownership are unknown. So
rescoping the realized ceiling to the verified-L5 population WOULD permit
trading with material account-wide losses entirely unresolved. This bound is
what must hold alongside it.
"""
from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates


def not_the_backstop(r):
    """The pre-existing gates refuse this minimal order for their own
    reasons (WORST_CASE_DEFICIT_NO_INVENTORY). What these tests assert is
    whether the BACKSTOP fired, not whether everything else passed."""
    return r is None or not str(r).startswith("UNRESOLVED_UNKNOWN")


def gates(**over):
    kw = dict(max_cash_deployed_usd=D("1"), max_unmatched_contracts=D("5"),
              max_cumulative_loss_usd=D("20"),
              max_worst_case_deficit_usd=D("3.60"))
    kw.update(over)
    return ValidationGates(**kw)


def order(**over):
    kw = dict(order_qty=D("0.01"), order_price=D("0.59"),
              order_intent="UP", cumulative_realized=D("0"),
              cumulative_unrealized_loss=D("0"))
    kw.update(over)
    return kw


# ------------------------------------------------- it is purely additive

def test_unset_it_changes_nothing():
    """No bound configured means no new refusal. Existing behaviour stands."""
    g = gates()
    assert g.max_unresolved_unknown_usd is None
    assert not_the_backstop(g.refuse(**order()))
    # and it does not care what the unknown is
    assert not_the_backstop(
        g.refuse(**order(unresolved_unknown_usd=D("-218.99"))))


def test_a_resolved_book_passes():
    g = gates(max_unresolved_unknown_usd=D("1"))
    assert not_the_backstop(g.refuse(**order(unresolved_unknown_usd=D("0"))))


def test_material_unresolved_unknown_REFUSES():
    """The production figure. -218.99 against any sane bound."""
    g = gates(max_unresolved_unknown_usd=D("1"))
    r = g.refuse(**order(unresolved_unknown_usd=D("-218.99")))
    assert r is not None and r.startswith("UNRESOLVED_UNKNOWN_EXCEEDED")
    assert "218.99" in r


def test_it_can_only_ADD_a_refusal_never_remove_one():
    """Every check above it has already run and returned. A breach of the
    account-wide ceiling still wins, with the backstop satisfied."""
    g = gates(max_cumulative_loss_usd=D("20"),
              max_unresolved_unknown_usd=D("1000"))
    r = g.refuse(**order(cumulative_realized=D("-25"),
                         unresolved_unknown_usd=D("0")))
    assert r is not None and r.startswith("CUMULATIVE_LOSS_LIMIT")


def test_the_backstop_does_not_shadow_the_L5_ceilings():
    g = gates(max_l5_daily_loss_usd=D("5"),
              max_unresolved_unknown_usd=D("1000"))
    r = g.refuse(**order(l5_scoped_realized=D("-6"),
                         unresolved_unknown_usd=D("0")))
    assert r is not None and r.startswith("L5_DAILY_LOSS_LIMIT")


# --------------------------------------------------------- fail closed

@pytest.mark.parametrize("bad", [None])
def test_an_absent_figure_REFUSES_rather_than_reading_as_zero(bad):
    """The whole point is that the unknown is unknown. An unreadable figure
    is the MAXIMUM possible unknown, not the minimum."""
    g = gates(max_unresolved_unknown_usd=D("1"))
    r = g.refuse(**order(unresolved_unknown_usd=bad))
    assert r == "UNRESOLVED_UNKNOWN_UNAVAILABLE"


def test_a_non_finite_figure_REFUSES():
    g = gates(max_unresolved_unknown_usd=D("1"))
    for bad in (D("NaN"), D("Infinity"), D("-Infinity")):
        assert g.refuse(**order(unresolved_unknown_usd=bad)) == \
            "UNRESOLVED_UNKNOWN_UNAVAILABLE"


def test_an_unexplained_CREDIT_is_as_unreconciled_as_a_debit():
    """Magnitude, either sign. A book that gained money it cannot explain is
    not a reconciled book."""
    g = gates(max_unresolved_unknown_usd=D("1"))
    r = g.refuse(**order(unresolved_unknown_usd=D("+50")))
    assert r is not None and r.startswith("UNRESOLVED_UNKNOWN_EXCEEDED")


def test_the_bound_is_inclusive_at_its_own_value():
    g = gates(max_unresolved_unknown_usd=D("1"))
    assert not_the_backstop(
        g.refuse(**order(unresolved_unknown_usd=D("-1.00"))))
    assert str(g.refuse(**order(unresolved_unknown_usd=D("-1.01")))
               ).startswith("UNRESOLVED_UNKNOWN_EXCEEDED")


# ------------------------------------------- it is NOT the rejected change

def test_the_protected_call_sites_still_measure_the_whole_account():
    """This backstop is additive. It does not substitute the account-wide
    realized POPULATION, which is the change the classifier refused.

    The daily halt now reads `realized_current_utc_day()` -- the same
    account-wide population over one day instead of over all history, which
    is the window its own limit name specifies. Both cumulative gates still
    read the all-time account-wide total, so historical losses keep
    restraining and nothing is reset.
    """
    import inspect
    from src import l5_live_execution as E
    src = inspect.getsource(E)
    assert src.count("cumulative_realized=self.store.realized_today()") == 2
    assert src.count("realized_today=day_realized") == 1
    assert 'gate_input("verified_l5")' not in src


def test_the_backstop_runs_AFTER_the_account_wide_check_in_source_order():
    """Placement is what makes it additive rather than a replacement."""
    import inspect
    src = inspect.getsource(ValidationGates.refuse)
    assert src.index("CUMULATIVE_LOSS_LIMIT") < src.index(
        "UNRESOLVED_UNKNOWN_EXCEEDED")
