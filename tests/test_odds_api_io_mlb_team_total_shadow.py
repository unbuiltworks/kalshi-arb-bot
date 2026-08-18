from datetime import datetime, timezone

from src.gemini_sports_adapter import normalize_event
from src.odds_api_io_mlb_team_total_shadow import TEAM_TOTAL_MARKETS, _quotes_from_market


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def test_team_total_market_subscription_is_scoped():
    assert TEAM_TOTAL_MARKETS == "Team Total Home,Team Total Away"


def test_home_team_total_half_run_normalizes_to_canonical_quote():
    now = 1787074500000
    quotes = _quotes_from_market(
        event_id="evt-1",
        event={"home": "PHI", "away": "NYM", "date": "2026-08-18T23:00:00Z"},
        book_raw="FanDuel",
        market={
            "name": "Team Total Home",
            "updatedAt": _iso(now),
            "odds": [{"hdp": 4.5, "over": "1.91", "under": "1.91"}],
        },
        received_ms=now,
        freshness_ms=3000,
    )
    assert len(quotes) == 1
    quote = quotes[0]
    assert quote.venue == "sportsbook:fanduel"
    assert quote.market_id.lower() == "evt-1:team_total:phi:4.5"
    assert "|team_total|" in quote.settlement_key
    assert "|team|phi|runs|GT|4.5" in quote.settlement_key
    assert quote.yes_cost > 0
    assert quote.no_cost > 0


def test_away_team_total_maps_to_away_subject():
    now = 1787074500000
    quotes = _quotes_from_market(
        event_id="evt-2",
        event={"home": "PHI", "away": "NYM", "date": "2026-08-18T23:00:00Z"},
        book_raw="DraftKings",
        market={
            "name": "Team Total Away",
            "updatedAt": _iso(now),
            "odds": [{"hdp": 3.5, "over": "2.05", "under": "1.80"}],
        },
        received_ms=now,
        freshness_ms=3000,
    )
    assert len(quotes) == 1
    assert quotes[0].market_id.lower() == "evt-2:team_total:nym:3.5"
    assert "|team|nym|runs|GT|3.5" in quotes[0].settlement_key


def test_integer_team_total_is_rejected_because_sportsbook_can_push():
    now = 1787074500000
    quotes = _quotes_from_market(
        event_id="evt-3",
        event={"home": "PHI", "away": "NYM", "date": "2026-08-18T23:00:00Z"},
        book_raw="FanDuel",
        market={
            "name": "Team Total Home",
            "updatedAt": _iso(now),
            "odds": [{"hdp": 4.0, "over": "1.90", "under": "1.90"}],
        },
        received_ms=now,
        freshness_ms=3000,
    )
    assert quotes == []


def test_gemini_tt_contract_normalizes_as_team_total():
    rows = normalize_event({
        "ticker": "GEMI-MLB-2608182300-NYM-PHI-TT",
        "contracts": [{
            "instrumentSymbol": "GEMI-MLB-2608182300-NYM-PHI-TT-PHI4",
            "label": "PHI Over 4.5 runs",
            "strike": {"type": "over", "value": "4.5"},
        }],
    })
    assert len(rows) == 1
    identity = rows[0].identity
    assert identity.market_family == "team_total"
    assert identity.subject_type == "team"
    assert identity.subject == "phi"
    assert identity.stat == "runs"
    assert identity.comparator == "GT"
    assert str(identity.line) == "4.5"
    assert identity.rule_audit.complete is False
