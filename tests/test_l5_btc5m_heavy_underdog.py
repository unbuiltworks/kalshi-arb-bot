"""The side that is both heavier and losing is not replenished.

Pins a MECHANISM. The behaviour comes from a trader's recorded fills
(analysis/l5/bonereaper_recent/INVENTORY_TRAJECTORY_RESULT.txt); it is not
shown to be an edge, and the switch is off by default.
"""
import itertools
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

ON = dict(straddle_stop_adding_heavy_underdog=True)
#: long UP 0.20 shares bought at 0.60, DOWN 0.10 at 0.38
UP_HEAVY = inv("0.20", "0.12", "0.10", "0.038")
DOWN_HEAVY = inv("0.10", "0.038", "0.20", "0.12")


def sides(r):
    return sorted(o["side"] for o in legs(r))


def cancels(r):
    return sorted(o["id"] for o in r["orders"] if o.get("op") == "CANCEL")


def leg(i, side, price, age=5.0, reason="STRADDLE_LEG"):
    return {"id": i, "side": side, "price": price, "qty": "0.10",
            "age_s": age, "reason": reason}


# ------------------------------------------------------------ off = as was

@pytest.mark.parametrize("inventory,bid", itertools.product(
    (inv(), UP_HEAVY, DOWN_HEAVY), ("0.30", "0.50", "0.70")))
def test_off_by_default_every_decision_is_unchanged(inventory, bid):
    ask = str(D(bid) + D("0.01"))
    rest = [leg("a", C.UP, bid), leg("b", C.DOWN, str(1 - D(ask)))]
    a = decide(inventory, bid, ask, resting=rest)
    b = decide(inventory, bid, ask, resting=rest,
               straddle_stop_adding_heavy_underdog=False)
    assert a["orders"] == b["orders"] and a["action"] == b["action"]
    assert set(a["evaluation"]["heavy_underdog"].values()) == {False}


# ------------------------------------------------------------------- on

def test_heavy_side_that_became_the_underdog_is_not_quoted():
    off = decide(UP_HEAVY, "0.35", "0.36")
    on = decide(UP_HEAVY, "0.35", "0.36", **ON)
    assert "UP" not in sides(on)
    assert on["evaluation"]["side_refusals"]["UP"] == "HEAVY_SIDE_IS_UNDERDOG"
    assert on["evaluation"]["heavy_underdog"] == {"UP": True, "DOWN": False}
    #: the other side is decided exactly as before
    assert legs(on, C.DOWN) == legs(off, C.DOWN)


def test_it_is_symmetric():
    on = decide(DOWN_HEAVY, "0.65", "0.66", **ON)
    assert "DOWN" not in sides(on)
    assert on["evaluation"]["heavy_underdog"] == {"UP": False, "DOWN": True}


def test_heavy_side_that_is_still_the_favourite_is_unchanged():
    assert decide(UP_HEAVY, "0.65", "0.66", **ON)["orders"] == \
        decide(UP_HEAVY, "0.65", "0.66")["orders"]


def test_a_balanced_book_is_unchanged():
    flat = inv("0.10", "0.05", "0.10", "0.05")
    for bid in ("0.30", "0.70"):
        ask = str(D(bid) + D("0.01"))
        assert decide(flat, bid, ask, **ON)["orders"] == \
            decide(flat, bid, ask)["orders"]


def test_an_empty_book_is_unchanged():
    assert decide(inv(), "0.30", "0.31", t=20.0, **ON)["orders"] == \
        decide(inv(), "0.30", "0.31", t=20.0)["orders"]


def test_its_resting_legs_are_cancelled_by_id_and_only_those():
    rest = [leg("u1", C.UP, "0.35"), leg("u2", C.UP, "0.34"),
            leg("d1", C.DOWN, "0.64"),
            leg("c1", C.UP, "0.33", reason="COMPLETION")]
    on = decide(UP_HEAVY, "0.35", "0.36", resting=rest, **ON)
    got = cancels(on)
    assert "u1" in got and "u2" in got
    assert "c1" not in got                 # not a two-sided leg: not ours
    off = decide(UP_HEAVY, "0.35", "0.36", resting=rest)
    assert ("d1" in got) == ("d1" in cancels(off))   # other side as before
    assert len(got) == len(set(got))


def test_it_places_nothing_it_would_not_have_placed():
    for inventory, bid in itertools.product(
            (UP_HEAVY, DOWN_HEAVY), ("0.20", "0.35", "0.50", "0.65", "0.80")):
        ask = str(D(bid) + D("0.01"))
        off = [(o["side"], o["price"], o["qty"])
               for o in legs(decide(inventory, bid, ask))]
        on = [(o["side"], o["price"], o["qty"])
              for o in legs(decide(inventory, bid, ask, **ON))]
        assert set(on) <= set(off)


def test_quoting_resumes_when_the_side_is_no_longer_heavier():
    even = inv("0.20", "0.12", "0.20", "0.10")
    assert decide(even, "0.35", "0.36", **ON)["orders"] == \
        decide(even, "0.35", "0.36")["orders"]


def test_the_margin_delays_the_stop():
    #: UP bid 0.48, DOWN bid 0.51: 3 c below
    m = dict(straddle_heavy_underdog_margin=D("0.05"), **ON)
    assert decide(UP_HEAVY, "0.48", "0.49", **m)["evaluation"][
        "heavy_underdog"]["UP"] is False
    assert decide(UP_HEAVY, "0.48", "0.49", **ON)["evaluation"][
        "heavy_underdog"]["UP"] is True
    assert decide(UP_HEAVY, "0.40", "0.41", **m)["evaluation"][
        "heavy_underdog"]["UP"] is True


# --------------------------------------------------------- the env switch

@pytest.mark.parametrize("raw,expect", [
    (None, False), ("", False), ("1", False), ("true", False),
    ("stop_adding", False), ("STOP_ADDING", True), (" STOP_ADDING ", True)])
def test_only_the_exact_token_enables_it(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_HEAVY_UNDERDOG_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_HEAVY_UNDERDOG_ENV, raw)
    assert X.straddle_stop_adding_heavy_underdog() is expect


@pytest.mark.parametrize("raw,expect", [
    (None, D("0")), ("abc", D("0")), ("-0.01", D("0")), ("0.50", D("0")),
    ("0.05", D("0.05")), ("0", D("0"))])
def test_the_margin_is_read_only_inside_its_range(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_HEAVY_UNDERDOG_MARGIN_ENV,
                           raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_HEAVY_UNDERDOG_MARGIN_ENV, raw)
    assert X.straddle_heavy_underdog_margin() == expect
