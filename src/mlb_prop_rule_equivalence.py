"""Participation-branch policy for Polymarket US <-> Kalshi MLB player props.

Identity equivalence is not enough. Venue participation rules differ before a
player has actually participated in the role Kalshi requires:

* Polymarket US: an MLB player prop can use an LFMP branch if the player does not
  participate.
* Kalshi batter props (Hits, H+R+RBI, Total Bases): a scratched/non-starting
  player uses a fair-market-price branch even if he later enters; a starter must
  also record at least one plate appearance before the statistic governs settlement.
* Kalshi strikeout props: the named pitcher must start and record at least one
  pitch; relief appearances do not satisfy the Kalshi participation branch.

IMPORTANT: resolving that participation branch does *not* prove full settlement
rule equivalence. Official-source hierarchy, suspension/cancellation treatment and
statistical-correction windows are audited separately by
:mod:`mlb_prop_rule_certifications`. This module therefore never grants execution
eligibility and its legacy ``settlement_equivalent_now`` flag is intentionally
fail-closed even after participation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuleEquivalence(str, Enum):
    # Enum names are retained for compatibility. EQUIVALENT_AFTER_PARTICIPATION
    # means only that the participation-specific divergence has been resolved.
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
    """Observable game-state proof required to resolve participation divergence."""

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
    participation_branch_resolved: bool
    # Legacy field retained to fail closed for any older consumer that interprets
    # it as complete settlement equivalence. Full equivalence lives in the separate
    # lifecycle certification engine.
    settlement_equivalent_now: bool = False
    execution_eligible: bool = False
    read_only: bool = True
    order_entry: bool = False
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "family": self.family,
            "status": self.status.value,
            "required_gate": self.required_gate,
            "participation_branch_resolved": self.participation_branch_resolved,
            "settlement_equivalent_now": self.settlement_equivalent_now,
            "execution_eligible": self.execution_eligible,
            "read_only": self.read_only,
            "order_entry": self.order_entry,
            "note": self.note,
        }


def assess_rule_equivalence(
    family: str, proof: ParticipationProof | None = None
) -> RuleAssessment:
    """Resolve only the participation branch; never certify full settlement.

    For batter props require a confirmed start and at least one plate appearance.
    For strikeouts require the named pitcher to be the starter and to have recorded
    at least one pitch. Passing this gate is necessary but not sufficient for a
    guaranteed cross-venue lock.
    """
    proof = proof or ParticipationProof()

    if family in BATTER_FAMILIES:
        gate = "CONFIRMED_START_AND_FIRST_PLATE_APPEARANCE"
        resolved = bool(
            proof.started
            and proof.plate_appearance_recorded
            and proof.source.strip()
        )
    elif family in PITCHER_FAMILIES:
        gate = "CONFIRMED_STARTING_PITCHER_AND_FIRST_PITCH"
        resolved = bool(
            proof.starting_pitcher
            and proof.pitch_recorded
            and proof.source.strip()
        )
    else:
        return RuleAssessment(
            family=family,
            status=RuleEquivalence.NON_EQUIVALENT,
            required_gate="UNSUPPORTED_FAMILY_RULE_AUDIT_REQUIRED",
            participation_branch_resolved=False,
            settlement_equivalent_now=False,
            note="Family is not covered by the audited MLB participation policy.",
        )

    if resolved:
        return RuleAssessment(
            family=family,
            status=RuleEquivalence.EQUIVALENT_AFTER_PARTICIPATION,
            required_gate=gate,
            participation_branch_resolved=True,
            settlement_equivalent_now=False,
            note=(
                "Participation-specific divergence is resolved only. Full lifecycle "
                "settlement certification is still required before execution."
            ),
        )

    return RuleAssessment(
        family=family,
        status=RuleEquivalence.CONDITIONAL_PARTICIPATION,
        required_gate=gate,
        participation_branch_resolved=False,
        settlement_equivalent_now=False,
        note=(
            "Do not count this pair as a guaranteed complementary settlement before "
            "the participation gate is observed. Shadow pricing is research-only."
        ),
    )
