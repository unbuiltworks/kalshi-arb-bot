from decimal import Decimal

from src.mlb_prop_episode_tracker import advance_episodes, summarize_events


def _row(profit="0.04", qty=4, px="0.45"):
    return {
        "family": "baseball_player_hits",
        "game_date": "2026-08-17",
        "player": "Example Player",
        "threshold": "1",
        "poly_slug": "poly-game-1-hits-example-gte1",
        "kalshi_ticker": "KXMLBHIT-26AUG171900AAA-BBBPLAYER-1",
        "book_comparable": True,
        "settlement_equivalent_now": True,
        "fetch_error": None,
        "quote_age_ms": 120,
        "quote_skew_ms": 35,
        "probe_1usd": None,
        "probe_5usd": {
            "direction": "POLY_YES_KALSHI_NO",
            "quantity": qty,
            "poly_price": px,
            "kalshi_price": "0.50",
            "poly_fee": "0.01",
            "kalshi_fee_upper_bound": "0.01",
            "total_cost_upper_bound": str(Decimal(qty) - Decimal(profit)),
            "conservative_locked_profit": profit,
        },
    }


def test_open_resting_move_close_lifecycle_without_double_count():
    snap = {"paper_probe_candidates": [_row()]}
    events, state = advance_episodes(snap, prior_state={}, observed_ms=1000)
    assert [e["event"] for e in events] == ["OPEN"]
    assert events[0]["quote_skew_ms"] == 35

    events2, state2 = advance_episodes(snap, prior_state=state, observed_ms=1200)
    assert events2 == []

    moved = {"paper_probe_candidates": [_row(profit="0.05", qty=5)]}
    events3, state3 = advance_episodes(moved, prior_state=state2, observed_ms=1400)
    assert [e["event"] for e in events3] == ["MOVE"]
    assert events3[0]["duration_ms"] == 400

    events4, state4 = advance_episodes({"paper_probe_candidates": []}, prior_state=state3, observed_ms=1800)
    assert state4 == {}
    assert [e["event"] for e in events4] == ["CLOSE"]
    assert events4[0]["duration_ms"] == 400
    assert summarize_events(events4)["close"] == 1


def test_below_three_cent_plan_never_opens():
    snap = {"paper_probe_candidates": [_row(profit="0.02")]}
    events, state = advance_episodes(snap, prior_state={}, observed_ms=1000, min_profit=Decimal("0.03"))
    assert events == []
    assert state == {}


def test_fail_closed_without_participation_equivalence():
    row = _row()
    row["settlement_equivalent_now"] = False
    events, state = advance_episodes({"paper_probe_candidates": [row]}, prior_state={}, observed_ms=1000)
    assert events == []
    assert state == {}
