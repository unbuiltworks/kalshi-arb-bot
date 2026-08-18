"""Fail-closed rule-evidence guard for Polymarket US <-> Kalshi MLB props.

A generic sports FAQ or a hand-written equivalence summary is not sufficient evidence
for an exact player-prop settlement audit. Polymarket US exposes instrument-specific
``instrument_rules`` metadata, while Kalshi exposes market-specific rule text and
settlement-source metadata. This module requires both exact-contract rule payloads
before a pair can be classified as fully rule-audited shadow evidence.

Read-only research only. No venue clients, credentials, or order-entry paths exist
here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

AUDIT_CONTRACT = "EXACT_CONTRACT_RULE_AUDIT_V1"


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
    equivalent: bool

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
            and self.equivalent
        )

    def as_dict(self) -> dict[str, Any]:
        """Emit a small, explicit evidence contract for downstream readiness code.

        We intentionally do not expose the full rule text in the operational row.
        ``full_rules_audited_shadow`` can only be true after ``complete`` has verified
        the exact-contract rule payloads and all required audit branches. Downstream
        consumers should require ``audit_contract`` plus the exact identity fields,
        not a generic hand-authored ``full_rules_audited=true`` boolean.
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
) -> ExactRuleAudit:
    """Build exact-contract audit evidence from already-fetched public payloads.

    The field extraction is deliberately conservative. If an endpoint/schema does
    not expose contract-specific rule text, ``complete`` remains false rather than
    substituting a generic FAQ, UI label, or analyst summary.
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
        equivalent=bool(equivalent),
    )
