from src.mlb_live_participation import candidate_game, participation_proof


def _games(*, batters_faced=1, pitches=1):
    return {
        ("2026-08-18", ("nym", "phi")): {
            "gamePk": 123,
            "status": "Live",
            "boxscore": {
                "teams": {
                    "away": {
                        "pitchers": [55],
                        "players": {
                            "ID10": {
                                "person": {"id": 10, "fullName": "Bryce Harper"},
                                "battingOrder": "300",
                                "gameStatus": {"isSubstitute": False},
                                "stats": {"batting": {"plateAppearances": 1}},
                            }
                        },
                    },
                    "home": {
                        "pitchers": [55],
                        "players": {
                            "ID55": {
                                "person": {"id": 55, "fullName": "Pitcher Example"},
                                "stats": {
                                    "pitching": {
                                        "pitchesThrown": pitches,
                                        "battersFaced": batters_faced,
                                    }
                                },
                            }
                        },
                    },
                }
            },
        }
    }


def test_candidate_game_parses_polymarket_mlb_slug():
    assert candidate_game("mlb-phi-nym-2026-08-18-bryce-harper-hits") == (
        "2026-08-18", ("nym", "phi")
    )


def test_batter_live_proof_requires_start_and_plate_appearance():
    proof = participation_proof(
        family="baseball_player_hits",
        player_name="Bryce Harper",
        poly_slug="mlb-phi-nym-2026-08-18-bryce-harper-hits",
        games=_games(),
    )
    assert proof.started is True
    assert proof.plate_appearance_recorded is True
    assert proof.source == "MLB_STATS_API_GAME_123"


def test_pitcher_live_proof_requires_starting_pitcher_and_batter_faced():
    proof = participation_proof(
        family="baseball_player_strikeouts",
        player_name="Pitcher Example",
        poly_slug="mlb-phi-nym-2026-08-18-pitcher-example-strikeouts",
        games=_games(),
    )
    assert proof.starting_pitcher is True
    assert proof.batter_faced_recorded is True
    assert proof.pitch_recorded is True
    assert proof.source == "MLB_STATS_API_GAME_123"


def test_one_pitch_without_batter_faced_does_not_prove_current_kalshi_gate():
    proof = participation_proof(
        family="baseball_player_strikeouts",
        player_name="Pitcher Example",
        poly_slug="mlb-phi-nym-2026-08-18-pitcher-example-strikeouts",
        games=_games(batters_faced=0, pitches=1),
    )
    assert proof.starting_pitcher is True
    assert proof.pitch_recorded is True
    assert proof.batter_faced_recorded is False
