"""Normalize and verify selected-side weather SHADOW economics.

The legacy settlement-certified weather producer can emit ``best_shadow_side`` as an
entire side-economics object while its top-level fee/EV fields are copied from YES.
That is unsafe for shared portfolio reporting when NO is actually the best side.

This read-only guard derives a canonical YES/NO selection from the producer's own
``side_economics`` and binds the top-level economics to that exact selected record.
It never grants live readiness or order entry.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def normalize_selected_side_schema(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    out = deepcopy(row)
    blockers = set(out.get("blockers") or [])
    best = out.get("best_shadow_side")

    if best is None:
        out["selected_side_schema_verified"] = False
        out["selected_side_schema_blockers"] = ["BEST_SHADOW_SIDE_MISSING"]
        out["read_only"] = True
        out["order_entry"] = False
        out["live_probe_ready"] = False
        return out

    if isinstance(best, str):
        side = best.strip().upper()
    elif isinstance(best, dict):
        side = str(best.get("side") or "").strip().upper()
    else:
        side = ""

    if side not in {"YES", "NO"}:
        blockers.add("BEST_SHADOW_SIDE_INVALID")
        out["selected_side_schema_verified"] = False
        out["selected_side_schema_blockers"] = ["BEST_SHADOW_SIDE_INVALID"]
        out["ev_certified_shadow"] = False
        out["modeled_taker_fee_cents"] = None
        out["modeled_after_fee_ev_cents"] = None
        out["best_shadow_side"] = None
        out["blockers"] = sorted(blockers)
        out["read_only"] = True
        out["order_entry"] = False
        out["live_probe_ready"] = False
        return out

    matches = [
        item for item in (out.get("side_economics") or [])
        if isinstance(item, dict) and str(item.get("side") or "").strip().upper() == side
    ]
    if len(matches) != 1:
        blockers.add("SELECTED_SIDE_ECONOMICS_AMBIGUOUS")
        out["selected_side_schema_verified"] = False
        out["selected_side_schema_blockers"] = ["SELECTED_SIDE_ECONOMICS_AMBIGUOUS"]
        out["ev_certified_shadow"] = False
        out["modeled_taker_fee_cents"] = None
        out["modeled_after_fee_ev_cents"] = None
        out["best_shadow_side"] = None
        out["blockers"] = sorted(blockers)
        out["read_only"] = True
        out["order_entry"] = False
        out["live_probe_ready"] = False
        return out

    selected = matches[0]
    fee = _finite_decimal(selected.get("modeled_taker_fee_cents"))
    ev = _finite_decimal(selected.get("modeled_after_fee_ev_cents"))
    if fee is None or fee < 0 or ev is None:
        blockers.add("SELECTED_SIDE_ECONOMICS_INVALID")
        out["selected_side_schema_verified"] = False
        out["selected_side_schema_blockers"] = ["SELECTED_SIDE_ECONOMICS_INVALID"]
        out["ev_certified_shadow"] = False
        out["modeled_taker_fee_cents"] = None
        out["modeled_after_fee_ev_cents"] = None
        out["best_shadow_side"] = None
        out["blockers"] = sorted(blockers)
        out["read_only"] = True
        out["order_entry"] = False
        out["live_probe_ready"] = False
        return out

    # Canonical selected-side schema. Preserve the full selected record separately for
    # diagnostics, but make the top-level fields unambiguous for downstream consumers.
    out["best_shadow_side_detail"] = deepcopy(selected)
    out["best_shadow_side"] = side
    out["modeled_taker_fee_cents"] = selected.get("modeled_taker_fee_cents")
    out["modeled_after_fee_ev_cents"] = selected.get("modeled_after_fee_ev_cents")
    out["modeled_after_fee_edge_cents_per_contract"] = selected.get(
        "modeled_after_fee_edge_cents_per_contract"
    )
    out["selected_side_schema_verified"] = True
    out["selected_side_schema_blockers"] = []
    out["read_only"] = True
    out["order_entry"] = False
    out["live_probe_ready"] = False
    return out
