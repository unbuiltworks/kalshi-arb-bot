"""Explicit lifecycle accounting for read-only MLB prop paper opportunities.

Turns snapshot rows into OPEN/MOVE/CLOSE episode events without treating repeated
resting depth as fresh capacity.  No venue clients, credentials, or order entry.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

D = Decimal
PROBE_FIELDS = ("probe_1usd", "probe_5usd")


def _d(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def settlement_key(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(k) or "") for k in (
        "family", "game_date", "player", "threshold", "poly_slug", "kalshi_ticker"
    ))


def opportunity_key(row: dict[str, Any], probe_field: str) -> str:
    return f"{settlement_key(row)}|{probe_field}"


def fingerprint(row: dict[str, Any], probe_field: str) -> str:
    plan = row.get(probe_field) or {}
    payload = {
        "direction": plan.get("direction"),
        "quantity": str(plan.get("quantity")),
        "poly_price": str(plan.get("poly_price")),
        "kalshi_price": str(plan.get("kalshi_price")),
        "poly_fee": str(plan.get("poly_fee")),
        "kalshi_fee": str(plan.get("kalshi_fee_upper_bound")),
        "cost": str(plan.get("total_cost_upper_bound")),
        "profit": str(plan.get("conservative_locked_profit")),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _qualifying(row: dict[str, Any], probe_field: str, min_profit: Decimal) -> bool:
    plan = row.get(probe_field)
    if not isinstance(plan, dict):
        return False
    profit = _d(plan.get("conservative_locked_profit"))
    return bool(
        profit is not None
        and profit >= min_profit
        and row.get("book_comparable")
        and not row.get("fetch_error")
        and row.get("poly_slug")
        and row.get("kalshi_ticker")
        and row.get("settlement_equivalent_now")
    )


def advance_episodes(
    snapshot: dict[str, Any],
    *,
    prior_state: dict[str, dict[str, Any]],
    observed_ms: int,
    min_profit: Decimal = D("0.03"),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return explicit lifecycle events and current active state.

    Repeated identical resting plans emit no event. A changed consumable plan emits
    MOVE while preserving the original opened_ms. Missing opportunities emit CLOSE
    with measured duration. State is JSON-serializable for a non-blocking sidecar.
    """
    rows = snapshot.get("paper_probe_candidates") or []
    current: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    for probe_field in PROBE_FIELDS:
        for row in rows:
            if not _qualifying(row, probe_field, min_profit):
                continue
            key = opportunity_key(row, probe_field)
            if key in current:
                continue
            plan = row[probe_field]
            fp = fingerprint(row, probe_field)
            old = prior_state.get(key)
            opened_ms = int(old.get("opened_ms", observed_ms)) if old else observed_ms
            state = {
                "fingerprint": fp,
                "opened_ms": opened_ms,
                "last_seen_ms": observed_ms,
                "settlement_key": settlement_key(row),
                "probe_field": probe_field,
                "family": row.get("family"),
                "poly_slug": row.get("poly_slug"),
                "kalshi_ticker": row.get("kalshi_ticker"),
                "direction": plan.get("direction"),
                "quantity": str(plan.get("quantity")),
                "capital": str(plan.get("total_cost_upper_bound")),
                "modeled_profit": str(plan.get("conservative_locked_profit")),
                "quote_age_ms": row.get("quote_age_ms"),
                "quote_skew_ms": row.get("quote_skew_ms"),
            }
            current[key] = state
            if old is None:
                kind = "OPEN"
            elif old.get("fingerprint") != fp:
                kind = "MOVE"
            else:
                continue
            events.append({
                **state,
                "event": kind,
                "observed_ms": observed_ms,
                "duration_ms": observed_ms - opened_ms,
                "read_only": True,
                "order_entry": False,
            })

    for key, old in prior_state.items():
        if key in current:
            continue
        opened_ms = int(old.get("opened_ms", observed_ms))
        last_seen_ms = int(old.get("last_seen_ms", opened_ms))
        events.append({
            **old,
            "event": "CLOSE",
            "observed_ms": observed_ms,
            "last_seen_ms": last_seen_ms,
            "duration_ms": max(0, last_seen_ms - opened_ms),
            "read_only": True,
            "order_entry": False,
        })

    return events, current


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [e for e in events if e.get("event") == "CLOSE"]
    durations = [int(e.get("duration_ms") or 0) for e in closed]
    return {
        "open": sum(e.get("event") == "OPEN" for e in events),
        "move": sum(e.get("event") == "MOVE" for e in events),
        "close": len(closed),
        "closed_duration_ms_total": sum(durations),
        "closed_duration_ms_max": max(durations, default=0),
        "read_only": True,
        "order_entry": False,
    }
