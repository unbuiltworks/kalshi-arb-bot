"""Read-only clock-consistency audit for decided MLB prop pair episodes.

The synchronized decided-prop producer records per-venue book receive timestamps plus
OPEN/MOVE/CLOSE episode state. Capacity and survival analysis must not accept an
episode observation timestamp that predates the actual venue responses used to build
the quote. Such a clock inversion can make an opportunity appear to open earlier and
survive longer than the evidence supports.

This module is deliberately downstream/fail-closed. It does not change pricing,
readiness, or execution state.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def _int_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def audit_row(row: dict[str, Any]) -> tuple[bool, str]:
    episode = row.get("pair_episode")
    if not isinstance(episode, dict):
        return False, "MISSING_PAIR_EPISODE"

    event = str(episode.get("event") or "").upper()
    if event in {"OPEN", "MOVE", "RESTING"}:
        poly_ms = _int_ms(row.get("poly_book_received_ms"))
        kalshi_ms = _int_ms(row.get("kalshi_book_received_ms"))
        observed_ms = _int_ms(episode.get("observed_ms"))
        if poly_ms is None or kalshi_ms is None:
            return False, "MISSING_BOOK_RECEIVE_TIMESTAMPS"
        if observed_ms is None:
            return False, "MISSING_EPISODE_OBSERVED_TIMESTAMP"
        latest_book_ms = max(poly_ms, kalshi_ms)
        if observed_ms < latest_book_ms:
            return False, "EPISODE_CLOCK_PRECEDES_BOOK_EVIDENCE"
        opened_ms = _int_ms(episode.get("opened_ms"))
        if opened_ms is None or opened_ms > observed_ms:
            return False, "INVALID_EPISODE_OPEN_TIMESTAMP"
        return True, "VALID_BOOK_EVIDENCE_CLOCK"

    if event == "CLOSE":
        opened_ms = _int_ms(episode.get("opened_ms"))
        last_seen_ms = _int_ms(episode.get("last_seen_ms"))
        closed_ms = _int_ms(episode.get("closed_observed_ms"))
        if opened_ms is None or last_seen_ms is None or closed_ms is None:
            return False, "MISSING_CLOSE_TIMESTAMPS"
        if not (opened_ms <= last_seen_ms <= closed_ms):
            return False, "INVALID_CLOSE_CLOCK_ORDER"
        return True, "VALID_INTERVAL_CENSORED_CLOSE_CLOCK"

    return False, "UNKNOWN_EPISODE_EVENT"


def audit_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    valid: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()

    for row in rows:
        ok, reason = audit_row(row)
        reasons[reason] += 1
        annotated = dict(row)
        annotated["episode_clock_audit"] = {
            "valid": ok,
            "reason": reason,
            "execution_eligible": False,
            "read_only": True,
        }
        (valid if ok else quarantined).append(annotated)

    return {
        "rows": len(valid) + len(quarantined),
        "valid_rows": len(valid),
        "quarantined_rows": len(quarantined),
        "reason_counts": dict(sorted(reasons.items())),
        "valid": valid,
        "quarantined": quarantined,
        "capacity_eligible_rows": len(valid),
        "clock_semantics": "EPISODE_OBSERVED_TIME_MUST_NOT_PRECEDE_LATEST_BOOK_RECEIVE_TIME",
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }
