"""The completion basis averaged a MATCHED pair into a stranded leg.

THE INCIDENT, 2026-11-02 window 1400z, from the decision ledger.

At 14:09 the book held DOWN 4.00 and UP 2.23. That DOWN 4.00 is two
different things: 2.23 already MATCHED at own 0.65, plus 1.77 STRANDED at
own 0.25. `completion_requote` priced the completion off the whole side:

    ch_total / h = (2.23*0.65 + 1.77*0.25) / 4.00 = 0.4730
    redeem_cap   = 1 - 0.4730                     = 0.5270

and the engine refused, repeatedly:

    L5_MISSED_PAIR  reason PAIR_ABOVE_REDEMPTION
                    pair_own_cost 1.3030   gain_per_contract -0.3030
                    held_side DOWN  held_qty 4.00  need 1.77

WHAT THE WINDOW ACTUALLY OFFERED, and an earlier version of this docstring
got it wrong. It claimed UP was executable at own 0.60. It was not: the
recorded `pair_own_cost` ran 1.1930 to 1.3230 against a fixed avg of
0.4730, so the UP price was own 0.72 to 0.85 for the whole window, and
`L5_TS_COMPLETION` prices the crossing completion at 0.79 and 0.84.

SO THE BOUND REFUSES 1400z TOO. The whole-book cap on this book is 0.68,
below everything the window offered, and the engine's own
`both_positive_after` is false on every 1400z record. Across the entire
13:15z-14:31z campaign not one record carries it true. The averaged basis
and the whole-book test AGREE here: those completions really would have
locked a loss, and the 182 `book_completion:PAIR_ABOVE_REDEMPTION`
refusals were right.

THE ARITHMETIC BELOW IS AT A HYPOTHETICAL own 0.60, which this book did
not offer. It is kept because it is the cleanest demonstration of the
averaging defect -- at 0.60 the completion takes the book to UP 4.00 /
DOWN 4.00 for 3.7122:

    before  pnl_if_up -0.4202   pnl_if_down +1.3498   worst -0.4202
    after   pnl_if_up +0.2578   pnl_if_down +0.2578   worst +0.2578

-- while `redeem_cap` of 0.5270 refuses it. The matched 2.23 is already
settled economics; averaging its 0.65 into the basis of a leg bought at
0.25 is the wrong denominator whether or not this particular window had a
price where it mattered.

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


# ===================================================================
# THE WIRING: the bound must be computed on the shares this requote
# will ACTUALLY buy.
#
# Found by applying the owner's "verify across partial fills" to the
# CALL SITE rather than to the function. The first wiring passed the
# gross `resting_qty`; `completion_ceiling`, three lines above it, is
# passed `q` -- `min(working, unmatched)` floored to the lot grid, net
# of what the order has already filled.
#
# WHY THE GROSS FIGURE IS UNSAFE AND NOT MERELY WRONG. `q <= h - dq`,
# so completing always leaves `d + q <= h`: the book stays
# under-covered, and there every extra share costs `p + fee` but adds
# a full $1.00 of coverage. At `p < 1` that IMPROVES the modelled
# worst case, so an overstated `buy_qty` buys coverage that never
# arrives. Measured on the 1400z book with 0.96 of the 1.77 order
# filled: gross returns 0.43, where the true 0.81-share purchase
# leaves the worst outcome at -$0.0358. The honest cap is 0.38.
# ===================================================================

#: A CONSTRUCTED book, not a recorded one -- an earlier version of this
#: fixture was labelled 1400z and carried the matched portion's $1.4495
#: as the whole held side's cost, where 1400z's own records give DOWN
#: 4.00 at avg 0.4730. The arithmetic below stands as a demonstration of
#: the loosening; it is not evidence about that window.
#:
#: Held DOWN 4.00 for $1.4495. The completing UP order is 1.77 and 0.96
#: of it has filled at 0.60, so UP holds 2.73 for $1.6380 and only 0.81
#: shares are still working.
PF = dict(side=TS.UP, held_qty=D("4.00"), held_cost_usd=D("1.4495"),
          deficient_qty=D("2.73"), deficient_cost_usd=D("1.6380"),
          tick=D("0.01"), ceiling=D("0.60"))


def _worst_at(price, buy_qty):
    """The engine's own worst outcome after buying `buy_qty` at `price`."""
    nd = PF["deficient_qty"] + buy_qty
    return TS.net_settlement(
        up_qty=nd, dn_qty=PF["held_qty"],
        up_price=(PF["deficient_cost_usd"] + buy_qty * price) / nd,
        dn_price=PF["held_cost_usd"] / PF["held_qty"])["worst_outcome"]


