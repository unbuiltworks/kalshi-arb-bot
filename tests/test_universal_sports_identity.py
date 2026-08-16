from decimal import Decimal

import pytest

from src.universal_sports_identity import (
    RuleAudit,
    SportsContractIdentity,
    SportsEventIdentity,
    compare_contracts,
    find_counterparts,
    stat_supported,
)


def event(start_ms: int = 1_800_000_000_000) -> SportsEventIdentity:
    return SportsEventIdentity(
        sport="baseball",
        league="mlb",
        participants=("NYM", "PHI"),
        scheduled_start_ms=start_ms,
    )


def audit(*, complete: bool = True, overtime: str = "extra innings included") -> RuleAudit:
    return RuleAudit(
        official_source="MLB official statistics",
        overtime=overtime,
        postponement="void if not played on listed game instance",
        stat_corrections="official corrections through settlement cutoff",
        push_tie="exact threshold semantics; no push on half lines",
        complete=complete,
    )


def prop(venue: str, market_id: str, *, start_ms: int = 1_800_000_000_000, rule=None):
    return SportsContractIdentity(
        venue=venue,
        market_id=market_id,
        event=event(start_ms),
        market_family="player_prop",
        scope="full_game",
        subject_type="player",
        subject="Bryce Harper",
        stat="total_bases",
        comparator="GE",
        line=Decimal("2"),
        rule_audit=rule or audit(),
    )


def test_exact_player_prop_and_rules_match_is_execution_eligible():
    match = compare_contracts(prop("kalshi", "k1"), prop("polymarket_us", "p1"))
    assert match is not None
    assert match.normal_event_match is True
    assert match.rules_match is True
    assert match.execution_eligible is True


def test_same_headline_but_different_rule_semantics_stays_shadow_only():
    left = prop("kalshi", "k1")
    right = prop("polymarket_us", "p1", rule=audit(overtime="regulation only"))
    match = compare_contracts(left, right)
    assert match is not None
    assert match.normal_event_match is True
    assert match.rules_match is False
    assert match.execution_eligible is False


def test_incomplete_rule_audit_stays_shadow_only():
    match = compare_contracts(
        prop("kalshi", "k1"),
        prop("polymarket_us", "p1", rule=audit(complete=False)),
    )
    assert match is not None
    assert match.execution_eligible is False


def test_wrong_player_or_line_does_not_match():
    left = prop("kalshi", "k1")
    wrong_player = SportsContractIdentity(
        venue="polymarket_us",
        market_id="p2",
        event=event(),
        market_family="player_prop",
        scope="full_game",
        subject_type="player",
        subject="Kyle Schwarber",
        stat="total_bases",
        comparator="GE",
        line=Decimal("2"),
        rule_audit=audit(),
    )
    wrong_line = SportsContractIdentity(
        venue="polymarket_us",
        market_id="p3",
        event=event(),
        market_family="player_prop",
        scope="full_game",
        subject_type="player",
        subject="Bryce Harper",
        stat="total_bases",
        comparator="GE",
        line=Decimal("3"),
        rule_audit=audit(),
    )
    assert compare_contracts(left, wrong_player) is None
    assert compare_contracts(left, wrong_line) is None


def test_consecutive_or_doubleheader_game_mismatch_fails_closed_on_start_time():
    left = prop("kalshi", "k1", start_ms=1_800_000_000_000)
    right = prop("polymarket_us", "p1", start_ms=1_800_010_000_000)
    assert compare_contracts(left, right, max_start_skew_ms=300_000) is None


def test_participant_order_is_canonicalized():
    a = SportsEventIdentity("baseball", "mlb", ("PHI", "NYM"), 1_800_000_000_000)
    b = SportsEventIdentity("baseball", "mlb", ("nym", "phi"), 1_800_000_000_000)
    assert a.event_key() == b.event_key()


def test_player_prop_requires_player_and_stat():
    with pytest.raises(ValueError):
        SportsContractIdentity(
            venue="kalshi",
            market_id="k",
            event=event(),
            market_family="player_prop",
            scope="full_game",
            subject_type="team",
            subject="PHI",
            stat="",
            comparator="GE",
            line=Decimal("2"),
            rule_audit=audit(),
        )


def test_find_counterparts_keeps_multiple_venues_without_same_venue_pairs():
    rows = [
        prop("kalshi", "k1"),
        prop("polymarket_us", "p1"),
        prop("gemini", "g1"),
    ]
    matches = find_counterparts(rows)
    assert len(matches) == 3
    assert all(m.left.venue != m.right.venue for m in matches)


def test_supported_prop_taxonomy_has_major_sports():
    assert stat_supported("baseball", "strikeouts")
    assert stat_supported("football", "receptions")
    assert stat_supported("basketball", "points_rebounds_assists")
    assert stat_supported("hockey", "shots_on_goal")
