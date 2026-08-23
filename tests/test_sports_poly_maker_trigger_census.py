from decimal import Decimal

from src.sports_poly_current_fees import maker_rebate, maker_then_taker_locked_profit, taker_fee
from src.sports_poly_maker_trigger_census import _score_maker_pair, _total_scope
from src.strict_mlb_prop_snapshot import TopBook

D = Decimal


def _book(yes_ask: str, yes_depth: str, no_ask: str, no_depth: str) -> TopBook:
    return TopBook(D(yes_ask), D(yes_depth), D(no_ask), D(no_depth))


def test_current_polymarket_fee_bankers_rounding_q1():
    assert taker_fee(1, D("0.50")) == D("0.01")
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
    low = _book("0.22", "10", "0.80", "20")
    high = _book("0.30", "20", "0.73", "10")
    rows = _score_maker_pair(low_book=low, low_buy_yes=True, high_book=high, high_buy_yes=False)
    assert len(rows) == 2
    by_direction = {row["direction"]: row for row in rows}
    assert D(by_direction["MAKER_LOW_HEDGE_HIGH"]["locked_profit_after_hypothetical_maker_fill"]) == D("0.06")
    assert rows[0]["direction"] == "MAKER_HIGH_HEDGE_LOW"
    assert D(rows[0]["locked_profit_after_hypothetical_maker_fill"]) == D("0.07")


def test_maker_trigger_requires_q1_hedge_depth():
    low = _book("0.22", "10", "0.80", "20")
    high = _book("0.30", "20", "0.73", "0.5")
    rows = _score_maker_pair(low_book=low, low_buy_yes=True, high_book=high, high_buy_yes=False)
    assert all(row["direction"] != "MAKER_LOW_HEDGE_HIGH" for row in rows)


def test_total_scope_never_mixes_event_and_team_total():
    event_total = {"sportsMarketType": "full_game_total", "title": "Over 37.5 total points", "slug": "tsc-nfl-sea-ten-2026-08-23-total-37pt5"}
    sea_total = {"sportsMarketType": "full_game_total", "title": "Seattle Seahawks over 23.5 total points", "slug": "tsc-nfl-sea-ten-2026-08-23-tt-sea-23pt5"}
    ten_total = {"sportsMarketType": "full_game_total", "title": "Tennessee Titans over 21.5 total points", "slug": "tsc-nfl-sea-ten-2026-08-23-tt-ten-21pt5"}
    assert _total_scope(event_total) == ("FULL_GAME_TOTAL", "EVENT")
    assert _total_scope(sea_total) == ("TEAM_TOTAL", "sea")
    assert _total_scope(ten_total) == ("TEAM_TOTAL", "ten")
