"""Read-only weather settlement rule-semantics audit.

Exact market/source identity is necessary but not sufficient to call a temperature
contract's settlement rules audited. Before weather SHADOW economics can be treated as
settlement/rule certified, require affirmative evidence for the remaining rule surfaces
that can change a terminal outcome at a boundary or after publication:

* rounding / tie / published-value semantics;
* correction / revision semantics;
* settlement lifecycle / final-record semantics.

The rule-semantics evidence must also be bound to the same exact Kalshi market page that
was independently audited for settlement-source identity. This prevents a generic Series,
FAQ, or different-event page from being reused as provenance for an exact contract.

This module is intentionally fail-closed and has no order-entry path.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

_REQUIRED_LITERAL_TRUE = (
    "rounding_tie_semantics_verified",
    "correction_revision_semantics_verified",
    "settlement_lifecycle_verified",
)

# Do not infer or guess a numerical transform from ticker shape or producer text. Add a
# new transform here only after the exact Kalshi weather rule semantics are independently
# audited and regression-tested end to end.
_AUDITED_TERMINAL_VALUE_TRANSFORMS = {"AS_PUBLISHED"}


def _official_kalshi_https_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme == "https"
        and (host == "kalshi.com" or host.endswith(".kalshi.com"))
        and bool(parsed.path and parsed.path != "/")
    )


def _normalized_url(value: Any) -> str | None:
    if not _official_kalshi_https_url(value):
        return None
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "/").rstrip("/") or "/"
    return urlunparse(("https", host, path, "", parsed.query, ""))


def _exact_market_evidence_url(row: dict[str, Any]) -> Any:
    nested = row.get("exact_market_source_audit")
    if isinstance(nested, dict) and nested.get("evidence_url"):
        return nested.get("evidence_url")
    return row.get("exact_market_evidence_url")


def audit_weather_settlement_rule_semantics(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    for key in _REQUIRED_LITERAL_TRUE:
        if row.get(key) is not True:
            blockers.append(f"{key.upper()}_REQUIRED")

    rounding_mode = row.get("terminal_value_transform")
    normalized_transform = rounding_mode.strip().upper() if isinstance(rounding_mode, str) else None
    if not normalized_transform:
        blockers.append("TERMINAL_VALUE_TRANSFORM_EVIDENCE_REQUIRED")
    elif normalized_transform not in _AUDITED_TERMINAL_VALUE_TRANSFORMS:
        blockers.append("TERMINAL_VALUE_TRANSFORM_UNAUDITED")

    lifecycle_source = row.get("settlement_lifecycle_evidence_url")
    lifecycle_normalized = _normalized_url(lifecycle_source)
    if lifecycle_normalized is None:
        blockers.append("SETTLEMENT_LIFECYCLE_OFFICIAL_SOURCE_REQUIRED")

    exact_market_source = _exact_market_evidence_url(row)
    exact_market_normalized = _normalized_url(exact_market_source)
    if exact_market_normalized is None:
        blockers.append("SETTLEMENT_RULE_EXACT_MARKET_EVIDENCE_REQUIRED")
    elif lifecycle_normalized is not None and lifecycle_normalized != exact_market_normalized:
        blockers.append("SETTLEMENT_RULE_EVIDENCE_NOT_BOUND_TO_EXACT_MARKET")

    verified = not blockers
    return {
        "settlement_rule_semantics_verified": verified,
        "blockers": sorted(set(blockers)),
        "terminal_value_transform": normalized_transform,
        "settlement_lifecycle_evidence_url": lifecycle_normalized,
        "exact_market_evidence_url": exact_market_normalized,
        "rule_evidence_bound_to_exact_market": (
            lifecycle_normalized is not None
            and exact_market_normalized is not None
            and lifecycle_normalized == exact_market_normalized
        ),
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
