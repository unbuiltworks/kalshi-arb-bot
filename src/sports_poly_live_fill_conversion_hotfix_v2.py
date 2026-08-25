"""Fill-conversion hotfix: executable proximity first with bounded sticky lifecycle.

The prior v2 wrapper accidentally hard-coded a six-hour maker lifecycle. That could
occupy the only live maker slot on a quote many cents from the contra ask even though
the inner loop was otherwise healthy. This version keeps the account-authoritative
startup cleanup and all existing economics/risk gates, but:

* caps one resting maker lifecycle at 180 seconds;
* admits live maker rows only when the safe quote is within two reported ticks of
  the current contra ask;
* keeps price distance first and activity only as a tie-breaker;
* preserves exact >=3c after-fee floor, q1 sizing, participation certification,
  one-maker exposure, hedge rechecks, flatten paths, and shared $5 risk ceiling.

A quote farther than two ticks may still be mathematically valid, but it is not a
useful live fill-conversion candidate and does not consume the maker slot.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import src.sports_poly_live_fill_conversion_hotfix as base
import src.sports_poly_structural_live_supervisor_recovered as recovered
from src.sports_poly_fast_orphan_cleanup import cleanup_current_revenue_orders_fast

STICKY_LIFECYCLE_SECONDS = 180.0
MAX_FILLABLE_GAP_TICKS = base.D("2")


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


def _fillable_gap(row: Mapping[str, Any]) -> bool:
    gap = base._d(row.get("maker_gap_to_ask") or "1")
    tick = base._d(row.get("reported_maker_tick") or "0")
    if tick <= 0 or gap <= 0:
        return False
    return gap <= tick * MAX_FILLABLE_GAP_TICKS


async def _fillable_ranked(
    feed: Mapping[str, Mapping[str, Any]],
    pairs: list[tuple[str, str]],
    *,
    require_near_touch: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, health = await base._activity_ranked(
        feed,
        pairs,
        require_near_touch=require_near_touch,
    )
    admitted = [row for row in rows if _fillable_gap(row)]
    health = dict(health)
    health["pre_fillability_rows"] = len(rows)
    health["fillable_rows"] = len(admitted)
    health["far_gap_rows_suppressed"] = len(rows) - len(admitted)
    health["max_fillable_gap_ticks"] = str(MAX_FILLABLE_GAP_TICKS)
    return admitted, health


def _cleanup_clear(result: Mapping[str, Any]) -> bool:
    return int(result.get("unresolved") or 0) == 0 and int(result.get("partial_unresolved") or 0) == 0


def configure() -> None:
    # Keep v1's safe-continue states and activity telemetry, then impose the
    # production fillability gate and bounded lifecycle at the outermost layer.
    base._activity_sort_key = _near_touch_activity_sort_key
    base.configure()
    base.mlb_v3.STICKY_REST_SECONDS = STICKY_LIFECYCLE_SECONDS
    base.mlb_v3._fresh_ranked = _fillable_ranked


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
