"""Production combo supervisor v9: durable restart-safe MVE creation budget.

v8 proved the live manufacturing/RFQ pipeline works, but the legacy daily
creation count lived only in process memory. This layer synchronizes that count
from Postgres before every generator run and atomically reserves a durable slot
before every Kalshi multivariate market create/resolve request.

If the ledger is unavailable, market creation fails closed. Read-only scanning,
RFQs for already-created MVEs, action-only fill notifications, and q=1 execution
safety remain unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any

import httpx

from src import sports_kp_live_combo_supervisor_v8 as v8
from src import sports_kp_mve_combo_generator_v2 as generator_v2
from src.auth import KalshiClient as _BaseKalshiClient
from src.sports_mve_creation_budget import get_or_seed_today, reserve_today

base = v8.base
logger = v8.logger
_ORIGINAL_V8_GENERATE = base.generate_mves


def _daily_cap() -> int:
    try:
        per_run = max(1, min(int(os.getenv("MVE_GENERATOR_MAX_CREATIONS_PER_RUN", "8")), 25))
    except Exception:
        per_run = 8
    try:
        return max(per_run, min(int(os.getenv("MVE_GENERATOR_DAILY_CREATION_CAP", "600")), 650))
    except Exception:
        return 600


class _BudgetedKalshiClient(_BaseKalshiClient):
    async def post(self, path: str, **kwargs: Any):
        is_create = path.startswith("/multivariate_event_collections/")
        reserved_count: int | None = None
        if is_create:
            try:
                reserved_count = await reserve_today(_daily_cap())
            except Exception as exc:
                logger.error(
                    "SPORTS_MVE_PERSISTENT_BUDGET_BLOCK %s",
                    json.dumps({"reason": type(exc).__name__, "detail": str(exc)[:180], "market_creation": False}),
                )
                return httpx.Response(503, request=httpx.Request("POST", "https://budget.invalid/"))
            if reserved_count is None:
                logger.warning(
                    "SPORTS_MVE_PERSISTENT_BUDGET_CAP %s",
                    json.dumps({"daily_cap": _daily_cap(), "market_creation": False}),
                )
                return httpx.Response(429, request=httpx.Request("POST", "https://budget.invalid/"))
            logger.info(
                "SPORTS_MVE_PERSISTENT_BUDGET_RESERVED %s",
                json.dumps({"created_or_attempted_today": reserved_count, "daily_cap": _daily_cap()}),
            )

        response = await super().post(path, **kwargs)
        if is_create and response.status_code != 200:
            try:
                detail = response.text[:400]
            except Exception:
                detail = ""
            logger.warning(
                "SPORTS_MVE_CREATE_REJECT %s",
                json.dumps(
                    {
                        "http_status": response.status_code,
                        "created_or_attempted_today": reserved_count,
                        "detail": detail,
                        "budget_refund": False,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                ),
            )
        return response


async def _persistent_generate() -> dict[str, Any]:
    enabled = os.getenv("ENABLE_KALSHI_MVE_GENERATOR", "NO").strip().upper() == "YES"
    if not enabled:
        return await _ORIGINAL_V8_GENERATE()
    try:
        durable_count = await get_or_seed_today()
    except Exception as exc:
        logger.error(
            "SPORTS_MVE_PERSISTENT_BUDGET_UNAVAILABLE %s",
            json.dumps({"reason": type(exc).__name__, "detail": str(exc)[:180], "market_creation": False}),
        )
        return {
            "status": "MVE_GENERATOR_PERSISTENT_BUDGET_UNAVAILABLE",
            "created_today": None,
            "daily_cap": _daily_cap(),
            "market_creation": False,
            "order_entry": False,
            "ranked": [],
        }

    # Legacy generator still uses this value for per-run remaining-budget math;
    # authoritative enforcement happens atomically in Postgres in post().
    generator_v2._DAY = datetime.now(timezone.utc).date().isoformat()
    generator_v2._CREATED_TODAY = durable_count
    logger.info(
        "SPORTS_MVE_PERSISTENT_BUDGET_SYNC %s",
        json.dumps({"created_or_attempted_today": durable_count, "daily_cap": _daily_cap(), "restart_safe": True}),
    )
    result = await _ORIGINAL_V8_GENERATE()
    try:
        durable_after = await get_or_seed_today()
    except Exception:
        durable_after = None
    if isinstance(result, dict):
        # Keep the legacy successful-create count separate from the authoritative
        # durable allowance, which intentionally counts every attempted create.
        result["successful_creates_this_process_counting_base"] = result.get("created_today")
        result["created_or_attempted_today"] = durable_after
        result["created_today"] = durable_after
        result["daily_cap"] = _daily_cap()
    return result


# generate_and_score resolves this module global at runtime when constructing its client.
generator_v2.KalshiClient = _BudgetedKalshiClient
base.generate_mves = _persistent_generate

run = base.run
main = base.main


if __name__ == "__main__":
    main()
