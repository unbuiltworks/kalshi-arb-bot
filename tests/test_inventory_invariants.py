"""INVENTORY CONSERVATION invariants for the replay engine.

These exist because a cost-accounting defect in `decide_completion`'s EXIT_NOW
branch silently corrupted every downstream economic figure: pair prices, the
basis gate, the completion budget and the flatten P&L. The defect was invisible
until pair forensics printed an impossible binary price of 5.65. Invariants are
now enforced in code and pinned here.
"""
from decimal import Decimal as D

import pytest

from src.l5_two_sided import ARM_REPLICATION, TwoSided


def eng(**kw):
    return TwoSided(arm=ARM_REPLICATION, clip=D(1), **kw)


def load(c, side, qty, px):
    c.qty[side] = D(str(qty))
    c.cost[side] = D(str(qty)) * D(str(px))


@pytest.mark.parametrize("held,gone", [(2, 1), (2, "1.5"), (3, 1), (10, 7),
                                       ("1.4200", "0.4200")])
def test_partial_disposal_preserves_the_basis_of_the_remainder(held, gone):
    c = eng()
    load(c, "up", held, "0.40")
    b = c.basis("up")
    c._dispose("up", D(str(gone)), b)
    assert c.basis("up") == b
    assert c.qty["up"] == D(str(held)) - D(str(gone))


def test_full_disposal_leaves_qty_and_cost_exactly_zero():
    c = eng()
    load(c, "up", 2, "0.40")
    c._dispose("up", D(2), c.basis("up"))
    assert c.qty["up"] == 0 and c.cost["up"] == 0


def test_cost_cannot_retain_disposed_inventory_across_a_reacquisition():
    """The actual corruption path: residue left by a full flatten inflated the
    next acquisition's basis to 1.30, impossible for a binary outcome."""
    c = eng()
    load(c, "up", 2, "0.40")
    c._dispose("up", D(2), c.basis("up"))
    c._book_fill("up", D(1), D("0.50"), maker=True, evidence="t")
    assert c.basis("up") == D("0.50")


def test_quantity_can_never_go_negative():
    c = eng()
    load(c, "up", 1, "0.40")
    with pytest.raises(AssertionError):
        c._dispose("up", D(2), c.basis("up"))


def test_remaining_basis_stays_inside_valid_binary_price_bounds():
    """Fees and flatten P&L are accounted on their own lines, so an inventory
    basis must always be a price in (0, 1)."""
    c = eng()
    load(c, "up", 3, "0.30")
    for gone in (D(1), D(1)):
        c._dispose("up", gone, c.basis("up"))
        if c.qty["up"] > 0:
            assert D(0) < c.basis("up") < D(1), c.basis("up")


def test_bank_pairs_cannot_manufacture_an_impossible_component_price():
    """_bank_pairs builds Pair from basis(), so a corrupted basis produced pair
    legs above $1. With the invariants held, both legs stay valid prices."""
    c = eng()
    c.now = 1.0
    c._book_fill("up", D(2), D("0.40"), maker=True, evidence="t")
    c._dispose("up", D(2), c.basis("up"))
    c._book_fill("up", D(1), D("0.45"), maker=True, evidence="t")
    c._book_fill("down", D(1), D("0.50"), maker=True, evidence="t")
    assert c.pairs, "a pair should have banked"
    for p in c.pairs:
        assert D(0) < p.up_px < D(1), p.up_px
        assert D(0) < p.down_px < D(1), p.down_px
        assert p.up_px + p.down_px < D(2)


def test_inventory_is_conserved_across_buy_match_and_exit():
    c = eng()
    c.now = 1.0
    c._book_fill("up", D(3), D("0.40"), maker=True, evidence="t")
    c._book_fill("down", D(1), D("0.55"), maker=True, evidence="t")
    assert c.matched() == D(1)
    bought_up, bought_dn = D(3), D(1)
    b = c.basis("up")
    c._dispose("up", D(1), b)
    assert c.qty["up"] == bought_up - D(1)
    assert c.qty["down"] == bought_dn
    assert c.basis("up") == b
    # cost equals the average basis times what is still held, on both sides
    assert c.cost["up"] == c.basis("up") * c.qty["up"]
    assert c.cost["down"] == c.basis("down") * c.qty["down"]


def test_disposing_nothing_is_a_no_op():
    c = eng()
    load(c, "up", 2, "0.40")
    before = (c.qty["up"], c.cost["up"])
    c._dispose("up", D(0), c.basis("up"))
    assert (c.qty["up"], c.cost["up"]) == before
