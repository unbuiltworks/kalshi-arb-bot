"""A leg that completes held inventory is priced against what is held.

Replayed over 187 recorded windows, the live configuration's matched pairs
averaged 1.017-1.023: the second leg was bought at its book bid after the
market had moved against the first. The pair ceiling bounds one round's two
quotes and never saw it.

THIS PINS A PRICING RULE. It does not say completing at the cap is
profitable, and it leaves the leg unmatched when no legal price completes
within the cap -- which the one-sided rule and the unmatched ceiling govern.
"""
import itertools
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

CAP = dict(straddle_max_completed_pair_cost=D("1.00"))


def price(r, side):
    got = legs(r, side)
    return None if not got else D(str(got[0]["price"]))


def test_the_defect_a_pair_locked_above_one_without_the_cap():
    #: UP bought at 0.60 as it fell; UP is now 0.50, so DOWN's bid is 0.49
    held = inv("0.08", "0.048")
    r = decide(held, "0.50", "0.51", t=40.0)
    assert price(r, C.DOWN) == D("0.49")              # 0.60 + 0.49 = 1.09


def test_the_completing_side_rests_at_the_price_that_keeps_the_pair_in_cap():
    held = inv("0.08", "0.048")
    r = decide(held, "0.50", "0.51", t=40.0, **CAP)
    assert price(r, C.DOWN) == D("0.40")              # 0.60 + 0.40 = 1.00
    plan = r["evaluation"]["pair_completion"]
    assert plan["action"] == "RESTS_AT_CAP"
    assert plan["held_side"] == C.UP and plan["completing_side"] == C.DOWN
    assert D(plan["held_avg"]) == D("0.6") and D(plan["book_bid"]) == D("0.49")


def test_when_the_book_already_completes_inside_the_cap_nothing_changes():
    #: UP bought at 0.45; UP is now 0.50 -> DOWN bid 0.49 -> pair 0.94
    held = inv("0.09", "0.0405")
    off = decide(held, "0.50", "0.51", t=40.0)
    on = decide(held, "0.50", "0.51", t=40.0, **CAP)
    assert legs(on) == legs(off)
    assert on["evaluation"]["pair_completion"]["action"] == "AT_BOOK"


def test_it_never_raises_a_price():
    for cap in ("0.90", "0.97", "1.00"):
        for b in ("0.30", "0.50", "0.70"):
            a = str(D(b) + D("0.01"))
            held = inv("0.08", "0.048")
            off = decide(held, b, a, t=40.0)
            on = decide(held, b, a, t=40.0,
                        straddle_max_completed_pair_cost=D(cap))
            for s in C.SIDES:
                if price(on, s) is not None:
                    assert price(off, s) is not None
                    assert price(on, s) <= price(off, s), (cap, b, s)


def test_only_the_completing_side_is_touched():
    held = inv("0.08", "0.048")
    off = decide(held, "0.50", "0.51", t=40.0)
    on = decide(held, "0.50", "0.51", t=40.0, **CAP)
    assert price(on, C.UP) == price(off, C.UP)


def test_no_legal_price_means_the_side_is_not_quoted():
    #: held at 0.995 average: completing within 1.00 needs 0.005 < one tick
    held = inv("0.08", "0.0796")
    r = decide(held, "0.50", "0.51", t=40.0, **CAP)
    assert legs(r, C.DOWN) == []
    assert r["evaluation"]["side_refusals"][C.DOWN] == "PAIR_COST_ABOVE_CAP"
    assert r["evaluation"]["pair_completion"]["action"] == "NOT_QUOTED"


def test_balanced_or_flat_inventory_is_not_affected():
    for held in (inv(), inv("0.08", "0.048", "0.08", "0.04")):
        off = decide(held, "0.50", "0.51", t=40.0)
        on = decide(held, "0.50", "0.51", t=40.0, **CAP)
        assert legs(on) == legs(off)
        assert on["evaluation"]["pair_completion"]["action"] is None


def test_a_filled_pair_can_never_cost_more_than_the_cap():
    """Whatever is held and wherever the book is, a completing fill at the
    quoted price locks a pair at or under the cap."""
    for q, c, b in itertools.product(
            ("0.05", "0.20"), ("0.20", "0.45", "0.60", "0.80", "0.95"),
            ("0.10", "0.35", "0.50", "0.65", "0.90")):
        held = inv(q, str(D(q) * D(c)))
        r = decide(held, b, str(D(b) + D("0.01")), t=40.0, **CAP)
        px = price(r, C.DOWN)
        if px is not None:
            assert D(c) + px <= D("1.00"), (c, b, px)


def test_with_the_one_sided_rule_a_blocked_partner_counts_as_closed():
    """If the completing side cannot be quoted, more of the held side is a
    lone bet and must stand on its own."""
    held = inv("0.02", "0.0199")
    r = decide(held, "0.50", "0.51", t=40.0, p_up="0.50",
               straddle_one_sided_control=True, **CAP)
    assert r["evaluation"]["side_refusals"][C.DOWN] == "PAIR_COST_ABOVE_CAP"
    v = r["evaluation"]["one_sided"][C.UP]
    assert v["partner_open"] is False and v["allowed"] is False
    assert legs(r) == []


def test_off_is_the_default_and_leaves_no_trace():
    assert C.Params().straddle_max_completed_pair_cost == 0
    r = decide(inv("0.08", "0.048"), "0.50", "0.51", t=40.0)
    assert r["evaluation"]["pair_completion"] == {"action": None}


@pytest.mark.parametrize("raw,want", [
    (None, "0"), ("", "0"), ("x", "0"), ("0.50", "0"), ("1.01", "0"),
    ("-1", "0"), ("1.00", "1.00"), ("0.97", "0.97"), ("1", "1")])
def test_the_switch_accepts_only_a_cost_inside_half_to_one(monkeypatch, raw,
                                                           want):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_MAX_COMPLETED_PAIR_COST_ENV,
                           raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_MAX_COMPLETED_PAIR_COST_ENV, raw)
    assert X.straddle_max_completed_pair_cost() == D(want)
