"""Normalize and verify selected-side weather SHADOW economics.

The legacy settlement-certified weather producer can emit ``best_shadow_side`` as an
entire side-economics object while its top-level fee/EV fields are copied from YES.
That is unsafe for shared portfolio reporting when NO is actually the best side.

This read-only guard independently derives the unique best YES/NO side from the
producer's own ``side_economics`` and requires the producer-declared side to agree with
that recomputation. It then binds the top-level economics to that exact selected record.
A stale/wrong declared side or a tie fails closed instead of silently changing portfolio
ranking. It never grants live readiness or order entry.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any


def _finite_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed.is_finite() else None


def _normalized_blockers(value: Any) -> tuple[set[str], bool]:
    """Return normalized blockers and whether producer telemetry was structurally valid."""
    if value is None:
        return set(), True
    if not isinstance(value, (list, tuple, set)):
        return {"MALFORMED_BLOCKER_TELEMETRY"}, False

    normalized: set[str] = set()
    valid = True
    for item in value:
        if not isinstance(item, str) or not item.strip():
            valid = False
            continue
        normalized.add(item.strip())
    if not valid:
        normalized.add("MALFORMED_BLOCKER_TELEMETRY")
    return normalized, valid


def _fail_closed(out: dict[str, Any], blocker: str) -> dict[str, Any]:
    """Strip all selected-side economics when canonical side binding is not proven."""
    blockers, _ = _normalized_blockers(out.get("blockers"))
    blockers.add(blocker)
    out["selected_side_schema_verified"] = False
    out["selected_side_schema_blockers"] = [blocker]
    out["ev_certified_shadow"] = False
    out["best_shadow_side"] = None
    out["best_shadow_side_detail"] = None
    out["modeled_taker_fee_cents"] = None
    out["modeled_after_fee_ev_cents"] = None
    out["modeled_after_fee_edge_cents_per_contract"] = None
    out["blockers"] = sorted(blockers)
    out["read_only"] = True
    out["order_entry"] = False
    out["live_probe_ready"] = False
    return out


def _declared_side(best: Any) -> str:
    if isinstance(best, str):
        return best.strip().upper()
    if isinstance(best, dict):
        return str(best.get("side") or "").strip().upper()
    return ""


def _side_records(out: dict[str, Any]) -> tuple[dict[str, dict[str, Any]] | None, str | None]:
    economics = out.get("side_economics")
    if not isinstance(economics, list):
        return None, "SIDE_ECONOMICS_EVIDENCE_REQUIRED"

    records: dict[str, dict[str, Any]] = {}
    for side in ("YES", "NO"):
        matches = [
            item for item in economics
            if isinstance(item, dict) and str(item.get("side") or "").strip().upper() == side
        ]
        if len(matches) != 1:
            return None, "SIDE_ECONOMICS_IDENTITY_UNVERIFIED"
        records[side] = matches[0]
    return records, None


def _validated_record(record: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal] | None:
    fee = _finite_decimal(record.get("modeled_taker_fee_cents"))
    ev = _finite_decimal(record.get("modeled_after_fee_ev_cents"))
    edge = _finite_decimal(record.get("modeled_after_fee_edge_cents_per_contract"))
    if fee is None or fee < 0 or ev is None or edge is None:
        return None
    return fee, ev, edge


def normalize_selected_side_schema(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise TypeError("row must be a dict")

    out = deepcopy(row)
    existing_blockers, blockers_valid = _normalized_blockers(out.get("blockers"))
    out["blockers"] = sorted(existing_blockers)
    if not blockers_valid:
        return _fail_closed(out, "MALFORMED_BLOCKER_TELEMETRY")

    best = out.get("best_shadow_side")

    if best is None:
        return _fail_closed(out, "BEST_SHADOW_SIDE_MISSING")

    declared_side = _declared_side(best)
    if declared_side not in {"YES", "NO"}:
        return _fail_closed(out, "BEST_SHADOW_SIDE_INVALID")

    records, records_blocker = _side_records(out)
    if records is None:
        return _fail_closed(out, records_blocker or "SIDE_ECONOMICS_EVIDENCE_REQUIRED")

    yes_values = _validated_record(records["YES"])
    no_values = _validated_record(records["NO"])
    if yes_values is None or no_values is None:
        return _fail_closed(out, "SELECTED_SIDE_ECONOMICS_INVALID")

    yes_ev = yes_values[1]
    no_ev = no_values[1]
    if yes_ev == no_ev:
        return _fail_closed(out, "BEST_SHADOW_SIDE_TIE")
    derived_side = "YES" if yes_ev > no_ev else "NO"
    if declared_side != derived_side:
        return _fail_closed(out, "BEST_SHADOW_SIDE_RECOMPUTE_MISMATCH")

    selected = records[derived_side]
    selected_values = yes_values if derived_side == "YES" else no_values
    fee, ev, edge = selected_values

    if isinstance(best, dict):
        embedded_fee = _finite_decimal(best.get("modeled_taker_fee_cents"))
        embedded_ev = _finite_decimal(best.get("modeled_after_fee_ev_cents"))
        embedded_edge = _finite_decimal(best.get("modeled_after_fee_edge_cents_per_contract"))
        if embedded_fee != fee or embedded_ev != ev or embedded_edge != edge:
            return _fail_closed(out, "LEGACY_BEST_SHADOW_SIDE_DETAIL_MISMATCH")

    out["best_shadow_side_detail"] = deepcopy(selected)
    out["best_shadow_side"] = derived_side
    out["modeled_taker_fee_cents"] = selected.get("modeled_taker_fee_cents")
    out["modeled_after_fee_ev_cents"] = selected.get("modeled_after_fee_ev_cents")
    out["modeled_after_fee_edge_cents_per_contract"] = selected.get(
        "modeled_after_fee_edge_cents_per_contract"
    )
    out["selected_side_schema_verified"] = True
    out["selected_side_schema_blockers"] = []
    out["selected_side_recomputed"] = True
    out["read_only"] = True
    out["order_entry"] = False
    out["live_probe_ready"] = False
    return out
