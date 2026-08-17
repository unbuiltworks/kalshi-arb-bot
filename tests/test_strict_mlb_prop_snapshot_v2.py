from src import strict_mlb_prop_adapters as adapters
from src.strict_mlb_prop_snapshot_v2 import _player_prop_rows


def _event(market: dict) -> dict:
    return {
        "slug": "mlb-stl-cin-2026-08-17",
        "title": "St. Louis Cardinals vs. Cincinnati Reds",
        "startTime": "2026-08-17T17:40:00Z",
        "markets": [market],
    }


def _polanco_hits() -> dict:
    return {
        "slug": "astatc-mlb-stl-cin-2026-08-17-hits-jorpol-gte2",
        "question": "Will Jorge Polanco record at least 2 Hits in STL vs CIN?",
        "title": "Jorge Polanco: 2+ Hits | STL-CIN",
        "line": "2",
    }


def test_search_row_without_type_metadata_is_strictly_normalized() -> None:
    rows = _player_prop_rows(_event(_polanco_hits()))
    assert len(rows) == 1
    row = rows[0]
    assert row["sportsMarketTypeV2"] == "SPORTS_MARKET_TYPE_PROP"
    assert row["eventStartTime"] == "2026-08-17T17:40:00Z"
    identity = adapters._poly_identity(row)
    assert identity is not None
    family, player, teams, game_date, threshold = identity
    assert family == "baseball_player_hits"
    assert player == "Jorge Polanco"
    assert set(teams) == {"stl", "cin"}
    assert game_date == "2026-08-17"
    assert str(threshold) == "2"


def test_missing_type_metadata_still_rejects_slug_text_family_disagreement() -> None:
    market = _polanco_hits()
    market["title"] = "Jorge Polanco: 2+ Total Bases?"
    market["question"] = "Jorge Polanco: 2+ Total Bases?"
    assert _player_prop_rows(_event(market)) == []


def test_missing_type_metadata_still_rejects_threshold_disagreement() -> None:
    market = _polanco_hits()
    market["line"] = "3"
    assert _player_prop_rows(_event(market)) == []


def test_generic_game_market_is_not_promoted_to_prop() -> None:
    market = {
        "slug": "mlb-stl-cin-2026-08-17",
        "question": "Will the Cardinals beat the Reds?",
        "title": "Cardinals vs Reds",
        "line": "0",
    }
    assert _player_prop_rows(_event(market)) == []


def test_explicit_conflicting_type_is_not_overwritten() -> None:
    market = _polanco_hits()
    market["sportsMarketType"] = "baseball_moneyline"
    assert _player_prop_rows(_event(market)) == []


def test_explicit_non_prop_v2_type_is_not_overwritten() -> None:
    market = _polanco_hits()
    market["sportsMarketTypeV2"] = "SPORTS_MARKET_TYPE_MONEYLINE"
    assert _player_prop_rows(_event(market)) == []
