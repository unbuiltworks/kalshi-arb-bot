"""Production combo supervisor v55: authenticate the richest strict MLB maker q1 path.

Runs one broad maker-trigger census, passes that exact snapshot into a mutation-free
authenticated q1 preview for the richest strict same-game/player/stat MLB threshold
pair, then runs the unchanged v53 Dutch/combo/MVE chain. No orders are created by
this revision.

Transient Polymarket/Cloudflare rate limits are contained at the supervisor boundary:
read-only stages emit a structured skip and the cycle exits cleanly instead of
crashing Railway. Non-rate-limit exceptions still propagate so logic/safety bugs are
never hidden.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from src import sports_poly_maker_trigger_census as maker_census
from src import sports_poly_maker_q1_preview as maker_preview
from src import sports_kp_live_combo_supervisor_v53 as v53


def _is_transient_poly_rate_limit(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) == 429:
        return True
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "ratelimit" in name
        or "rate limit" in text
        or "too many requests" in text
        or "error 1015" in text
        or "cloudflare" in text and "1015" in text
    )


async def _run_rate_limit_tolerant(
    stage: str,
    call: Callable[[], Awaitable[Any]],
) -> Any | None:
    try:
        return await call()
    except Exception as exc:
        if not _is_transient_poly_rate_limit(exc):
            raise
        out = {
            "status": "POLYMARKET_RATE_LIMIT_SKIPPED",
            "stage": stage,
            "exception_type": type(exc).__name__,
            "order_entry": False,
            "realized": False,
        }
        print(
            "SPORTS_COMBO_TRANSIENT_SKIP "
            + json.dumps(out, separators=(",", ":"), default=str),
            flush=True,
        )
        return None


async def run() -> None:
    snapshot = await _run_rate_limit_tolerant("maker_census", maker_census.run_once)
    if snapshot is not None:
        await _run_rate_limit_tolerant(
            "maker_q1_preview", lambda: maker_preview.run_once(snapshot)
        )
    await _run_rate_limit_tolerant("v53_chain", v53.run)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
