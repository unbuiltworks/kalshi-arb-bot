"""Read-only weather certification dimensions with authoritative evidence binding.

This wrapper closes two trust-boundary gaps in the canonical weather portfolio report:

* when Kalshi fixed-point ask/quantity fields are present, they are authoritative and
  cannot silently fall back to legacy integer-cent/integer-contract compatibility fields;
* exact market/source/station/day/bracket evidence is not enough to call settlement rules
  audited. Rounding/tie treatment, corrections/revisions, and final settlement lifecycle
  must also be affirmatively verified.

It never authorizes order entry or live probes.
"""
from __future__ import annotations

from typing import Any

from src.weather_book_representation_guard import audit_weather_book_representation
from src.weather_certification_dimensions import audit_weather_certification_dimensions
from src.weather_settlement_rule_semantics_guard import audit_weather_settlement_rule_semantics


def audit_weather_certification_dimensions_v2(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    base = audit_weather_certification_dimensions(row)
    yes_audit = audit_weather_book_representation(row, "yes")
    no_audit = audit_weather_book_representation(row, "no")
    semantics = audit_weather_settlement_rule_semantics(row)

    usable = [
        audit
        for audit in (yes_audit, no_audit)
        if audit.get("book_representation_verified") is True
    ]
    representation_verified = bool(usable)

    market_blockers = list(base.get("market_data_blockers") or [])
    if not representation_verified:
        side_blockers: list[str] = []
        for audit in (yes_audit, no_audit):
            side = str(audit.get("side") or "unknown").upper()
            for blocker in audit.get("blockers") or []:
                side_blockers.append(f"{side}_{blocker}")
        market_blockers.extend(side_blockers or ["EXECUTABLE_BOOK_REPRESENTATION_REQUIRED"])

    settlement_blockers = list(base.get("settlement_blockers") or [])
    semantics_verified = semantics.get("settlement_rule_semantics_verified") is True
    if not semantics_verified:
        settlement_blockers.extend(semantics.get("blockers") or ["SETTLEMENT_RULE_SEMANTICS_EVIDENCE_REQUIRED"])

    market_ok = base.get("market_data_verified") is True and representation_verified
    settlement_ok = base.get("settlement_rules_provenance_verified") is True and semantics_verified
    executable = (
        base.get("executable_shadow_prereqs_verified") is True
        and market_ok
        and settlement_ok
    )

    return {
        **base,
        "settlement_rules_provenance_verified": settlement_ok,
        "settlement_rule_semantics_verified": semantics_verified,
        "settlement_rule_semantics_audit": semantics,
        "settlement_blockers": sorted(set(settlement_blockers)),
        "market_data_verified": market_ok,
        "book_representation_verified": representation_verified,
        "yes_book_representation_audit": yes_audit,
        "no_book_representation_audit": no_audit,
        "market_data_blockers": sorted(set(market_blockers)),
        "executable_shadow_prereqs_verified": executable,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
