"""Fail-closed audit for weather SHADOW edge-per-contract economics.

The selected-side arithmetic guard proves total modeled EV from probability, executable
price, quantity, and fee. Shared ranking also carries modeled edge per contract, so that
field must not remain producer-trusted: a malformed row could report coherent total EV
but an arbitrary per-contract edge and distort category/portfolio ranking.

This helper independently binds each YES/NO edge to total EV / quantity and binds the
top-level edge to the selected side. It is read-only and cannot authorize live trading.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


BLOCKER = "SELECTED_SIDE_EDGE_PER_CONTRACT_UNVERIFIED"


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def audit_weather_selected_side_edge(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    blockers: list[str] = []
    side = str(row.get("best_shadow_side") or "").strip().upper()
    if side not in {"YES", "NO"}:
        blockers.append("SELECTED_SIDE_REQUIRED")

    economics = row.get("side_economics")
    records: dict[str, dict[str, Any]] = {}
    if not isinstance(economics, list) or len(economics) != 2:
        blockers.append("SIDE_ECONOMICS_REQUIRED")
    else:
        for item in economics:
            if not isinstance(item, dict):
                blockers.append("SIDE_ECONOMICS_REQUIRED")
                records = {}
                break
            item_side = str(item.get("side") or "").strip().upper()
            if item_side not in {"YES", "NO"} or item_side in records:
                blockers.append("SIDE_ECONOMICS_REQUIRED")
                records = {}
                break
            records[item_side] = item
        if set(records) != {"YES", "NO"}:
            blockers.append("SIDE_ECONOMICS_REQUIRED")
            records = {}

    recomputed_edges: dict[str, Decimal] = {}
    reported_edges: dict[str, Decimal] = {}
    if records:
        for item_side in ("YES", "NO"):
            record = records[item_side]
            quantity = _decimal(record.get("quantity", record.get("available_contracts")))
            ev = _decimal(record.get("modeled_after_fee_ev_cents"))
            edge = _decimal(record.get("modeled_after_fee_edge_cents_per_contract"))
            prefix = "SELECTED_SIDE" if item_side == side else "OPPOSITE_SIDE"
            if quantity is None or quantity <= 0:
                blockers.append(f"{prefix}_QUANTITY_INVALID")
                continue
            if ev is None:
                blockers.append(f"{prefix}_EV_INVALID")
                continue
            if edge is None:
                blockers.append(f"{prefix}_EDGE_INVALID")
                continue
            recomputed = ev / quantity
            recomputed_edges[item_side] = recomputed
            reported_edges[item_side] = edge
            if recomputed != edge:
                blockers.append(f"{prefix}_EDGE_ARITHMETIC_MISMATCH")

    selected_reported = reported_edges.get(side)
    top_level_edge = _decimal(row.get("modeled_after_fee_edge_cents_per_contract"))
    if selected_reported is not None and (
        top_level_edge is None or top_level_edge != selected_reported
    ):
        blockers.append("SELECTED_SIDE_TOP_LEVEL_EDGE_MISMATCH")

    blockers = sorted(set(blockers))
    verified = not blockers
    return {
        "category": "weather",
        "classification": "SHADOW",
        "selected_side": side if side in {"YES", "NO"} else None,
        "selected_side_edge_per_contract_verified": verified,
        "recomputed_side_edge_cents_per_contract": {
            item_side: format(value, "f") for item_side, value in sorted(recomputed_edges.items())
        },
        "blockers": blockers,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
