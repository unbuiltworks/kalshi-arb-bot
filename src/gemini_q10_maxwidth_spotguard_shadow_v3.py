"""Read-only V3 diagnostic wrapper for the BTC5 q10 reward shadow.

Adds rate-limited candidate rejection telemetry without changing V2 strategy behavior.
No private endpoints and no order mutation.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal as D
import time

from src import gemini_q10_maxwidth_spotguard_shadow as base
from src import gemini_q10_maxwidth_spotguard_shadow_v2 as v2


async def run() -> None:
    original = v2.evaluate_v2
    last_emit = 0.0

    def instrumented(**kwargs):
        nonlocal last_emit
        out = original(**kwargs)
        now = time.monotonic()
        if not out["qualified_safe"] and now - last_emit >= 1.0:
            reasons = []
            if not out["reward_ok"]:
                reasons.append("REWARD_GEOMETRY")
            if not out["maker_ok"]:
                reasons.append("WOULD_CROSS")
            if not out["rescue_safe"]:
                reasons.append("RESCUE_BOUND")
            try:
                if D(out["locked_edge"]) <= D("0"):
                    reasons.append("NONPOSITIVE_PAIR_EDGE")
            except Exception:
                reasons.append("EDGE_PARSE")
            last_emit = now
            base.emit(
                "CANDIDATE_REJECT",
                reasons=reasons,
                candidate=out,
                read_only=True,
                order_entry=False,
            )
        return out

    v2.evaluate_v2 = instrumented
    base.emit("V3_DIAGNOSTICS_ACTIVE", read_only=True, order_entry=False)
    try:
        await v2.run()
    finally:
        v2.evaluate_v2 = original


if __name__ == "__main__":
    asyncio.run(run())
