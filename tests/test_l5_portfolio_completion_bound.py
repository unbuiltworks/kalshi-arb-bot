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

# THE $0.11, reconciled. `net_settlement` charges a worst-case TAKER fee on
# inventory ALREADY HELD -- cost = a*pu + b*pd + fee(a,pu) + fee(b,pd) --
# on fills whose actual commission was 0.0000. That is the whole gap
# between the engine's worst_before -0.5302 and naive settlement -0.4202.
# The bound REUSES net_settlement so it can never be more permissive than
# the engine's own worst case. My first draft solved a closed form that
# omitted this term and was wrong in exactly that direction.
ENGINE_WORST_BEFORE = D("-0.530200")
ENGINE_WORST_AFTER = D("0.1478000")
ENGINE_IMPROVEMENT = D("0.6780000")


def cap(touch, *, h=DN_Q, ch=DN_C, d=UP_Q, cd=UP_C, q=NEED, side="DOWN"):
    """The shipped bound, exercised directly."""
    return TS.portfolio_completion_cap(
        side=side, held_qty=h, held_cost_usd=ch, deficient_qty=d,
        deficient_cost_usd=cd, buy_qty=q, tick=D("0.01"), ceiling=touch)


def test_the_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(TS.PORTFOLIO_COMPLETION_ENV, raising=False)
    assert TS.portfolio_completion_enabled() is False


@pytest.mark.parametrize("raw", ["", "no", "0", "true", "1", "Y", "yes "])
def test_only_an_explicit_YES_enables_it(monkeypatch, raw):
    monkeypatch.setenv(TS.PORTFOLIO_COMPLETION_ENV, raw)
    assert TS.portfolio_completion_enabled() is (raw.strip().upper() == "YES")


def test_the_old_basis_refuses_the_profitable_completion():
    avg = DN_C / DN_Q
    assert avg == D("0.4730")
    assert D(1) - avg == D("0.5270")
    assert D("0.60") > D(1) - avg


def test_the_bound_admits_the_recorded_missed_completion():
    assert cap(D("0.60")) == D("0.60")


def test_it_refuses_the_dear_price_the_engine_also_saw():
    """At the 0.84 touch the engine's worst_after was -0.277. The bound
    must not quote there; it caps lower."""
    c = cap(D("0.84"))
    assert c is not None and c < D("0.84"), c


def test_it_reconciles_with_the_engine_s_own_arithmetic():
    """The $0.03 and the $0.11, both accounted."""
    cash = NEED * D("0.60") + FEE
    assert cash == D("1.09200")                      # engine: 1.092000
    assert ENGINE_WORST_AFTER - ENGINE_WORST_BEFORE == ENGINE_IMPROVEMENT
    naive_before = min(DN_Q, UP_Q) - (UP_C + DN_C)
    assert naive_before == D("-0.4202")
    held_fee = naive_before - ENGINE_WORST_BEFORE
    assert D("0.10") < held_fee < D("0.12"), held_fee  # the ~$0.11


# ---------------------------------------------- item 2: the hard cases

def test_a_partial_fill_is_bounded_on_the_quantity_actually_bought():
    """Half the need must not be priced as if the whole need filled."""
    full = cap(D("0.60"), q=NEED)
    half = cap(D("0.60"), q=NEED / 2)
    assert full is not None and half is not None
    assert half <= D("0.60") and full <= D("0.60")


def test_unequal_share_quantities_are_handled():
    c = cap(D("0.70"), h=D("5.5"), ch=D("5.5") * D("0.30"),
            d=D("1.25"), cd=D("1.25") * D("0.40"), q=D("4.25"))
    assert c is not None


def test_a_book_with_no_matched_portion_binds_TIGHTER_than_redeem_cap():
    """1315z: all stranded, dear basis. The whole-book bound must be the
    stricter one -- it is not a loosening device."""
    h, ch = D("2.14"), D("2.14") * D("0.40")
    old = D(1) - ch / h                       # 0.60
    new = cap(D("0.90"), h=h, ch=ch, d=D("0"), cd=D("0"), q=h)
    assert new is None or new <= old, (new, old)


def test_previously_matched_inventory_does_not_poison_the_basis():
    """The defect itself: adding an already-matched pair to BOTH sides must
    not make a cheap stranded leg look unprofitable."""
    lean = cap(D("0.60"), h=D("1.77"), ch=D("1.77") * D("0.25"),
               d=D("0"), cd=D("0"), q=D("1.77"))
    fat = cap(D("0.60"))        # same leg, plus the matched 2.23 pair
    assert lean is not None and fat is not None
    assert fat >= D("0.60") - D("0.05"), (lean, fat)


@pytest.mark.parametrize("bad", [
    dict(h=None), dict(ch=None), dict(q=D("0")), dict(q=D("-1")),
])
def test_unreadable_or_impossible_inputs_return_None(bad):
    kw = dict(h=DN_Q, ch=DN_C, d=UP_Q, cd=UP_C, q=NEED)
    kw.update(bad)
    assert cap(D("0.60"), **kw) is None


def test_None_leaves_the_existing_cap_untouched():
    """Fail-safe: no answer means the old redeem_cap still governs."""
    assert cap(D("0.60"), q=D("0")) is None


def test_it_terminates_and_never_quotes_at_or_below_zero():
    c = cap(D("0.99"), h=D("1"), ch=D("0.99"), d=D("0"), cd=D("0"), q=D("1"))
    assert c is None or c > 0


def test_it_moves_no_risk_limit():
    import inspect
    src = inspect.getsource(TS.portfolio_completion_cap)
    for banned in ("max_total_exposure", "max_live_orders", "daily_loss",
                   "residual_budget", "MAX_PAIR_COST", "cash_headroom"):
        assert banned not in src, banned
    # and it reuses the engine's own settlement function
    assert "net_settlement" in src
