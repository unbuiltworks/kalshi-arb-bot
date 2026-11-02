"""The completion basis averaged a MATCHED pair into a stranded leg.

THE INCIDENT, 2026-11-02 window 1400z, from the decision ledger.

At 14:09 the book held DOWN 4.00 and UP 2.23. That DOWN 4.00 is two
different things: 2.23 already MATCHED at own 0.65, plus 1.77 STRANDED at
own 0.25. `completion_requote` priced the completion off the whole side:

    ch_total / h = (2.23*0.65 + 1.77*0.25) / 4.00 = 0.4730
    redeem_cap   = 1 - 0.4730                     = 0.5270

UP was executable at own 0.60 against 1377 contracts of displayed depth, so
the engine refused:

    L5_MISSED_PAIR  reason PAIR_ABOVE_REDEMPTION
                    pair_own_cost 1.3030   gain_per_contract -0.3030
                    held_side DOWN  held_qty 4.00  need 1.77

THE TRUE ECONOMICS OF THAT ORDER. Buying UP 1.77 at own 0.60 (fee 0.03)
takes the book to UP 4.00 / DOWN 4.00 for 3.7122:

    before  pnl_if_up -0.4202   pnl_if_down +1.3498   worst -0.4202
    after   pnl_if_up +0.2578   pnl_if_down +0.2578   worst +0.2578

BOTH outcomes positive, worst case improved by +0.6780. It was a PROFIT
LOCK that the engine scored as a 1.30 pair and declined. The matched 2.23
is already settled economics; averaging its 0.65 into the basis of a leg
bought at 0.25 is simply the wrong denominator.

THE BOUND, which needs no lot tracking. Both outcomes stay >= 0 iff
    min(h, d + q)  >=  ch + cd + q*p + fee
        =>  p <= [min(h, d + q) - ch - cd - fee] / q
STRICTLY STRONGER than redeem_cap: that one promises the MATCHED portion
never settles at a loss, this promises the WHOLE BOOK never does.

DEFAULT OFF, so the deployed engine is byte-for-byte unchanged.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_portfolio_payoff as PP
from src import l5_two_sided_probe as TS

# the recorded 1400z state at 14:08:13
UP_Q, UP_C = D("2.23"), D("2.23") * D("0.34")
DN_Q, DN_C = D("4.00"), D("2.23") * D("0.65") + D("1.77") * D("0.25")
NEED, FEE = D("1.77"), D("0.03")


def portfolio_cap(h, ch, d, cd, q, fee):
    """p <= [min(h, d+q) - ch - cd - fee] / q"""
    return (min(h, d + q) - ch - cd - fee) / q


def test_the_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(TS.PORTFOLIO_COMPLETION_ENV, raising=False)
    assert TS.portfolio_completion_enabled() is False


@pytest.mark.parametrize("raw", ["", "no", "0", "true", "1", "Y", "yes "])
def test_only_an_explicit_YES_enables_it(monkeypatch, raw):
    monkeypatch.setenv(TS.PORTFOLIO_COMPLETION_ENV, raw)
    assert TS.portfolio_completion_enabled() is (raw.strip().upper() == "YES")


def test_the_old_basis_refuses_the_profitable_completion():
    """Reproduces the refusal exactly: average basis -> cap 0.5270."""
    avg = DN_C / DN_Q
    assert avg == D("0.4730")
    assert D(1) - avg == D("0.5270")
    assert D("0.60") > D(1) - avg          # UP at 0.60 is refused


def test_the_whole_book_bound_admits_it():
    cap = portfolio_cap(DN_Q, DN_C, UP_Q, UP_C, NEED, FEE)
    assert cap > D("0.60"), cap            # UP at 0.60 is allowed
    assert D("0.74") < cap < D("0.75"), cap


def test_and_the_order_is_genuinely_a_profit_lock():
    """Not merely allowed -- both settlement outcomes become positive."""
    cost = UP_C + DN_C
    before = PP.settlement_payoff(UP_Q, DN_Q, cost)
    after = PP.settlement_payoff(UP_Q + NEED, DN_Q,
                                 cost + NEED * D("0.60") + FEE)
    assert before["worst_usd"] == D("-0.4202")
    assert after["worst_usd"] == D("0.2578")
    assert after["pnl_if_up_usd"] > 0 and after["pnl_if_down_usd"] > 0
    assert after["worst_usd"] - before["worst_usd"] == D("0.6780")


def test_the_bound_still_refuses_a_price_that_loses_on_an_outcome():
    """At UP 0.84, which the engine also saw, the book goes negative again
    -- and the whole-book bound refuses it, as it must."""
    cap = portfolio_cap(DN_Q, DN_C, UP_Q, UP_C, NEED, FEE)
    assert D("0.84") > cap
    cost = UP_C + DN_C
    after = PP.settlement_payoff(UP_Q + NEED, DN_Q,
                                 cost + NEED * D("0.84") + FEE)
    assert after["worst_usd"] < 0


def test_it_is_stricter_than_redeem_cap_when_the_book_is_underwater():
    """It is NOT uniformly more permissive. With no matched portion and a
    dear basis the whole-book bound binds TIGHTER, which is the point:
    it bounds the book, not one side."""
    h, ch = D("2.14"), D("2.14") * D("0.40")     # 1315z: all stranded
    d, cd, q = D("0"), D("0"), D("2.14")
    old = D(1) - ch / h
    new = portfolio_cap(h, ch, d, cd, q, D("0"))
    assert new <= old


def test_it_moves_no_risk_limit():
    import inspect
    src = inspect.getsource(TS.portfolio_completion_enabled)
    for banned in ("max_total_exposure", "max_live_orders", "daily_loss",
                   "residual", "MAX_PAIR_COST", "cash"):
        assert banned not in src, banned
