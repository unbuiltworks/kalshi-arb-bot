"""Losses the venue's fills show and the account ledger lacks count
against the cumulative ceiling. Only ever downward."""
from decimal import Decimal as D

import pytest

from tests.test_l5_btc5m_lifetime_own_losses import (   # noqa: F401
    life_of, own, read)


def vb(adj, state="REPORT", **summary):
    s = dict(summary, downward_adjustment_usd=adj)
    return {"state": state, "applied": False, "summary": s}


def test_nothing_published_changes_nothing():
    r = read(own({}))
    assert r["ok"] and life_of(r) == D("-40")
    assert r["venue_basis_state"] == "NOT_PUBLISHED"
    assert D(r["venue_basis_losses_added_usd"]) == 0


def test_a_missing_loss_is_added_to_the_lifetime_figure():
    r = read(own({}), venue_basis=vb("-1.25"))
    assert life_of(r) == D("-41.25")
    assert D(r["venue_basis_losses_added_usd"]) == D("-1.25")
    assert r["venue_basis_state"] == "REPORT"


def test_it_can_bind_the_cumulative_ceiling():
    r = read(own({}), life="-74.50", venue_basis=vb("-0.60"))
    assert r["ok"] is False
    assert r["reason"] == "ACCOUNT_CUMULATIVE_LOSS_CEILING"


@pytest.mark.parametrize("adj", ["0", "0.00", "2.34", "100"])
def test_an_upward_figure_never_adds_room(adj):
    r = read(own({}), venue_basis=vb(adj))
    assert life_of(r) == D("-40")
    assert D(r["venue_basis_losses_added_usd"]) == 0


def test_the_pending_upward_total_is_never_read():
    r = read(own({}), venue_basis=vb(
        "0", upward_difference_pending_usd="97.31"))
    assert life_of(r) == D("-40")


@pytest.mark.parametrize("block", [
    {"state": "REPORT", "summary": None},
    {"state": "REPORT", "summary": {"downward_adjustment_usd": None}},
    {"state": "REPORT", "summary": {"downward_adjustment_usd": "n/a"}},
    {"state": "REPORT"}])
def test_a_report_with_no_readable_figure_adds_nothing_and_says_so(block):
    r = read(own({}), venue_basis=block)
    assert r["ok"] and life_of(r) == D("-40")
    assert r["venue_basis_state"] == "REPORT_NO_READABLE_FIGURE"


@pytest.mark.parametrize("state", ["OFF", "UNAVAILABLE", "APPLY", ""])
def test_only_a_report_is_read(state):
    r = read(own({}), venue_basis=vb("-5", state=state))
    assert life_of(r) == D("-40")
