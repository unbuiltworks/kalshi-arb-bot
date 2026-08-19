from src.mlb_terminal_safe_state import (
    TerminalFinalTracker,
    final_games_from_schedule,
    match_final_game,
    parse_terminal_total_key,
    terminal_shadow_annotation,
)


KEY_GT = "baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9.5"
KEY_LT = "baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|LT|9.5"


def _schedule(*, abstract="Final", detailed="Final", away_score=4, home_score=6, game_pk=777):
    return {
        "dates": [{
            "date": "2026-08-18",
            "games": [{
                "gamePk": game_pk,
                "officialDate": "2026-08-18",
                "status": {
                    "abstractGameState": abstract,
                    "detailedState": detailed,
                    "codedGameState": "F",
                },
                "teams": {
                    "away": {"score": away_score, "team": {"name": "Athletics", "abbreviation": "ATH"}},
                    "home": {"score": home_score, "team": {"name": "Kansas City Royals", "abbreviation": "KC"}},
                },
            }],
        }],
    }


def test_parser_accepts_only_half_run_full_game_totals():
    spec = parse_terminal_total_key(KEY_GT)
    assert spec.participants == ("ath", "kc")
    assert str(spec.line) == "9.5"
    assert spec.comparator == "GT"


def test_parser_rejects_non_terminal_lane_shapes():
    bad = [
        "baseball|mlb|ath,kc|spread|full_contest|contest|contest|runs|GT|1.5",
        "baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9",
        "baseball|mlb|ath,kc|game_total|first_five|contest|contest|runs|GT|9.5",
    ]
    for key in bad:
        try:
            parse_terminal_total_key(key)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected rejection: {key}")


def test_extracts_only_exact_mlb_final_with_scores():
    finals = final_games_from_schedule(_schedule())
    assert len(finals) == 1
    row = finals[0]
    assert row.game_pk == 777
    assert row.participants == ("ath", "kc")
    assert row.total_runs == 10

    assert final_games_from_schedule(_schedule(abstract="Live", detailed="In Progress")) == []
    assert final_games_from_schedule(_schedule(abstract="Final", detailed="Game Over")) == []


def test_matches_exact_participants_only():
    finals = final_games_from_schedule(_schedule())
    assert match_final_game(finals, KEY_GT) is not None
    other = "baseball|mlb|bos,nyy|game_total|full_contest|contest|contest|runs|GT|9.5"
    assert match_final_game(finals, other) is None


def test_repeated_identical_final_becomes_stable_but_never_execution_eligible():
    final = final_games_from_schedule(_schedule())[0]
    tracker = TerminalFinalTracker(min_stable_ms=60_000, min_observations=3)
    p1 = tracker.observe(KEY_GT, final, now_ms=1_000_000)
    p2 = tracker.observe(KEY_GT, final, now_ms=1_030_000)
    p3 = tracker.observe(KEY_GT, final, now_ms=1_060_000)
    assert not p1.stable
    assert not p2.stable
    assert p3.stable
    assert p3.normalized_winning_outcome == "YES"
    assert p3.final_total_runs == 10
    assert p3.execution_eligible is False
    assert p3.order_entry is False
    annotation = terminal_shadow_annotation(p3)
    assert annotation["terminal_equivalence_candidate"] is True
    assert annotation["approved_for_execution"] is False
    assert annotation["read_only"] is True


def test_lt_key_derives_opposite_winning_outcome():
    final = final_games_from_schedule(_schedule())[0]
    tracker = TerminalFinalTracker(min_stable_ms=0, min_observations=1)
    proof = tracker.observe(KEY_LT, final, now_ms=1_000)
    assert proof.stable
    assert proof.normalized_winning_outcome == "NO"


def test_score_change_resets_stability_clock():
    tracker = TerminalFinalTracker(min_stable_ms=60_000, min_observations=2)
    first = final_games_from_schedule(_schedule(away_score=4, home_score=6))[0]
    corrected = final_games_from_schedule(_schedule(away_score=5, home_score=6))[0]
    tracker.observe(KEY_GT, first, now_ms=1_000_000)
    p2 = tracker.observe(KEY_GT, first, now_ms=1_060_000)
    assert p2.stable
    reset = tracker.observe(KEY_GT, corrected, now_ms=1_061_000)
    assert not reset.stable
    assert reset.observations == 1
    assert reset.stable_ms == 0
    assert reset.final_total_runs == 11


def test_team_or_score_ambiguity_fails_closed():
    payload = _schedule()
    payload["dates"][0]["games"][0]["teams"]["away"]["score"] = None
    assert final_games_from_schedule(payload) == []
