"""Audit MLB decided-prop shadow signals without overstating cross-venue locks.

The post-decision/terminal prop lane can expose two different research signals:

* single-venue known-winner discounts; and
* cross-venue complementary q=1 locks.

A concurrent fetch alone is not proof the two books were received closely enough in
time to represent a simultaneous lock. This analyzer therefore counts cross-venue
pair locks only when the producer records explicit receive timestamps and a bounded
receive skew. Older rows without synchronization telemetry remain useful for the
single-venue known-winner study, but their pair locks are rejected fail-closed.

Repeated polling of the same resting q=1 pair is not additional consumable liquidity.
Raw synchronized samples are retained as timing evidence, while capacity/P&L is
collapsed to one best observed sample per exact pair identity + direction. This is
intentionally conservative: without explicit OPEN/CLOSE episode telemetry the analyzer
will under-count a later genuinely renewed opportunity rather than double-count the
same resting depth.

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
SIGNAL_MARKER = "DECIDED_MLB_PROP_SIGNAL "


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
    # Fail closed if producer telemetry is internally inconsistent. One millisecond
    # tolerance only accommodates integer/rounded timestamp serialization.
    if abs(calculated - recorded_skew_ms) > Decimal("1"):
        return False, "INCONSISTENT_SYNCHRONIZATION_EVIDENCE"
    if recorded_skew_ms < 0 or recorded_skew_ms > max_skew_ms:
        return False, "PAIR_RECEIVE_SKEW_EXCEEDED"
    return True, "SYNCHRONIZED_PAIR_EVIDENCE"


def _pair_identity(row: dict[str, Any], pair: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return strict pair identity used for consumable-liquidity de-duplication."""
    family = str(row.get("family") or "").strip()
    poly_slug = str(row.get("poly_slug") or "").strip()
    kalshi_ticker = str(row.get("kalshi_ticker") or "").strip()
    direction = str(pair.get("direction") or "").strip()
    if not family or not poly_slug or not kalshi_ticker or not direction:
        return None
    return family, poly_slug, kalshi_ticker, direction


def parse_signal_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        marker_at = line.find(SIGNAL_MARKER)
        if marker_at < 0:
            continue
        payload = line[marker_at + len(SIGNAL_MARKER) :].strip()
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
    unique_best_profit: dict[tuple[str, str, str, str], Decimal] = {}
    synchronized_without_identity = 0

    for row in rows:
        signal_rows += 1
        edges = row.get("known_winner_edges") or []
        if isinstance(edges, list):
            known_winner_edges += len(edges)

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

        profit = _d(
            pair.get("profit")
            if pair.get("profit") is not None
            else pair.get("locked_profit")
        )
        if profit is None or profit <= 0:
            continue
        previous = unique_best_profit.get(identity)
        if previous is None or profit > previous:
            unique_best_profit[identity] = profit

    unique_profit = sum(unique_best_profit.values(), Decimal("0"))
    duplicate_samples = max(0, synchronized_pair_lock_samples - len(unique_best_profit) - synchronized_without_identity)

    return {
        "signal_rows": signal_rows,
        "single_venue_known_winner_edges": known_winner_edges,
        "cross_venue_pair_locks_seen": pair_locks_seen,
        "synchronized_cross_venue_pair_lock_samples": synchronized_pair_lock_samples,
        # Backward-compatible name now reflects unique consumable identities, not raw samples.
        "synchronized_cross_venue_pair_locks": len(unique_best_profit),
        "unique_synchronized_pair_identities": len(unique_best_profit),
        "duplicate_synchronized_pair_samples_excluded": duplicate_samples,
        "synchronized_pair_samples_missing_strict_identity": synchronized_without_identity,
        "synchronized_pair_profit_dollars": str(unique_profit),
        "pair_lock_reasons": dict(sorted(reasons.items())),
        "pair_lock_accounting_rule": (
            "REQUIRE_EXPLICIT_RECEIVE_TIMESTAMPS_AND_LE_500MS_SKEW; "
            "CAPACITY_COLLAPSE_TO_BEST_Q1_SAMPLE_PER_STRICT_PAIR_IDENTITY_AND_DIRECTION"
        ),
        "repeated_resting_depth": "RAW_SAMPLE_TIMING_EVIDENCE_ONLY_NOT_ADDITIVE_CAPACITY",
        "episode_limitation": (
            "WITHOUT_EXPLICIT_OPEN_CLOSE_EPISODES_LATER_RENEWED_LIQUIDITY_IS_INTENTIONALLY_UNDERCOUNTED"
        ),
        "legacy_pair_locks_without_timing": "RESEARCH_ONLY_EXCLUDED_FROM_CAPACITY",
        "execution_eligible": False,
        "approved_for_execution": False,
        "read_only": True,
        "order_entry": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="log file containing DECIDED_MLB_PROP_SIGNAL rows")
    args = parser.parse_args()
    rows = parse_signal_lines(args.path.read_text(encoding="utf-8").splitlines())
    print(json.dumps(analyze(rows), sort_keys=True))


if __name__ == "__main__":
    main()
