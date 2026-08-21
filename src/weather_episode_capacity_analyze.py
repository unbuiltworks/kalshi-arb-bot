"""Conservative capacity analysis for weather PAPER opportunity episodes.

Consumes OPEN/MOVE/CLOSE output from ``weather_paper_episode_tracker``. Only OPEN
rows represent fresh consumable paper opportunities; MOVE/resting observations are
never re-counted as new capacity.

This module deliberately separates quote-opportunity lifetime from capital lock.
For a one-sided weather position, capital remains tied up until official settlement,
not merely until the quote edge disappears. Therefore this analyzer reports modeled
EV and one-time planned capital, but withholds profit-per-capital-hour until later
settlement reconciliation supplies terminal timestamps.

Pure/read-only research utility. No venue client or order-entry path exists here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

D = Decimal
DEFAULT_MIN_PROJECTION_WINDOW_MS = 60 * 60 * 1000


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return out if out.is_finite() else None


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _valid_open(event: dict[str, Any]) -> bool:
    if event.get("event") != "OPEN":
        return False
    if event.get("category") != "weather" or event.get("classification") != "PAPER":
        return False
    if event.get("read_only") is not True or event.get("order_entry") is not False:
        return False
    if event.get("live_probe_ready") is not False:
        return False
    if not str(event.get("episode_id") or "").strip():
        return False
    if not str(event.get("trade_group_id") or "").strip():
        return False
    if not str(event.get("identity") or "").strip():
        return False
    if _strict_nonnegative_int(event.get("observed_ms")) is None:
        return False
    qty = event.get("quantity")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
        return False
    cost = _d(event.get("paper_cost_cents"))
    ev = _d(event.get("modeled_after_fee_ev_cents"))
    if cost is None or cost <= 0 or ev is None:
        return False
    return True


def _economic_signature(event: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(event.get(field))
        for field in (
            "identity",
            "trade_group_id",
            "observed_ms",
            "quantity",
            "ask_cents",
            "modeled_fee_cents",
            "paper_cost_cents",
            "modeled_after_fee_ev_cents",
            "modeled_after_fee_edge_cents_per_contract",
        )
    )


def analyze_weather_episode_capacity(
    events: Iterable[dict[str, Any]],
    *,
    observation_start_ms: int | None = None,
    observation_end_ms: int | None = None,
    observation_window_verified: bool = False,
    min_projection_window_ms: int = DEFAULT_MIN_PROJECTION_WINDOW_MS,
) -> dict[str, Any]:
    """Return de-duplicated modeled weather opportunity capacity.

    Daily projection is a mechanical research rate only. It is emitted only when a
    literal-true verification flag accompanies a complete observation window of at
    least ``min_projection_window_ms`` and that window encloses every counted OPEN.
    This prevents quiet scanner time from being omitted from the denominator.

    Duplicate identical OPEN rows for one episode are idempotent. Conflicting OPEN
    rows sharing an episode_id fail closed and are excluded rather than hindsight-
    selecting the better representation.
    """
    min_window = _strict_nonnegative_int(min_projection_window_ms)
    if min_window is None or min_window == 0:
        raise ValueError("MIN_PROJECTION_WINDOW_MS_MUST_BE_POSITIVE_INTEGER")

    by_episode: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[str, ...]] = {}
    invalid_open_rows = 0
    conflicting_episode_ids: set[str] = set()

    for raw in events:
        event = raw if isinstance(raw, dict) else {}
        if event.get("event") != "OPEN":
            continue
        if not _valid_open(event):
            invalid_open_rows += 1
            continue
        episode_id = str(event["episode_id"]).strip()
        signature = _economic_signature(event)
        if episode_id in signatures and signatures[episode_id] != signature:
            conflicting_episode_ids.add(episode_id)
            continue
        signatures[episode_id] = signature
        by_episode.setdefault(episode_id, event)

    for episode_id in conflicting_episode_ids:
        by_episode.pop(episode_id, None)
        invalid_open_rows += 1

    valid = list(by_episode.values())
    total_cost_cents = sum((_d(e.get("paper_cost_cents")) or D(0)) for e in valid)
    total_ev_cents = sum((_d(e.get("modeled_after_fee_ev_cents")) or D(0)) for e in valid)

    start = _strict_nonnegative_int(observation_start_ms)
    end = _strict_nonnegative_int(observation_end_ms)
    verified = observation_window_verified is True
    window_ms = end - start if start is not None and end is not None and end > start else None
    covers_opens = (
        window_ms is not None
        and all(start <= int(e["observed_ms"]) <= end for e in valid)
    )

    projection_reason = None
    projected_ev_usd_per_day: str | None = None
    if not verified:
        projection_reason = "OBSERVATION_WINDOW_UNVERIFIED"
    elif window_ms is None:
        projection_reason = "OBSERVATION_WINDOW_INVALID"
    elif window_ms < min_window:
        projection_reason = "OBSERVATION_WINDOW_TOO_SHORT"
    elif not covers_opens:
        projection_reason = "OBSERVATION_WINDOW_DOES_NOT_COVER_ALL_OPENS"
    else:
        projected = (total_ev_cents / D(100)) * D(86_400_000) / D(window_ms)
        projected_ev_usd_per_day = str(projected)

    return {
        "unique_open_opportunities": len(valid),
        "invalid_or_conflicting_open_rows": invalid_open_rows,
        "conflicting_episode_ids": sorted(conflicting_episode_ids),
        "modeled_after_fee_ev_total_usd": str(total_ev_cents / D(100)),
        "planned_capital_once_total_usd": str(total_cost_cents / D(100)),
        "modeled_ev_usd_per_day": projected_ev_usd_per_day,
        "modeled_ev_usd_per_day_status": "AVAILABLE" if projected_ev_usd_per_day is not None else "WITHHELD",
        "projection_withheld_reason": projection_reason,
        "observation_window_verified": verified,
        "observation_window_ms": window_ms,
        "observation_window_covers_all_opens": covers_opens if window_ms is not None else False,
        "minimum_projection_window_ms": min_window,
        "fresh_capacity_semantics": "ONE_FIRST_OBSERVED_OPEN_PER_EPISODE_NO_MOVE_RESTING_DEPTH_DOUBLE_COUNT",
        "capital_hours": None,
        "profit_per_capital_hour": None,
        "capital_lock_status": "WITHHELD_UNTIL_OFFICIAL_SETTLEMENT_TIMESTAMPS",
        "capital_lock_semantics": "WEATHER_POSITION_CAPITAL_LOCKS_UNTIL_SETTLEMENT_NOT_QUOTE_CLOSE",
        "projection_semantics": "MODELED_EV_NOT_REALIZED_PNL_FULL_VERIFIED_SCANNER_WINDOW_REQUIRED",
        "paper_only": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