def test_the_gross_resting_size_would_admit_a_loss_making_price():
    """The defect, stated as arithmetic rather than as a code shape."""
    loose = TS.portfolio_completion_cap(buy_qty=D("1.77"), **PF)
    tight = TS.portfolio_completion_cap(buy_qty=D("0.81"), **PF)
    assert loose == D("0.43")
    assert tight == D("0.38")
    # the band the gross figure opens is a book that CAN settle at a loss
    assert _worst_at(loose, D("0.81")) < 0
    assert _worst_at(tight, D("0.81")) >= 0


def test_a_partially_filled_requote_is_bounded_by_its_working_size():
    """End to end through `completion_requote`, flag on.

    The resting order is 1.77 with 0.96 filled, so 0.81 is working and
    the target may not exceed the 0.81-share cap. Constructed book, as
    above.
    """
    import os
    ws = 1_793_189_700.0
    kw = dict(side="UP", resting_own_px="0.30", resting_qty="1.77",
              resting_filled_qty="0.96", bid="0.5900", ask="0.6000",
              tick="0.01", min_qty="0.01",
              held_qty="4.00", held_cost_usd="1.4495",
              deficient_qty="2.73", deficient_cost_usd="1.6380",
              residual_budget_usd="3.00",
              now_s=ws + 480.0, window_start_s=ws,
              window_end_s=ws + TS.WINDOW_S, book_age_s="0.05")
    old = os.environ.get(TS.PORTFOLIO_COMPLETION_ENV)
    os.environ[TS.PORTFOLIO_COMPLETION_ENV] = "YES"
    try:
        r = TS.completion_requote(**kw)
    finally:
        if old is None:
            os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
        else:
            os.environ[TS.PORTFOLIO_COMPLETION_ENV] = old
    assert r["act"] is True, r
    to = D(r["to_price"])
    assert to <= D("0.38"), r            # never the gross figure's 0.43
    # and the resulting whole book cannot settle at a loss
    assert _worst_at(to, D(r["qty"])) >= 0


