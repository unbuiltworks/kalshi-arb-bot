"""Second fill-conversion hotfix: price proximity first, activity second.

The v1 hotfix proved Polymarket book stats are available, but lifetime activity alone
can select a maker far from the contra ask. This wrapper keeps all v1 safety changes
while requiring the ranking to prefer a one-tick/near-touch maker before using traded
activity to choose among similarly fillable candidates.
"""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

import src.sports_poly_live_fill_conversion_hotfix as base
import src.sports_poly_structural_live_supervisor_recovered as recovered


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


def configure() -> None:
    base._activity_sort_key = _near_touch_activity_sort_key
    base.configure()


async def main() -> None:
    configure()
    await recovered.main()


if __name__ == "__main__":
    asyncio.run(main())
