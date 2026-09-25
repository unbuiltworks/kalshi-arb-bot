"""Concurrency is the question. Cumulative notional answers nothing."""
from __future__ import annotations

from decimal import Decimal as D

import src.l5_maker_replay as R

LAM = D("1")          # identity, so sizes in these tests are literal


def w(max_live=1, cap="5"):
    return R.WindowReplay("m", lam=LAM, cap_usd=D(cap), max_live=max_live)


def test_one_live_order_serializes_everything_after_the_first():
    x = w(max_live=1)
    assert x.offer(key="a", side="UP", price=D("0.5"), size=D("1"), t=0) is None
    assert x.offer(key="b", side="UP", price=D("0.5"), size=D("1"),
                   t=1) == "MAX_LIVE_ORDERS"
    assert x.peak_concurrent == 1


def test_without_the_count_limit_the_dollar_cap_is_what_binds():
    x = w(max_live=None, cap="1.20")
    assert x.offer(key="a", side="UP", price=D("0.5"), size=D("1"), t=0) is None
    assert x.offer(key="b", side="UP", price=D("0.5"), size=D("1"), t=1) is None
    # 1.00 resting; a third 0.50 would make 1.50 > 1.20
    assert x.offer(key="c", side="UP", price=D("0.5"), size=D("1"),
                   t=2) == "MAX_USD_PER_MARKET"
    assert x.peak_concurrent == 2
    assert x.peak_premium == D("1.00")


def test_worst_case_for_a_bought_token_is_the_premium():
    x = w(max_live=None)
    x.offer(key="a", side="UP", price=D("0.30"), size=D("2"), t=0)
    assert x._exposure() == D("0.60")


def test_book_reached_is_not_a_fill():
    x = w(max_live=None)
    x.offer(key="a", side="UP", price=D("0.40"), size=D("1"), t=0)
    x.observe_book({"UP": D("0.40"), "DOWN": None})
    assert x.resting[0].book_reached is True
    x.close(10.0)
    d = x.as_dict()
    assert d["book_reached"] == 1
    # nothing filled, and no inventory exists anywhere in this replay
    assert x.filled_premium == D(0)


def test_trade_flow_is_not_a_fill():
    x = w(max_live=None)
    x.offer(key="a", side="UP", price=D("0.40"), size=D("1"), t=0)
    x.observe_prints([{"px": "0.39"}])
    assert x.resting[0].trade_flow is True
    assert x.filled_premium == D(0)


def test_a_print_is_compared_in_the_orders_own_terms():
    x = w(max_live=None)
    x.offer(key="d", side="DOWN", price=D("0.30"), size=D("1"), t=0)
    x.observe_prints([{"px": "0.75"}])       # YES 0.75 -> DOWN 0.25 <= 0.30
    assert x.resting[0].trade_flow is True


def test_l5s_reversal_cancels_only_the_opposing_side():
    x = w(max_live=None)
    x.offer(key="u", side="UP", price=D("0.4"), size=D("1"), t=0)
    x.offer(key="d", side="DOWN", price=D("0.4"), size=D("1"), t=1)
    x.cancel("L5_REVERSAL", t=2, side="UP")
    assert [r.side for r in x.resting] == ["DOWN"]
    assert x.done[0].end_reason == "L5_REVERSAL"


def test_the_window_guard_ends_everything_still_resting():
    x = w(max_live=None)
    x.offer(key="a", side="UP", price=D("0.4"), size=D("1"), t=0)
    x.close(100.0)
    assert x.resting == []
    assert x.done[0].lifetime(0.0) == 100.0


def test_arrivals_while_resting_are_counted_even_when_placed():
    x = w(max_live=None)
    x.offer(key="a", side="UP", price=D("0.4"), size=D("1"), t=0)
    x.offer(key="b", side="UP", price=D("0.4"), size=D("1"), t=1)
    assert x.arrived_while_resting == 1


def test_a_size_below_the_increment_is_refused_not_rounded_up():
    x = w(max_live=None)
    assert x.offer(key="a", side="UP", price=D("0.5"), size=D("0.001"),
                   t=0) == "BELOW_MIN_INCREMENT"
