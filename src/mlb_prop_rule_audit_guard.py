"""Fail-closed rule-evidence guard for Polymarket US <-> Kalshi MLB props.

A generic sports FAQ or a hand-written equivalence summary is not sufficient evidence
for an exact player-prop settlement audit. Polymarket US exposes instrument-specific
``instrument_rules`` metadata, while Kalshi exposes market-specific rule text and
settlement-source metadata. This module requires both exact-contract rule payloads
plus explicit independent per-dimension audit decisions before a pair can be
classified as fully rule-audited shadow evidence.

Read-only research only. No venue clients, credentials, or order-entry paths exist
here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

AUDIT_CONTRACT = "EXACT_CONTRACT_RULE_AUDIT_V2"


@dataclass(frozen=True)
class ExactRuleAudit:
    family: str
    polymarket_instrument_id: str
    polymarket_instrument_rules: str
    kalshi_ticker: str
    kalshi_market_rules: str
    kalshi_settlement_sources: tuple[str, ...]
    participation_branch_checked: bool
    stat_definition_checked: bool
    cancellation_postponement_checked: bool
    source_hierarchy_checked: bool
    participation_branch_equivalent: bool
    stat_definition_equivalent: bool
    cancellation_postponement_equivalent: bool
    source_hierarchy_equivalent: bool
    independent_reviewer: str
    audit_decision_id: str
    equivalent: bool

    @property
    def decision_provenance_complete(self) -> bool:
        return bool(self.independent_reviewer.strip() and self.audit_decision_id.strip())

    @property
    def complete(self) -> bool:
        return bool(
            self.family.strip()
            and self.polymarket_instrument_id.strip()
            and self.polymarket_instrument_rules.strip()
            and self.kalshi_ticker.strip()
            and self.kalshi_market_rules.strip()
            and self.kalshi_settlement_sources
            and all(str(source).strip() for source in self.kalshi_settlement_sources)
            and self.participation_branch_checked
            and self.stat_definition_checked
            and self.cancellation_postponement_checked
            and self.source_hierarchy_checked
            and self.participation_branch_equivalent
            and self.stat_definition_equivalent
            and self.cancellation_postponement_equivalent
            and self.source_hierarchy_equivalent
            and self.decision_provenance_complete
            and self.equivalent
        )

    def as_dict(self) -> dict[str, Any]:
        """Emit explicit exact-contract audit evidence for downstream readiness code.

        ``full_rules_audited_shadow`` is deliberately stronger than a caller-supplied
        global ``equivalent`` boolean: every required semantic dimension must have an
        explicit equivalent decision and the independent review must have stable
        provenance. This module still never grants live-probe readiness.
        """
        return {
            "audit_contract": AUDIT_CONTRACT,
            "family": self.family,
            "polymarket_instrument_id": self.polymarket_instrument_id,
            "kalshi_ticker": self.kalshi_ticker,
            "participation_branch_checked": self.participation_branch_checked,
            "stat_definition_checked": self.stat_definition_checked,
            "cancellation_postponement_checked": self.cancellation_postponement_checked,
            "source_hierarchy_checked": self.source_hierarchy_checked,
            "participation_branch_equivalent": self.participation_branch_equivalent,
            "stat_definition_equivalent": self.stat_definition_equivalent,
            "cancellation_postponement_equivalent": self.cancellation_postponement_equivalent,
            "source_hierarchy_equivalent": self.source_hierarchy_equivalent,
            "independent_reviewer": self.independent_reviewer,
            "audit_decision_id": self.audit_decision_id,
            "decision_provenance_complete": self.decision_provenance_complete,
            "equivalent": self.equivalent,
            "full_rules_audited_shadow": self.complete,
            "live_probe_ready": False,
            "read_only": True,
            "order_entry": False,
        }


def exact_rule_audit_from_payloads(
    *,
    family: str,
    polymarket: Mapping[str, Any],
    kalshi: Mapping[str, Any],
    participation_branch_checked: bool,
    stat_definition_checked: bool,
    cancellation_postponement_checked: bool,
    source_hierarchy_checked: bool,
    equivalent: bool,
    participation_branch_equivalent: bool = False,
    stat_definition_equivalent: bool = False,
    cancellation_postponement_equivalent: bool = False,
    source_hierarchy_equivalent: bool = False,
    independent_reviewer: str = "",
    audit_decision_id: str = "",
) -> ExactRuleAudit:
    """Build exact-contract audit evidence from already-fetched public payloads.

    Field extraction is deliberately conservative. If an endpoint/schema does not
    expose contract-specific rule text, or if the independent audit has not recorded
    explicit equivalence for every required semantic dimension, ``complete`` remains
    false rather than substituting a generic FAQ, UI label, global boolean, or analyst
    summary.
    """
    poly_id = str(
        polymarket.get("instrument_id")
        or polymarket.get("instrumentId")
        or polymarket.get("id")
        or ""
    )
    poly_rules = str(
        polymarket.get("instrument_rules")
        or polymarket.get("instrumentRules")
        or ""
    )
    kalshi_ticker = str(kalshi.get("ticker") or "")
    kalshi_rules = str(
        kalshi.get("rules")
        or kalshi.get("market_rules")
        or kalshi.get("rules_primary")
        or ""
    )
    raw_sources = (
        kalshi.get("settlement_sources")
        or kalshi.get("settlementSources")
        or kalshi.get("source_agencies")
        or ()
    )
    if isinstance(raw_sources, str):
        sources = (raw_sources,) if raw_sources.strip() else ()
    else:
        sources = tuple(str(value) for value in raw_sources or () if str(value).strip())

    return ExactRuleAudit(
        family=str(family or ""),
        polymarket_instrument_id=poly_id,
        polymarket_instrument_rules=poly_rules,
        kalshi_ticker=kalshi_ticker,
        kalshi_market_rules=kalshi_rules,
        kalshi_settlement_sources=sources,
        participation_branch_checked=bool(participation_branch_checked),
        stat_definition_checked=bool(stat_definition_checked),
        cancellation_postponement_checked=bool(cancellation_postponement_checked),
        source_hierarchy_checked=bool(source_hierarchy_checked),
        participation_branch_equivalent=bool(participation_branch_equivalent),
        stat_definition_equivalent=bool(stat_definition_equivalent),
        cancellation_postponement_equivalent=bool(cancellation_postponement_equivalent),
        source_hierarchy_equivalent=bool(source_hierarchy_equivalent),
        independent_reviewer=str(independent_reviewer or ""),
        audit_decision_id=str(audit_decision_id or ""),
        equivalent=bool(equivalent),
    )
