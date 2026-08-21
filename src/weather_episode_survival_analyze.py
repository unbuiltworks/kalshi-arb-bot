"""Conservative survival analysis for weather PAPER opportunity episodes.

Consumes CLOSE events from ``weather_paper_episode_tracker``. Polling makes the
true close time interval-censored between ``last_seen_ms`` and the next complete
observation, so a threshold is *confirmed* only when the lower duration bound is
at least that threshold. If only the upper bound clears the threshold, survival
is merely possible/unresolved and must not be counted as executable evidence.

Pure/read-only research utility. No venue clients or order-entry paths.
"""
from __future__ import annotations

from typing import Any, Iterable

DEFAULT_THRESHOLDS_MS = (50, 100, 250, 500)


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def analyze_closed_episode_survival(
    events: Iterable[dict[str, Any]],
    *,
    thresholds_ms: tuple[int, ...] = DEFAULT_THRESHOLDS_MS,
) -> dict[str, Any]:
    """Summarize conservative persistence for interval-censored CLOSE events.

    ``confirmed_survival`` means the episode is known to have lasted at least the
    threshold. ``possible_survival`` includes confirmed rows plus rows whose upper
    censoring bound crosses the threshold. The gap between possible and confirmed
    is explicitly unresolved and is never promoted into confirmed evidence.
    """
    clean_thresholds: list[int] = []
    for threshold in thresholds_ms:
        value = _strict_nonnegative_int(threshold)
        if value is None or value == 0:
            raise ValueError("SURVIVAL_THRESHOLDS_MUST_BE_POSITIVE_INTEGERS")
        if value not in clean_thresholds:
            clean_thresholds.append(value)
    clean_thresholds.sort()

    valid_closed: list[tuple[int, int]] = []
    invalid_closed = 0
    for event in events:
        if event.get("event") != "CLOSE":
            continue
        if event.get("read_only") is not True or event.get("order_entry") is not False:
            invalid_closed += 1
            continue
        if event.get("live_probe_ready") is not False:
            invalid_closed += 1
            continue
        lower = _strict_nonnegative_int(event.get("duration_lower_bound_ms"))
        upper = _strict_nonnegative_int(event.get("duration_upper_bound_ms"))
        if lower is None or upper is None or upper < lower:
            invalid_closed += 1
            continue
        valid_closed.append((lower, upper))

    by_threshold: dict[str, dict[str, Any]] = {}
    for threshold in clean_thresholds:
        confirmed = sum(lower >= threshold for lower, _ in valid_closed)
        possible = sum(upper >= threshold for _, upper in valid_closed)
        unresolved = possible - confirmed
        total = len(valid_closed)
        by_threshold[str(threshold)] = {
            "threshold_ms": threshold,
            "confirmed_survival_episodes": confirmed,
            "possible_survival_episodes": possible,
            "unresolved_due_to_polling_censoring": unresolved,
            "confirmed_survival_rate": (confirmed / total) if total else None,
            "possible_survival_rate_upper_bound": (possible / total) if total else None,
        }

    return {
        "closed_episodes_valid": len(valid_closed),
        "closed_episodes_invalid": invalid_closed,
        "thresholds": by_threshold,
        "survival_semantics": "LOWER_BOUND_CONFIRMED_UPPER_BOUND_POSSIBLE_INTERVAL_CENSORED",
        "confirmed_evidence_only_for_execution_research": True,
        "paper_only": True,
        "read_only": True,
        "order_entry": False,
        "live_probe_ready": False,
    }
