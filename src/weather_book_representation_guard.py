"""Fail-closed read-only audit for weather executable-book representations.

Kalshi weather rows can carry either fixed-point ask/quantity fields or legacy
integer-cent/integer-contract compatibility fields. Once any fixed-point field is
present for a side, that representation is authoritative for the audit and we must not
silently fall back to legacy fields if the fixed-point pair is malformed. If both
representations are present, they must agree on price to the exact cent boundary and
both must independently show at least one executable contract.

This helper never authorizes order entry or live probes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _legacy(side: str, row: dict[str, Any]) -> tuple[bool, Decimal | None, Decimal | None]:
    cents = row.get(f"{side}_ask_cents")
    qty = row.get(f"{side}_available_contracts")
    valid = (
        isinstance(cents, int)
        and not isinstance(cents, bool)
        and 1 <= cents <= 99
        and isinstance(qty, int)
        and not isinstance(qty, bool)
        and qty >= 1
    )
    if not valid:
        return False, None, None
    return True, Decimal(cents) / Decimal(100), Decimal(qty)


def _fixed(side: str, row: dict[str, Any]) -> tuple[bool, Decimal | None, Decimal | None, bool]:
    price_key = f"{side}_ask_dollars"
    qty_key = f"{side}_ask_qty_fp"
    present = price_key in row or qty_key in row
    if not present:
        return False, None, None, False
    price = _decimal(row.get(price_key))
    qty = _decimal(row.get(qty_key))
    valid = (
        price is not None
        and qty is not None
        and Decimal("0") < price < Decimal("1")
        and qty >= Decimal("1")
    )
    return valid, price, qty, True


def audit_weather_book_representation(row: dict[str, Any], side: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")
    side = str(side).strip().lower()
    if side not in {"yes", "no"}:
        raise ValueError("side must be yes or no")

    legacy_ok, legacy_price, legacy_qty = _legacy(side, row)
    fixed_ok, fixed_price, fixed_qty, fixed_present = _fixed(side, row)
    blockers: list[str] = []

    if fixed_present and not fixed_ok:
        blockers.append("FIXED_POINT_BOOK_REPRESENTATION_INVALID")
    elif fixed_present:
        # Fixed-point is authoritative when present. If legacy compatibility fields are
        # also present, require their independently executable representation to agree.
        legacy_fields_present = (
            f"{side}_ask_cents" in row or f"{side}_available_contracts" in row
        )
        if legacy_fields_present and not legacy_ok:
            blockers.append("LEGACY_BOOK_REPRESENTATION_INVALID")
        elif legacy_ok and legacy_price != fixed_price:
            blockers.append("BOOK_PRICE_REPRESENTATION_CONFLICT")
    elif not legacy_ok:
        blockers.append("EXECUTABLE_BOOK_REPRESENTATION_REQUIRED")

    verified = not blockers
    source = "fixed_point" if fixed_present else ("legacy" if legacy_ok else None)
    return {
        "category": "weather",
        "classification": "SHADOW",
        "side": side,
        "book_representation_verified": verified,
        "book_representation_source": source,
        "fixed_point_present": fixed_present,
        "fixed_point_price": str(fixed_price) if fixed_price is not None else None,
        "fixed_point_quantity": str(fixed_qty) if fixed_qty is not None else None,
        "legacy_price": str(legacy_price) if legacy_price is not None else None,
        "legacy_quantity": str(legacy_qty) if legacy_qty is not None else None,
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
