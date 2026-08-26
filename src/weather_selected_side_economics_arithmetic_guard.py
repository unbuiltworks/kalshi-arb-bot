"""Fail-closed arithmetic audit for selected-side weather SHADOW economics.

Book identity and depth are necessary but not sufficient evidence that a reported weather
EV is economically coherent.  A producer could otherwise attach a plausible current ask
and quantity to stale or malformed probability/fee/EV fields and contaminate portfolio
ranking.  This helper independently recomputes selected-side after-fee EV from the
producer's explicit probability, executable price, quantity, and modeled fee.

It is read-only and cannot authorize order entry or live probes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


BLOCKER = "SELECTED_SIDE_ECONOMICS_ARITHMETIC_UNVERIFIED"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _price_dollars(record: dict[str, Any]) -> Decimal | None:
    if "ask_dollars" in record:
        price = _decimal(record.get("ask_dollars"))
    else:
        cents = record.get("ask_cents")
        if not isinstance(cents, int) or isinstance(cents, bool):
            return None
        price = Decimal(cents) / Decimal(100)
    if price is None or not (Decimal(0) < price < Decimal(1)):
        return None
    return price


def _quantity(record: dict[str, Any]) -> Decimal | None:
    value = record.get("quantity", record.get("available_contracts"))
    qty = _decimal(value)
    return qty if qty is not None and qty > 0 else None


def _fee_cents(record: dict[str, Any]) -> Decimal | None:
    if "modeled_taker_fee_cents" in record:
        fee = _decimal(record.get("modeled_taker_fee_cents"))
    elif "modeled_taker_fee_dollars" in record:
        dollars = _decimal(record.get("modeled_taker_fee_dollars"))
        fee = None if dollars is None else dollars * Decimal(100)
    else:
        return None
    return fee if fee is not None and fee >= 0 else None


def audit_weather_selected_side_economics_arithmetic(row: dict[str, Any]) -> dict[str, Any]:
    """Independently recompute the selected-side modeled after-fee EV.

    Required evidence:
    - literal YES/NO selected side;
    - exactly one matching side_economics record;
    - finite probability in [0, 1];
    - finite executable price, positive quantity, and non-negative modeled fee;
    - reported selected-side EV exactly equals the independent recomputation.

    This guard deliberately does not decide whether the probability model or fee source is
    authoritative; those are separate certification dimensions.  It only proves the
    arithmetic is internally coherent before those evidence surfaces are combined.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    side = str(row.get("best_shadow_side") or "").strip().upper()
    if side not in {"YES", "NO"}:
        blockers.append("SELECTED_SIDE_REQUIRED")

    economics = row.get("side_economics")
    selected: dict[str, Any] | None = None
    if not isinstance(economics, list) or side not in {"YES", "NO"}:
        blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
    else:
        matches = [
            item for item in economics
            if isinstance(item, dict) and str(item.get("side") or "").strip().upper() == side
        ]
        if len(matches) != 1:
            blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
        else:
            selected = matches[0]

    recomputed_ev: Decimal | None = None
    reported_ev: Decimal | None = None
    if selected is not None:
        probability = _decimal(selected.get("forecast_side_probability"))
        price = _price_dollars(selected)
        quantity = _quantity(selected)
        fee_cents = _fee_cents(selected)
        reported_ev = _decimal(selected.get("modeled_after_fee_ev_cents"))

        if probability is None or probability < 0 or probability > 1:
            blockers.append("SELECTED_SIDE_PROBABILITY_INVALID")
        if price is None:
            blockers.append("SELECTED_SIDE_PRICE_INVALID")
        if quantity is None:
            blockers.append("SELECTED_SIDE_QUANTITY_INVALID")
        if fee_cents is None:
            blockers.append("SELECTED_SIDE_FEE_INVALID")
        if reported_ev is None:
            blockers.append("SELECTED_SIDE_EV_INVALID")

        if (
            probability is not None and Decimal(0) <= probability <= Decimal(1)
            and price is not None and quantity is not None and fee_cents is not None
        ):
            recomputed_ev = (
                probability * Decimal(100) * quantity
                - price * Decimal(100) * quantity
                - fee_cents
            )
            if reported_ev is None or recomputed_ev != reported_ev:
                blockers.append("SELECTED_SIDE_EV_ARITHMETIC_MISMATCH")

    top_level_ev = _decimal(row.get("modeled_after_fee_ev_cents"))
    if reported_ev is not None and (top_level_ev is None or top_level_ev != reported_ev):
        blockers.append("SELECTED_SIDE_TOP_LEVEL_EV_MISMATCH")

    blockers = sorted(set(blockers))
    verified = not blockers
    return {
        "category": "weather",
        "classification": "SHADOW",
        "selected_side": side if side in {"YES", "NO"} else None,
        "selected_side_economics_arithmetic_verified": verified,
        "recomputed_after_fee_ev_cents": (
            format(recomputed_ev, "f") if recomputed_ev is not None else None
        ),
        "reported_after_fee_ev_cents": (
            format(reported_ev, "f") if reported_ev is not None else None
        ),
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
