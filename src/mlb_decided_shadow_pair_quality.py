"""Audit MLB decided-prop shadow signals without overstating cross-venue locks.

The post-decision/terminal prop lane can expose two different research signals:

* single-venue known-winner discounts; and
* cross-venue complementary q=1 locks.

A concurrent fetch alone is not proof the two books were received closely enough in
time to represent a simultaneous lock.  This analyzer therefore counts cross-venue
pair locks only when the producer records explicit receive timestamps and a bounded
receive skew.  Older rows without synchronization telemetry remain useful for the
single-venue known-winner study, but their pair locks are rejected fail-closed.

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
    # Fail closed if producer telemetry is internally inconsistent.  One millisecond
    # tolerance only accommodates integer/rounded timestamp serialization.
    if abs(calculated - recorded_skew_ms) > Decimal("1"):
        return False, "INCONSISTENT_SYNCHRONIZATION_EVIDENCE"
    if recorded_skew_ms < 0 or recorded_skew_ms > max_skew_ms:
        return False, "PAIR_RECEIVE_SKEW_EXCEEDED"
    return True, "SYNCHRONIZED_PAIR_EVIDENCE"


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
    synchronized_pair_locks = 0
    synchronized_pair_profit = Decimal("0")

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
        synchronized_pair_locks += 1
        profit = _d(
            pair.get("profit")
            if pair.get("profit") is not None
            else pair.get("locked_profit")
        )
        if profit is not None and profit > 0:
            synchronized_pair_profit += profit

    return {
        "signal_rows": signal_rows,
        "single_venue_known_winner_edges": known_winner_edges,
        "cross_venue_pair_locks_seen": pair_locks_seen,
        "synchronized_cross_venue_pair_locks": synchronized_pair_locks,
        "synchronized_pair_profit_dollars": str(synchronized_pair_profit),
        "pair_lock_reasons": dict(sorted(reasons.items())),
        "pair_lock_accounting_rule": "REQUIRE_EXPLICIT_RECEIVE_TIMESTAMPS_AND_LE_500MS_SKEW",
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
