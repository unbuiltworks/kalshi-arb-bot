"""The first-leg floor must survive a completed pair.

The exemption exists for inventory that needs MANAGING. A matched position
needs no managing -- up == dn means there is nothing to rebalance -- so a
further opening must face the same floor as the first leg.
"""
import sys
from decimal import Decimal as D

sys.path.insert(0, "src")
import l5_two_sided_probe as TS

FLOOR = TS.MIN_FIRST_LEG_PRICE if hasattr(TS, "MIN_FIRST_LEG_PRICE") else None


def refusal(**kw):
    return TS.first_leg_entry_refusal(**kw)


def test_the_regression_2115z_would_now_be_refused():
    """2115z: paired 2.18 at 0.95, then opened DOWN at own 0.17.

    up == dn == 2.18, nothing to manage, 0.17 below the 0.20 floor.
    """
    r = refusal(side="DOWN", own_px=D("0.17"),
                legs_claimed={"UP": False, "DOWN": False},
                held_up=D("2.18"), held_down=D("2.18"))
    assert r is not None, "a post-pair opening below the floor must be refused"
    assert r["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"


def test_a_post_pair_opening_above_the_floor_is_still_allowed():
    """The fix must not stop continuous pairing, only cheap openings.

    2100z's second-round leg was own 0.26, above the 0.20 floor, and must
    still be permitted -- L5 continues after the first pair in 99.4% of
    windows and so should we.
    """
    assert refusal(side="DOWN", own_px=D("0.26"),
                   legs_claimed={"UP": False, "DOWN": False},
                   held_up=D("1.72"), held_down=D("1.72")) is None


def test_a_genuine_imbalance_is_never_refused():
    """Completion and inventory management must stay unconditional."""
    for up, dn in ((D("2.18"), D("0")), (D("0"), D("1.77")),
                   (D("3.86"), D("2.18")), (D("1"), D("5"))):
        assert refusal(side="UP", own_px=D("0.01"),
                       legs_claimed=None, held_up=up, held_down=dn) is None
        assert refusal(side="DOWN", own_px=D("0.01"),
                       legs_claimed=None, held_up=up, held_down=dn) is None


def test_flat_still_faces_the_floor():
    """The very first leg of a window: unchanged behaviour."""
    r = refusal(side="UP", own_px=D("0.05"), legs_claimed=None,
                held_up=D("0"), held_down=D("0"))
    assert r is not None and r["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"
    assert refusal(side="UP", own_px=D("0.42"), legs_claimed=None,
                   held_up=D("0"), held_down=D("0")) is None


def test_a_claimed_round_still_short_circuits_before_inventory():
    """A claimed leg means we are completing; that path is untouched."""
    assert refusal(side="DOWN", own_px=D("0.01"),
                   legs_claimed={"UP": True, "DOWN": False},
                   held_up=D("2.18"), held_down=D("2.18")) is None


def test_unreadable_inventory_still_permits_rather_than_strands():
    """Unreadable inventory is not this gate's to adjudicate."""
    assert refusal(side="UP", own_px=D("0.01"), legs_claimed=None,
                   held_up=None, held_down=D("1")) is None
    assert refusal(side="UP", own_px=D("0.01"), legs_claimed=None,
                   held_up=D("1"), held_down=None) is None


def test_every_paired_opening_in_the_record_would_still_be_allowed():
    """Scope check: refuses the one leg that stranded, none that paired.

    The six opening legs that went on to complete a pair filled at own
    0.21, 0.28, 0.29, 0.35, 0.37 and 0.42. None may be refused by this
    change, whatever inventory was held at the time.
    """
    for px in ("0.21", "0.28", "0.29", "0.35", "0.37", "0.42"):
        for up, dn in ((D("0"), D("0")), (D("2.18"), D("2.18"))):
            assert refusal(side="UP", own_px=D(px), legs_claimed=None,
                           held_up=up, held_down=dn) is None, px


def test_price_is_still_fail_closed():
    """An opening we cannot price is one we cannot judge."""
    for bad in ("nan", "abc", None):
        r = refusal(side="UP", own_px=bad, legs_claimed=None,
                    held_up=D("0"), held_down=D("0"))
        assert r is not None
        assert r["reason"] == "FIRST_LEG_PRICE_UNREADABLE"
