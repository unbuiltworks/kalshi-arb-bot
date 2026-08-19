from src.mlb_prop_decided_pair_shadow_v2 import PairEpisodeTracker, strict_pair_identity


def _row(*, direction="POLY_YES_KALSHI_NO", profit="0.05", synchronized=True):
    return {
        "family": "baseball_player_hits",
        "poly_slug": "astatc-mlb-phi-nyy-2026-08-19-hits-bryceharper-gte2",
        "kalshi_ticker": "KXMLBHIT-26AUG19PHINYY-HARPER2",
        "synchronized_book_pair": synchronized,
        "cross_venue_q1_pair_lock": {
            "direction": direction,
            "quantity": 1,
            "total_cost": "0.95",
            "profit": profit,
            "poly_price": "0.40",
            "kalshi_price": "0.55",
            "poly_depth": "10",
            "kalshi_depth": "12",
        },
    }


def test_strict_pair_identity_requires_exact_market_and_direction():
    row = _row()
    assert strict_pair_identity(row) == (
        "baseball_player_hits|"
        "astatc-mlb-phi-nyy-2026-08-19-hits-bryceharper-gte2|"
        "KXMLBHIT-26AUG19PHINYY-HARPER2|POLY_YES_KALSHI_NO"
    )
    row["poly_slug"] = ""
    assert strict_pair_identity(row) is None


def test_episode_tracker_resting_depth_does_not_reopen():
    tracker = PairEpisodeTracker()
    first, closes = tracker.transition([_row()], 1_000)
    assert closes == []
    assert first[0]["pair_episode"]["event"] == "OPEN"

    second, closes = tracker.transition([_row()], 1_100)
    assert closes == []
    assert second[0]["pair_episode"]["event"] == "RESTING"
    assert second[0]["pair_episode"]["opened_ms"] == 1_000


def test_episode_tracker_price_change_is_move_not_new_open():
    tracker = PairEpisodeTracker()
    tracker.transition([_row(profit="0.05")], 1_000)
    moved, closes = tracker.transition([_row(profit="0.06")], 1_200)
    assert closes == []
    assert moved[0]["pair_episode"]["event"] == "MOVE"
    assert moved[0]["pair_episode"]["opened_ms"] == 1_000


def test_episode_tracker_close_is_interval_censored_and_reopen_is_new_episode():
    tracker = PairEpisodeTracker()
    tracker.transition([_row()], 1_000)
    tracker.transition([_row()], 1_100)
    current, closes = tracker.transition([], 1_300)
    assert current == []
    assert len(closes) == 1
    episode = closes[0]["pair_episode"]
    assert episode["event"] == "CLOSE"
    assert episode["duration_lower_bound_ms"] == 100
    assert episode["duration_upper_bound_ms"] == 300
    assert episode["interval_censored"] is True

    reopened, closes = tracker.transition([_row()], 1_500)
    assert closes == []
    assert reopened[0]["pair_episode"]["event"] == "OPEN"
    assert reopened[0]["pair_episode"]["opened_ms"] == 1_500


def test_opposite_directions_are_distinct_episode_identities():
    tracker = PairEpisodeTracker()
    rows, closes = tracker.transition(
        [_row(direction="POLY_YES_KALSHI_NO"), _row(direction="KALSHI_YES_POLY_NO")],
        2_000,
    )
    assert closes == []
    assert len(rows) == 2
    assert {row["pair_episode"]["event"] for row in rows} == {"OPEN"}
    assert len({row["pair_episode"]["identity"] for row in rows}) == 2
