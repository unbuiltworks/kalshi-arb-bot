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

logger = v27.logger
v23 = v27.v26.v25.v24.v23
base = v23.base
D = Decimal


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
