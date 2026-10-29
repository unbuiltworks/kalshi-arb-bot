"""The wrong-side residual defect: 1245z and 0130z, reproduced exactly.

WHAT WENT WRONG. The leg to offer next came only from the frozen pair plan,
cheapest leg first. That is right while flat and wrong the instant one leg has
filled alone. On 1245z the book was `up 1.4 / dn 2.8` and the engine spent the
last nine minutes of the window quoting DOWN -- the side it ALREADY HELD --
while the UP that would have completed the pair was never offered once. 0130z
shows the same shape against a DOWN 1.87 residual.

WHAT MUST NOT CHANGE, and each is a test here: a flat market's cheapest-first
ordering, 0145z (where the frozen side already matched the residual and 5m23s
of patience paid +$0.2028), and the quantity of an opening leg.

A NOTE ON WHAT THIS DOES NOT CLAIM. Both windows' correct side WAS quoted once,
inside the only interval where it priced under the 0.96 ceiling, and did not
fill. So this fix recovers no measured money. Its value is the window where the
book leaves and comes BACK -- there the old code would be pointing the wrong
way when the opportunity returned.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX


class _Store:
    """Only what probe_leg_order and probe_leg_qty_cap read."""

    def __init__(self, up=D(0), dn=D(0), outstanding=D(0), raises=False):
        self._up, self._dn, self._out, self._raises = up, dn, outstanding, raises

    def residual(self, market):
        if self._raises:
            raise RuntimeError("state unreadable")
        return self._up - self._dn

    def outstanding_residual(self, market):
        if self._raises:
            raise RuntimeError("state unreadable")
        return self._out


def _ex(store):
    ex = LX.FaithfulExecutor.__new__(LX.FaithfulExecutor)
    ex.store = store
    return ex


#: the pair plan both windows carried: a cheap leg and a dear one
LEGS = {"UP": {"side": "UP", "price": "0.18"},
        "DOWN": {"side": "DOWN", "price": "0.77"}}
CHEAPEST_FIRST = ["UP", "DOWN"]          # 0.18 before 0.77


# ------------------------------------------------- the two failing windows

def test_1245z_the_engine_must_offer_UP_not_the_DOWN_it_already_holds():
    """up 1.4 / dn 2.8 -> residual -1.4. The completing side is UP."""
    ex = _ex(_Store(up=D("1.4"), dn=D("2.8")))
    assert ex.store.residual("m") == D("-1.4")
    order = ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)
    assert order[0] == "UP", order


def test_0130z_the_same_shape_against_a_DOWN_1_87_residual():
    ex = _ex(_Store(up=D(0), dn=D("1.87")))
    assert ex.store.residual("m") == D("-1.87")
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)[0] == "UP"


def test_the_old_rule_would_have_chosen_the_wrong_side_in_both():
    """Proves the test is testing something: cheapest-first alone puts UP
    first here only by accident of price. Flip the prices so cheapest-first
    says DOWN, and the fix must still say UP."""
    legs = {"UP": {"side": "UP", "price": "0.77"},
            "DOWN": {"side": "DOWN", "price": "0.18"}}
    cheapest = ["DOWN", "UP"]
    ex = _ex(_Store(up=D("1.4"), dn=D("2.8")))
    assert cheapest[0] == "DOWN"                      # what the old rule did
    assert ex.probe_leg_order("m", legs, cheapest)[0] == "UP"


def test_an_UP_heavy_residual_asks_for_DOWN():
    ex = _ex(_Store(up=D("2.0"), dn=D("0.5")))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)[0] == "DOWN"


# ------------------------------------------- 0145z must be untouched

def test_0145z_is_unchanged_because_the_frozen_side_already_matched():
    """DOWN 1.56 filled first, residual -1.56, and the engine DID quote UP --
    which filled as a maker at own 0.52 after 5m23s for +$0.2028. The fix
    must produce the same side, so the profitable wait is not disturbed."""
    ex = _ex(_Store(up=D(0), dn=D("1.56")))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)[0] == "UP"
    # and it must not shrink that complement: 1.56 against 1.56 held
    assert ex.probe_leg_qty_cap("m", "UP", D("1.56")) == D("1.56")


def test_a_flat_market_keeps_cheapest_first_byte_for_byte():
    ex = _ex(_Store())
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST) == CHEAPEST_FIRST
    assert ex.probe_leg_order("m", LEGS, ["DOWN", "UP"]) == ["DOWN", "UP"]


# ------------------------------- partial fills, resting and pending orders

def test_a_resting_complement_is_counted_so_it_is_not_ordered_twice():
    """Residual -1.4 held, but an UP 1.4 is already resting: prospective is
    zero, so there is nothing to reduce and cheapest-first returns."""
    ex = _ex(_Store(up=D(0), dn=D("1.4"), outstanding=D("1.4")))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST) == CHEAPEST_FIRST


def test_a_partially_filled_complement_leaves_the_remainder_to_reduce():
    """0.5 of the UP filled and 0.9 still resting against DOWN 1.4: the
    prospective residual is zero, so no further complement is ordered."""
    ex = _ex(_Store(up=D("0.5"), dn=D("1.4"), outstanding=D("0.9")))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST) == CHEAPEST_FIRST


def test_an_under_sized_resting_complement_still_asks_for_UP():
    ex = _ex(_Store(up=D(0), dn=D("1.4"), outstanding=D("0.4")))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)[0] == "UP"


# ------------------------------ no reversal, no duplicate, no growth

def test_a_reducing_leg_is_capped_at_the_residual_it_reduces():
    """1.4 offered against 0.5 held would flip the book to 0.9 the other
    way -- an opening dressed as a completion."""
    ex = _ex(_Store(up=D(0), dn=D("0.5")))
    assert ex.probe_leg_qty_cap("m", "UP", D("1.4")) == D("0.5")


def test_an_opening_leg_is_never_capped():
    """DOWN against a DOWN-heavy book increases the imbalance, so it is an
    opening and its size is the sizing path's business, not this one's."""
    ex = _ex(_Store(up=D(0), dn=D("0.5")))
    assert ex.probe_leg_qty_cap("m", "DOWN", D("1.4")) == D("1.4")


def test_the_cap_only_ever_shrinks():
    ex = _ex(_Store(up=D(0), dn=D("9.0")))
    assert ex.probe_leg_qty_cap("m", "UP", D("1.4")) == D("1.4")


def test_a_flat_book_caps_nothing():
    ex = _ex(_Store())
    for side in ("UP", "DOWN"):
        assert ex.probe_leg_qty_cap("m", side, D("1.4")) == D("1.4")


@pytest.mark.parametrize("side", ["up", "Up", "UP", "down", "DOWN"])
def test_side_casing_does_not_defeat_the_cap(side):
    ex = _ex(_Store(up=D(0), dn=D("0.5")))
    got = ex.probe_leg_qty_cap("m", side, D("1.4"))
    assert got == (D("0.5") if side.upper() == "UP" else D("1.4"))


# ---------------------------------------------- it is a preference, not a gate

def test_an_unreadable_residual_keeps_todays_rule_rather_than_stopping():
    """This chooses between two already-gated legs. Failing it closed would
    stop the engine quoting at all, which is strictly worse than quoting the
    cheaper leg as it does today."""
    ex = _ex(_Store(raises=True))
    assert ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST) == CHEAPEST_FIRST
    assert ex.probe_leg_qty_cap("m", "UP", D("1.4")) == D("1.4")


def test_a_missing_leg_in_the_plan_keeps_todays_rule():
    ex = _ex(_Store(up=D(0), dn=D("1.4")))
    only_down = {"DOWN": {"side": "DOWN", "price": "0.77"}}
    assert ex.probe_leg_order("m", only_down, ["DOWN"]) == ["DOWN"]


def test_both_legs_are_always_returned_exactly_once():
    for up, dn in ((D(0), D(0)), (D("1.4"), D("2.8")), (D("2.0"), D("0.5"))):
        ex = _ex(_Store(up=up, dn=dn))
        got = ex.probe_leg_order("m", LEGS, CHEAPEST_FIRST)
        assert sorted(got) == ["DOWN", "UP"], got
        assert len(got) == 2


def test_the_call_sites_are_wired():
    """Structural: the ordering must be applied where the leg is chosen, and
    the cap where the quantity is taken."""
    import inspect
    src = inspect.getsource(LX.FaithfulExecutor.handle)
    at_order = src.index("probe_leg_order")
    at_nxt = src.index("nxt = next((k for k in order")
    assert at_order < at_nxt, "ordering must be applied before the choice"
    assert "probe_leg_qty_cap(market, nxt," in src
