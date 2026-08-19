from dataclasses import replace

from src.mlb_terminal_safe_state import final_games_from_schedule
from src.mlb_terminal_total_shadow import TerminalContract
from src.mlb_terminal_total_shadow_v2 import (
    RetainedContract,
    game_start_by_pk,
    match_final_game_by_start,
    retain_contracts,
)


KEY = "baseball|mlb|ath,kc|game_total|full_contest|contest|contest|runs|GT|9.5"


def _contract(start_ms=1_000_000):
    return TerminalContract(
        key=KEY,
        sides={},
        game_date_et="2026-08-18",
        scheduled_start_ms=start_ms,
        kalshi_fee_multiplier=None,
    )


def _schedule(game_pk=777, game_date="2026-08-18T23:40:00Z", away_score=4, home_score=6):
    return {
        "dates": [{
            "date": "2026-08-18",
            "games": [{
                "gamePk": game_pk,
                "gameDate": game_date,
                "officialDate": "2026-08-18",
                "status": {
                    "abstractGameState": "Final",
                    "detailedState": "Final",
                    "codedGameState": "F",
                },
                "teams": {
                    "away": {"score": away_score, "team": {"name": "Athletics", "abbreviation": "ATH"}},
                    "home": {"score": home_score, "team": {"name": "Kansas City Royals", "abbreviation": "KC"}},
                },
            }],
        }],
    }


def test_retention_keeps_missing_contract_until_ttl_then_expires():
    contract = _contract()
    cache = retain_contracts({}, {KEY: contract}, now_ms=1_000, retention_ms=10_000)
    assert cache[KEY].last_seen_ms == 1_000
    kept = retain_contracts(cache, {}, now_ms=10_999, retention_ms=10_000)
    assert KEY in kept
    expired = retain_contracts(cache, {}, now_ms=11_001, retention_ms=10_000)
    assert KEY not in expired


def test_fresh_discovery_refreshes_retention_clock():
    contract = _contract()
    cache = {KEY: RetainedContract(contract=contract, last_seen_ms=1_000)}
    refreshed = retain_contracts(cache, {KEY: contract}, now_ms=20_000, retention_ms=1_000)
    assert KEY in refreshed
    assert refreshed[KEY].last_seen_ms == 20_000


def test_game_start_by_pk_reads_mlb_game_date():
    payload = _schedule()
    starts = game_start_by_pk(payload)
    assert starts[777] == 1787096400000


def test_doubleheader_matching_uses_closest_scheduled_start():
    first_payload = _schedule(game_pk=777, game_date="2026-08-18T17:10:00Z")
    second_payload = _schedule(game_pk=778, game_date="2026-08-18T23:40:00Z")
    first = final_games_from_schedule(first_payload)[0]
    second = final_games_from_schedule(second_payload)[0]
    starts = {**game_start_by_pk(first_payload), **game_start_by_pk(second_payload)}
    target = starts[778] + 2 * 60 * 1000
    matched = match_final_game_by_start([first, second], starts, KEY, target, tolerance_ms=20 * 60 * 1000)
    assert matched is not None
    assert matched.game_pk == 778


def test_doubleheader_matching_fails_closed_without_start_metadata():
    finals = final_games_from_schedule(_schedule())
    assert match_final_game_by_start(finals, {}, KEY, 1_000, tolerance_ms=20 * 60 * 1000) is None


def test_equal_distance_doubleheader_tie_fails_closed():
    a_payload = _schedule(game_pk=777, game_date="2026-08-18T20:00:00Z")
    b_payload = _schedule(game_pk=778, game_date="2026-08-18T20:20:00Z")
    finals = final_games_from_schedule(a_payload) + final_games_from_schedule(b_payload)
    starts = {**game_start_by_pk(a_payload), **game_start_by_pk(b_payload)}
    midpoint = (starts[777] + starts[778]) // 2
    assert match_final_game_by_start(finals, starts, KEY, midpoint, tolerance_ms=20 * 60 * 1000) is None
