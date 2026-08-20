"""Fail-closed Gemini<->Kalshi MLB rule-equivalence classifier.

This module is intentionally read-only. It converts independently collected rule
evidence into a conservative shadow-readiness status. Ordinary threshold wording
is not enough: event identity, source hierarchy, postponement/cancellation,
suspended-game handling, corrections/review, expiration/fallback behavior, and
market-specific threshold semantics must all be explicitly audited.

It never authorizes live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


FULL_RULES_AUDITED_SHADOW = "FULL_RULES_AUDITED_SHADOW"
RULES_PENDING = "RULES_PENDING"
EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY = (
    "EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY"
)

_REQUIRED_BOOL_FIELDS = (
    "exact_event_identity",
    "threshold_orientation_equivalent",
    "source_hierarchy_equivalent",
    "postponement_reschedule_equivalent",
    "cancellation_forfeit_disqualification_equivalent",
    "suspended_interrupted_game_equivalent",
    "corrections_review_equivalent",
    "expiration_close_equivalent",
)


def _literal_true(value: Any) -> bool:
    """Require an actual boolean True; strings/numbers fail closed."""
    return value is True


@dataclass(frozen=True)
class RuleAuditResult:
    status: str
    full_rules_audited: bool
    fully_audited_shadow_ready: bool
    live_probe_ready: bool
    hard_blockers: tuple[str, ...]


def classify_mlb_rule_audit(evidence: Mapping[str, Any]) -> RuleAuditResult:
    """Classify exact Gemini/Kalshi MLB total/spread rule evidence.

    `gemini_market_outcome_review_fallback` and
    `kalshi_market_outcome_review_equivalent` are treated explicitly because an
    exceptional-result fallback on only one venue creates unresolved lifecycle
    basis even when ordinary winner/total/spread semantics match.
    """
    missing = [field for field in _REQUIRED_BOOL_FIELDS if not _literal_true(evidence.get(field))]

    gemini_review = _literal_true(evidence.get("gemini_market_outcome_review_fallback"))
    kalshi_review_equivalent = _literal_true(evidence.get("kalshi_market_outcome_review_equivalent"))

    blockers: list[str] = []
    if missing:
        blockers.extend(f"RULE_AUDIT_MISSING:{field}" for field in missing)

    if gemini_review and not kalshi_review_equivalent:
        blockers.append("ASYMMETRIC_EXCEPTION_REVIEW_FALLBACK")
        return RuleAuditResult(
            status=EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY,
            full_rules_audited=False,
            fully_audited_shadow_ready=False,
            live_probe_ready=False,
            hard_blockers=tuple(blockers),
        )

    if blockers:
        return RuleAuditResult(
            status=RULES_PENDING,
            full_rules_audited=False,
            fully_audited_shadow_ready=False,
            live_probe_ready=False,
            hard_blockers=tuple(blockers),
        )

    return RuleAuditResult(
        status=FULL_RULES_AUDITED_SHADOW,
        full_rules_audited=True,
        fully_audited_shadow_ready=True,
        live_probe_ready=False,
        hard_blockers=(),
    )


def current_public_mlb_total_spread_evidence() -> dict[str, Any]:
    """Conservative machine-readable snapshot of currently established facts.

    Ordinary identity/threshold semantics are established for the exact matched
    MLB total/spread families, but exceptional lifecycle equivalence is not.
    Gemini explicitly exposes a Market Outcome Review fallback when an expiration
    value cannot be determined; no independently proven Kalshi-equivalent fallback
    is asserted here.
    """
    return {
        "exact_event_identity": True,
        "threshold_orientation_equivalent": True,
        "source_hierarchy_equivalent": False,
        "postponement_reschedule_equivalent": False,
        "cancellation_forfeit_disqualification_equivalent": False,
        "suspended_interrupted_game_equivalent": False,
        "corrections_review_equivalent": False,
        "expiration_close_equivalent": False,
        "gemini_market_outcome_review_fallback": True,
        "kalshi_market_outcome_review_equivalent": False,
        "read_only": True,
        "order_entry": False,
    }
