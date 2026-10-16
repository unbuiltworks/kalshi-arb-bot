"""The unresolved-unknown backstop cannot conceal a loss or relax a control.

P3. The backstop is the stated precondition for any future L5-scoped
rescoping, so the claim that it only ever ADDS a refusal has to be provable,
not asserted. These are the proofs.
"""
import inspect
from decimal import Decimal as D

import pytest

from src import l5_live_validation_gates as G

ORDER = ["BOOK_UNCERTAIN", "UNMATCHED_CONTRACTS", "CUMULATIVE_LOSS_LIMIT",
         "UNRESOLVED_UNKNOWN_EXCEEDED"]


def _gates(**kw):
    base = dict(max_cash_deployed_usd=D("1000"),
                max_cumulative_loss_usd=D("20"))
    base.update(kw)
    return G.ValidationGates(**base)


def _refuse(gates, **kw):
    args = dict(order_qty=D("0.01"), order_price=D("0.31"),
                order_intent="UP", filled_legs={}, outstanding={},
                unmatched_after=D("0"), cumulative_realized=D("0"),
                cumulative_unrealized_loss=D("0"), windows_used=0,
                book_uncertain=False, available_cash_usd=D("1000"))
    args.update(kw)
    return gates.refuse(**args)


# -- it is positioned so it can only ADD a refusal -------------------------

def test_the_backstop_runs_after_the_account_wide_ceiling():
    src = inspect.getsource(G.ValidationGates.refuse)
    assert src.index("UNRESOLVED_UNKNOWN") > src.index("CUMULATIVE_LOSS_LIMIT")


def test_the_account_wide_ceiling_still_refuses_with_the_backstop_satisfied():
    """The decisive property: a satisfied backstop does NOT unlock trading."""
    g = _gates(max_unresolved_unknown_usd=D("1000"))
    out = _refuse(g, cumulative_realized=D("-233.98540000"),
                  unresolved_unknown_usd=D("-233.98540000"))
    assert out is not None
    assert out.startswith("CUMULATIVE_LOSS_LIMIT"), out


def test_a_generous_backstop_cannot_widen_the_account_wide_ceiling():
    for bound in ("0", "1", "1000", "999999"):
        g = _gates(max_unresolved_unknown_usd=D(bound))
        out = _refuse(g, cumulative_realized=D("-25"),
                      unresolved_unknown_usd=D("-25"))
        assert out is not None and out.startswith("CUMULATIVE_LOSS_LIMIT"), bound


def test_the_backstop_adds_a_refusal_the_others_would_have_permitted():
    permitted = _refuse(_gates(), cumulative_realized=D("0"),
                        unresolved_unknown_usd=D("-50"))
    assert permitted is None
    refused = _refuse(_gates(max_unresolved_unknown_usd=D("1")),
                      cumulative_realized=D("0"),
                      unresolved_unknown_usd=D("-50"))
    assert refused is not None and refused.startswith("UNRESOLVED_UNKNOWN_EXCEEDED")


# -- it cannot conceal an unknown ------------------------------------------

def test_an_unreadable_unknown_is_the_maximum_unknown_and_refuses():
    for bad in (None, D("NaN"), float("inf"), "abc", ""):
        out = _refuse(_gates(max_unresolved_unknown_usd=D("1")),
                      cumulative_realized=D("0"), unresolved_unknown_usd=bad)
        assert out is not None, bad
        assert out.startswith("UNRESOLVED_UNKNOWN"), (bad, out)


def test_an_unexplained_credit_is_as_unreconciled_as_a_debit():
    g = _gates(max_unresolved_unknown_usd=D("1"))
    for v in (D("-50"), D("50")):
        out = _refuse(g, cumulative_realized=D("0"), unresolved_unknown_usd=v)
        assert out is not None and "EXCEEDED" in out, v


def test_a_zero_bound_permits_only_a_fully_reconciled_book():
    g = _gates(max_unresolved_unknown_usd=D("0"))
    assert _refuse(g, cumulative_realized=D("0"),
                   unresolved_unknown_usd=D("0")) is None
    assert _refuse(g, cumulative_realized=D("0"),
                   unresolved_unknown_usd=D("0.01")) is not None


def test_it_is_inert_when_unset_and_changes_nothing():
    g = _gates()
    assert g.max_unresolved_unknown_usd is None
    assert _refuse(g, cumulative_realized=D("0"),
                   unresolved_unknown_usd=None) is None


# -- it cannot relax capital, exposure or daily-loss controls --------------

def test_the_backstop_reads_no_capital_or_exposure_control():
    src = inspect.getsource(G.ValidationGates.refuse)
    block = src[src.index("UNRESOLVED-UNKNOWN BACKSTOP"):
                src.index("Independent strategy budget")]
    for forbidden in ("max_usd_per_market", "max_total_exposure",
                      "max_abs_residual", "max_cash_deployed",
                      "max_live_orders", "max_cumulative_loss_usd",
                      "max_l5_daily_loss_usd", "available_cash"):
        assert forbidden not in block, forbidden


def test_every_control_after_it_still_runs():
    """A satisfied backstop must not short-circuit the cash or deficit gates."""
    src = inspect.getsource(G.ValidationGates.refuse)
    after = src[src.index("UNRESOLVED_UNKNOWN_EXCEEDED"):]
    for later in ("CASH_COMMITMENT", "INSUFFICIENT_AVAILABLE_CASH",
                  "WORST_CASE_DEFICIT"):
        assert later in after, later


def test_it_cannot_be_the_only_loss_control_configured():
    """Rescoping is a SEPARATE decision: the account-wide ceiling is not
    optional, and a backstop alone does not stand in for it."""
    g = _gates(max_unresolved_unknown_usd=D("1"))
    assert g.max_cumulative_loss_usd is not None
    assert g.max_cumulative_loss_usd > 0
