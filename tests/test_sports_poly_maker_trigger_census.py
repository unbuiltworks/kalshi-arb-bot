from decimal import Decimal

from src.sports_poly_current_fees import maker_rebate, maker_then_taker_locked_profit, taker_fee
from src.sports_poly_maker_trigger_census import _score_maker_pair
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def _book(yes_ask: str, yes_depth: str, no_ask: str, no_depth: str) -> TopBook:
    return TopBook(D(yes_ask), D(yes_depth), D(no_ask), D(no_depth))


def test_current_polymarket_fee_bankers_rounding_q1():
    assert taker_fee(1, D("0.50")) == D("0.02")  # exact 0.015 -> half-even 0.02
    assert taker_fee(1, D("0.20")) == D("0.01")
    assert maker_rebate(1, D("0.50")) == D("0.00")
    assert maker_rebate(100, D("0.50")) == D("0.31")


def test_maker_then_taker_locked_profit_includes_rebate_credit():
    row = maker_then_taker_locked_profit(maker_price=D("0.20"), taker_price=D("0.73"))
    assert row["taker_fee"] == D("0.01")
    assert row["maker_rebate"] == D("0.00")
    assert row["all_in_cost"] == D("0.94")
    assert row["locked_profit"] == D("0.06")


def test_maker_trigger_scores_both_nested_directions_and_sorts_best_first():
    # Desired low=YES: yes bid is 1-no_ask = .20, ask .22.
    # Desired high=NO: no ask .73, no bid is 1-yes_ask = .70.
    low = _book("0.22", "10", "0.80", "20")
    high = _book("0.30", "20", "0.73", "10")
    rows = _score_maker_pair(low_book=low, low_buy_yes=True, high_book=high, high_buy_yes=False)
    assert len(rows) == 2
    by_direction = {row["direction"]: row for row in rows}
    low_maker = by_direction["MAKER_LOW_HEDGE_HIGH"]
    assert low_maker["maker_price_current_best_bid"] == "0.20"
    assert low_maker["hedge_ask"] == "0.73"
    assert D(low_maker["locked_profit_after_hypothetical_maker_fill"]) == D("0.06")
    assert low_maker["classification"] == "MAKER_TRIGGER_CANDIDATE"

    best = rows[0]
    assert best["direction"] == "MAKER_HIGH_HEDGE_LOW"
    assert best["maker_price_current_best_bid"] == "0.70"
    assert best["hedge_ask"] == "0.22"
    assert D(best["locked_profit_after_hypothetical_maker_fill"]) == D("0.07")


def test_maker_trigger_requires_q1_hedge_depth():
    low = _book("0.22", "10", "0.80", "20")
    high = _book("0.30", "20", "0.73", "0.5")
    rows = _score_maker_pair(low_book=low, low_buy_yes=True, high_book=high, high_buy_yes=False)
    # maker-low/hedge-high is removed because high NO depth <1; reverse may remain.
    assert all(row["direction"] != "MAKER_LOW_HEDGE_HIGH" for row in rows)
