"""Production combo supervisor v28: return live-capable MVE results immediately.

v10 and v11 intentionally wrapped the same-Kalshi generator with post-generation
Gemini and Polymarket replacement research. Those replacements are not settlement-
certified and cannot be used by the current live q=1 executor, yet both network
sweeps occur after RFQs are scored and before the supervisor can inspect an
EXECUTABLE_CANDIDATE. On live/short-horizon sports that delays the candidate-to-
revalidation handoff by roughly 15-20 seconds.

This layer restores v9's durable-budget -> v8 dual-scalar-safe generator as the
synchronous return path, while retaining all later runtime patches already loaded
by v27: robust leg parsing, expected-resolution horizon, current candidate ranking,
duplicate/redundancy filters, and exact same-Kalshi RFQ scoring. It also preserves
v23 process-local RFQ feedback bookkeeping after the core result is available.

Research-only external replacement annotations are deliberately removed from this
service's synchronous generation path. Direct K/P and rule-audit services continue
to research those venues independently.

To improve manufacturing breadth without weakening execution gates, candidate
creation now reserves up to half of each bounded candidate list for leg-count
diversity across 2/3/4/5-leg baskets, then fills the remainder in the existing
best-first order. This prevents a cheap 2-leg cluster from consuming every scarce
creation/RFQ slot while preserving the original ranking for at least half the
budget. The live executor still independently revalidates every created market.

No live invariant changes: q=1, >=3c after conservative fees, final component/RFQ
depth, capital/position checks, fresh quote refresh, flatten/hard-halt behavior,
one live attempt per process, and settlement accounting remain unchanged.
"""
from __future__ import annotations

from decimal import Decimal
import json
from typing import Any

from src import sports_kp_live_combo_supervisor_v27 as v27
from src import sports_kp_live_combo_supervisor_v9 as v9
from src import sports_kp_mve_combo_generator as generator

logger = v27.logger
v23 = v27.v26.v25.v24.v23
base = v23.base
D = Decimal

# Capture the fully-patched v24+ candidate rank after importing v27. That rank
# already contains duplicate/redundancy rejection, same-game family priority,
# feedback, and pure same-Kalshi executable economics.
_ORIGINAL_CANDIDATES = generator._candidate_combos


def _leg_count_diverse_candidates(collection, event_legs, *, max_candidates, min_locked):
    """Keep best-first economics while reserving bounded exploration by leg count."""
    if max_candidates <= 0:
        return []
    expanded_cap = min(max(max_candidates * 4, max_candidates), 1400)
    raw = _ORIGINAL_CANDIDATES(
        collection,
        event_legs,
        max_candidates=expanded_cap,
        min_locked=min_locked,
    )
    if len(raw) <= max_candidates:
        return raw

    by_count: dict[int, list[Any]] = {n: [] for n in generator.SUPPORTED_LEGS}
    for row in raw:
        legs = row[1]
        n = len(legs)
        if n in by_count:
            by_count[n].append(row)

    # At most half the list is diversity-reserved. Round-robin keeps this bounded
    # and cannot crowd out the original best-first rank.
    diversity_cap = max(1, max_candidates // 2)
    diverse: list[Any] = []
    seen: set[tuple[str, ...]] = set()
    cursors = {n: 0 for n in by_count}
    while len(diverse) < diversity_cap:
        progressed = False
        for n in generator.SUPPORTED_LEGS:
            i = cursors[n]
            rows = by_count[n]
            if i >= len(rows):
                continue
            row = rows[i]
            cursors[n] = i + 1
            key = tuple(sorted(
                f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in row[1]
            ))
            if key in seen:
                continue
            seen.add(key)
            diverse.append(row)
            progressed = True
            if len(diverse) >= diversity_cap:
                break
        if not progressed:
            break

    out = list(diverse)
    for row in raw:
        if len(out) >= max_candidates:
            break
        key = tuple(sorted(
            f"{leg.event_ticker}:{leg.market_ticker}:{leg.side}" for leg in row[1]
        ))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)

    logger.warning("SPORTS_MVE_LEG_DIVERSITY %s", json.dumps({
        "raw_candidates": len(raw),
        "returned_candidates": len(out),
        "diversity_reserved": len(diverse),
        "available_by_leg_count": {str(n): len(rows) for n, rows in by_count.items()},
        "returned_by_leg_count": {
            str(n): sum(1 for row in out if len(row[1]) == n)
            for n in generator.SUPPORTED_LEGS
        },
        "original_best_first_slots_preserved_min": max_candidates - diversity_cap,
        "live_threshold_changed": False,
        "execution_semantics_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return out


generator._candidate_combos = _leg_count_diverse_candidates


async def _fast_executable_generate() -> dict[str, Any]:
    # v9's function preserves the durable restart-safe creation allowance and
    # calls the v8 dual-direction same-Kalshi generator captured before v10/v11
    # installed their external post-generation network wrappers.
    result = await v9._persistent_generate()
    if not isinstance(result, dict):
        return result

    # Keep v23's process-local family feedback without another network call.
    rows = [x for x in (result.get("ranked") or []) if isinstance(x, dict)]
    cycle_summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        sig = v23._family_signature_from_legs(row.get("legs") or [])
        if not sig:
            continue
        fb = v23._FEEDBACK[sig]
        fb["observations"] = int(fb["observations"]) + 1
        if int(row.get("quote_count") or 0) > 0:
            fb["quoted"] = int(fb["quoted"]) + 1
        lock = v23._lock_value(row)
        if lock is not None:
            fb["last_lock"] = lock
            fb["best_lock"] = max(D(fb["best_lock"]), lock)
        cycle_summary[sig] = {
            "observations": int(fb["observations"]),
            "quoted": int(fb["quoted"]),
            "best_lock": str(fb["best_lock"]),
            "last_lock": str(fb["last_lock"]),
        }

    top = sorted(
        ({"signature": sig, **data} for sig, data in cycle_summary.items()),
        key=lambda x: D(str(x["best_lock"])),
        reverse=True,
    )[:12]
    logger.warning("SPORTS_MVE_FAST_HANDOFF %s", json.dumps({
        "markets_scored": len(rows),
        "executable_candidates": result.get("executable_candidates", 0),
        "inverse_executable_candidates": result.get("inverse_executable_candidates", 0),
        "top_family_signatures": top,
        "post_rfq_gemini_shadow_blocking": False,
        "post_rfq_polymarket_shadow_blocking": False,
        "external_replacements_execution_enabled": False,
        "live_threshold_changed": False,
        "order_entry": False,
    }, separators=(",", ":"), default=str))
    return result


base.generate_mves = _fast_executable_generate

run = v27.run
main = v27.main

if __name__ == "__main__":
    main()
