"""Final live coordinator for one bounded Kalshi/Polymarket sports attempt.

The transport performs account/market preflight first.  Only after that work is
complete does the coordinator ask the caller for a newly rebuilt plan from the
current hot books.  The single-use ARM token is consumed only if that fresh plan
still passes all immutable gates.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import os
import time
from typing import Awaitable, Callable, Any

from src.sports_kp_execution import (
    DEFAULT_ARM_TOKEN,
    DEFAULT_MAX_BOOK_AGE_MS,
    DEFAULT_MAX_QUOTE_SKEW_MS,
    DEFAULT_MIN_PROFIT,
    KPExecutionPlan,
    KalshiExecutor,
    PolymarketExecutor,
    VenueResult,
)

FreshPlanProvider = Callable[[], Awaitable[KPExecutionPlan]]


def _same_instrument(a: KPExecutionPlan, b: KPExecutionPlan) -> bool:
    return (
        a.settlement_key == b.settlement_key
        and a.rule_fingerprint_sha256 == b.rule_fingerprint_sha256
        and a.poly.market_id == b.poly.market_id
        and a.poly.native_outcome == b.poly.native_outcome
        and a.kalshi.market_id == b.kalshi.market_id
        and a.kalshi.native_outcome == b.kalshi.native_outcome
    )


async def execute_after_fresh_revalidation(
    initial_plan: KPExecutionPlan,
    fresh_plan_provider: FreshPlanProvider,
    *,
    arm_token: Path = DEFAULT_ARM_TOKEN,
    live_requested: bool = False,
    poly: PolymarketExecutor | None = None,
    kalshi: KalshiExecutor | None = None,
) -> dict[str, Any]:
    if not live_requested or os.getenv("ENABLE_SPORTS_KP_LIVE", "NO").strip().upper() != "YES":
        raise PermissionError("sports K/P live execution is disabled")
    if not arm_token.is_file():
        raise PermissionError("single-use sports ARM token is absent")

    poly = poly or PolymarketExecutor()
    kalshi = kalshi or KalshiExecutor()
    try:
        # Slow validation happens before the final market snapshot.
        await asyncio.gather(poly.preflight(initial_plan.poly), kalshi.preflight(initial_plan.kalshi))

        fresh = await fresh_plan_provider()
        if not _same_instrument(initial_plan, fresh):
            raise PermissionError("instrument or rule fingerprint changed during preflight")
        now_ms = int(time.time() * 1000)
        if now_ms - fresh.created_ms > DEFAULT_MAX_BOOK_AGE_MS:
            raise PermissionError("post-preflight plan is stale")
        if fresh.unit_profit < DEFAULT_MIN_PROFIT:
            raise PermissionError("post-preflight edge fell below threshold")
        if fresh.quote_skew_ms > DEFAULT_MAX_QUOTE_SKEW_MS:
            raise PermissionError("post-preflight quote skew exceeds threshold")

        # Authorization is consumed immediately before the first mutation.
        try:
            arm_token.unlink()
        except FileNotFoundError as exc:
            raise PermissionError("ARM token disappeared before send") from exc

        p_result, k_result = await asyncio.gather(
            poly.place_fok(fresh.poly),
            kalshi.place_fok(fresh.kalshi),
        )
        ambiguous = p_result.status == "UNKNOWN" or k_result.status == "UNKNOWN"
        p_fill, k_fill = p_result.filled_one, k_result.filled_one
        flatten: VenueResult | None = None

        if ambiguous:
            outcome = "AMBIGUOUS_SUBMISSION"
            hard_halt = True
        elif p_fill and k_fill:
            outcome = "BOTH_FILLED"
            hard_halt = False
        elif not p_fill and not k_fill:
            outcome = "NEITHER_FILLED"
            hard_halt = False
        else:
            outcome = "ONE_LEG_FILLED"
            hard_halt = True
            flatten = await (
                poly.emergency_flatten(fresh.poly)
                if p_fill else kalshi.emergency_flatten(fresh.kalshi)
            )

        return {
            "outcome": outcome,
            "hard_halt_required": hard_halt,
            "single_use_arm_consumed": True,
            "post_preflight_revalidated": True,
            "plan": fresh.as_dict(),
            "polymarket": p_result.as_dict(),
            "kalshi": k_result.as_dict(),
            "flatten": None if flatten is None else flatten.as_dict(),
        }
    finally:
        try:
            poly.close()
        except Exception:
            pass
        try:
            await kalshi.close()
        except Exception:
            pass
