from decimal import Decimal

from src.kalshi_nested_strike_shadow import (
    MarketLeg,
    evaluate_depth_pair,
    guaranteed_payout_per_pair,
    opposite_ask_ladder,
    sweep_ladder,
)

D = Decimal


def _leg(ticker: str, strike: str) -> MarketLeg:
    return MarketLeg(
        ticker=ticker,
        event_ticker="KXBTC-EVENT",
        strike=D(strike),
        strike_type="greater",
        title=ticker,
        yes_ask=None,
        no_ask=None,
        close_time="",
        rules_primary="same",
        rules_secondary="same",
    )


def test_yes_lower_plus_no_higher_never_pays_less_than_one():
    lower, higher = D("60000"), D("61000")
    assert guaranteed_payout_per_pair(lower, higher, D("59000")) == D("1")
    assert guaranteed_payout_per_pair(lower, higher, D("60000")) == D("1")
    assert guaranteed_payout_per_pair(lower, higher, D("60500")) == D("2")
    assert guaranteed_payout_per_pair(lower, higher, D("61000")) == D("2")
    assert guaranteed_payout_per_pair(lower, higher, D("62000")) == D("1")


def test_orderbook_bid_sides_convert_to_opposite_asks():
    book = {
        "orderbook_fp": {
            "yes_dollars": [["0.55", "2.00"], ["0.50", "3.00"]],
            "no_dollars": [["0.40", "4.00"], ["0.35", "5.00"]],
        }
    }
    assert opposite_ask_ladder(book, buy_side="yes") == [
        (D("0.60"), D("4.00")),
        (D("0.65"), D("5.00")),
    ]
    assert opposite_ask_ladder(book, buy_side="no") == [
        (D("0.45"), D("2.00")),
        (D("0.50"), D("3.00")),
    ]


def test_sweep_fails_closed_on_insufficient_depth():
    assert sweep_ladder([(D("0.40"), D("0.5"))], D("1"), fee_multiplier=D("1")) is None


def test_depth_pair_reports_positive_guaranteed_profit_when_cost_survives_fees():
    lower = _leg("LOWER", "60000")
    higher = _leg("HIGHER", "61000")
    # YES(lower) ask = 0.40 from NO bid 0.60.
    lower_book = {"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.60", "5.00"]]}}
    # NO(higher) ask = 0.40 from YES bid 0.60.
    higher_book = {"orderbook_fp": {"yes_dollars": [["0.60", "5.00"]], "no_dollars": []}}

    rows = evaluate_depth_pair(
        lower=lower,
        higher=higher,
        lower_book=lower_book,
        higher_book=higher_book,
        q_max=5,
        fee_multiplier=D("1"),
    )

    assert len(rows) == 5
    assert all(row.worst_payout == row.quantity for row in rows)
    assert all(row.profit > 0 for row in rows)


def test_wrong_strike_order_rejected():
    lower = _leg("LOWER", "61000")
    higher = _leg("HIGHER", "60000")
    try:
        evaluate_depth_pair(
            lower=lower,
            higher=higher,
            lower_book={"orderbook_fp": {}},
            higher_book={"orderbook_fp": {}},
            q_max=1,
            fee_multiplier=D("1"),
        )
    except ValueError as exc:
        assert "lower strike" in str(exc)
    else:
        raise AssertionError("invalid nesting must fail closed")
