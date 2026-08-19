from types import SimpleNamespace
from decimal import Decimal

from src.sports_pushover_alerts import AlertPolicy, SportsPushoverAlerts

D = Decimal


def lock(**overrides):
    data = dict(
        settlement_key="baseball|mlb|test",
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


def test_message_contains_required_operational_fields():
    s = sink(); l = lock()
    rows = [
        row("sportsbook:fanduel", "fd:1", "0.45", "0.60"),
        row("kalshi", "kx:1", "0.55", "0.48"),
    ]
    title, message, age = s.build_message(l, rows, rule_status="FULL_RULES_AUDITED", source="test", now_ms=100_000)
    assert "+$0.07" in title
    for token in ("YES sportsbook:fanduel", "NO kalshi", "Qty", "Cost $", "Payout $", "Fees $", "After-fee P/L", "Freshness", "skew", "Settlement", "Rules", "Manual sportsbook action: YES"):
        assert token in message
    assert age == 1000


def test_readable_message_puts_player_prop_and_prices_first():
    s = sink(); l = lock()
    rows = [
        row("sportsbook:fanduel", "fd:1", "0.45", "0.60"),
        row("kalshi", "kx:1", "0.55", "0.48"),
    ]
    title, message, age = s.build_message(
        l,
        rows,
        rule_status="CERTIFIED",
        source="MLB_PLAYER_PROP",
        now_ms=100_000,
        display_title="Rhys Hoskins • 2+ Total Bases • CERTIFIED ARB",
        display_market="Rhys Hoskins — 2+ Total Bases",
    )
    assert title.startswith("Rhys Hoskins • 2+ Total Bases")
    assert message.splitlines()[0] == "Rhys Hoskins — 2+ Total Bases"
    assert "YES — FanDuel: 45¢" in message
    assert "NO — Kalshi: 48¢" in message
    assert "Locked: +$0.07 total • +7¢/contract" in message
    assert age == 1000
