from decimal import Decimal

from src.gemini_sports_adapter import normalize_event, parse_team_event_ticker


def _event(market_type, subject, metric, code, contracts):
    return {
        "ticker": f"GEMI-MLB-2608171840-MIA-PHI-{code}",
        "sportsMarket": {
            "sport": "baseball",
            "type": market_type,
            "subject": subject,
            "scope": {"type": "full_contest"},
            **({"metric": metric} if metric else {}),
        },
        "contracts": contracts,
    }


def test_parse_team_event_ticker():
    row = parse_team_event_ticker("GEMI-MLB-2608171840-MIA-PHI-M")
    assert row is not None
    assert row["league"] == "MLB"
    assert row["away"] == "MIA"
    assert row["home"] == "PHI"
    assert row["start"].isoformat() == "2026-08-17T18:40:00+00:00"


def test_moneyline_contracts_normalize_to_team_predicates():
    rows = normalize_event(_event("moneyline", "contest", None, "M", [
        {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-M-MIA", "label": "Miami"},
        {"instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-M-PHI", "label": "Philadelphia"},
    ]))
    assert len(rows) == 2
    assert {row.identity.subject for row in rows} == {"mia", "phi"}
    assert all(row.identity.market_family == "moneyline" for row in rows)
    assert all(row.identity.comparator == "YES" for row in rows)
    assert all(not row.identity.fully_rule_audited for row in rows)


def test_run_line_requires_structured_spread_strike():
    rows = normalize_event(_event("spread", "team", "runs", "S", [
        {
            "instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-S-PHI1",
            "label": "Philadelphia -1.5",
            "strike": {"type": "spread", "value": "-1.5"},
        }
    ]))
    assert len(rows) == 1
    row = rows[0]
    assert row.identity.market_family == "spread"
    assert row.identity.subject == "phi"
    assert row.identity.stat == "runs"
    assert row.identity.line == Decimal("-1.5")


def test_game_total_uses_structured_direction_and_line():
    rows = normalize_event(_event("total", "contest", "runs", "T", [
        {
            "instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-T-O85",
            "label": "Over 8.5",
            "strike": {"type": "over", "value": "8.5"},
        },
        {
            "instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-T-U85",
            "label": "Under 8.5",
            "strike": {"type": "under", "value": "8.5"},
        },
    ]))
    assert len(rows) == 2
    assert {row.identity.comparator for row in rows} == {"GT", "LT"}
    assert {row.identity.line for row in rows} == {Decimal("8.5")}
    assert all(row.identity.market_family == "game_total" for row in rows)


def test_player_prop_maps_metric_and_player_but_remains_rule_unverified():
    rows = normalize_event(_event("prop", "player", "runs_batted_in", "PPRBI", [
        {
            "instrumentSymbol": "GEMI-MLB-2608171840-MIA-PHI-PPRBI-HARPERO15",
            "label": "Bryce Harper Over 1.5",
            "strike": {"type": "over", "value": "1.5"},
        }
    ]))
    assert len(rows) == 1
    row = rows[0]
    assert row.identity.market_family == "player_prop"
    assert row.identity.subject == "bryce harper"
    assert row.identity.stat == "rbis"
    assert row.identity.comparator == "GT"
    assert row.identity.line == Decimal("1.5")
    assert not row.identity.fully_rule_audited


def test_missing_sports_market_or_strike_fails_closed():
    assert normalize_event({"ticker": "GEMI-MLB-2608171840-MIA-PHI-T", "contracts": []}) == []
    rows = normalize_event(_event("total", "contest", "runs", "T", [
        {"instrumentSymbol": "GEMI-X", "label": "Over 8.5"}
    ]))
    assert rows == []
