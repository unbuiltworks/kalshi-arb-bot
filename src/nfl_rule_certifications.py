"""Fail-closed NFL rule certification registry.

This file records the rule fingerprints observed from current Gemini contract PDFs
and Kalshi NFL market rule fields.  It is intentionally conservative: identity or
price overlap is never enough to make a route executable.

Current evidence (2026-08-20):
- Gemini full-contest spread/total include regulation + overtime.
- Gemini NFL win/lose event terms allow a game to be rescheduled within seven days;
  beyond that they fall to last-fair-price handling.
- Current Kalshi preseason KXNFLGAME uses a 48-hour start window and resolves an
  official tie to $0.50 for each team.
- Gemini score-differential/total terms contain explicit abandonment/cancellation
  contingencies; the sampled Kalshi spread/total market rule fields do not expose a
  complete equivalent lifecycle fingerprint.
- Gemini total terms preserve totals already reached in an abandoned event; Kalshi
  totals close early after the threshold event occurs.  That is a research-only
  terminal-state lane, not a family-wide certification.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class NflRuleStatus(str, Enum):
    UNAUDITED = "UNAUDITED"
    CONDITIONAL = "CONDITIONAL"
    FULL_RULES_AUDITED_EXECUTABLE = "FULL_RULES_AUDITED_EXECUTABLE"


@dataclass(frozen=True)
class NflRuleCertification:
    venue_pair: tuple[str, str]
    family: str
    status: NflRuleStatus
    blockers: tuple[str, ...]
    matching_proofs: tuple[str, ...] = ()
    conditional_lane: str | None = None

    @property
    def executable(self) -> bool:
        return self.status is NflRuleStatus.FULL_RULES_AUDITED_EXECUTABLE and not self.blockers


GK_MONEYLINE = NflRuleCertification(
    venue_pair=("gemini", "kalshi"),
    family="moneyline",
    status=NflRuleStatus.CONDITIONAL,
    matching_proofs=(
        "same_official_game_winner_underlying",
        "full_contest_scope",
        "governing_league_official_result_source",
    ),
    blockers=(
        "tie_payout_mismatch_gemini_win_lose_vs_kalshi_50_50",
        "postponement_window_mismatch_gemini_7d_vs_current_kalshi_48h",
        "market_outcome_review_and_fair_price_branches_not_identical",
    ),
)

GK_SPREAD = NflRuleCertification(
    venue_pair=("gemini", "kalshi"),
    family="spread",
    status=NflRuleStatus.CONDITIONAL,
    matching_proofs=(
        "same_team_margin_metric",
        "half_point_line_has_no_push",
        "gemini_full_contest_includes_overtime",
        "kalshi_primary_rule_is_official_final_margin_threshold",
    ),
    blockers=(
        "kalshi_interruption_abandonment_lifecycle_not_complete_in_sampled_market_rule_fields",
        "gemini_cancel_abandon_after_start_24h_fair_price_branch_requires_equivalent_kalshi_proof",
        "postponement_reschedule_window_equivalence_unproven",
        "market_outcome_review_and_fair_price_branches_not_identical_or_unproven",
    ),
)

GK_TOTAL = NflRuleCertification(
    venue_pair=("gemini", "kalshi"),
    family="game_total",
    status=NflRuleStatus.CONDITIONAL,
    matching_proofs=(
        "same_combined_points_metric",
        "half_point_line_has_no_push",
        "gemini_full_contest_includes_overtime",
        "kalshi_primary_rule_is_official_combined_points_threshold",
    ),
    blockers=(
        "kalshi_interruption_abandonment_lifecycle_not_complete_in_sampled_market_rule_fields",
        "postponement_reschedule_window_equivalence_unproven",
        "market_outcome_review_and_fair_price_branches_not_identical_or_unproven",
        "terminal_over_state_not_yet_machine_certified",
    ),
    conditional_lane="TERMINAL_OVER_RESEARCH_ONLY",
)

REGISTRY = {
    ("gemini", "kalshi", "moneyline"): GK_MONEYLINE,
    ("gemini", "kalshi", "spread"): GK_SPREAD,
    ("gemini", "kalshi", "game_total"): GK_TOTAL,
}


def certification(venue_a: str, venue_b: str, family: str) -> NflRuleCertification:
    pair = tuple(sorted((str(venue_a).lower(), str(venue_b).lower())))
    key = (pair[0], pair[1], str(family).lower())
    row = REGISTRY.get(key)
    if row is not None:
        return row
    return NflRuleCertification(
        venue_pair=pair,
        family=str(family).lower(),
        status=NflRuleStatus.UNAUDITED,
        blockers=("no_complete_nfl_rule_fingerprint",),
    )


def assert_no_unintended_executable(rows: Iterable[NflRuleCertification] = REGISTRY.values()) -> None:
    bad = [row for row in rows if row.executable]
    if bad:
        raise AssertionError(f"NFL registry unexpectedly contains executable families: {bad}")
