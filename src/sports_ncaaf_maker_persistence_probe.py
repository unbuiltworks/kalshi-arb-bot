"""Five-snapshot read-only persistence check for NCAAF maker->hedge candidates.

Measures whether the >=3c shadow candidates survive repeated fresh public-book
snapshots and whether any become one-tick-from-touch. No authentication/order entry.
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import logging
from typing import Any

import src.sports_ncaaf_gk_maker_shadow as shadow

log = logging.getLogger("kalshi_arb.sports_ncaaf_maker_persistence_probe")


def ident(row: dict[str, Any]) -> str:
    return "|".join([
        ",".join(row.get("pair") or []),
        str(row.get("family") or ""),
        str(row.get("subject") or ""),
        str(row.get("line") or ""),
        str(row.get("g") or ""),
        str(row.get("k") or ""),
        str(row.get("maker_venue") or ""),
        str(row.get("maker_side") or ""),
    ])


async def main_async() -> None:
    # The underlying scanner logs a full snapshot; suppress those repeats and emit
    # only the compact persistence result from this wrapper.
    logging.getLogger("kalshi_arb.sports_ncaaf_gk_maker_shadow").setLevel(logging.CRITICAL)
    cycles = 5
    appearances: Counter[str] = Counter()
    near_touch: Counter[str] = Counter()
    rows_by_id: dict[str, dict[str, Any]] = {}
    prices: dict[str, list[str]] = defaultdict(list)
    profits: dict[str, list[str]] = defaultdict(list)
    cycle_counts: list[dict[str, int]] = []

    for i in range(cycles):
        out = await shadow.run_once()
        rows = list(out.get("top_maker") or [])
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = ident(row)
            if key in seen:
                continue
            seen.add(key)
            appearances[key] += 1
            rows_by_id[key] = row
            prices[key].append(str(row.get("maker_price")))
            profits[key].append(str(row.get("fallback_profit")))
            if bool(row.get("near_touch")):
                near_touch[key] += 1
        cycle_counts.append({
            "maker_floor_3c": int(out.get("maker_hedge_floor_3c") or 0),
            "near_touch_floor_3c": int(out.get("maker_hedge_near_touch_floor_3c") or 0),
            "priced_instances": int(out.get("priced_instances") or 0),
        })
        if i + 1 < cycles:
            await asyncio.sleep(2.0)

    persistent = []
    for key, count in appearances.most_common():
        row = rows_by_id[key]
        persistent.append({
            "id": key,
            "appearances": count,
            "appearance_rate": count / cycles,
            "near_touch_appearances": near_touch[key],
            "maker_prices": prices[key],
            "fallback_profits": profits[key],
            "family": row.get("family"),
            "pair": row.get("pair"),
            "subject": row.get("subject"),
            "line": row.get("line"),
            "maker_venue": row.get("maker_venue"),
            "maker_side": row.get("maker_side"),
            "hedge_venue": row.get("hedge_venue"),
            "hedge_side": row.get("hedge_side"),
            "g": row.get("g"),
            "k": row.get("k"),
        })

    out = {
        "status": "SPORTS_NCAAF_MAKER_PERSISTENCE_PROBE",
        "cycles": cycles,
        "cycle_counts": cycle_counts,
        "unique_candidates_seen": len(appearances),
        "persistent_all_cycles": sum(1 for n in appearances.values() if n == cycles),
        "persistent_3plus_cycles": sum(1 for n in appearances.values() if n >= 3),
        "ever_near_touch": sum(1 for n in near_touch.values() if n > 0),
        "candidates": persistent[:40],
        "read_only": True,
        "order_entry": False,
    }
    log.warning("SPORTS_NCAAF_MAKER_PERSISTENCE_PROBE %s", json.dumps(out,separators=(",",":"),default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(main_async())
