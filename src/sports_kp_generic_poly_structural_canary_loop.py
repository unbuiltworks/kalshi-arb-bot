"""Bounded continuous driver for the strict same-Polymarket totals q1 campaign.

This is intentionally a promotion canary, not unlimited scaling.  It repeatedly
runs the fully guarded v5 campaign, allows at most one reconciled two-leg success
per epoch, and exits after 20 successes or on an unresolved reconciliation state.
The Railway service uses restartPolicy=NEVER so the 20-success cap cannot silently
reset itself into an unbounded loop.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from typing import Any, Mapping

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once

MAX_RECONCILED_SUCCESSES = 20
SUCCESS_COOLDOWN_SECONDS = 3.0
NO_FILL_COOLDOWN_SECONDS = 8.0
ERROR_COOLDOWN_SECONDS = 45.0


def _attempts(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(x) for x in (result.get("attempts") or []) if isinstance(x, Mapping)]


def _status_counts(result: Mapping[str, Any]) -> Counter[str]:
    return Counter(str(x.get("status") or "UNKNOWN") for x in _attempts(result))


def _unresolved(result: Mapping[str, Any]) -> str | None:
    for row in _attempts(result):
        status = str(row.get("status") or "")
        if status == "BOTH_FILLED_RECONCILIATION_PENDING":
            return status
        # Any filled maker that cannot be positively confirmed flattened is a
        # stop condition.  Existing v4 statuses include explicit FLATTENED when
        # the close call returned; unknown future filled states fail closed.
        if "MAKER_FILLED" in status and status != "BOTH_FILLED_RECONCILED" and "FLATTENED" not in status:
            return status
    return None


async def main_loop() -> None:
    if os.getenv("SPORTS_POLY_STRUCTURAL_CANARY", "NO").strip().upper() != "YES":
        raise RuntimeError("structural canary gate is not enabled")

    epoch = 0
    successes = 0
    cumulative_attempts = 0
    cumulative_statuses: Counter[str] = Counter()
    while successes < MAX_RECONCILED_SUCCESSES:
        epoch += 1
        result = await run_once()
        attempts = _attempts(result)
        statuses = _status_counts(result)
        cumulative_attempts += len(attempts)
        cumulative_statuses.update(statuses)
        epoch_success = sum(1 for row in attempts if str(row.get("status") or "") == "BOTH_FILLED_RECONCILED")
        successes += epoch_success

        summary = {
            "epoch": epoch,
            "epoch_attempts": len(attempts),
            "epoch_statuses": dict(statuses),
            "epoch_reconciled_successes": epoch_success,
            "cumulative_attempts": cumulative_attempts,
            "cumulative_reconciled_successes": successes,
            "success_cap": MAX_RECONCILED_SUCCESSES,
            "cumulative_statuses": dict(cumulative_statuses),
            "order_entry_gate": os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") == "1",
        }
        print("SPORTS_POLY_STRUCTURAL_CANARY_LOOP " + json.dumps(summary, separators=(",", ":")), flush=True)

        unresolved = _unresolved(result)
        if unresolved:
            print("SPORTS_POLY_STRUCTURAL_CANARY_HARD_HALT " + json.dumps({
                "epoch": epoch,
                "reason": unresolved,
                "successes": successes,
                "attempts": cumulative_attempts,
            }, separators=(",", ":")), flush=True)
            return

        if epoch_success:
            await asyncio.sleep(SUCCESS_COOLDOWN_SECONDS)
        elif any("ERROR" in status or "RATE_LIMIT" in status for status in statuses):
            await asyncio.sleep(ERROR_COOLDOWN_SECONDS)
        else:
            await asyncio.sleep(NO_FILL_COOLDOWN_SECONDS)

    print("SPORTS_POLY_STRUCTURAL_CANARY_COMPLETE " + json.dumps({
        "reconciled_successes": successes,
        "attempts": cumulative_attempts,
        "status_counts": dict(cumulative_statuses),
        "cap_reached": True,
    }, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    asyncio.run(main_loop())
