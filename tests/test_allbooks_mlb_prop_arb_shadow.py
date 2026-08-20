from decimal import Decimal

import src.allbooks_mlb_prop_arb_shadow as arb

D = Decimal


def _row(book, side, odds, *, threshold=D("7"), player_key="georgekirby", family="baseball_player_strikeouts"):
    return {
        "book": book, "side": side, "decimal_odds": D(str(odds)),
        "teams": ("MIL", "SEA"), "start_ms": 1787249460000,
        "family": family, "stat": "strikeouts", "player": "George Kirby",
        "player_key": player_key, "threshold": threshold,
        "quote_ms": 1000, "market_key": "pitcher_strikeouts_alternate",
    }


def test_exact_two_book_positive_plan_is_cent_rounded_and_locked_both_ways():
    a = _row("draftkings", "YES", "2.10")
    b = _row("betrivers", "NO", "2.10")
    plan = arb.best_two_book_plan(a, b)
    assert plan is not None
    assert D(plan["shadow_locked_profit"]) > 0
    total = D(plan["stake_a"]) + D(plan["stake_b"])
    assert total <= D("5.00")
    assert D(plan["payout_if_a"]) - total > 0
    assert D(plan["payout_if_b"]) - total > 0
    assert D(plan["stake_a"]).as_tuple().exponent >= -2
    assert D(plan["stake_b"]).as_tuple().exponent >= -2


def test_same_book_or_same_side_rejected():
    assert arb.best_two_book_plan(_row("draftkings", "YES", "2.2"), _row("draftkings", "NO", "2.2")) is None
    assert arb.best_two_book_plan(_row("draftkings", "YES", "2.2"), _row("betrivers", "YES", "2.2")) is None


def test_player_threshold_family_mismatch_fail_closed():
    base = _row("draftkings", "YES", "2.2")
    assert arb.best_two_book_plan(base, _row("betrivers", "NO", "2.2", threshold=D("8"))) is None
    assert arb.best_two_book_plan(base, _row("betrivers", "NO", "2.2", player_key="other")) is None
    assert arb.best_two_book_plan(base, _row("betrivers", "NO", "2.2", family="baseball_player_hits")) is None


def test_no_arbitrage_when_inverse_odds_sum_not_below_one():
    assert arb.best_two_book_plan(_row("draftkings", "YES", "1.90"), _row("betrivers", "NO", "1.90")) is None
