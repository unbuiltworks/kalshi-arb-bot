"""Fail-closed blocker telemetry normalization for weather SHADOW reporting.

Read-only research helper. Explicitly present blocker fields must be structurally valid;
falsey malformed values such as ``""``, ``0``, ``False``, or ``{}`` are not equivalent
to an empty blocker list. Individual blocker entries must also be non-empty strings so a
container such as ``[None, ""]`` cannot silently collapse to "no blockers".
"""
from __future__ import annotations

from typing import Any

_ALLOWED_BLOCKER_CONTAINERS = (list, tuple, set)


def audit_weather_blocker_telemetry(row: dict[str, Any]) -> dict[str, Any]:
    """Return canonical blocker values plus structural verification state.

    Precedence is explicit: if ``blockers`` is present, it is authoritative and must be
    a list/tuple/set. Only when that key is absent may ``readiness_blockers`` be used.
    If both keys are absent, an empty blocker list is valid. Any malformed explicitly
    supplied field or malformed blocker entry fails closed.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    if "blockers" in row:
        source = "blockers"
        values = row["blockers"]
    elif "readiness_blockers" in row:
        source = "readiness_blockers"
        values = row["readiness_blockers"]
    else:
        source = "absent"
        values = []

    if not isinstance(values, _ALLOWED_BLOCKER_CONTAINERS):
        return {
            "blocker_telemetry_verified": False,
            "blocker_source": source,
            "blockers": ["MALFORMED_BLOCKER_TELEMETRY"],
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }

    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            return {
                "blocker_telemetry_verified": False,
                "blocker_source": source,
                "blockers": ["MALFORMED_BLOCKER_TELEMETRY"],
                "read_only": True,
                "order_entry": False,
                "live_probe_ready": False,
            }
        normalized.add(value.strip())

    blockers = sorted(normalized)
    return {
        "blocker_telemetry_verified": True,
        "blocker_source": source,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
