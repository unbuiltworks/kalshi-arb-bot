"""Second fill-conversion hotfix: executable price proximity first, activity second.

The v1 hotfix proved Polymarket book stats are available, but lifetime activity alone
can select a maker far from the contra ask. This wrapper keeps all v1 safety changes
while ranking the smallest safe gap to the contra ask first. Activity only breaks ties
among similarly fillable prices. The resting window is effectively lifecycle-bounded:
the proven inner loop still rechecks hedge depth/economics every second and exits on
fill, cancel, reject, expiry, missing hedge book, or loss of the exact >=3c floor.

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
        gap,
        -both,
        -combined,
        not bool(row.get("improves_bid")),
        -base._d(row.get("hedge_depth")),
        -base._d(row.get("quote_locked_profit")),
    )


def _cleanup_clear(result: Mapping[str, Any]) -> bool:
    return int(result.get("unresolved") or 0) == 0 and int(result.get("partial_unresolved") or 0) == 0


def configure() -> None:
    base._activity_sort_key = _near_touch_activity_sort_key
    base.configure()
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
