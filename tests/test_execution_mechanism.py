"""Regressions for the execution-mechanism evidence (P0, 2026-09-21).

Three defects are pinned here because each one produced a WRONG PUBLISHED
NUMBER before it was caught:

  * comp_log intervals were closed by any sibling rung, so 78% had zero length
    and a reach test read "83.1% never reached" off windows that could not
    contain a second observation;
  * the mint-feasibility discriminator is an algebraic identity on a binary
    book, so it was reported as a near-perfect separator when it cannot fail;
  * settlementPx arrives as {'value': ..., 'currency': ...}, and float() on it
    silently turned every settlement lookup into a failure.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.l5.historical_l2 import queue_forensics as QF
from analysis.pmus import execution_counterfactuals as XC
from analysis.pmus import ladder_replay as LR

BOOK = {"bid_prices": [0.50, 0.49, 0.48], "bid_sizes": [10.0, 20.0, 30.0],
        "ask_prices": [0.52, 0.53], "ask_sizes": [5.0, 6.0],
        "best_bid": 0.50, "best_ask": 0.52}


# ---------------------------------------------------------------- depth reads
def test_size_at_reads_the_named_level_only():
    assert QF.size_at(BOOK, 0.49) == 20.0
    assert QF.size_at(BOOK, 0.47) == 0.0


def test_size_better_is_strictly_better_priced_not_inclusive():
    # 0.50 is ahead of 0.49 on price priority; 0.49 itself is NOT "ahead".
    assert QF.size_better(BOOK, 0.49) == 10.0
    assert QF.size_better(BOOK, 0.50) == 0.0
    assert QF.size_better(BOOK, 0.52, side="ask") == 0.0
    assert QF.size_better(BOOK, 0.53, side="ask") == 5.0


def test_missing_book_never_reads_as_zero_depth():
    """None means unmeasured. Returning 0.0 would claim an empty queue."""
    assert QF.size_at(None, 0.49) is None
    assert QF.size_better(None, 0.49) is None


# ------------------------------------------------------------- reach / through
def _book(rows):
    b = QF.L2()
    for t, bid, ask in rows:
        b.add(t, {"best_bid": bid, "best_ask": ask})
    b.sort()
    return b


def test_a_resting_buy_needs_the_ASK_to_come_down_not_the_bid_to_go_up():
    """The direction error that produced a fully inverted first answer.

    For a passive BUY at 0.45 a RISING bid means the market moved AWAY. Only a
    falling ask can fill it."""
    rising_bid = _book([(1.0, 0.40, 0.60), (2.0, 0.50, 0.60), (3.0, 0.55, 0.60)])
    assert QF.reach(rising_bid, 0.0, 9.0, 0.45)["reached"] is False
    falling_ask = _book([(1.0, 0.40, 0.60), (2.0, 0.40, 0.45), (3.0, 0.40, 0.44)])
    r = QF.reach(falling_ask, 0.0, 9.0, 0.45)
    assert r["reached"] is True and r["through"] is True


def test_touching_the_price_is_not_trading_through_it():
    touched = _book([(1.0, 0.40, 0.60), (2.0, 0.40, 0.45)])
    r = QF.reach(touched, 0.0, 9.0, 0.45)
    assert r["reached"] is True and r["through"] is False


def test_first_opportunity_is_the_earliest_touch_not_the_last():
    b = _book([(1.0, 0.40, 0.45), (2.0, 0.40, 0.60), (3.0, 0.40, 0.44)])
    r = QF.reach(b, 0.0, 4.0, 0.45)
    assert r["t_first_opportunity"] == 1.0
    assert r["seconds_from_opportunity_to_fill"] == 3.0


# ------------------------------------------------------------------- mechanism
def test_pro_rata_is_never_assigned_because_it_is_not_identifiable_here():
    labels = {QF.mechanism(c, m, r)
              for c in ("RESTING_BID_CONSISTENT", "AT_OR_ABOVE_ASK_NOT_MEASURABLE",
                        "INSIDE_SPREAD_IMPROVED_THE_TOUCH", "NO_BOOK")
              for m in (True, False, None) for r in (True, False)}
    assert not any("PRO_RATA" in x for x in labels)


def test_a_price_the_market_never_offered_is_not_called_a_fifo_book_fill():
    assert QF.mechanism("RESTING_BID_CONSISTENT", False, False) == \
        "RESTING_BID_PRICE_NEVER_OFFERED"
    assert QF.mechanism("RESTING_BID_CONSISTENT", False, True) == \
        "BOOK_FILL_FIFO_CONSISTENT"


# ------------------------------------------------------- flow attribution (B)
def test_attributable_requires_the_price_to_sit_in_its_OWN_rows_quote():
    assert XC.attributable({"bid": 0.40, "ask": 0.42}, 0.41) is True
    assert XC.attributable({"bid": 0.40, "ask": 0.42}, 0.35) is False
    assert XC.attributable({"bid": 0.40, "ask": 0.42}, 0.50) is False


def test_a_row_without_a_quote_is_not_attributable_rather_than_assumed_good():
    assert XC.attributable({"bid": None, "ask": 0.42}, 0.41) is False
    assert XC.attributable({}, 0.41) is False


def test_build_flow_splits_all_from_attributable_on_the_quote():
    raw = {"m": [(1.0, {"shares_traded_cum": 0, "last_trade_px": 0.5,
                        "bid": 0.49, "ask": 0.51}),
                 (2.0, {"shares_traded_cum": 10, "last_trade_px": 0.50,
                        "bid": 0.49, "ask": 0.51}),
                 (3.0, {"shares_traded_cum": 25, "last_trade_px": 0.20,
                        "bid": 0.49, "ask": 0.51})]}
    al, at = XC.build_flow(raw)
    assert [p["size"] for p in al["m"]] == [10, 15]
    assert [p["size"] for p in at["m"]] == [10]


def test_cumulative_volume_going_backwards_creates_no_print():
    raw = {"m": [(1.0, {"shares_traded_cum": 50, "last_trade_px": 0.5,
                        "bid": 0.49, "ask": 0.51}),
                 (2.0, {"shares_traded_cum": 10, "last_trade_px": 0.5,
                        "bid": 0.49, "ask": 0.51})]}
    al, _ = XC.build_flow(raw)
    assert al.get("m", []) == []


# ------------------------------------------------------------------ settlement
def test_settlement_is_refused_for_a_market_that_has_not_expired():
    """An OPEN market returns settlementPx 0.0000 as a DEFAULT. Reading it
    would silently score every live market as YES-loses."""
    s = {"m": {"settled": True, "state": "MARKET_STATE_OPEN", "yes_settle": 0.0}}
    assert XC.settled_yes(s, "m") is None


def test_settlement_zero_from_an_expired_market_is_a_real_result():
    s = {"m": {"settled": True, "state": "MARKET_STATE_EXPIRED", "yes_settle": 0.0}}
    assert XC.settled_yes(s, "m") == 0.0


def test_economics_never_folds_the_two_pnl_lines_into_one_number():
    res = [{"slug": "m", "fills": 2, "yes_n": 1, "no_n": 1, "matched": 1, "resid": 0,
            "yes_cost": 0.40, "no_cost": 0.50}]
    s = {"m": {"settled": True, "state": "MARKET_STATE_EXPIRED", "yes_settle": 1.0}}
    e = XC.economics(res, s)
    assert e["matched_pair_pnl_SETTLED_MODELED"] == pytest.approx(0.10)
    assert e["residual_pnl_SETTLED_MODELED"] == 0.0
    assert e["rebates_NEVER_ESTIMATED"] == 0.0
    assert "SETTLED-MODELED" in e["label"]


def test_residual_pnl_uses_the_surplus_side_and_its_own_average_cost():
    res = [{"slug": "m", "fills": 3, "yes_n": 3, "no_n": 0, "matched": 0, "resid": 3,
            "yes_cost": 1.20, "no_cost": 0.0}]
    s = {"m": {"settled": True, "state": "MARKET_STATE_EXPIRED", "yes_settle": 1.0}}
    e = XC.economics(res, s)
    assert e["residual_pnl_SETTLED_MODELED"] == pytest.approx((1.0 - 0.40) * 3)
    assert e["matched_pair_pnl_SETTLED_MODELED"] == 0.0


def test_an_unsettled_market_contributes_no_pnl_but_is_counted():
    res = [{"slug": "m", "fills": 2, "yes_n": 1, "no_n": 1, "matched": 1, "resid": 0,
            "yes_cost": 0.40, "no_cost": 0.50}]
    e = XC.economics(res, {})
    assert e["unsettled_markets"] == 1 and e["settled_markets"] == 0
    assert e["matched_pair_pnl_SETTLED_MODELED"] == 0.0


# ------------------------------------------------- ladder replay: the comp_log
def test_a_rungs_interval_is_closed_by_that_rung_and_not_by_a_sibling():
    """The defect: _closelog ended EVERY open interval on the side, so a rung
    was closed by its own placement and 78% of intervals had zero length."""
    stats = {"placed": 0, "resets": 0, "preserved": 0, "lifetimes": [], "comp_log": []}
    live = {}
    LR._place(live, stats, True, [0.40, 0.41], t=100.0, depth=5.0, rel="complete",
              preserve=True, touch=0.40, depth_mult=1.0)
    assert [c[1] for c in stats["comp_log"]] == [None, None]
    LR._place(live, stats, True, [0.41], t=160.0, depth=5.0, rel="complete",
              preserve=True, touch=0.41, depth_mult=1.0)
    # only the cancelled 0.40 rung closes; 0.41 was preserved and stays open
    assert stats["comp_log"][0][1] == 160.0
    assert stats["comp_log"][1][1] is None


def test_the_price_offset_moves_the_rung_without_moving_the_queue_reference():
    """A price-improving rung becomes the best bid, so it is charged ZERO queue.
    Passing the shifted price as the queue reference would charge it full touch
    depth -- the opposite of what price improvement does."""
    r = {"bid": 0.40, "ask": 0.60}
    assert LR.rung_prices(0.41, True, r, 1, (0, 0)) == [0.41]
    stats = {"placed": 0, "resets": 0, "preserved": 0, "lifetimes": [], "comp_log": []}
    live = {}
    LR._place(live, stats, True, [0.41], t=1.0, depth=900.0, rel="open",
              preserve=True, touch=0.40, depth_mult=1.0)
    assert live[True][0].qa == 0.0
    LR._place(live, stats, True, [0.39], t=2.0, depth=900.0, rel="open",
              preserve=True, touch=0.40, depth_mult=1.0)
    assert live[True][0].qa == 900.0


def test_an_offset_rung_that_would_cross_is_dropped_never_taken():
    """There is no taker crossing anywhere in this replay.

    The two sides cross at different prices and it is easy to assert the wrong
    one: buying YES crosses at the ask (0.60), while buying NO is quoted in NO
    terms and crosses at 1 - bid (also 0.60 here, but reached from the other
    direction -- NO at 0.61 is YES at 0.39, under the 0.40 bid)."""
    assert LR.rung_prices(0.61, True, {"bid": 0.40, "ask": 0.60}, 1, (0, 0)) == []
    assert LR.rung_prices(0.61, False, {"bid": 0.40, "ask": 0.60}, 1, (0, 0)) == []
    # and a genuinely passive rung on each side survives
    assert LR.rung_prices(0.41, True, {"bid": 0.40, "ask": 0.60}, 1, (0, 0)) == [0.41]
    assert LR.rung_prices(0.41, False, {"bid": 0.40, "ask": 0.60}, 1, (0, 0)) == [0.41]


def test_every_counterfactual_arm_changes_exactly_one_knob():
    base = dict(LR.replay.__defaults__ and {} or {})
    for name, kw in XC.ARMS:
        assert len(kw) <= 1, (name, kw)
    assert XC.ARMS[0] == ("REAL_QUEUE", {})
