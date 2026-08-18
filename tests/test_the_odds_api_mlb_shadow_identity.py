from datetime import datetime, timezone
from decimal import Decimal

from src.the_odds_api_mlb_shadow import sportsbook_quotes
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


def test_sportsbook_moneyline_spread_and_total_keys_match_direct_schema():
    events = [{
        "id": "odds-test",
        "home_team": "Philadelphia Phillies",
        "away_team": "New York Mets",
        "commence_time": "2026-08-18T23:05:00Z",
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
    }]

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
