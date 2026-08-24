"""Bounded fail-closed supervisor for strict same-Polymarket totals ladders.

The v5 campaign owns semantic validation, >=3c after-fee economics, post-only q1
maker placement, exact hedge revalidation, FOK hedging, partial-fill flattening and
fill reconciliation. This wrapper makes that campaign safe to run continuously:

* one campaign / one maker at a time;
* current-universe orphan cleanup before any new maker and after rate limits;
* bounded cool-down instead of hot retries on Polymarket rate limits;
* hard halt on partials, flatten paths, ambiguous reconciliation or unknown
  mutation outcomes;
* stop after 20 positively reconciled two-leg fills so promotion cannot silently
  turn into unlimited live exposure.

A BOTH_FILLED_RECONCILED result is an executed locked pair, not authoritative
realized P&L. Final settlement/reconciliation remains the accounting source of truth.
"""
from __future__ import annotations

import asyncio
from collections import Counter
import json
import os
from typing import Any, Mapping

from polymarket_us import PolymarketUS

from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_campaign
from src.sports_poly_structural_monotone import discover_structural_pairs, structural_identity

MAX_RECONCILED_SUCCESSES = 20
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
    return sorted({
        str(slug)
        for slug, market in feed.items()
        if (identity := structural_identity(market)) is not None
        and str(identity[0]).startswith("TOTAL::")
    })


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
        open_orders = [
            raw for raw in orders
            if isinstance(raw, Mapping)
            and str(raw.get("id") or "")
            and str(raw.get("marketSlug") or "") in slugs
            and str(raw.get("state") or "") not in TERMINAL_STATES
        ]
        canceled: list[str] = []
        unresolved: list[str] = []
        for raw in open_orders:
            oid = str(raw.get("id") or "")
            slug = str(raw.get("marketSlug") or "")
            try:
                await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)

        # If any open order existed, a second scoped read must positively verify
        # no structural totals maker remains before another campaign can start.
        if open_orders:
            await asyncio.sleep(1.0)
            verify = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
            for raw in verify.get("orders") or []:
                if not isinstance(raw, Mapping):
                    continue
                oid = str(raw.get("id") or "")
                slug = str(raw.get("marketSlug") or "")
                state = str(raw.get("state") or "")
                if oid and slug in slugs and state not in TERMINAL_STATES:
                    unresolved.append(oid)
        return {
            "status": "STRUCTURAL_TOTAL_ORPHAN_CLEANUP",
            "slugs": len(slugs),
            "open_seen": len(open_orders),
            "canceled": len(canceled),
            "unresolved": len(set(unresolved)),
        }
    finally:
        client.close()


def _attempt_statuses(result: Mapping[str, Any]) -> list[str]:
    return [
        str(attempt.get("status") or "")
        for attempt in result.get("attempts") or []
        if isinstance(attempt, Mapping) and str(attempt.get("status") or "")
    ]


def _hard_halt_reason(statuses: list[str]) -> str | None:
    for status in statuses:
        if status in RATE_LIMIT_STATUSES or status in SAFE_CONTINUE_STATUSES:
            continue
        # Every mutation/reconciliation state not explicitly reviewed above is a
        # stop. This intentionally includes all partial-fill and flatten paths.
        return status
    return None


async def _ensure_clean() -> bool:
    try:
        result = await cleanup_current_total_orders()
    except Exception as exc:
        print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
            "status": "CLEANUP_UNCONFIRMED_COOLDOWN",
            "error_type": type(exc).__name__,
            "rate_limited": _is_rate_limit(exc),
            "sleep_seconds": CLEANUP_RETRY_SECONDS,
            "order_entry": False,
        }, separators=(",", ":")), flush=True)
        return False
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(result, separators=(",", ":")), flush=True)
    return int(result.get("unresolved") or 0) == 0


async def main() -> None:
    clean = await _ensure_clean()
    while not clean:
        await asyncio.sleep(CLEANUP_RETRY_SECONDS)
        clean = await _ensure_clean()

    live_gate = os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") == "1"
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
        "status": "LIVE_LOOP_READY",
        "live_gate": live_gate,
        "q": 1,
        "min_after_fee_profit": "0.03",
        "overlapping_makers": False,
        "success_cap": MAX_RECONCILED_SUCCESSES,
        "order_entry": live_gate,
    }, separators=(",", ":")), flush=True)

    successes = 0
    campaigns = 0
    attempts = 0
    counts: Counter[str] = Counter()
    while successes < MAX_RECONCILED_SUCCESSES:
        campaigns += 1
        try:
            result = await run_campaign()
        except Exception as exc:
            print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                "status": "CAMPAIGN_EXCEPTION_HARD_HALT",
                "error_type": type(exc).__name__,
                "campaigns": campaigns,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
            return

        statuses = _attempt_statuses(result)
        attempts += len(statuses)
        counts.update(statuses)
        new_successes = sum(status == "BOTH_FILLED_RECONCILED" for status in statuses)
        successes += new_successes
        print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
            "status": "CAMPAIGN_SUMMARY",
            "campaigns": campaigns,
            "attempts": attempts,
            "reconciled_two_leg_fills": successes,
            "success_cap": MAX_RECONCILED_SUCCESSES,
            "status_counts": dict(counts),
            "order_entry": live_gate,
        }, separators=(",", ":")), flush=True)

        hard_halt = _hard_halt_reason(statuses)
        if hard_halt is not None:
            print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                "status": "UNSAFE_RESULT_HARD_HALT",
                "attempt_status": hard_halt,
                "campaigns": campaigns,
                "attempts": attempts,
                "reconciled_two_leg_fills": successes,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
            return

        if successes >= MAX_RECONCILED_SUCCESSES:
            print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                "status": "SUCCESS_CAP_REACHED_REVIEW_REQUIRED",
                "reconciled_two_leg_fills": successes,
                "campaigns": campaigns,
                "attempts": attempts,
                "status_counts": dict(counts),
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
            return

        if any(status in RATE_LIMIT_STATUSES for status in statuses):
            print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                "status": "RATE_LIMIT_COOLDOWN",
                "sleep_seconds": RATE_LIMIT_SLEEP_SECONDS,
                "order_entry": False,
            }, separators=(",", ":")), flush=True)
            await asyncio.sleep(RATE_LIMIT_SLEEP_SECONDS)
            # Never place another maker after a rate-limited mutation/read path
            # until a scoped account read positively confirms no orphan remains.
            clean = await _ensure_clean()
            while not clean:
                await asyncio.sleep(CLEANUP_RETRY_SECONDS)
                clean = await _ensure_clean()
            continue

        status = str(result.get("status") or "")
        await asyncio.sleep(EMPTY_SLEEP_SECONDS if status.startswith("NO_EXACT_") else NORMAL_SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
