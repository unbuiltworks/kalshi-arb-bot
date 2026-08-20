from src.mlb_prop_rule_equivalence import (
    ParticipationProof,
    RuleEquivalence,
    assess_rule_equivalence,
)


def test_batter_prop_is_not_equivalent_before_participation() -> None:
    result = assess_rule_equivalence("baseball_player_hits")
    assert result.status == RuleEquivalence.CONDITIONAL_PARTICIPATION
    assert result.participation_branch_resolved is False
    assert result.settlement_equivalent_now is False
    assert result.execution_eligible is False
    assert result.order_entry is False


def test_starting_lineup_alone_is_not_enough_for_batter() -> None:
    result = assess_rule_equivalence(
        "baseball_player_total_bases",
        ParticipationProof(started=True, source="official MLB game state"),
    )
    assert result.status == RuleEquivalence.CONDITIONAL_PARTICIPATION
    assert result.participation_branch_resolved is False
    assert result.settlement_equivalent_now is False


def test_first_pa_resolves_only_participation_branch() -> None:
    result = assess_rule_equivalence(
        "baseball_player_hits_runs_rbis",
        ParticipationProof(
            started=True,
            plate_appearance_recorded=True,
            source="official MLB game state",
        ),
    )
    assert result.status == RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION
    assert result.participation_branch_resolved is True
    # Full settlement equivalence is deliberately never granted by this module.
    assert result.settlement_equivalent_now is False
    assert result.execution_eligible is False


def test_pitcher_must_be_starter_and_throw_pitch() -> None:
    not_enough = assess_rule_equivalence(
        "baseball_player_strikeouts",
        ParticipationProof(starting_pitcher=True, source="official MLB game state"),
    )
    assert not_enough.participation_branch_resolved is False
    assert not_enough.settlement_equivalent_now is False

    aligned = assess_rule_equivalence(
        "baseball_player_strikeouts",
        ParticipationProof(
            starting_pitcher=True,
            pitch_recorded=True,
            source="official MLB game state",
        ),
    )
    assert aligned.status == RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION
    assert aligned.participation_branch_resolved is True
    assert aligned.settlement_equivalent_now is False
    assert aligned.execution_eligible is False


def test_untrusted_boolean_without_source_does_not_unlock_gate() -> None:
    result = assess_rule_equivalence(
        "baseball_player_hits",
        ParticipationProof(started=True, plate_appearance_recorded=True),
    )
    assert result.participation_branch_resolved is False
    assert result.settlement_equivalent_now is False


def test_unsupported_family_fails_closed() -> None:
    result = assess_rule_equivalence("baseball_player_home_runs")
    assert result.status == RuleEquivalence.NON_EQUIVALENT
    assert result.participation_branch_resolved is False
    assert result.settlement_equivalent_now is False
    assert result.execution_eligible is False
