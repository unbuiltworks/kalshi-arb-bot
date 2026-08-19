from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_mlb_hits_final_lag_shadow import _game_teams, _winner_edge

D = Decimal


def _book(*, bid="0.12", bid_size="1", ask="0.90", ask_size="1") -> TopOfBook:
    return TopOfBook(
        venue="test",
        market_id="X",
        ts_ms=123,
        yes_bid=D(bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(ask),
        yes_ask_size=D(ask_size),
    )


def test_synthetic_game_slug_team_tuple():
    assert _game_teams("mlb-det-pit-2026-08-19") == ("det", "pit")
    assert _game_teams("bad-slug") is None


def test_known_yes_gemini_winner_uses_fee_inclusive_quote():
    row = _winner_edge(
        venue="gemini", market_id="GEMI-X", book=_book(ask="0.90"), outcome="YES"
    )
    assert row is not None
    # Gemini q=1 taker fee at 90c rounds up to 1c.
    assert row["all_in_cost"] == "0.91"
    assert row["modeled_known_winner_profit"] == "0.09"


def test_known_no_gemini_winner_uses_complement_bid():
    row = _winner_edge(
        venue="gemini", market_id="GEMI-X", book=_book(bid="0.80"), outcome="NO"
    )
    assert row is not None
    assert row["raw_winner_ask"] == "0.20"
    assert row["fee_upper_bound"] == "0.02"
    assert row["all_in_cost"] == "0.22"
    assert row["modeled_known_winner_profit"] == "0.78"


def test_known_no_kalshi_winner_uses_symmetric_fee_math():
    row = _winner_edge(
        venue="kalshi",
        market_id="K-X",
        book=_book(bid="0.80"),
        outcome="NO",
        fee_multiplier=D("1"),
    )
    assert row is not None
    assert row["raw_winner_ask"] == "0.20"
    assert row["fee_upper_bound"] == "0.02"
    assert row["all_in_cost"] == "0.22"


def test_no_signal_without_one_contract_depth():
    assert _winner_edge(
        venue="gemini",
        market_id="G-X",
        book=_book(ask_size="0.5"),
        outcome="YES",
    ) is None


def test_no_signal_if_winner_ask_is_at_boundary():
    assert _winner_edge(
        venue="gemini",
        market_id="G-X",
        book=_book(ask="1"),
        outcome="YES",
    ) is None
