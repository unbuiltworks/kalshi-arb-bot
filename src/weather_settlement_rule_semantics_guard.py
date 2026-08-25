"""Read-only weather settlement rule-semantics audit.

Exact market/source identity is necessary but not sufficient to call a temperature
contract's settlement rules audited.  Before weather SHADOW economics can be treated as
settlement/rule certified, require affirmative evidence for the remaining rule surfaces
that can change a terminal outcome at a boundary or after publication:

* rounding / tie / published-value semantics;
* correction / revision semantics;
* settlement lifecycle / final-record semantics.

This module is intentionally fail-closed and has no order-entry path.
"""
from __future__ import annotations

from typing import Any

_REQUIRED_LITERAL_TRUE = (
    "rounding_tie_semantics_verified",
    "correction_revision_semantics_verified",
    "settlement_lifecycle_verified",
)


def audit_weather_settlement_rule_semantics(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    for key in _REQUIRED_LITERAL_TRUE:
        if row.get(key) is not True:
            blockers.append(f"{key.upper()}_REQUIRED")

    rounding_mode = row.get("terminal_value_transform")
    if not isinstance(rounding_mode, str) or not rounding_mode.strip():
        blockers.append("TERMINAL_VALUE_TRANSFORM_EVIDENCE_REQUIRED")

    lifecycle_source = row.get("settlement_lifecycle_evidence_url")
    if not isinstance(lifecycle_source, str) or not lifecycle_source.startswith("https://"):
        blockers.append("SETTLEMENT_LIFECYCLE_SOURCE_EVIDENCE_REQUIRED")

    verified = not blockers
    return {
        "settlement_rule_semantics_verified": verified,
        "blockers": sorted(set(blockers)),
        "terminal_value_transform": rounding_mode.strip() if isinstance(rounding_mode, str) else None,
        "settlement_lifecycle_evidence_url": lifecycle_source if isinstance(lifecycle_source, str) else None,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
