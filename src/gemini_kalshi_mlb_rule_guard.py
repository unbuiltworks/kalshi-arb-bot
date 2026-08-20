"""Fail-closed Gemini<->Kalshi MLB rule-equivalence classifier.

This module is intentionally read-only. It converts independently collected rule
evidence into a conservative shadow-readiness status. Ordinary threshold wording
is not enough: event identity, source hierarchy, postponement/cancellation,
suspended-game handling, corrections/review, expiration/fallback behavior, and
market-specific threshold semantics must all be explicitly audited.

A boolean assertion is not sufficient audit evidence. Every lifecycle-equivalence
claim that is used to reach FULL_RULES_AUDITED_SHADOW must carry official HTTPS
venue-specific source references plus SHA-256 content fingerprints for the exact
rule evidence that was reviewed. This prevents a mutable generic URL plus a bare
boolean from being treated as a durable rule audit.

The known Gemini Market Outcome Review fallback is an explicit part of the audit:
a full shadow audit must prove that fact from an official Gemini source and exact
fingerprint and must separately prove the claimed Kalshi-equivalent exception
handling with bilateral official provenance and fingerprints.

It never authorizes live execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any
from urllib.parse import urlparse


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

_OFFICIAL_SOURCE_HOSTS = {
    "gemini": (
        "gemini.com",
        "www.gemini.com",
        "developer.gemini.com",
        "docs.gemini.com",
    ),
    "kalshi": (
        "kalshi.com",
        "www.kalshi.com",
        "docs.kalshi.com",
        "help.kalshi.com",
    ),
}


def _literal_true(value: Any) -> bool:
    return value is True


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256_hex(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


def _official_https_source(value: Any, venue: str) -> bool:
    if not _nonempty_text(value):
        return False
    try:
        parsed = urlparse(value.strip())
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return host in _OFFICIAL_SOURCE_HOSTS.get(venue, ())


def _field_mapping(evidence: Mapping[str, Any], container: str, field: str) -> Mapping[str, Any] | None:
    values = evidence.get(container)
    if not isinstance(values, Mapping):
        return None
    field_values = values.get(field)
    return field_values if isinstance(field_values, Mapping) else None


def _has_venue_source(evidence: Mapping[str, Any], field: str, venue: str) -> bool:
    field_sources = _field_mapping(evidence, "evidence_sources", field)
    return field_sources is not None and _official_https_source(field_sources.get(venue), venue)


def _has_bilateral_source(evidence: Mapping[str, Any], field: str) -> bool:
    return _has_venue_source(evidence, field, "gemini") and _has_venue_source(
        evidence, field, "kalshi"
    )


def _has_venue_fingerprint(evidence: Mapping[str, Any], field: str, venue: str) -> bool:
    field_fingerprints = _field_mapping(evidence, "evidence_fingerprints", field)
    return field_fingerprints is not None and _sha256_hex(field_fingerprints.get(venue))


def _has_bilateral_fingerprint(evidence: Mapping[str, Any], field: str) -> bool:
    return _has_venue_fingerprint(evidence, field, "gemini") and _has_venue_fingerprint(
        evidence, field, "kalshi"
    )


@dataclass(frozen=True)
class RuleAuditResult:
    status: str
    full_rules_audited: bool
    fully_audited_shadow_ready: bool
    live_probe_ready: bool
    hard_blockers: tuple[str, ...]


def classify_mlb_rule_audit(evidence: Mapping[str, Any]) -> RuleAuditResult:
    """Classify exact Gemini/Kalshi MLB total/spread rule evidence.

    A complete shadow audit requires literal-true semantic checks, official HTTPS
    provenance, and immutable SHA-256 fingerprints for the exact source material
    reviewed. The fingerprint is evidence bookkeeping only; it never promotes a
    market to live execution.
    """
    missing = [field for field in _REQUIRED_BOOL_FIELDS if not _literal_true(evidence.get(field))]
    unsourced = [
        field
        for field in _REQUIRED_BOOL_FIELDS
        if _literal_true(evidence.get(field)) and not _has_bilateral_source(evidence, field)
    ]
    unfingerprinted = [
        field
        for field in _REQUIRED_BOOL_FIELDS
        if _literal_true(evidence.get(field))
        and _has_bilateral_source(evidence, field)
        and not _has_bilateral_fingerprint(evidence, field)
    ]

    gemini_review = _literal_true(evidence.get("gemini_market_outcome_review_fallback"))
    kalshi_review_equivalent = _literal_true(evidence.get("kalshi_market_outcome_review_equivalent"))

    blockers: list[str] = []
    blockers.extend(f"RULE_AUDIT_MISSING:{field}" for field in missing)
    blockers.extend(f"RULE_AUDIT_MISSING_SOURCE:{field}" for field in unsourced)
    blockers.extend(f"RULE_AUDIT_MISSING_FINGERPRINT:{field}" for field in unfingerprinted)

    if not gemini_review:
        blockers.append("RULE_AUDIT_MISSING:gemini_market_outcome_review_fallback")
    elif not _has_venue_source(evidence, "gemini_market_outcome_review_fallback", "gemini"):
        blockers.append("RULE_AUDIT_MISSING_SOURCE:gemini_market_outcome_review_fallback")
    elif not _has_venue_fingerprint(
        evidence, "gemini_market_outcome_review_fallback", "gemini"
    ):
        blockers.append("RULE_AUDIT_MISSING_FINGERPRINT:gemini_market_outcome_review_fallback")

    if not kalshi_review_equivalent:
        blockers.append("ASYMMETRIC_EXCEPTION_REVIEW_FALLBACK")
        return RuleAuditResult(
            status=EXCEPTION_LIFECYCLE_UNRESOLVED_RESEARCH_ONLY,
            full_rules_audited=False,
            fully_audited_shadow_ready=False,
            live_probe_ready=False,
            hard_blockers=tuple(blockers),
        )

    if not _has_bilateral_source(evidence, "kalshi_market_outcome_review_equivalent"):
        blockers.append("RULE_AUDIT_MISSING_SOURCE:kalshi_market_outcome_review_equivalent")
    elif not _has_bilateral_fingerprint(evidence, "kalshi_market_outcome_review_equivalent"):
        blockers.append("RULE_AUDIT_MISSING_FINGERPRINT:kalshi_market_outcome_review_equivalent")

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
    """Conservative snapshot: ordinary semantics known, lifecycle equivalence unresolved."""
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
        "evidence_sources": {
            "exact_event_identity": {
                "gemini": "https://developer.gemini.com/prediction-markets/tickers-sports",
                "kalshi": "https://kalshi.com/markets/kxmlbtotal",
            },
            "threshold_orientation_equivalent": {
                "gemini": "https://developer.gemini.com/prediction-markets/tickers-sports",
                "kalshi": "https://kalshi.com/markets/kxmlbspread",
            },
            "gemini_market_outcome_review_fallback": {
                "gemini": "https://www.gemini.com/legal/prediction-market-rules",
            },
        },
        # Deliberately no evidence_fingerprints: current public facts have not yet
        # been captured as an immutable complete bilateral lifecycle audit.
        "read_only": True,
        "order_entry": False,
    }
