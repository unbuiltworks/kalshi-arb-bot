"""Settlement-equivalence participation policy for MLB player props.

Identity equivalence is not enough. The audited MLB player-prop participation
branch is treated conservatively before any maker opportunity is considered live:

* batter props: player is in the starting lineup and records >=1 plate appearance;
* strikeout props: player is the starting pitcher and records >=1 batter faced.

The second condition is intentionally not approximated with "one pitch". A pitch
can occur without a completed batter-faced statistic. This module remains
research-only and exposes no order entry.
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
        "baseball_player_total_bases",
    }
)
PITCHER_FAMILIES = frozenset({"baseball_player_strikeouts"})
SUPPORTED_FAMILIES = BATTER_FAMILIES | PITCHER_FAMILIES


@dataclass(frozen=True)
class ParticipationProof:
    """Observable official-game-state proof for the participation branch."""

    started: bool = False
    plate_appearance_recorded: bool = False
    starting_pitcher: bool = False
    batter_faced_recorded: bool = False
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
    """Classify the participation branch without granting execution eligibility."""
    proof = proof or ParticipationProof()

    if family in BATTER_FAMILIES:
        gate = "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE"
        equivalent = bool(
            proof.started
            and proof.plate_appearance_recorded
            and proof.source.strip()
        )
    elif family in PITCHER_FAMILIES:
        gate = "CONFIRMED_STARTING_PITCHER_AND_FIRST_BATTER_FACED"
        equivalent = bool(
            proof.starting_pitcher
            and proof.batter_faced_recorded
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
                "Participation branch is resolved; exact lifecycle, source, correction, "
                "threshold, identity, timing, and cancellation rules still apply."
            ),
        )

    return RuleAssessment(
        family=family,
        status=RuleEquivalence.CONDITIONAL_PARTICIPATION,
        required_gate=gate,
        settlement_equivalent_now=False,
        note=(
            "Do not count this pair as guaranteed complementary settlement before "
            "the audited participation gate is observed. Shadow pricing is research-only."
        ),
    )
