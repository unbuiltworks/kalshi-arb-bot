"""Fail-closed binding between certified weather EV and executable book evidence.

A weather row may have a valid executable book on one side while its scored/modelled
best side points at the other side. For portfolio SHADOW economics, proving that
*either* side is executable is insufficient: the side whose EV is being ranked must be
bound to defensible q1 executable depth.

The selected side economics must also be bound to the audited executable ask and
quantity. A stale/lower economics price cannot ride alongside a newer valid book and
manufacture shadow edge, and modeled quantity cannot exceed displayed executable depth.

The legacy settlement-certified producer historically emitted ``best_shadow_side`` as
an entire side-economics object and copied the top-level modeled EV from YES regardless
of which side actually ranked best. This guard accepts that legacy object only as
identity evidence, then requires the top-level scored EV to agree with the selected side.
That makes the producer/reporting schema mismatch fail closed instead of silently
ranking the wrong side.

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


def _scored_side(raw_side: Any) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Normalize explicit side strings and legacy side-economics objects fail closed."""
    blockers: list[str] = []
    embedded: dict[str, Any] | None = None
    if isinstance(raw_side, dict):
        embedded = raw_side
        side = str(raw_side.get("side") or "").strip().upper()
        if side not in {"YES", "NO"}:
            blockers.append("SCORED_SIDE_REQUIRED")
            return "", embedded, blockers
        blockers.append("LEGACY_SCORED_SIDE_OBJECT_SCHEMA")
        return side, embedded, blockers

    side = str(raw_side or "").strip().upper()
    if side not in {"YES", "NO"}:
        blockers.append("SCORED_SIDE_REQUIRED")
        return "", None, blockers
    return side, None, blockers


def audit_weather_scored_side_book_binding(row: dict[str, Any]) -> dict[str, Any]:
    """Require the scored weather side and its economics to match audited executable depth."""
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    side, embedded_best, side_blockers = _scored_side(row.get("best_shadow_side"))
    blockers.extend(side_blockers)

    if side not in {"YES", "NO"}:
        side_audit: dict[str, Any] | None = None
    else:
        side_audit = audit_weather_book_representation(row, side=side.lower())
        if side_audit.get("book_representation_verified") is not True:
            blockers.append("SCORED_SIDE_EXECUTABLE_BOOK_UNVERIFIED")
            blockers.extend(
                f"SCORED_SIDE_{b}" for b in (side_audit.get("blockers") or [])
            )

    top_level_ev = _decimal(row.get("modeled_after_fee_ev_cents"))
    if top_level_ev is None:
        blockers.append("SCORED_SIDE_EV_REQUIRED")

    economics = row.get("side_economics")
    selected: dict[str, Any] | None = None
    selected_ev: Decimal | None = None
    if not isinstance(economics, list):
        blockers.append("SIDE_ECONOMICS_EVIDENCE_REQUIRED")
    elif side in {"YES", "NO"}:
        matches = [
            x for x in economics
            if isinstance(x, dict) and str(x.get("side") or "").strip().upper() == side
        ]
        if len(matches) != 1:
            blockers.append("SCORED_SIDE_ECONOMICS_IDENTITY_UNVERIFIED")
        else:
            selected = matches[0]
            selected_ev = _decimal(selected.get("modeled_after_fee_ev_cents"))
            if selected_ev is None:
                blockers.append("SCORED_SIDE_EV_REQUIRED")
            elif top_level_ev is None or selected_ev != top_level_ev:
                blockers.append("SCORED_SIDE_EV_MISMATCH")

            if embedded_best is not None:
                embedded_ev = _decimal(embedded_best.get("modeled_after_fee_ev_cents"))
                embedded_side = str(embedded_best.get("side") or "").strip().upper()
                if embedded_side != side or embedded_ev is None or selected_ev is None or embedded_ev != selected_ev:
                    blockers.append("LEGACY_SCORED_SIDE_OBJECT_MISMATCH")

            audited_price, audited_qty = _audited_price_and_quantity(side_audit)
            selected_price = _selected_economics_price(selected)
            selected_qty = _selected_economics_quantity(selected)
            if audited_price is None or selected_price is None or selected_price != audited_price:
                blockers.append("SCORED_SIDE_ASK_MISMATCH")
            if audited_qty is None or selected_qty is None or selected_qty > audited_qty:
                blockers.append("SCORED_SIDE_QUANTITY_EXCEEDS_DEPTH")

    blockers = sorted(set(blockers))
    verified = not blockers
    return {
        "category": "weather",
        "classification": "SHADOW",
        "scored_side": side if side in {"YES", "NO"} else None,
        "scored_side_book_binding_verified": verified,
        "scored_side_book_audit": side_audit,
        "scored_side_economics": selected,
        "scored_side_modeled_after_fee_ev_cents": str(selected_ev) if selected_ev is not None else None,
        "legacy_scored_side_schema_detected": embedded_best is not None,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }