"""Continuous fail-closed supervisor for strict same-Polymarket totals ladders.

The underlying v5 campaign owns all order construction, exact semantic checks,
post-only q1 maker placement, hedge revalidation, FOK hedge execution, partial
flattening and after-fee economics.  This wrapper adds production lifecycle
protections needed for continuous operation:

* one campaign at a time; never overlapping makers;
* startup / post-rate-limit scoped orphan cleanup over the current structural
  totals universe before another maker may be placed;
* bounded cool-down after authenticated Polymarket rate limits instead of a hot
  retry loop;
* hard halt on partial fills, flatten paths, ambiguous reconciliation, or other
  unexpected mutation outcomes;
* successful two-leg fills may continue to the next campaign, but are still not
  called realized P&L here. Final settlement/reconciliation remains authoritative.

Live mutation remains gated by SPORTS_POLY_RELAY_LIVE_Q1=1 inside v5.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_campaign
from src.sports_poly_structural_monotone import discover_structural_pairs, structural_identity

NORMAL_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_NORMAL_SLEEP_SECONDS", "8"))
EMPTY_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_EMPTY_SLEEP_SECONDS", "30"))
RATE_LIMIT_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_RATE_LIMIT_SLEEP_SECONDS", "90"))
CLEANUP_RETRY_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_CLEANUP_RETRY_SECONDS", "120"))

TERMINAL_STATES = {
    "ORDER_STATE_FILLED",
    "ORDER_STATE_CANCELED",
    "ORDER_STATE_REJECTED",
    "ORDER_STATE_EXPIRED",
}
RATE_LIMIT_STATUSES = {
    "READ_RATE_LIMIT_EXHAUSTED_SCOPED_CANCEL",
    "MAKER_CREATE_RATE_LIMIT_SCOPED_CANCEL",
}
SAFE_CONTINUE_STATUSES = {
    "MAKER_NOT_FILLED_CANCELED",
    "MAKER_CANCELED_EDGE_GONE",
    "SKIP_BOOK_MISSING",
    "SKIP_HEDGE_DEPTH",
    "SKIP_FLOOR",
    "BOTH_FILLED_RECONCILED",
}


def _is_rate_limit(exc: BaseException) -> bool:
    return type(exc).__name__ == "RateLimitError"


async def _current_total_slugs() -> list[str]:
    feed, _, _ = await discover_structural_pairs(horizon_hours=72)
    slugs: list[str] = []
    for slug, market in feed.items():
        identity = structural_identity(market)
        if identity is not None and str(identity[0]).startswith("TOTAL::"):
            slugs.append(str(slug))
    return sorted(set(slugs))


async def cleanup_current_total_orders() -> dict[str, Any]:
    """Cancel and verify only open orders in the current strict totals universe."""
    slugs = await _current_total_slugs()
    if not slugs:
        return {"status": "CLEANUP_NO_TOTAL_SLUGS", "slugs": 0, "open_seen": 0, "canceled": 0, "unresolved": 0}

    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        response = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
        orders = response.get("orders") or [] if isinstance(response, Mapping) else []
        canceled: list[str] = []
        unresolved: list[str] = []
        for raw in orders:
            if not isinstance(raw, Mapping):
                continue
            oid = str(raw.get("id") or "")
            slug = str(raw.get("marketSlug") or "")
            state = str(raw.get("state") or "")
            if not oid or slug not in slugs or state in TERMINAL_STATES:
                continue
            try:
                await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)

        # A second scoped read is mandatory after any discovered open order. If
        # it cannot confirm the account is clean, the supervisor fails closed.
        remaining: list[str] = []
        if orders:
            await asyncio.sleep(1.0)
            verify = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
            for raw in verify.get("orders") or []:
                if not isinstance(raw, Mapping):
                    continue
                oid = str(raw.get("id") or "")
                state = str(raw.get("state") or "")
                if oid and state not in TERMINAL_STATES:
                    remaining.append(oid)
        unresolved.extend(remaining)
        return {
            "status": "STRUCTURAL_TOTAL_ORPHAN_CLEANUP",
            "slugs": len(slugs),
            "open_seen": len(orders),
            "canceled": len(canceled),
            "unresolved": len(set(unresolved)),
        }
    finally:
        client.close()


def _attempt_statuses(result: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for attempt in result.get("attempts") or []:
        if isinstance(attempt, Mapping):
            out.append(str(attempt.get("status") or ""))
    return [s for s in out if s]


def _hard_halt_reason(statuses: list[str]) -> str | None:
    for status in statuses:
        if status in RATE_LIMIT_STATUSES or status in SAFE_CONTINUE_STATUSES:
            continue
        # Any mutation/reconciliation state not explicitly reviewed above is a
        # production stop. This includes partial fills and every flatten path.
        return status
    return None


async def _ensure_clean() -> bool:
    try:
        result = await cleanup_current_total_orders()
    except Exception as exc:
        print(
            "SPORTS_STRUCTURAL_SUPERVISOR "
            + json.dumps({
                "status": "CLEANUP_UNCONFIRMED_COOLDOWN",
                "error_type": type(exc).__name__,
                "rate_limited": _is_rate_limit(exc),
                "sleep_seconds": CLEANUP_RETRY_SECONDS,
                "order_entry": False,
            }, separators=(",", ":")),
            flush=True,
        )
        return False
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(result, separators=(",", ":")), flush=True)
    return int(result.get("unresolved") or 0) == 0


async def main() -> None:
    clean = await _ensure_clean()
    while not clean:
        await asyncio.sleep(CLEANUP_RETRY_SECONDS)
        clean = await _ensure_clean()

    print(
        "SPORTS_STRUCTURAL_SUPERVISOR "
        + json.dumps({
            "status": "LIVE_LOOP_READY",
            "live_gate": os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") == "1",
            "q": 1,
            "min_after_fee_profit": "0.03",
            "overlapping_makers": False,
            "order_entry": os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") == "1",
        }, separators=(",", ":")),
        flush=True,
    )

    while True:
        try:
            result = await run_campaign()
        except Exception as exc:
            print(
                "SPORTS_STRUCTURAL_SUPERVISOR "
                + json.dumps({
                    "status": "CAMPAIGN_EXCEPTION_HARD_HALT",
                    "error_type": type(exc).__name__,
                    "order_entry": False,
                }, separators=(",", ":")),
                flush=True,
            )
            return

        statuses = _attempt_statuses(result)
        hard_halt = _hard_halt_reason(statuses)
        if hard_halt is not None:
            print(
                "SPORTS_STRUCTURAL_SUPERVISOR "
                + json.dumps({
                    "status": "UNSAFE_RESULT_HARD_HALT",
                    "attempt_status": hard_halt,
                    "order_entry": False,
                }, separators=(",", ":")),
                flush=True,
            )
            return

        if any(status in RATE_LIMIT_STATUSES for status in statuses):
            print(
                "SPORTS_STRUCTURAL_SUPERVISOR "
                + json.dumps({
                    "status": "RATE_LIMIT_COOLDOWN",
                    "sleep_seconds": RATE_LIMIT_SLEEP_SECONDS,
                    "order_entry": False,
                }, separators=(",", ":")),
                flush=True,
            )
            await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)
            # Do not place another maker until a scoped account read confirms
            # the structural totals universe has no lingering open order.
            clean = await _ensure_clean()
            while not clean:
                await asyncio.sleep(CLEANUP_RETRY_SECONDS)
                clean = await _ensure_clean()
            continue

        status = str(result.get("status") or "")
        if status.startswith("NO_EXACT_"):
            await asyncio.sleep(EMPTY_SLEEP_SECONDS)
        else:
            await asyncio.sleep(NORMAL_SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
