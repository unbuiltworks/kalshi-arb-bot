"""Fail-closed synchronization audit for MLB synthetic paper-probe records.

This is research-only accounting validation. It does not place orders or alter
scanner/runtime state. A paper fill group is synchronization-valid only when the
audit record explicitly says the books were synchronized and supplies a finite
cross-venue receive skew in [0, MAX_QUOTE_SKEW_MS].
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable

D = Decimal
MAX_QUOTE_SKEW_MS = D("500")


def _finite_decimal(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def classify_probe_sync(row: dict[str, Any]) -> tuple[bool, str]:
    """Return (valid, reason) for one paper-probe audit row."""
    if row.get("synchronized_book_pair") is not True:
        return False, "SYNCHRONIZED_BOOK_PAIR_NOT_LITERAL_TRUE"
    skew = _finite_decimal(row.get("quote_receive_skew_ms"))
    if skew is None:
        return False, "MISSING_OR_NONFINITE_QUOTE_RECEIVE_SKEW"
    if skew < 0:
        return False, "NEGATIVE_QUOTE_RECEIVE_SKEW"
    if skew > MAX_QUOTE_SKEW_MS:
        return False, "QUOTE_RECEIVE_SKEW_EXCEEDS_500MS"
    return True, "SYNC_EVIDENCE_VALID"


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = valid = 0
    reasons: dict[str, int] = {}
    valid_groups: list[str] = []
    invalid_groups: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += 1
        ok, reason = classify_probe_sync(row)
        reasons[reason] = reasons.get(reason, 0) + 1
        group = str(row.get("trade_group_id") or "")
        if ok:
            valid += 1
            if group:
                valid_groups.append(group)
        elif group:
            invalid_groups.append(group)
    return {
        "paper_probe_groups_seen": total,
        "synchronization_valid_groups": valid,
        "synchronization_invalid_groups": total - valid,
        "all_groups_synchronization_valid": total > 0 and valid == total,
        "max_quote_receive_skew_ms": str(MAX_QUOTE_SKEW_MS),
        "reason_counts": dict(sorted(reasons.items())),
        "valid_trade_group_ids": valid_groups,
        "invalid_trade_group_ids": invalid_groups,
        "pnl_capacity_policy": "EXCLUDE_INVALID_SYNC_GROUPS_FROM_EXECUTABLE_SHADOW_ECONOMICS",
        "read_only": True,
        "order_entry": False,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"synchronized_book_pair": False, "_parse_error": True})
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit_jsonl", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(load_jsonl(args.audit_jsonl)), sort_keys=True))


if __name__ == "__main__":
    main()
