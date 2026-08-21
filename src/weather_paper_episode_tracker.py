"""Stateful OPEN/MOVE/CLOSE accounting for certified weather PAPER opportunities.

Pure/read-only. Repeated polling of resting depth is not fresh capacity; a true
close followed by a later same-side re-open becomes a new episode. No venue client
or order-entry path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from src.weather_paper_microprobe import select_paper_probe

D = Decimal


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def identity(plan: dict[str, Any]) -> str:
    ticker = str(plan.get("market_ticker") or "").strip().upper()
    market_date = str(plan.get("market_date") or "").strip()
    side = str(plan.get("side") or "").strip().upper()
    return f"weather|{ticker}|{market_date}|{side}"


def fingerprint(plan: dict[str, Any]) -> str:
    fields = (
        plan.get("side"), plan.get("quantity"), plan.get("ask_cents"),
        plan.get("modeled_fee_cents"), plan.get("paper_cost_cents"),
        plan.get("modeled_after_fee_ev_cents"),
        plan.get("modeled_after_fee_edge_cents_per_contract"),
        plan.get("available_contracts_at_quote"),
    )
    return sha256("|".join(str(x) for x in fields).encode("utf-8")).hexdigest()


def _episode_id(key: str, opened_ms: int) -> str:
    return "weather-episode-" + sha256(f"{key}|{opened_ms}".encode("utf-8")).hexdigest()[:24]


def _close(old: dict[str, Any], observed_ms: int) -> dict[str, Any]:
    opened_ms = int(old["opened_ms"])
    last_seen_ms = int(old["last_seen_ms"])
    lower = max(0, last_seen_ms - opened_ms)
    upper = max(lower, observed_ms - opened_ms)
    return {
        **old,
        "event": "CLOSE",
        "observed_ms": observed_ms,
        "duration_lower_bound_ms": lower,
        "duration_upper_bound_ms": upper,
        "close_censoring_gap_ms": max(0, observed_ms - last_seen_ms),
        "duration_censoring": "INTERVAL_CENSORED_POLLING",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }


def advance_weather_episode(
    row: dict[str, Any] | None,
    *,
    prior_state: dict[str, dict[str, Any]],
    observed_ms: int,
    min_edge_cents: Decimal = D("3"),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Advance one weather observation without double-counting resting liquidity."""
    plan = select_paper_probe(row or {}, min_edge_cents=min_edge_cents) if row else None
    events: list[dict[str, Any]] = []
    current: dict[str, dict[str, Any]] = {}

    if plan is not None:
        key = identity(plan)
        old = prior_state.get(key)
        opened_ms = int(old["opened_ms"]) if old else observed_ms
        state = {
            "identity": key,
            "episode_id": old.get("episode_id") if old else _episode_id(key, opened_ms),
            "opened_ms": opened_ms,
            "last_seen_ms": observed_ms,
            "fingerprint": fingerprint(plan),
            "market_ticker": plan.get("market_ticker"),
            "market_date": plan.get("market_date"),
            "side": plan.get("side"),
            "quantity": plan.get("quantity"),
            "ask_cents": plan.get("ask_cents"),
            "paper_cost_cents": plan.get("paper_cost_cents"),
            "modeled_after_fee_ev_cents": plan.get("modeled_after_fee_ev_cents"),
            "classification": "PAPER",
            "category": "weather",
            "read_only": True,
            "order_entry": False,
            "live_probe_ready": False,
        }
        current[key] = state
        if old is None:
            events.append({**state, "event": "OPEN", "observed_ms": observed_ms})
        elif old.get("fingerprint") != state["fingerprint"]:
            events.append({**state, "event": "MOVE", "observed_ms": observed_ms})

    for key, old in prior_state.items():
        if key not in current:
            events.append(_close(old, observed_ms))

    return events, current


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [e for e in events if e.get("event") == "CLOSE"]
    return {
        "open": sum(e.get("event") == "OPEN" for e in events),
        "move": sum(e.get("event") == "MOVE" for e in events),
        "close": len(closed),
        "closed_duration_lower_bound_ms": sum(int(e.get("duration_lower_bound_ms") or 0) for e in closed),
        "closed_duration_upper_bound_ms": sum(int(e.get("duration_upper_bound_ms") or 0) for e in closed),
        "episode_semantics": "RESTING_DEPTH_DEDUPED_TRUE_CLOSE_REOPEN_IS_NEW_EPISODE",
        "paper_only": True,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
