from decimal import Decimal
from types import SimpleNamespace

from src.gemini_kalshi_mlb_hits_final_lag_shadow import _game_teams, _winner_edge

D = Decimal


def test_synthetic_game_slug_team_tuple():
    assert _game_teams("mlb-det-pit-2026-08-19") == ("det", "pit")
    assert _game_teams("bad-slug") is None


def test_known_yes_winner_uses_fee_inclusive_quote():
    priced = SimpleNamespace(
        quote=SimpleNamespace(
            venue="gemini", market_id="GEMI-X", yes_cost=D("0.91"), no_cost=D("0.13"),
            yes_depth=D("1"), no_depth=D("1"),
        ),
        raw_yes_ask=D("0.90"), raw_no_ask=D("0.12"),
        yes_fee=D("0.01"), no_fee=D("0.01"),
    )
    row = _winner_edge(priced, "YES")
    assert row is not None
    assert row["all_in_cost"] == "0.91"
    assert row["modeled_known_winner_profit"] == "0.09"


def test_known_no_winner_uses_complement_side():
    priced = SimpleNamespace(
        quote=SimpleNamespace(
            venue="kalshi", market_id="K-X", yes_cost=D("0.85"), no_cost=D("0.21"),
            yes_depth=D("1"), no_depth=D("1"),
        ),
        raw_yes_ask=D("0.84"), raw_no_ask=D("0.20"),
        yes_fee=D("0.01"), no_fee=D("0.01"),
    )
    row = _winner_edge(priced, "NO")
    assert row is not None
    assert row["all_in_cost"] == "0.21"
    assert row["modeled_known_winner_profit"] == "0.79"


def test_no_signal_when_fee_inclusive_cost_is_one():
    priced = SimpleNamespace(
        quote=SimpleNamespace(
            venue="gemini", market_id="G-X", yes_cost=D("1.00"), no_cost=D("1.00"),
            yes_depth=D("1"), no_depth=D("1"),
        ),
        raw_yes_ask=D("0.99"), raw_no_ask=D("0.99"),
        yes_fee=D("0.01"), no_fee=D("0.01"),
    )
    assert _winner_edge(priced, "YES") is None