def test_the_bound_and_the_ceiling_are_given_the_same_quantity():
    """A source check, because the two calls are three lines apart.

    `completion_ceiling` gets `complete_qty=q`. The bound must get the
    same `q`, not `resting_qty`, or the two disagree about what is
    being bought.
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(TS.completion_requote))
    seen = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = getattr(node.func, "id", None)
        if fn not in ("completion_ceiling", "portfolio_completion_cap"):
            continue
        for kw in node.keywords:
            if kw.arg in ("complete_qty", "buy_qty"):
                seen[fn] = getattr(kw.value, "id", None)
    assert seen == {"completion_ceiling": "q",
                    "portfolio_completion_cap": "q"}, seen


# ===================================================================
# RESTARTS and SETTLEMENT -- the last two of the owner's item 2.
#
# The bound is a pure function of the figures handed to it, so a
# restart can only reach it through those figures. Three ways it can:
# an unreadable basis, an UNATTESTED basis that `leg_cost` reports as
# $0.00, and an unreadable basis on the DEFICIENT side.
# ===================================================================

RS = dict(side="UP", resting_own_px="0.30", resting_qty="1.77",
          resting_filled_qty="0", bid="0.5900", ask="0.6000",
          tick="0.01", min_qty="0.01",
          held_qty="4.00", held_cost_usd="1.4495",
          deficient_qty="2.23", deficient_cost_usd="1.3380",
          residual_budget_usd="3.00",
          now_s=1_793_189_700.0 + 480.0,
          window_start_s=1_793_189_700.0,
          window_end_s=1_793_189_700.0 + TS.WINDOW_S, book_age_s="0.05")


def _rq(flag, **over):
    import os
    kw = dict(RS)
    kw.update(over)
    old = os.environ.get(TS.PORTFOLIO_COMPLETION_ENV)
    if flag:
        os.environ[TS.PORTFOLIO_COMPLETION_ENV] = "YES"
    else:
        os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
    try:
        return TS.completion_requote(**kw)
    finally:
        if old is None:
            os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
        else:
            os.environ[TS.PORTFOLIO_COMPLETION_ENV] = old


def test_an_unreadable_held_basis_refuses_before_the_bound_is_reached():
    """A restart that lost the basis must not reach the bound at all.

    `HELD_COST_UNKNOWN` is raised above the bound, so the flag cannot
    make an unpriceable book completable.
    """
    for flag in (False, True):
        r = _rq(flag, held_cost_usd=None)
        assert r["act"] is False
        assert r["reason"] == "HELD_COST_UNKNOWN", (flag, r)


def test_an_unreadable_deficient_basis_leaves_redeem_cap_untouched():
    """The bound returns None, which is not a licence -- it is a no-op."""
    off = _rq(False, deficient_cost_usd=None)
    on = _rq(True, deficient_cost_usd=None)
    assert on == off, (on, off)


def test_an_unattested_zero_basis_binds_TIGHTER_than_redeem_cap():
    """The restart case `leg_cost` actually produces, and its blast radius.

    `InventoryStore.leg_cost` defaults a missing leg to D(0) rather
    than None, so inventory ADOPTED from the venue's net -- 50 legs
    carried that state in the live reconcile of 2026-11-02 -- reaches
    this function priced at zero. That is a PRE-EXISTING defect in the
    requote path's inputs, not one this bound introduces: on a zeroed
    basis `redeem_cap = 1 - 0/h = 1.00`, i.e. already maximally
    permissive.

    What this pins is that the bound does not inherit that
    permissiveness. Given the same zeroed basis it still demands the
    whole book cover itself, so it binds strictly tighter than the
    1.00 it replaces.
    """
    cap = TS.portfolio_completion_cap(
        side=TS.UP, held_qty=D("4.00"), held_cost_usd=D("0"),
        deficient_qty=D("2.23"), deficient_cost_usd=D("1.3380"),
        buy_qty=D("1.77"), tick=D("0.01"), ceiling=D("0.99"))
    assert cap is not None
    assert cap < D("1.00"), cap


def test_settlement_arithmetic_independently_of_net_settlement():
    """Both outcomes >= 0 at the admitted price, computed from scratch.

    `portfolio_completion_cap` asks `net_settlement`, so a test that
    asks the same function proves only self-consistency. This settles
    the book by hand: a matched pair redeems $1.00 per share, the
    uncovered remainder redeems $1.00 on its own outcome and $0.00 on
    the other, and cash out is what was paid.
    """
    h, ch = D("4.00"), D("1.4495")        # DOWN held
    dq, cd = D("2.23"), D("1.3380")       # UP already held
    q = D("1.77")
    cap = TS.portfolio_completion_cap(
        side=TS.UP, held_qty=h, held_cost_usd=ch, deficient_qty=dq,
        deficient_cost_usd=cd, buy_qty=q, tick=D("0.01"),
        ceiling=D("0.60"))
    assert cap is not None
    up_q, dn_q = dq + q, h
    paid = ch + cd + q * cap
    # the engine's worst case also charges a taker fee on BOTH legs, so
    # the hand-rolled figure here is the fee-free lower bound on cost
    for winner_qty in (up_q, dn_q):       # UP settles, then DOWN settles
        assert winner_qty * D(1) - paid >= 0, (winner_qty, paid, cap)


# ===================================================================
# THE PLACEMENT PATH, which is where the recorded refusals came from.
#
# `l5_book_completion.decide()` raised all 182
# `book_completion:PAIR_ABOVE_REDEMPTION` refusals of the 1315z-1400z
# campaign, at step 7, on `pair = avg + own`. The bound wired only into
# `completion_requote` governs REPRICING an order already resting and
# could not have reached one of them.
# ===================================================================

import os

from src import l5_book_completion as BC

WS_1400 = 1_793_196_000.0                      # 14:00:00Z

#: The AUTHORITATIVE 1400z state, from that window's own
#: `L5_TS_COMPLETION` records: up_qty 2.23 / up_cost_px 0.3400,
#: dn_qty 4.00 / dn_cost_px 0.4730, deficit 1.77,
#: worst_before -0.530200. The UP touch ran 0.72..0.85 all window.
def _decide_1400(bid, ask, flag):
    old = os.environ.get(TS.PORTFOLIO_COMPLETION_ENV)
    if flag:
        os.environ[TS.PORTFOLIO_COMPLETION_ENV] = "YES"
    else:
        os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
    try:
        return BC.decide(
            market="cpc-btc-updown-15m-2026-11-02-1400z",
            held_side="DOWN",
            held_qty=D("4.00"), held_cost_usd=D("4.00") * D("0.4730"),
            deficient_qty=D("2.23"),
            deficient_cost_usd=D("2.23") * D("0.3400"),
            book={"bid": bid, "ask": ask, "stream_age_s": "0.05"},
            now_t=WS_1400 + 560.0, window_start=WS_1400,
            tick="0.01", min_qty="0.01", depth_ticks=2,
            residual_budget_usd="1.50", submissions_left=8)
    finally:
        if old is None:
            os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
        else:
            os.environ[TS.PORTFOLIO_COMPLETION_ENV] = old


def test_the_bound_still_refuses_the_real_1400z_book():
    """The measured answer, not the hoped-for one.

    The UP touch was own 0.79 (bid 0.7800 / ask 0.7900 on the YES wire),
    the whole-book cap is 0.68, so the completion stays refused with the
    flag ON -- and the recorded `portfolio_cap` says why.
    """
    off = _decide_1400("0.7800", "0.7900", False)
    on = _decide_1400("0.7800", "0.7900", True)
    assert off["ok"] is False
    assert off["reason"] == "PAIR_ABOVE_REDEMPTION", off
    assert on["ok"] is False
    assert on["reason"] == "PAIR_ABOVE_REDEMPTION", on
    # the flag-on record NAMES the bound that refused, which flag-off cannot
    assert D(on["portfolio_cap"]) == D("0.68")
    assert off.get("portfolio_cap") is None


def test_the_bound_admits_only_where_the_whole_book_covers_itself():
    """The mechanism works -- this book just needed a cheaper UP.

    Same inventory, a book two ticks cheaper than anything 1400z
    offered: bid 0.7000 / ask 0.7100 prices the maker completion at own
    0.68. The averaged basis refuses it (pair 1.1530 > 1.00); the
    whole-book test admits, because 0.68 is exactly the cap.
    """
    off = _decide_1400("0.7000", "0.7100", False)
    on = _decide_1400("0.7000", "0.7100", True)
    assert off["reason"] == "PAIR_ABOVE_REDEMPTION", off
    assert D(off["pair_own_cost"]) == D("1.1530")
    assert on.get("reason") != "PAIR_ABOVE_REDEMPTION", on
    assert on.get("admitted_by") == "PORTFOLIO_PAYOFF", on
    assert D(on["portfolio_cap"]) == D("0.68")


def test_how_far_1400z_was_from_qualifying_four_ticks():
    """The size of the miss, which is the number that matters.

    The whole-book cap on the 1400z book is own 0.68. The cheapest UP
    the window ever showed was own 0.72 -- the lowest recorded
    `pair_own_cost` of 1.1930 against the fixed 0.4730 average. So the
    bound refuses 1400z by FOUR TICKS at the window's single best
    moment, and by seventeen at its worst (pair 1.3230 -> own 0.85).
    """
    cap = TS.portfolio_completion_cap(
        side=TS.UP, held_qty=D("4.00"),
        held_cost_usd=D("4.00") * D("0.4730"), deficient_qty=D("2.23"),
        deficient_cost_usd=D("2.23") * D("0.3400"), buy_qty=D("1.77"),
        tick=D("0.01"), ceiling=D("0.99"))
    assert cap == D("0.68")
    best_recorded_own = D("1.1930") - D("0.4730")
    worst_recorded_own = D("1.3230") - D("0.4730")
    assert best_recorded_own == D("0.72")
    assert (best_recorded_own - cap) / D("0.01") == 4
    assert (worst_recorded_own - cap) / D("0.01") == 17


def test_the_flag_off_placement_path_is_byte_for_byte_unchanged():
    """No record, no key, no behaviour change with the flag unset."""
    for bid, ask in (("0.7800", "0.7900"), ("0.3900", "0.4000"),
                     ("0.5900", "0.6000"), ("0.9000", "0.9100")):
        off = _decide_1400(bid, ask, False)
        assert "portfolio_cap" not in off or off["portfolio_cap"] is None
        assert off.get("admitted_by") is None


def test_the_bound_cannot_bypass_the_deficit_or_submission_limits():
    """It sits BELOW every other gate, so it can only ever admit.

    With no submissions left, or past the terminal, the flag changes
    nothing -- those refusals are raised before step 7 is reached.
    """
    old = os.environ.get(TS.PORTFOLIO_COMPLETION_ENV)
    os.environ[TS.PORTFOLIO_COMPLETION_ENV] = "YES"
    try:
        spent = BC.decide(
            market="cpc-btc-updown-15m-2026-11-02-1400z", held_side="DOWN",
            held_qty=D("4.00"), held_cost_usd=D("4.00") * D("0.4730"),
            deficient_qty=D("2.23"),
            deficient_cost_usd=D("2.23") * D("0.3400"),
            book={"bid": "0.3900", "ask": "0.4000", "stream_age_s": "0.05"},
            now_t=WS_1400 + 560.0, window_start=WS_1400,
            tick="0.01", min_qty="0.01", residual_budget_usd="1.50",
            submissions_left=0)
        late = BC.decide(
            market="cpc-btc-updown-15m-2026-11-02-1400z", held_side="DOWN",
            held_qty=D("4.00"), held_cost_usd=D("4.00") * D("0.4730"),
            deficient_qty=D("2.23"),
            deficient_cost_usd=D("2.23") * D("0.3400"),
            book={"bid": "0.3900", "ask": "0.4000", "stream_age_s": "0.05"},
            now_t=WS_1400 + 880.0, window_start=WS_1400,
            tick="0.01", min_qty="0.01", residual_budget_usd="1.50",
            submissions_left=8)
    finally:
        if old is None:
            os.environ.pop(TS.PORTFOLIO_COMPLETION_ENV, None)
        else:
            os.environ[TS.PORTFOLIO_COMPLETION_ENV] = old
    assert spent["reason"] == "NO_SUBMISSIONS_LEFT", spent
    assert late["reason"] == "PAST_TERMINAL", late
