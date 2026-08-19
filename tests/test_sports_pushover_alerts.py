from types import SimpleNamespace
from decimal import Decimal

from src.sports_pushover_alerts import (
    AlertPolicy,
    SportsPushoverAlerts,
    parse_sports_display_identity,
)

D = Decimal


def lock(**overrides):
    data = dict(
        settlement_key="baseball|mlb|phi,sea|player_prop|full_contest|player|trea_turner|hits|GT|0.5",
        yes_venue="sportsbook:fanduel",
        yes_market_id="fd:1",
        no_venue="kalshi",
        no_market_id="kx:1",
        unit_cost=D("0.93"),
        unit_profit=D("0.07"),
        max_quantity=D("1"),
        max_capital=D("0.93"),
        max_locked_profit=D("0.07"),
        quote_skew_ms=1000,
        settlement_confidence="FULL_RULES_AUDITED_EXECUTABLE",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def row(venue, market_id, yes_cost, no_cost, age_ms=1000):
    q = SimpleNamespace(venue=venue, market_id=market_id, yes_cost=D(yes_cost), no_cost=D(no_cost), received_ms=100_000-age_ms)
    return SimpleNamespace(quote=q, yes_fee=D("0.01"), no_fee=D("0.01"))


def sink():
    return SportsPushoverAlerts(AlertPolicy(
        min_unit_profit=D("0.02"), min_total_profit=D("0.05"),
        max_quote_skew_ms=5000, max_quote_age_ms=5000,
        realert_profit_abs=D("0.02"), realert_profit_pct=D("0.25"),
        realert_size_pct=D("0.50"),
    ))


def test_qualification_requires_actionable_fully_audited_settlement():
    s = sink()
    assert s.qualifies(lock(), max_age_ms=1000, actionable=True)[0]
    ok, reason = s.qualifies(lock(), max_age_ms=1000, actionable=False)
    assert not ok and reason == "not_actionable"
    ok, reason = s.qualifies(
        lock(settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE"),
        max_age_ms=1000,
        actionable=True,
    )
    assert not ok and reason == "settlement_not_execution_audited"


def test_qualification_rejects_stale_and_small_profit():
    s = sink()
    assert not s.qualifies(lock(unit_profit=D("0.01")), max_age_ms=1000, actionable=True)[0]
    assert not s.qualifies(lock(), max_age_ms=6000, actionable=True)[0]
    assert not s.qualifies(lock(quote_skew_ms=6000), max_age_ms=1000, actionable=True)[0]


def test_realert_only_on_material_profit_or_size_gain():
    s = sink(); first = lock()
    assert s.should_realert(first)
    s.mark_sent(first)
    assert not s.should_realert(lock(max_locked_profit=D("0.08")))
    assert s.should_realert(lock(max_locked_profit=D("0.09")))
    assert s.should_realert(lock(max_quantity=D("1.5"), max_locked_profit=D("0.07")))


def test_display_identity_uses_bettor_friendly_mlb_prop_names():
    hits = parse_sports_display_identity(
        "baseball|mlb|phi,sea|player_prop|full_contest|player|trea_turner|hits|GT|0.5"
    )
    assert hits is not None
    assert hits.league == "MLB"
    assert hits.matchup == "PHI vs. SEA"
    assert hits.market_family == "Player Prop"
    assert hits.headline == "Trea Turner — 1+ Hit"

    total_bases = parse_sports_display_identity(
        "baseball|mlb|phi,sea|player_prop|full_contest|player|bryce_harper|total_bases|GT|1.5"
    )
    assert total_bases is not None
    assert total_bases.headline == "Bryce Harper — Over 1.5 Total Bases"

    home_run = parse_sports_display_identity(
        "baseball|mlb|phi,sea|player_prop|full_contest|player|kyle_schwarber|home_runs|GT|0.5"
    )
    assert home_run is not None
    assert home_run.headline == "Kyle Schwarber — Home Run YES"


def test_display_identity_handles_game_total():
    identity = parse_sports_display_identity(
        "baseball|mlb|cle,sf|game_total|full_contest|contest|contest|runs|GT|9.5"
    )
    assert identity is not None
    assert identity.matchup == "CLE vs. SF"
    assert identity.market_family == "Game Total"
    assert identity.headline == "Game Total — Over 9.5 Runs"


def test_message_contains_human_market_identity_and_operational_fields():
    s = sink(); l = lock()
    rows = [
        row("sportsbook:fanduel", "fd:1", "0.45", "0.60"),
        row("kalshi", "kx:1", "0.55", "0.48"),
    ]
    title, message, age = s.build_message(l, rows, rule_status="FULL_RULES_AUDITED", source="test", now_ms=100_000)
    assert title == "MLB ARB — Trea Turner — 1+ Hit"
    for token in (
        "PHI vs. SEA",
        "YES FanDuel 45¢",
        "NO Kalshi 48¢",
        "Buy 1 each — Cost $0.93",
        "Locked profit: +$0.07 / +7.5%",
        "Fees included: $0.02",
        "Market family: Player Prop",
        "Freshness: 1000ms max",
        "1000ms skew",
        "Settlement: FULL_RULES_AUDITED_EXECUTABLE",
        "Rules: FULL_RULES_AUDITED",
        "Manual sportsbook leg: YES",
    ):
        assert token in message
    assert "baseball|mlb|" not in message
    assert age == 1000


def test_malformed_key_falls_back_without_changing_safety_fields():
    s = sink(); l = lock(settlement_key="baseball|mlb|test")
    rows = [
        row("sportsbook:fanduel", "fd:1", "0.45", "0.60"),
        row("kalshi", "kx:1", "0.55", "0.48"),
    ]
    title, message, _ = s.build_message(l, rows, rule_status="FULL_RULES_AUDITED", source="test", now_ms=100_000)
    assert title == "Sports arb +$0.07"
    assert "test | baseball|mlb|test" in message
    assert "Manual sportsbook leg: YES" in message
