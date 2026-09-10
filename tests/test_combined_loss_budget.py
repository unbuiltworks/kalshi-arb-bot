"""One loss budget for settled AND guaranteed-but-unsettled losses.

OWNER 2026-09-10: "I don't want settled losses and unsettled guaranteed losses
independently allowed to reach -$15 each." The -$15.00 daily stop read SETTLED
realized only, so on 2026-09-10 it could not see a position whose worst branch
was already -$5.53 and whose best branch was -$1.25.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import pmus_spring_l5_session as ses


def _inv(up, dn, cost_up, cost_dn):
    i = ses.Inventory()
    i.up, i.dn = D(up), D(dn)
    i.cost_up, i.cost_dn = D(cost_up), D(cost_dn)
    return i


# the real position at the halt
MIL = _inv("20.34", "16.06", "7.3464", "14.2475")


def test_the_live_floor_reproduces_from_the_real_numbers():
    """min(20.34, 16.06) - 21.5939 = -5.5339. Knowable before settlement."""
    assert ses.guaranteed_settlement_floor(MIL).quantize(D("0.01")) == D("-5.53")


def test_the_floor_uses_no_price_and_no_book():
    """The property that lets it sit in a risk stop at all."""
    import ast, inspect
    src = inspect.getsource(ses.guaranteed_settlement_floor)
    tree = ast.parse(src.lstrip())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for banned in ("bid", "ask", "touch", "mark", "bbo", "px"):
        assert banned not in names, f"floor must not read {banned}"


def test_a_winning_open_position_cannot_subsidise_a_losing_one():
    """min(0, floor) per market. Otherwise a paper winner masks a real loser."""
    loser = _inv("10", "5", "4.00", "4.50")        # floor 5 - 8.50 = -3.50
    winner = _inv("10", "10", "2.00", "2.00")      # floor 10 - 4.00 = +6.00
    assert ses.guaranteed_settlement_floor(loser) == D("-3.50")
    assert ses.guaranteed_settlement_floor(winner) == D("6.00")
    assert ses.unsettled_floor_total([loser, winner]) == D("-3.50")


def test_multiple_held_markets_sum_even_though_concurrency_is_one():
    """Concurrency is 1 today. The budget must not silently assume it."""
    a = _inv("10", "5", "4.00", "4.50")            # -3.50
    b = _inv("8", "3", "3.00", "2.80")             # 3 - 5.80 = -2.80
    c = _inv("6", "6", "1.00", "1.00")             # +4.00 -> clamped to 0
    assert ses.unsettled_floor_total([a, b, c]) == D("-6.30")


def test_combined_is_realized_plus_the_negative_floors():
    assert ses.combined_loss(D("-2.00"), [MIL]).quantize(D("0.01")) == D("-7.53")


# ------------------------------------------------ the -15.00 boundary, exactly

def test_exactly_minus_fifteen_has_fired_not_one_cent_later():
    inv = _inv("10", "4", "4.00", "6.00")          # floor 4 - 10 = -6.00
    assert ses.guaranteed_settlement_floor(inv) == D("-6.00")
    assert ses.combined_budget_tripped(D("-9.00"), [inv], budget=D("-15.00")) is True
    assert ses.combined_budget_tripped(D("-8.99"), [inv], budget=D("-15.00")) is False


def test_the_case_the_owner_named_neither_stop_alone_would_catch():
    """-12 realized plus -10 locked: neither individually at -15, together -22."""
    inv = _inv("10", "0", "10.00", "0.00")         # floor 0 - 10 = -10.00
    assert ses.combined_budget_tripped(D("-12.00"), [inv], budget=D("-15.00")) is True
    # and each alone would NOT have fired
    assert D("-12.00") > D("-15.00")
    assert ses.guaranteed_settlement_floor(inv) > D("-15.00")


# ------------------------------------------------- no double counting on settle

def test_settlement_moves_a_market_from_floor_to_realized_without_double_count():
    """The transition is structural: a settled market leaves HELD and its
    result enters realized_today. Nothing is ever in both."""
    inv = _inv("10", "4", "4.00", "6.00")          # floor -6.00
    before = ses.combined_loss(D("-2.00"), [inv])
    assert before == D("-8.00")
    # it settles on the DN branch for exactly its floor: realized -2 + -6
    after = ses.combined_loss(D("-8.00"), [])      # no longer held
    assert after == D("-8.00")
    assert after == before, "the total must not move across the transition"


def test_double_counting_would_be_visible_if_held_were_not_cleared():
    """The failure mode this shape avoids, asserted so a future refactor that
    forgets to drop the settled market fails loudly."""
    inv = _inv("10", "4", "4.00", "6.00")
    wrong = ses.combined_loss(D("-8.00"), [inv])   # settled AND still held
    assert wrong == D("-14.00") != D("-8.00")


# ------------------------------------ what may still run once the budget trips

def test_an_economic_complete_still_raises_the_floor_and_is_permitted():
    """Not an empty permitted set: the lane can complete its way out."""
    inv = _inv("10", "4", "4.00", "3.00")          # floor 4 - 7 = -3.00
    assert ses.reduces_risk_only(inv, side_up=False, px=D("0.50"), qty=D("6")) is True
    assert ses.floor_after_fill(inv, False, D("0.50"), D("6")) == D("0.00")


def test_a_new_first_leg_lowers_the_floor_and_is_refused():
    inv = _inv("10", "4", "4.00", "3.00")
    assert ses.reduces_risk_only(inv, side_up=True, px=D("0.40"), qty=D("5")) is False


def test_overshooting_past_parity_is_caught_by_arithmetic_not_by_reasoning():
    """Completing the deficient side raises the floor only until parity. Past
    it, extra contracts are just cash out with no floor benefit."""
    inv = _inv("10", "4", "4.00", "3.00")
    assert ses.reduces_risk_only(inv, False, D("0.50"), D("6")) is True     # to parity
    assert ses.reduces_risk_only(inv, False, D("0.50"), D("20")) is False   # past it


def test_the_live_pair_that_caused_this_would_not_have_been_permitted():
    """0.3612 first leg, complement at 0.880. Under the <=1.00 ceiling the
    price is refused upstream; here we show it also fails the floor test at
    the size that actually filled once parity is passed."""
    inv = _inv("20.34", "16.06", "7.3464", "14.2475")
    assert ses.reduces_risk_only(inv, False, D("0.880"), D("20")) is False
