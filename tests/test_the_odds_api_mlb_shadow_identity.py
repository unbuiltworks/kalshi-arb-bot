from datetime import datetime, timezone
from decimal import Decimal

from src.the_odds_api_mlb_shadow import (
    PREGAME_SAFETY_BUFFER_MS,
    pregame_sportsbook_events,
    sportsbook_quotes,
)
from src.universal_sports_identity import RuleAudit, SportsContractIdentity, SportsEventIdentity

D = Decimal


def _rule() -> RuleAudit:
    return RuleAudit(
        official_source="test",
        overtime="test",
        postponement="test",
        stat_corrections="test",
        push_tie="test",
        complete=False,
    )


def _direct_key(*, family: str, subject_type: str, subject: str, stat: str, comparator: str, line):
    start = datetime(2026, 8, 18, 23, 5, tzinfo=timezone.utc)
    event = SportsEventIdentity(
        sport="baseball",
        league="mlb",
        participants=("PHI", "NYM"),
        scheduled_start_ms=int(start.timestamp() * 1000),
    )
    return SportsContractIdentity(
        venue="kalshi",
        market_id="direct-test",
        event=event,
        market_family=family,
        scope="full_contest",
        subject_type=subject_type,
        subject=subject,
        stat=stat,
        comparator=comparator,
        line=line,
        rule_audit=_rule(),
    ).contract_key()


def _sportsbook_event(commence_time: str = "2026-08-18T23:05:00Z"):
    return {
        "id": "odds-test",
        "home_team": "Philadelphia Phillies",
        "away_team": "New York Mets",
        "commence_time": commence_time,
        "bookmakers": [{
            "key": "fanduel",
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Philadelphia Phillies", "price": 1.80},
                    {"name": "New York Mets", "price": 2.10},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "Philadelphia Phillies", "point": -1.5, "price": 2.20},
                    {"name": "New York Mets", "point": 1.5, "price": 1.70},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "point": 8.5, "price": 1.91},
                    {"name": "Under", "point": 8.5, "price": 1.91},
                ]},
            ],
        }],
    }


def test_sportsbook_moneyline_spread_and_total_keys_match_direct_schema():
    events = [_sportsbook_event()]
    # Deliberately far before the 2026 event so the pregame-only gate is open.
    quotes = sportsbook_quotes(events, received_ms=1234567890)
    keys = {q.settlement_key for q in quotes}

    assert _direct_key(
        family="moneyline", subject_type="team", subject="PHI",
        stat="", comparator="YES", line=None,
    ) in keys
    assert _direct_key(
        family="spread", subject_type="team", subject="PHI",
        stat="runs", comparator="GT", line=D("1.5"),
    ) in keys
    assert _direct_key(
        family="game_total", subject_type="contest", subject="contest",
        stat="runs", comparator="GT", line=D("8.5"),
    ) in keys


def test_started_sportsbook_event_emits_zero_quotes():
    start_ms = int(datetime(2026, 8, 18, 23, 5, tzinfo=timezone.utc).timestamp() * 1000)
    quotes = sportsbook_quotes([_sportsbook_event()], received_ms=start_ms + 1)
    assert quotes == []


def test_event_inside_safety_buffer_emits_zero_quotes():
    start_ms = int(datetime(2026, 8, 18, 23, 5, tzinfo=timezone.utc).timestamp() * 1000)
    received_ms = start_ms - (PREGAME_SAFETY_BUFFER_MS - 1)
    quotes = sportsbook_quotes([_sportsbook_event()], received_ms=received_ms)
    assert quotes == []


def test_event_outside_safety_buffer_remains_priceable():
    start_ms = int(datetime(2026, 8, 18, 23, 5, tzinfo=timezone.utc).timestamp() * 1000)
    received_ms = start_ms - PREGAME_SAFETY_BUFFER_MS - 1
    quotes = sportsbook_quotes([_sportsbook_event()], received_ms=received_ms)
    assert quotes


def test_timing_filter_reports_started_and_invalid_events_fail_closed():
    start_ms = int(datetime(2026, 8, 18, 23, 5, tzinfo=timezone.utc).timestamp() * 1000)
    rows, stats = pregame_sportsbook_events(
        [
            _sportsbook_event("2026-08-18T23:05:00Z"),
            _sportsbook_event("2026-08-18T23:06:00Z"),
            _sportsbook_event("not-a-date"),
        ],
        asof_ms=start_ms,
    )
    assert len(rows) == 1
    assert rows[0]["commence_time"] == "2026-08-18T23:06:00Z"
    assert stats == {
        "input_events": 3,
        "pregame_events": 1,
        "started_or_near_start_filtered": 1,
        "invalid_start_filtered": 1,
        "safety_buffer_ms": PREGAME_SAFETY_BUFFER_MS,
    }
