"""Participation gating for MLB player-prop settlement audits.

Identity equivalence is not enough.  Prediction venues can use different player
participation branches before a stat governs settlement.  Kalshi batter props,
for example, require a starter to record at least one plate appearance; a
non-starter remains on the fair-market-price branch even if he later pinch hits.
Gemini's current MLB player-prop contracts switch to official-stat settlement once
the listed player appears.  Venue-specific lifecycle and cancellation rules are
audited separately by :mod:`mlb_prop_rule_certifications`.

This module answers only whether the observable role-participation gate has been
crossed.  It never grants execution eligibility and exposes no order entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuleEquivalence(str, Enum):
    NON_EQUIVALENT = "NON_EQUIVALENT"
    CONDITIONAL_PARTICIPATION = "CONDITIONAL_PARTICIPATION"
    EQUIVALENT_AFTER_PARTICIPATION = "EQUIVALENT_AFTER_PARTICIPATION"


BATTER_FAMILIES = frozenset(
    {
        "baseball_player_hits",
        "baseball_player_hits_runs_rbis",
        "baseball_player_home_runs",
        "baseball_player_total_bases",
    }
)
PITCHER_FAMILIES = frozenset({"baseball_player_strikeouts"})
SUPPORTED_FAMILIES = BATTER_FAMILIES | PITCHER_FAMILIES


@dataclass(frozen=True)
class ParticipationProof:
    """Observable game-state proof required before the participation branch clears."""

    started: bool = False
    plate_appearance_recorded: bool = False
    starting_pitcher: bool = False
    pitch_recorded: bool = False
    source: str = ""


@dataclass(frozen=True)
class RuleAssessment:
    family: str
    status: RuleEquivalence
    required_gate: str
    settlement_equivalent_now: bool
    execution_eligible: bool = False
    read_only: bool = True
    order_entry: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "status": self.status.value,
            "required_gate": self.required_gate,
            "settlement_equivalent_now": self.settlement_equivalent_now,
            "execution_eligible": self.execution_eligible,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
            "note": self.note,
        }


def assess_rule_equivalence(
    family: str, proof: ParticipationProof | None = None
) -> RuleAssessment:
    """Classify the observable participation gate without granting execution.

    For batter props we require a confirmed start *and* at least one plate
    appearance.  For strikeouts we require the named pitcher to be the starter and
    to have recorded at least one pitch.  Clearing this gate removes only the
    participation-specific uncertainty; exact lifecycle equivalence is a separate
    mandatory certification step.
    """
    proof = proof or ParticipationProof()

    if family in BATTER_FAMILIES:
        gate = "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE"
        equivalent = bool(
            proof.started
            and proof.plate_appearance_recorded
            and proof.source.strip()
        )
    elif family in PITCHER_FAMILIES:
        gate = "CONFIRMED_STARTING_PITCHER_AND_FIRST_PITCH"
        equivalent = bool(
            proof.starting_pitcher
            and proof.pitch_recorded
            and proof.source.strip()
        )
    else:
        return RuleAssessment(
            family=family,
            status=RuleEquivalence.NON_EQUIVALENT,
            required_gate="UNSUPPORTED_FAMILY_RULE_AUDIT_REQUIRED",
            settlement_equivalent_now=False,
            note="Family is not covered by the audited MLB participation policy.",
        )

    if equivalent:
        return RuleAssessment(
            family=family,
            status=RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION,
            required_gate=gate,
            settlement_equivalent_now=True,
            note=(
                "Participation branch is resolved; continue to require exact event, "
                "player, statistic, threshold, timing, source and lifecycle identity."
            ),
        )

    return RuleAssessment(
        family=family,
        status=RuleEquivalence.CONDITIONAL_PARTICIPATION,
        required_gate=gate,
        settlement_equivalent_now=False,
        note=(
            "Do not count this pair as guaranteed complementary settlement before "
            "the participation gate is observed. Shadow pricing is research-only."
        ),
    )
