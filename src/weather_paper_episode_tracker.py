"""Stateful OPEN/MOVE/CLOSE accounting for certified weather PAPER opportunities.

Pure/read-only. Repeated polling of resting depth is not fresh capacity; a true
close followed by a later same-side re-open becomes a new episode. No venue client
or order-entry path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from src.cross_venue_ledger import LedgerEvent, make_event
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


def _episode_trade_group_id(episode_id: str) -> str:
    return "paper-" + episode_id


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
        episode_id = str(old.get("episode_id")) if old else _episode_id(key, opened_ms)
        state = {
            "identity": key,
            "episode_id": episode_id,
            "trade_group_id": _episode_trade_group_id(episode_id),
            "opened_ms": opened_ms,
            "last_seen_ms": observed_ms,
            "fingerprint": fingerprint(plan),
            "market_ticker": plan.get("market_ticker"),
            "market_date": plan.get("market_date"),
            "side": plan.get("side"),
            "quantity": plan.get("quantity"),
            "ask_cents": plan.get("ask_cents"),
            "modeled_fee_cents": plan.get("modeled_fee_cents"),
            "paper_cost_cents": plan.get("paper_cost_cents"),
            "modeled_after_fee_ev_cents": plan.get("modeled_after_fee_ev_cents"),
            "modeled_after_fee_edge_cents_per_contract": plan.get("modeled_after_fee_edge_cents_per_contract"),
            "available_contracts_at_quote": plan.get("available_contracts_at_quote"),
            "settlement_key": plan.get("settlement_key"),
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


def ledger_events_for_episode_open(event: dict[str, Any]) -> list[LedgerEvent]:
    """Emit one idempotent synthetic fill+fee for a true weather episode OPEN.

    MOVE/resting observations emit nothing. A genuine close/re-open gets a new
    episode_id/trade_group_id and can therefore become a new independent paper fill.
    """
    if event.get("event") != "OPEN":
        return []
    if event.get("classification") != "PAPER" or event.get("read_only") is not True:
        return []
    if event.get("order_entry") is not False or event.get("live_probe_ready") is not False:
        return []

    trade_group_id = str(event.get("trade_group_id") or "")
    settlement_key = str(event.get("settlement_key") or "")
    ticker = str(event.get("market_ticker") or "").strip().upper()
    side = str(event.get("side") or "").strip().upper()
    qty = event.get("quantity")
    ask = event.get("ask_cents")
    fee = _d(event.get("modeled_fee_cents"))
    ts_ms = event.get("observed_ms")
    if not trade_group_id or not settlement_key or not ticker or side not in {"YES", "NO"}:
        return []
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        return []
    if not isinstance(ask, int) or isinstance(ask, bool) or not 1 <= ask <= 99:
        return []
    if fee is None or fee < 0:
        return []
    if not isinstance(ts_ms, int) or isinstance(ts_ms, bool) or ts_ms < 0:
        return []

    fill = make_event(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category="weather",
        venue="kalshi",
        market_id=ticker,
        event_type="fill",
        side=side,
        quantity=D(qty),
        price=D(ask) / D(100),
        ts_ms=ts_ms,
        external_id=f"{trade_group_id}:fill",
    )
    fee_event = make_event(
        trade_group_id=trade_group_id,
        settlement_key=settlement_key,
        category="weather",
        venue="kalshi",
        market_id=ticker,
        event_type="fee",
        side=side,
        fee=fee / D(100),
        ts_ms=ts_ms,
        external_id=f"{trade_group_id}:fee",
    )
    return [fill, fee_event]


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [e for e in events if e.get("event") == "CLOSE"]
    return {
        "open": sum(e.get("event") == "OPEN" for e in events),
        "move": sum(e.get("event") == "MOVE" for e in events),
        "close": len(closed),
        "closed_duration_lower_bound_ms": sum(int(e.get("duration_lower_bound_ms") or 0) for e in closed),
        "closed_duration_upper_bound_ms": sum(int(e.get("duration_upper_bound_ms") or 0) for e in closed),
        "episode_semantics": "RESTING_DEPTH_DEDUPED_TRUE_CLOSE_REOPEN_IS_NEW_EPISODE",
        "paper_fill_semantics": "ONE_SYNTHETIC_FILL_PER_OBSERVED_OPEN",
        "paper_only": True,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
