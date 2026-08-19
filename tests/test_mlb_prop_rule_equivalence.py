from src.mlb_prop_rule_equivalence import (
    ParticipationProof,
    RuleEquivalence,
    assess_rule_equivalence,
)


def test_batter_prop_is_not_equivalent_before_participation() -> None:
    result = assess_rule_equivalence("baseball_player_hits")
    assert result.status == RuleEquivalence.CONDITIONAL_PARTICIPATION
    assert result.settlement_equivalent_now is False
    assert result.execution_eligible is False
    assert result.order_entry is False


def test_starting_lineup_alone_is_not_enough_for_batter() -> None:
    result = assess_rule_equivalence(
        "baseball_player_total_bases",
        ParticipationProof(started=True, source="official MLB game state"),
    )
    assert result.status == RuleEquivalence.CONDITIONAL_PARTICIPATION
    assert result.settlement_equivalent_now is False


def test_batter_becomes_rule_equivalent_after_first_pa() -> None:
    result = assess_rule_equivalence(
        "baseball_player_hits_runs_rbis",
        ParticipationProof(
            started=True,
            plate_appearance_recorded=True,
            source="official MLB game state",
        ),
    )
    assert result.status == RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION
    assert result.settlement_equivalent_now is True
    assert result.execution_eligible is False


def test_pitcher_requires_starter_and_batter_faced() -> None:
    not_enough = assess_rule_equivalence(
        "baseball_player_strikeouts",
        ParticipationProof(starting_pitcher=True, source="official MLB game state"),
    )
    assert not_enough.settlement_equivalent_now is False
    assert not_enough.required_gate == "CONFIRMED_STARTING_PITCHER_AND_FIRST_BATTER_FACED"

    # A legacy first-pitch flag must not unlock the audited >=1 batter-faced rule.
    one_pitch_only = assess_rule_equivalence(
        "baseball_player_strikeouts",
        ParticipationProof(
            starting_pitcher=True,
            pitch_recorded=True,
            source="official MLB game state",
        ),
    )
    assert one_pitch_only.settlement_equivalent_now is False

    aligned = assess_rule_equivalence(
        "baseball_player_strikeouts",
        ParticipationProof(
            starting_pitcher=True,
            batter_faced_recorded=True,
            pitch_recorded=True,
            source="official MLB game state",
        ),
    )
    assert aligned.status == RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION
    assert aligned.settlement_equivalent_now is True
    assert aligned.execution_eligible is False


def test_untrusted_boolean_without_source_does_not_unlock_gate() -> None:
    result = assess_rule_equivalence(
        "baseball_player_hits",
        ParticipationProof(started=True, plate_appearance_recorded=True),
    )
    assert result.settlement_equivalent_now is False


def test_unsupported_family_fails_closed() -> None:
    result = assess_rule_equivalence("baseball_player_home_runs")
    assert result.status == RuleEquivalence.NON_EQUIVALENT
    assert result.settlement_equivalent_now is False
    assert result.execution_eligible is False
