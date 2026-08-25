"""Fail-closed binding between certified weather EV and executable book evidence.

A weather row may have a valid executable book on one side while its scored/modelled
best side points at the other side. For portfolio SHADOW economics, proving that
*either* side is executable is insufficient: the side whose EV is being ranked must be
bound to defensible q1 executable depth.

This helper is read-only. It cannot authorize live probes or order entry.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from src.weather_book_representation_guard import audit_weather_book_representation


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def audit_weather_scored_side_book_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Require the scored weather side to be the side with audited executable depth."""
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    raw_side = row.get("best_shadow_side")
    side = str(raw_side or "").strip().upper()
    if side not in {"YES", "NO"}:
        blockers.append("SCORED_SIDE_REQUIRED")
        side_audit: dict[str, Any] | None = None
    else:
        side_audit = audit_weather_book_representation(row, side=side.lower())
        if side_audit.get("book_representation_verified") is not True:
            blockers.append("SCORED_SIDE_EXECUTABLE_BOOK_UNVERIFIED")
            blockers.extend(
                f"SCORED_SIDE_{b}" for b in (side_audit.get("blockers") or [])
            )

    ev = _decimal(row.get("modeled_after_fee_ev_cents"))
    if ev is None:
        blockers.append("SCORED_SIDE_EV_REQUIRED")

    # If side_economics is present, bind the selected summary to the same side and EV.
    # Missing detail remains fail-closed because otherwise a stale top-level best-side
    # summary could be detached from the executable side economics it supposedly ranks.
    economics = row.get("side_economics")
    selected: dict[str, Any] | None = None
    if not isinstance(economics, list):
        blockers.append("SIDE_ECONOMICS_EVIDENCE_REQUIRED")
    elif side in {"YES", "NO"}:
        matches = [x for x in economics if isinstance(x, dict) and str(x.get("side") or "").strip().upper() == side]
        if len(matches) != 1:
            blockers.append("SCORED_SIDE_ECONOMICS_IDENTITY_UNVERIFIED")
        else:
            selected = matches[0]
            selected_ev = _decimal(selected.get("modeled_after_fee_ev_cents"))
            if ev is None or selected_ev is None or selected_ev != ev:
                blockers.append("SCORED_SIDE_EV_MISMATCH")

    blockers = sorted(set(blockers))
    return {
        "category": "weather",
        "classification": "SHADOW",
        "scored_side": side if side in {"YES", "NO"} else None,
        "scored_side_book_binding_verified": not blockers,
        "scored_side_book_audit": side_audit,
        "scored_side_economics": selected,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
