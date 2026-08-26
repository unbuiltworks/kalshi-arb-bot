"""Fail-closed arithmetic audit for selected-side weather SHADOW economics.

Book identity and depth are necessary but not sufficient evidence that a reported weather
EV is economically coherent. A producer could otherwise attach a plausible current ask
to stale or malformed probability/fee/EV fields and contaminate portfolio ranking.

This helper independently recomputes arithmetic for *both* YES and NO records before it
accepts the producer-selected best side. Auditing only the selected side is insufficient:
a fabricated EV on the opposite side can change which side appears best without ever
being checked. Both records must therefore be arithmetically coherent, their forecast
probabilities must describe one binary distribution (YES + NO = 1), and the unique best
side from the independently recomputed EVs must match ``best_shadow_side``.

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


def _audit_record(record: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, list[str]]:
    blockers: list[str] = []
    probability = _decimal(record.get("forecast_side_probability"))
    price = _price_dollars(record)
    quantity = _quantity(record)
    fee_cents = _fee_cents(record)
    reported_ev = _decimal(record.get("modeled_after_fee_ev_cents"))

    if probability is None or probability < 0 or probability > 1:
        blockers.append("PROBABILITY_INVALID")
    if price is None:
        blockers.append("PRICE_INVALID")
    if quantity is None:
        blockers.append("QUANTITY_INVALID")
    if fee_cents is None:
        blockers.append("FEE_INVALID")
    if reported_ev is None:
        blockers.append("EV_INVALID")

    recomputed_ev: Decimal | None = None
    if (
        probability is not None
        and Decimal(0) <= probability <= Decimal(1)
        and price is not None
        and quantity is not None
        and fee_cents is not None
    ):
        recomputed_ev = (
            probability * Decimal(100) * quantity
            - price * Decimal(100) * quantity
            - fee_cents
        )
        if reported_ev is None or recomputed_ev != reported_ev:
            blockers.append("EV_ARITHMETIC_MISMATCH")

    return recomputed_ev, reported_ev, blockers


def audit_weather_selected_side_economics_arithmetic(row: dict[str, Any]) -> dict[str, Any]:
    """Independently recompute both-side modeled after-fee EV before ranking.

    Required evidence:
    - literal YES/NO selected side;
    - exactly one YES and one NO ``side_economics`` record;
    - finite probability in [0, 1] for both sides;
    - YES and NO probabilities sum exactly to one binary distribution;
    - finite executable price, positive quantity, and non-negative modeled fee for both;
    - each reported side EV exactly equals its independent recomputation;
    - the unique best independently recomputed side matches the declared selected side;
    - top-level EV exactly matches that selected record.

    This guard deliberately does not decide whether the probability model or fee source is
    authoritative; those are separate certification dimensions. It proves the arithmetic
    and side ranking are internally coherent before those evidence surfaces are combined.
    """
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    side = str(row.get("best_shadow_side") or "").strip().upper()
    if side not in {"YES", "NO"}:
        blockers.append("SELECTED_SIDE_REQUIRED")

    economics = row.get("side_economics")
    records: dict[str, dict[str, Any]] = {}
    if not isinstance(economics, list) or len(economics) != 2:
        blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
    else:
        for item in economics:
            if not isinstance(item, dict):
                blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
                records = {}
                break
            item_side = str(item.get("side") or "").strip().upper()
            if item_side not in {"YES", "NO"} or item_side in records:
                blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
                records = {}
                break
            records[item_side] = item
        if set(records) != {"YES", "NO"}:
            blockers.append("SELECTED_SIDE_ECONOMICS_REQUIRED")
            records = {}

    probabilities: dict[str, Decimal] = {}
    if records:
        for item_side in ("YES", "NO"):
            probability = _decimal(records[item_side].get("forecast_side_probability"))
            if probability is not None and Decimal(0) <= probability <= Decimal(1):
                probabilities[item_side] = probability
        if set(probabilities) == {"YES", "NO"} and probabilities["YES"] + probabilities["NO"] != Decimal(1):
            blockers.append("PROBABILITY_COMPLEMENT_MISMATCH")

    recomputed: dict[str, Decimal] = {}
    reported: dict[str, Decimal] = {}
    if records:
        for item_side in ("YES", "NO"):
            recomputed_ev, reported_ev, record_blockers = _audit_record(records[item_side])
            selected_record = item_side == side
            for blocker in record_blockers:
                if selected_record:
                    blockers.append(f"SELECTED_SIDE_{blocker}")
                else:
                    blockers.append(f"OPPOSITE_SIDE_{blocker}")
            if recomputed_ev is not None:
                recomputed[item_side] = recomputed_ev
            if reported_ev is not None:
                reported[item_side] = reported_ev

    recomputed_best_side: str | None = None
    if set(recomputed) == {"YES", "NO"}:
        if recomputed["YES"] == recomputed["NO"]:
            blockers.append("RECOMPUTED_BEST_SIDE_TIE")
        else:
            recomputed_best_side = "YES" if recomputed["YES"] > recomputed["NO"] else "NO"
            if side in {"YES", "NO"} and recomputed_best_side != side:
                blockers.append("RECOMPUTED_BEST_SIDE_MISMATCH")

    selected_recomputed_ev = recomputed.get(side)
    selected_reported_ev = reported.get(side)
    top_level_ev = _decimal(row.get("modeled_after_fee_ev_cents"))
    if selected_reported_ev is not None and (
        top_level_ev is None or top_level_ev != selected_reported_ev
    ):
        blockers.append("SELECTED_SIDE_TOP_LEVEL_EV_MISMATCH")

    blockers = sorted(set(blockers))
    verified = not blockers
    return {
        "category": "weather",
        "classification": "SHADOW",
        "selected_side": side if side in {"YES", "NO"} else None,
        "recomputed_best_side": recomputed_best_side,
        "selected_side_economics_arithmetic_verified": verified,
        "recomputed_after_fee_ev_cents": (
            format(selected_recomputed_ev, "f") if selected_recomputed_ev is not None else None
        ),
        "reported_after_fee_ev_cents": (
            format(selected_reported_ev, "f") if selected_reported_ev is not None else None
        ),
        "recomputed_side_economics": {
            item_side: format(value, "f") for item_side, value in sorted(recomputed.items())
        },
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
