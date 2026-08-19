"""Audit MLB decided-prop shadow signals without overstating cross-venue locks.

The post-decision/terminal prop lane can expose two different research signals:

* single-venue known-winner discounts; and
* cross-venue complementary q=1 locks.

Cross-venue capacity is counted only with explicit receive timestamps and bounded
receive skew. Newer producer rows may also include explicit OPEN/MOVE/RESTING/CLOSE
episode telemetry. When that evidence exists, consumable capacity is counted once per
OPEN episode, allowing genuinely renewed liquidity after a CLOSE to count again while
never summing repeated resting samples. Older synchronized rows remain supported, but
are conservatively collapsed to one best sample per strict pair identity + direction
for the entire log.

No execution clients, credentials, or approval state exist here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable

MAX_PAIR_RECEIVE_SKEW_MS = Decimal("500")
LEGACY_SIGNAL_MARKER = "DECIDED_MLB_PROP_SIGNAL "
SYNC_SIGNAL_MARKER = "DECIDED_MLB_PROP_SYNC_PAIR "


def _d(value: Any) -> Decimal | None:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def pair_synchronization_status(
    row: dict[str, Any], *, max_skew_ms: Decimal = MAX_PAIR_RECEIVE_SKEW_MS
) -> tuple[bool, str]:
    """Return whether a cross-venue pair lock has explicit synchronized-book proof."""
    poly_received_ms = _d(row.get("poly_book_received_ms"))
    kalshi_received_ms = _d(row.get("kalshi_book_received_ms"))
    recorded_skew_ms = _d(row.get("quote_receive_skew_ms"))
    if poly_received_ms is None or kalshi_received_ms is None or recorded_skew_ms is None:
        return False, "MISSING_SYNCHRONIZATION_EVIDENCE"

    calculated = abs(poly_received_ms - kalshi_received_ms)
    if abs(calculated - recorded_skew_ms) > Decimal("1"):
        return False, "INCONSISTENT_SYNCHRONIZATION_EVIDENCE"
    if recorded_skew_ms < 0 or recorded_skew_ms > max_skew_ms:
        return False, "PAIR_RECEIVE_SKEW_EXCEEDED"
    return True, "SYNCHRONIZED_PAIR_EVIDENCE"


def _pair_identity(row: dict[str, Any], pair: dict[str, Any]) -> tuple[str, str, str, str] | None:
    family = str(row.get("family") or "").strip()
    poly_slug = str(row.get("poly_slug") or "").strip()
    kalshi_ticker = str(row.get("kalshi_ticker") or "").strip()
    direction = str(pair.get("direction") or "").strip()
    if not family or not poly_slug or not kalshi_ticker or not direction:
        return None
    return family, poly_slug, kalshi_ticker, direction


def _episode_identity(
    row: dict[str, Any], pair_identity: tuple[str, str, str, str]
) -> tuple[tuple[str, str, str, str], int] | None:
    episode = row.get("pair_episode")
    if not isinstance(episode, dict):
        return None
    event = str(episode.get("event") or "").upper()
    opened_raw = episode.get("opened_ms")
    if event not in {"OPEN", "MOVE", "RESTING"} or opened_raw is None:
        return None
    try:
        opened_ms = int(opened_raw)
    except (TypeError, ValueError):
        return None
    if opened_ms <= 0:
        return None
    return pair_identity, opened_ms


def parse_signal_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        marker = None
        marker_at = -1
        for candidate in (SYNC_SIGNAL_MARKER, LEGACY_SIGNAL_MARKER):
            at = line.find(candidate)
            if at >= 0:
                marker = candidate
                marker_at = at
                break
        if marker is None:
            continue
        payload = line[marker_at + len(marker) :].strip()
        try:
            row = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def analyze(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    signal_rows = 0
    known_winner_edges = 0
    pair_locks_seen = 0
    synchronized_pair_lock_samples = 0
    synchronized_without_identity = 0

    # Legacy rows have no episode lifecycle, so conservatively count one best sample
    # per strict identity over the whole log.
    legacy_best_profit: dict[tuple[str, str, str, str], Decimal] = {}

    # Episode-aware rows can safely count renewed liquidity once per OPEN episode.
    episode_best_profit: dict[tuple[tuple[str, str, str, str], int], Decimal] = {}
    episode_events: Counter[str] = Counter()
    malformed_episode_rows = 0

    for row in rows:
        signal_rows += 1
        edges = row.get("known_winner_edges") or []
        if isinstance(edges, list):
            known_winner_edges += len(edges)

        episode = row.get("pair_episode")
        if isinstance(episode, dict):
            event = str(episode.get("event") or "").upper()
            if event:
                episode_events[event] += 1

        pair = row.get("cross_venue_q1_pair_lock")
        if not isinstance(pair, dict) or not pair:
            continue
        pair_locks_seen += 1
        ok, reason = pair_synchronization_status(row)
        reasons[reason] += 1
        if not ok:
            continue

        synchronized_pair_lock_samples += 1
        identity = _pair_identity(row, pair)
        if identity is None:
            synchronized_without_identity += 1
            reasons["MISSING_STRICT_PAIR_IDENTITY"] += 1
            continue

        profit = _d(pair.get("profit") if pair.get("profit") is not None else pair.get("locked_profit"))
        if profit is None or profit <= 0:
            continue

        if isinstance(episode, dict):
            event = str(episode.get("event") or "").upper()
            episode_key = _episode_identity(row, identity)
            if event in {"OPEN", "MOVE", "RESTING"} and episode_key is None:
                malformed_episode_rows += 1
                reasons["MALFORMED_EPISODE_EVIDENCE"] += 1
                continue
            if episode_key is not None:
                previous = episode_best_profit.get(episode_key)
                if previous is None or profit > previous:
                    episode_best_profit[episode_key] = profit
                continue

        previous = legacy_best_profit.get(identity)
        if previous is None or profit > previous:
            legacy_best_profit[identity] = profit

    legacy_profit = sum(legacy_best_profit.values(), Decimal("0"))
    episode_profit = sum(episode_best_profit.values(), Decimal("0"))
    total_profit = legacy_profit + episode_profit
    consumable_units = len(legacy_best_profit) + len(episode_best_profit)
    duplicate_samples = max(
        0,
        synchronized_pair_lock_samples
        - consumable_units
        - synchronized_without_identity
        - malformed_episode_rows,
    )

    return {
        "signal_rows": signal_rows,
        "single_venue_known_winner_edges": known_winner_edges,
        "cross_venue_pair_locks_seen": pair_locks_seen,
        "synchronized_cross_venue_pair_lock_samples": synchronized_pair_lock_samples,
        "synchronized_cross_venue_pair_locks": consumable_units,
        "unique_synchronized_pair_identities": len(set(legacy_best_profit) | {key[0] for key in episode_best_profit}),
        "consumable_episode_units": consumable_units,
        "episode_aware_consumable_units": len(episode_best_profit),
        "legacy_identity_collapsed_units": len(legacy_best_profit),
        "duplicate_synchronized_pair_samples_excluded": duplicate_samples,
        "synchronized_pair_samples_missing_strict_identity": synchronized_without_identity,
        "malformed_episode_rows_excluded": malformed_episode_rows,
        "pair_episode_events": dict(sorted(episode_events.items())),
        "synchronized_pair_profit_dollars": str(total_profit),
        "episode_aware_profit_dollars": str(episode_profit),
        "legacy_collapsed_profit_dollars": str(legacy_profit),
        "pair_lock_reasons": dict(sorted(reasons.items())),
        "pair_lock_accounting_rule": (
            "REQUIRE_EXPLICIT_RECEIVE_TIMESTAMPS_AND_LE_500MS_SKEW; "
            "EPISODE_ROWS_COUNT_ONCE_PER_STRICT_IDENTITY_PLUS_OPENED_MS; "
            "LEGACY_ROWS_COLLAPSE_TO_BEST_Q1_SAMPLE_PER_STRICT_IDENTITY_AND_DIRECTION"
        ),
        "repeated_resting_depth": "MOVE_AND_RESTING_SAMPLES_WITHIN_ONE_OPEN_EPISODE_NOT_ADDITIVE_CAPACITY",
        "episode_limitation": (
            "RENEWED_LIQUIDITY_COUNTS_AGAIN_ONLY_WHEN_PRODUCER_EMITS_A_NEW_OPENED_MS_AFTER_CLOSE"
        ),
        "legacy_pair_locks_without_timing": "RESEARCH_ONLY_EXCLUDED_FROM_CAPACITY",
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="log file containing decided-prop signal rows")
    args = parser.parse_args()
    rows = parse_signal_lines(args.path.read_text(encoding="utf-8").splitlines())
    print(json.dumps(analyze(rows), sort_keys=True))


if __name__ == "__main__":
    main()
