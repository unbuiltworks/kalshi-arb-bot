"""is_moneyline: the venue's real labels (catalogue 28 Nov). Regression for
PR #253 commit 9408c40, which refused 'basketball_team_full_game_winner'."""
import pytest

from src import pmus_sports_sharp as SH

ML = "SPORTS_MARKET_TYPE_MONEYLINE"


@pytest.mark.parametrize("t,v2", [
    ("basketball_team_full_game_winner", ML),
    ("hockey_team_full_game_winner", ML),
    ("football_team_full_game_winner", ML),
    ("tennis_match_winner", ML),
    ("table_tennis_match_winner", ML),
    ("moneyline", None), ("", None)])
def test_moneylines_accepted(t, v2):
    assert SH.is_moneyline({"sportsMarketType": t, "sportsMarketTypeV2": v2})


@pytest.mark.parametrize("t,v2", [
    ("basketball_team_full_game_spread", "SPORTS_MARKET_TYPE_SPREAD"),
    ("basketball_team_full_game_total", "SPORTS_MARKET_TYPE_TOTAL"),
    ("football_team_first_half_winner", "SPORTS_MARKET_TYPE_PROP"),
    ("hockey_team_regulation_winner", "SPORTS_MARKET_TYPE_PROP"),
    ("table_tennis_set_1_winner", "SPORTS_MARKET_TYPE_PROP"),
    ("soccer_team_full_time_winner", "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"),
    ("basketball_player_points", "SPORTS_MARKET_TYPE_PROP"),
    ("football_team_first_half_winner", None),
    ("futures", "SPORTS_MARKET_TYPE_FUTURE")])
def test_everything_else_refused(t, v2):
    assert not SH.is_moneyline({"sportsMarketType": t,
                                "sportsMarketTypeV2": v2})
