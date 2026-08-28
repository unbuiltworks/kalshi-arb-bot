"""Explicit lifecycle accounting for read-only MLB prop paper opportunities.

Turns snapshot rows into OPEN/MOVE/CLOSE episode events without treating repeated
resting depth as fresh capacity. No venue clients, credentials, or order entry.

Participation clearance is deliberately distinct from a full cross-venue settlement
rules audit. This tracker records paper-shadow economics only and never promotes live
readiness.

The <=$1 and <=$5 plans are alternative sizing views of one displayed book, not
independent liquidity. Episode selection therefore emits at most one active episode
per strict settlement identity and chooses the best valid synchronized plan across
budget tiers and duplicate rows. A flip to the opposite complementary direction is a
new opportunity episode, not a MOVE of the prior one.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

D = Decimal
PROBE_FIELDS = (("probe_1usd", D("1")), ("probe_5usd", D("5")))


def _d(value: Any) -> Decimal | None:
    try:
        return D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def settlement_key(row: dict[str, Any]) -> str:
    return "|".join(str(row.get(k) or "") for k in (
        "family", "game_date", "player", "threshold", "poly_slug", "kalshi_ticker"
    ))


def opportunity_key(row: dict[str, Any], probe_field: str | None = None) -> str:
    # Budget tiers are alternative views of the same strict counterpart pair.
    # Keep one active identity; a direction flip is handled as CLOSE + OPEN below.
    return settlement_key(row)


def fingerprint(row: dict[str, Any], probe_field: str) -> str:
    plan = row.get(probe_field) or {}
    payload = {
        "probe_field": probe_field,
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
    """Return True only for synchronized, participation-cleared read-only paper plans."""
    plan = row.get(probe_field)
    if not isinstance(plan, dict):
        return False
    profit = _d(plan.get("conservative_locked_profit"))
    return bool(
        profit is not None
        and profit >= min_profit
        and row.get("book_comparable")
        and row.get("synchronized_book_pair") is True
        and not row.get("fetch_error")
        and row.get("poly_slug")
        and row.get("kalshi_ticker")
        and row.get("participation_gate_satisfied") is True
        and row.get("read_only") is True
        and row.get("order_entry") is False
        and row.get("execution_eligible") is False
        and row.get("live_probe_ready") is False
    )


def _best_valid_choice(
    rows: list[dict[str, Any]], min_profit: Decimal
) -> tuple[dict[str, Any], str] | None:
    """Choose one deterministic best plan across duplicate rows and budget tiers."""
    choices: list[tuple[Decimal, Decimal, str, str, dict[str, Any], str]] = []
    for row in rows:
        for probe_field, cap in PROBE_FIELDS:
            if not _qualifying(row, probe_field, min_profit):
                continue
            plan = row[probe_field]
            profit = _d(plan.get("conservative_locked_profit")) or D("0")
            fp = fingerprint(row, probe_field)
            direction = str(plan.get("direction") or "")
            # Highest conservative profit wins. On ties, prefer the smaller cap;
            # deterministic strings remove any dependence on source row ordering.
            choices.append((profit, -cap, direction, fp, row, probe_field))
    if not choices:
        return None
    _profit, _neg_cap, _direction, _fp, row, probe_field = max(choices)
    return row, probe_field


def _close_event(old: dict[str, Any], observed_ms: int) -> dict[str, Any]:
    """Close an episode with interval-censored duration telemetry.

    With a polled scanner, the opportunity is known to have survived through
    last_seen_ms and known to be absent by observed_ms. The true disappearance time
    lies inside that interval. Keep the legacy duration_ms as the conservative lower
    bound and expose the upper bound explicitly so survival/capacity analysis cannot
    pretend the exact close timestamp was observed.
    """
    opened_ms = int(old.get("opened_ms", observed_ms))
    last_seen_ms = int(old.get("last_seen_ms", opened_ms))
    duration_lower_bound_ms = max(0, last_seen_ms - opened_ms)
    duration_upper_bound_ms = max(duration_lower_bound_ms, observed_ms - opened_ms)
    censoring_gap_ms = max(0, observed_ms - last_seen_ms)
    return {
        **old,
        "event": "CLOSE",
        "observed_ms": observed_ms,
        "last_seen_ms": last_seen_ms,
        "duration_ms": duration_lower_bound_ms,
        "duration_lower_bound_ms": duration_lower_bound_ms,
        "duration_upper_bound_ms": duration_upper_bound_ms,
        "close_censoring_gap_ms": censoring_gap_ms,
        "duration_censoring": "INTERVAL_CENSORED_POLLING",
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
        "paper_probe_only": True,
    }


def advance_episodes(
    snapshot: dict[str, Any],
    *,
    prior_state: dict[str, dict[str, Any]],
    observed_ms: int,
    min_profit: Decimal = D("0.03"),
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return explicit lifecycle events and current active state.

    Repeated identical resting plans emit no event. A changed consumable plan in the
    same direction emits MOVE while preserving the original opened_ms. A flip to the
    opposite complementary direction closes the old episode and opens a new one at
    the current observation, preventing unique-opportunity/capital accounting from
    blending economically distinct trades. Missing opportunities emit CLOSE with an
    interval-censored duration: last_seen-open is the guaranteed lower bound and the
    current observation-open is the upper bound. State is JSON-serializable for a
    non-blocking sidecar.
    """
    rows = [row for row in (snapshot.get("paper_probe_candidates") or []) if isinstance(row, dict)]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(settlement_key(row), []).append(row)

    current: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []

    for key in sorted(grouped):
        selected = _best_valid_choice(grouped[key], min_profit)
        if selected is None:
            continue
        row, probe_field = selected
        plan = row[probe_field]
        fp = fingerprint(row, probe_field)
        direction = plan.get("direction")
        old = prior_state.get(key)
        direction_flip = bool(old is not None and old.get("direction") != direction)
        opened_ms = observed_ms if direction_flip else int(old.get("opened_ms", observed_ms)) if old else observed_ms
        state = {
            "fingerprint": fp,
            "opened_ms": opened_ms,
            "last_seen_ms": observed_ms,
            "settlement_key": settlement_key(row),
            "probe_field": probe_field,
            "family": row.get("family"),
            "poly_slug": row.get("poly_slug"),
            "kalshi_ticker": row.get("kalshi_ticker"),
            "direction": direction,
            "quantity": str(plan.get("quantity")),
            "capital": str(plan.get("total_cost_upper_bound")),
            "modeled_profit": str(plan.get("conservative_locked_profit")),
            "quote_age_ms": row.get("quote_age_ms"),
            "quote_skew_ms": row.get("quote_skew_ms"),
            "quote_receive_skew_ms": row.get("quote_receive_skew_ms"),
            "synchronized_book_pair": True,
            "duplicate_identity_rows_considered": len(grouped[key]),
            "participation_gate_satisfied": True,
            "full_rules_audited": bool(row.get("full_rules_audited")),
            "rules_status": row.get("rules_status") or "RULES_PENDING",
            "fully_audited_shadow_ready": bool(row.get("fully_audited_shadow_ready")),
            "live_probe_ready": False,
            "paper_probe_only": True,
        }
        current[key] = state

        if old is None:
            events.append({
                **state,
                "event": "OPEN",
                "observed_ms": observed_ms,
                "duration_ms": 0,
                "read_only": True,
                "order_entry": False,
            })
            continue

        if direction_flip:
            events.append(_close_event(old, observed_ms))
            events.append({
                **state,
                "event": "OPEN",
                "observed_ms": observed_ms,
                "duration_ms": 0,
                "read_only": True,
                "order_entry": False,
            })
            continue

        if old.get("fingerprint") != fp:
            events.append({
                **state,
                "event": "MOVE",
                "observed_ms": observed_ms,
                "duration_ms": observed_ms - opened_ms,
                "read_only": True,
                "order_entry": False,
            })

    for key, old in prior_state.items():
        if key in current:
            continue
        events.append(_close_event(old, observed_ms))

    return events, current


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [e for e in events if e.get("event") == "CLOSE"]
    lower_bounds = [int(e.get("duration_lower_bound_ms", e.get("duration_ms") or 0)) for e in closed]
    upper_bounds = [int(e.get("duration_upper_bound_ms", e.get("duration_ms") or 0)) for e in closed]
    censoring_gaps = [int(e.get("close_censoring_gap_ms") or 0) for e in closed]
    return {
        "open": sum(e.get("event") == "OPEN" for e in events),
        "move": sum(e.get("event") == "MOVE" for e in events),
        "close": len(closed),
        "closed_duration_ms_total": sum(lower_bounds),
        "closed_duration_ms_max": max(lower_bounds, default=0),
        "closed_duration_lower_bound_ms_total": sum(lower_bounds),
        "closed_duration_upper_bound_ms_total": sum(upper_bounds),
        "closed_duration_censoring_gap_ms_total": sum(censoring_gaps),
        "duration_semantics": "INTERVAL_CENSORED_CLOSE_LOWER_BOUND_IS_LAST_SEEN",
        "budget_semantics": "ALTERNATIVE_SIZING_TIERS_ONE_ACTIVE_EPISODE_PER_SETTLEMENT_IDENTITY",
        "direction_flip_semantics": "CLOSE_OLD_OPEN_NEW_EPISODE",
        "duplicate_identity_semantics": "BEST_VALID_ROW_ORDER_INDEPENDENT",
        "synchronized_books_only": True,
        "paper_probe_only": True,
        "live_probe_ready": False,
        "read_only": True,
        "order_entry": False,
    }
