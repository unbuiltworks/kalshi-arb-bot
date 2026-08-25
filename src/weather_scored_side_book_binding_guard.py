"""Fail-closed binding between certified weather EV and executable book evidence.

A weather row may have a valid executable book on one side while its scored/modelled
best side points at the other side. For portfolio SHADOW economics, proving that
*either* side is executable is insufficient: the side whose EV is being ranked must be
bound to defensible q1 executable depth.

The selected side economics must also be bound to the audited executable ask and
quantity. A stale/lower economics price cannot ride alongside a newer valid book and
manufacture shadow edge, and modeled quantity cannot exceed displayed executable depth.

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


def _audited_price_and_quantity(audit: dict[str, Any] | None) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(audit, dict) or audit.get("book_representation_verified") is not True:
        return None, None
    source = audit.get("book_representation_source")
    if source == "fixed_point":
        return _decimal(audit.get("fixed_point_price")), _decimal(audit.get("fixed_point_quantity"))
    if source == "legacy":
        return _decimal(audit.get("legacy_price")), _decimal(audit.get("legacy_quantity"))
    return None, None


def _selected_economics_price(selected: dict[str, Any]) -> Decimal | None:
    if "ask_dollars" in selected:
        price = _decimal(selected.get("ask_dollars"))
        return price if price is not None and Decimal("0") < price < Decimal("1") else None
    cents = selected.get("ask_cents")
    if not isinstance(cents, int) or isinstance(cents, bool) or not 1 <= cents <= 99:
        return None
    return Decimal(cents) / Decimal(100)


def _selected_economics_quantity(selected: dict[str, Any]) -> Decimal | None:
    value = selected.get("quantity", selected.get("available_contracts"))
    qty = _decimal(value)
    return qty if qty is not None and qty > 0 else None


def audit_weather_scored_side_book_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Require the scored weather side and its economics to match audited executable depth."""
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

            audited_price, audited_qty = _audited_price_and_quantity(side_audit)
            selected_price = _selected_economics_price(selected)
            selected_qty = _selected_economics_quantity(selected)
            if audited_price is None or selected_price is None or selected_price != audited_price:
                blockers.append("SCORED_SIDE_ASK_MISMATCH")
            if audited_qty is None or selected_qty is None or selected_qty > audited_qty:
                blockers.append("SCORED_SIDE_QUANTITY_EXCEEDS_DEPTH")

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
