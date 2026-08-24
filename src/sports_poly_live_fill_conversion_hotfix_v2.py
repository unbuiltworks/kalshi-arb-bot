"""Second fill-conversion hotfix: price proximity first, activity second.

The v1 hotfix proved Polymarket book stats are available, but lifetime activity alone
can select a maker far from the contra ask. This wrapper keeps all v1 safety changes,
requires a one-tick/near-touch maker before using traded activity to break ties, and
makes the resting window effectively lifecycle-bounded: the proven inner loop still
rechecks hedge depth/economics every second and exits on fill, cancel, reject, expiry,
missing hedge book, or loss of the exact >=3c floor. The large time bound is only a
last-resort process guard, not the normal reason to surrender queue position.

Rollout safety is account-authoritative: before Command Center reservation recovery,
startup performs one all-open account read, cancels matching GTC sports orphans,
flattens any known partial quantity, verifies the open set when needed, and only then
allows the existing trade-activity recovery logic to decide whether a reservation can
be released. Any cleanup ambiguity hard-stops.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import src.sports_poly_live_fill_conversion_hotfix as base
import src.sports_poly_structural_live_supervisor_recovered as recovered
from src.sports_poly_fast_orphan_cleanup import cleanup_current_revenue_orders_fast

# Six hours is intentionally longer than an MLB market's useful live lifecycle.
# Orders should normally leave via fill/market lifecycle/hedge economics, not TTL.
STICKY_LIFECYCLE_SECONDS = 6 * 60 * 60.0


def _near_touch_activity_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    maker = base._d(row.get("activity_maker_shares"))
    hedge = base._d(row.get("activity_hedge_shares"))
    combined = maker + hedge
    both = min(maker, hedge)
    gap = base._d(row.get("maker_gap_to_ask") or "1")
    tick = base._d(row.get("reported_maker_tick") or "0.01")
    if tick <= 0:
        tick = base.D("0.01")
    near_touch = gap <= tick
    return (
        not near_touch,
        -both,
        -combined,
        not bool(row.get("improves_bid")),
        gap,
        -base._d(row.get("hedge_depth")),
        -base._d(row.get("quote_locked_profit")),
    )


def _cleanup_clear(result: Mapping[str, Any]) -> bool:
    return int(result.get("unresolved") or 0) == 0 and int(result.get("partial_unresolved") or 0) == 0


def configure() -> None:
    base._activity_sort_key = _near_touch_activity_sort_key
    base.configure()
    # Override v1's short diagnostic window only after all v1 safety hooks install.
    # v3's attempt wrapper reads this value immediately before each maker attempt.
    base.mlb_v3.STICKY_REST_SECONDS = STICKY_LIFECYCLE_SECONDS


async def main() -> None:
    configure()
    try:
        cleanup = await cleanup_current_revenue_orders_fast()
    except Exception as exc:
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "PRE_RECOVERY_ACCOUNT_CLEANUP_EXCEPTION_HARD_HALT",
                    "error_type": type(exc).__name__,
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(cleanup, separators=(",", ":"), default=str), flush=True)
    if not _cleanup_clear(cleanup):
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps(
                {
                    "status": "PRE_RECOVERY_ACCOUNT_CLEANUP_AMBIGUOUS_HARD_HALT",
                    "unresolved": cleanup.get("unresolved"),
                    "partial_unresolved": cleanup.get("partial_unresolved"),
                    "order_entry": False,
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return
    await recovered.main()


if __name__ == "__main__":
    asyncio.run(main())
