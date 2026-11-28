"""Independent regression checks for L5 partial recovery, issue #195.

No venue/network access. Uses the real runner harness from the existing tests.
These are acceptance checks, not a claim of profitable execution.
"""
from decimal import Decimal as D

import tests.test_l5_partial_completion_basis as PC
import tests.test_l5_ladder_runner as LAD
import tests.test_l5_continuous_config as CC


def test_partial_recovery_requotes_never_exceed_per_contract_hard_ceiling(
        tmp_path, monkeypatch):
    """The 04:00 multi-clip failure: cost of all DOWN clips cannot be
    charged against the quantity of one replacement UP order.
    """
    h, off, sent_before = PC.stranded_three(
        tmp_path, monkeypatch, on=True, rest=True,
        away=("0.55", "0.56"))
    assert h.pos("DOWN") > D("1")
    assert h.pos("UP") == D("0")
    assert len(h.sent()) > sent_before
    held_unit_basis = PC.avg_down(h)
    assert held_unit_basis > D("0")
    for side, price, quantity in h.sent()[sent_before:]:
        assert side == "UP"
        assert D("0") < quantity <= h.pos("DOWN")
        assert held_unit_basis + price <= D("1.00")
    assert h.r.store.orders_uncertain() is None
    assert CC.worst_loss(h) <= CC.DEFICIT + CC.CENT


def test_partial_recovery_does_not_cross_the_ceiling_when_book_runs_away(
        tmp_path, monkeypatch):
    """A stale or moving book is not permission to submit an uneconomic
    complement. Repricing must preserve hard economic and risk limits.
    """
    h, off, sent_before = PC.stranded_three(
        tmp_path, monkeypatch, on=True, rest=True,
        away=("0.88", "0.89"))
    assert h.pos("UP") == D("0")
    basis = PC.avg_down(h)
    for side, price, quantity in h.sent()[sent_before:]:
        assert side == "UP"
        assert basis + price <= D("1.00")
        assert quantity <= h.pos("DOWN")
    assert h.r.store.orders_uncertain() is None
    assert CC.worst_loss(h) <= CC.DEFICIT + CC.CENT
