"""Resilient wrapper for the structural/MLB revenue campaign.

Only pre-mutation operations are bounded here. A stuck balance read safely skips the
cycle before any reservation/order; a stuck public persistence recheck skips that
candidate. Once a reservation or order mutation begins, the underlying fail-closed
transport remains unchanged.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
import src.sports_poly_direct_mlb_campaign_q1_v3 as mlb_v3
import src.sports_poly_revenue_campaign_q1 as revenue

BALANCE_TIMEOUT_S = 12.0
PERSISTENCE_TIMEOUT_S = 12.0


class PreMutationBalanceTimeout(RuntimeError):
    pass


def _emit(stage: str, **fields: Any) -> None:
    print("SPORTS_MLB_PREORDER " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


async def run_once() -> dict[str, Any]:
    original_read = v4._read_call
    original_persistent = mlb_v3._persistent_row

    async def bounded_read(stage: str, fn: Any, *args: Any) -> Any:
        if stage != "account_balances_mlb_v2":
            return await original_read(stage, fn, *args)
        _emit("BALANCE_READ_START", timeout_seconds=BALANCE_TIMEOUT_S)
        try:
            out = await asyncio.wait_for(original_read(stage, fn, *args), timeout=BALANCE_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            _emit("BALANCE_READ_TIMEOUT", order_entry=False, mutation_started=False)
            raise PreMutationBalanceTimeout("Polymarket MLB balance read timed out before reservation") from exc
        _emit("BALANCE_READ_OK")
        return out

    async def bounded_persistent(feed: Any, pair: Any, expected: Any) -> Any:
        _emit("PERSISTENCE_START", low_slug=pair[0], high_slug=pair[1], timeout_seconds=PERSISTENCE_TIMEOUT_S)
        try:
            out = await asyncio.wait_for(original_persistent(feed, pair, expected), timeout=PERSISTENCE_TIMEOUT_S)
        except asyncio.TimeoutError:
            _emit("PERSISTENCE_TIMEOUT", low_slug=pair[0], high_slug=pair[1], order_entry=False, mutation_started=False)
            return None
        _emit("PERSISTENCE_DONE", low_slug=pair[0], high_slug=pair[1], persistent=out is not None)
        return out

    v4._read_call = bounded_read
    mlb_v3._persistent_row = bounded_persistent
    try:
        try:
            return await revenue.run_once()
        except PreMutationBalanceTimeout:
            return {
                "status": "MLB_BALANCE_READ_TIMEOUT_SAFE_SKIP",
                "attempts": [],
                "attempt_count": 0,
                "order_entry": False,
                "locked_execution": False,
                "realized": False,
            }
    finally:
        v4._read_call = original_read
        mlb_v3._persistent_row = original_persistent
