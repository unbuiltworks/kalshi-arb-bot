"""Bounded fail-closed supervisor for strict same-Polymarket totals ladders.

The v5 campaign owns semantic validation, >=3c after-fee economics, post-only q1
maker placement, exact hedge revalidation, FOK hedging, partial-fill flattening and
fill reconciliation. This wrapper makes that campaign safe to run continuously:

* one campaign / one maker at a time;
* current-universe orphan cleanup before any new maker and after rate limits;
* any recovered open maker with a positive partial fill is flattened before the
  startup state can be considered clean;
* canceled orders with positive historical fills are surfaced for restart audit;
* private order WebSocket is the primary resting-maker state transport, avoiding
  high-frequency authenticated REST polling; exact REST remains the fallback;
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
from decimal import Decimal, InvalidOperation
import json
import os
from typing import Any, Callable, Mapping

from polymarket_us import PolymarketUS

import src.sports_kp_poly_relay_campaign_q1_v4 as v4
from src.sports_kp_poly_relay_campaign_q1_v5 import run_once as run_campaign
from src.sports_poly_private_order_stream import PrivateOrderStream
from src.sports_poly_structural_monotone import discover_structural_pairs, structural_identity

D = Decimal
MAX_RECONCILED_SUCCESSES = 20
NORMAL_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_NORMAL_SLEEP_SECONDS", "8"))
EMPTY_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_EMPTY_SLEEP_SECONDS", "30"))
RATE_LIMIT_SLEEP_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_RATE_LIMIT_SLEEP_SECONDS", "90"))
CLEANUP_RETRY_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_CLEANUP_RETRY_SECONDS", "120"))
WS_REFRESH_WAIT_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WS_REFRESH_WAIT_SECONDS", "0.10"))
WS_CANCEL_CONFIRM_SECONDS = float(os.getenv("SPORTS_STRUCTURAL_WS_CANCEL_CONFIRM_SECONDS", "1.50"))

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


def _d(value: Any) -> D:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        return D(str(value or "0"))
    except (InvalidOperation, ValueError, TypeError):
        return D("0")


def _order_summary(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or ""),
        "market_slug": str(raw.get("marketSlug") or raw.get("market_slug") or ""),
        "state": str(raw.get("state") or ""),
        "cum_quantity": str(_d(raw.get("cumQuantity") or raw.get("cum_quantity"))),
        "quantity": str(_d(raw.get("quantity") or raw.get("qty"))),
        "created_at": raw.get("createdAt") or raw.get("created_at"),
        "updated_at": raw.get("updatedAt") or raw.get("updated_at"),
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
    """Cancel/verify current structural orders and flatten recovered partials."""
    slugs = await _current_total_slugs()
    if not slugs:
        return {
            "status": "CLEANUP_NO_TOTAL_SLUGS",
            "slugs": 0,
            "open_seen": 0,
            "canceled": 0,
            "unresolved": 0,
            "partial_flattened": 0,
            "partial_unresolved": 0,
            "canceled_partial_audit_count": 0,
        }

    client = PolymarketUS(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        response = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
        orders = response.get("orders") or [] if isinstance(response, Mapping) else []
        rows = [raw for raw in orders if isinstance(raw, Mapping)]
        canceled_partial_audit = [
            _order_summary(raw)
            for raw in rows
            if str(raw.get("marketSlug") or raw.get("market_slug") or "") in slugs
            and str(raw.get("state") or "") == "ORDER_STATE_CANCELED"
            and _d(raw.get("cumQuantity") or raw.get("cum_quantity")) > 0
        ]
        open_orders = [
            raw for raw in rows
            if str(raw.get("id") or "")
            and str(raw.get("marketSlug") or raw.get("market_slug") or "") in slugs
            and str(raw.get("state") or "") not in TERMINAL_STATES
        ]
        canceled: list[str] = []
        unresolved: list[str] = []
        partial_flattened: list[str] = []
        partial_unresolved: list[str] = []
        for raw in open_orders:
            oid = str(raw.get("id") or "")
            slug = str(raw.get("marketSlug") or raw.get("market_slug") or "")
            cum_before = _d(raw.get("cumQuantity") or raw.get("cum_quantity"))
            try:
                await asyncio.to_thread(client.orders.cancel, oid, {"marketSlug": slug})
                canceled.append(oid)
            except Exception:
                unresolved.append(oid)
                continue

            if cum_before > 0:
                try:
                    await asyncio.to_thread(
                        client.orders.close_position,
                        {
                            "marketSlug": slug,
                            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
                            "synchronousExecution": True,
                            "maxBlockTime": "2",
                        },
                    )
                    partial_flattened.append(oid)
                except Exception:
                    partial_unresolved.append(oid)

        # If any open order existed, a second scoped read must positively verify
        # no structural totals maker remains before another campaign can start.
        if open_orders:
            await asyncio.sleep(1.0)
            verify = await asyncio.to_thread(client.orders.list, {"slugs": slugs})
            for raw in verify.get("orders") or []:
                if not isinstance(raw, Mapping):
                    continue
                oid = str(raw.get("id") or "")
                slug = str(raw.get("marketSlug") or raw.get("market_slug") or "")
                state = str(raw.get("state") or "")
                if oid and slug in slugs and state not in TERMINAL_STATES:
                    unresolved.append(oid)
        return {
            "status": "STRUCTURAL_TOTAL_ORPHAN_CLEANUP",
            "slugs": len(slugs),
            "open_seen": len(open_orders),
            "canceled": len(canceled),
            "unresolved": len(set(unresolved)),
            "partial_flattened": len(partial_flattened),
            "partial_unresolved": len(set(partial_unresolved)),
            "canceled_partial_audit_count": len(canceled_partial_audit),
            "canceled_partial_audit": canceled_partial_audit[-8:],
        }
    finally:
        client.close()


def install_private_order_transport(stream: PrivateOrderStream) -> Callable[[], None]:
    """Patch only v4's resting-order refresh/cancel hooks for this process.

    Final fill reconciliation still uses the unmodified exact REST reads in v4.
    If a cancellation is not observed as terminal on the private stream, the next
    refresh is forced through the original REST implementation before v4 decides
    that no quantity filled.
    """
    original_refresh = v4._refresh_order
    original_cancel = v4._cancel_scoped
    force_rest_refresh: set[str] = set()

    async def ws_refresh(
        client: PolymarketUS,
        oid: str,
        slug: str,
        fallback: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        stream.register_fallback(oid, fallback)
        if oid in force_rest_refresh:
            force_rest_refresh.discard(oid)
            return await original_refresh(client, oid, slug, fallback)
        if not stream.healthy:
            return await original_refresh(client, oid, slug, fallback)
        cached = stream.get(oid)
        if cached is not None and cached != dict(fallback):
            return cached
        updated = await stream.wait_for_update(oid, WS_REFRESH_WAIT_SECONDS)
        return updated if updated is not None else fallback

    async def ws_cancel(client: PolymarketUS, oid: str, slug: str) -> None:
        await original_cancel(client, oid, slug)
        if not stream.healthy:
            force_rest_refresh.add(oid)
            return
        terminal = await stream.wait_terminal(oid, WS_CANCEL_CONFIRM_SECONDS)
        if terminal is None:
            # Safety over throughput: one exact REST reconciliation is cheaper
            # than assuming a canceled maker had zero fill.
            force_rest_refresh.add(oid)

    v4._refresh_order = ws_refresh
    v4._cancel_scoped = ws_cancel

    def restore() -> None:
        v4._refresh_order = original_refresh
        v4._cancel_scoped = original_cancel

    return restore


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
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps(result, separators=(",", ":"), default=str), flush=True)
    return int(result.get("unresolved") or 0) == 0 and int(result.get("partial_unresolved") or 0) == 0


async def main() -> None:
    clean = await _ensure_clean()
    while not clean:
        await asyncio.sleep(CLEANUP_RETRY_SECONDS)
        clean = await _ensure_clean()

    stream = PrivateOrderStream(
        key_id=os.environ["POLYMARKET_KEY_ID"],
        secret_key=os.environ["POLYMARKET_SECRET_KEY"],
        timeout=8.0,
    )
    try:
        await stream.start()
    except Exception as exc:
        await stream.close()
        print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
            "status": "PRIVATE_ORDER_WS_START_FAILED_HARD_HALT",
            "error_type": type(exc).__name__,
            "order_entry": False,
        }, separators=(",", ":")), flush=True)
        return

    restore_transport = install_private_order_transport(stream)
    live_gate = os.getenv("SPORTS_POLY_RELAY_LIVE_Q1", "0") == "1"
    print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
        "status": "LIVE_LOOP_READY",
        "live_gate": live_gate,
        "q": 1,
        "min_after_fee_profit": "0.03",
        "overlapping_makers": False,
        "success_cap": MAX_RECONCILED_SUCCESSES,
        "order_state_transport": "PRIVATE_WS_PRIMARY_REST_RECONCILIATION_FALLBACK",
        "order_entry": live_gate,
    }, separators=(",", ":")), flush=True)

    successes = 0
    campaigns = 0
    attempts = 0
    counts: Counter[str] = Counter()
    try:
        while successes < MAX_RECONCILED_SUCCESSES:
            campaigns += 1
            try:
                result = await run_campaign()
            except Exception as exc:
                print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                    "status": "CAMPAIGN_EXCEPTION_HARD_HALT",
                    "error_type": type(exc).__name__,
                    "campaigns": campaigns,
                    "ws_healthy": stream.healthy,
                    "ws_updates": stream.updates,
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
                "ws_healthy": stream.healthy,
                "ws_updates": stream.updates,
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

            if not stream.healthy:
                print("SPORTS_STRUCTURAL_SUPERVISOR " + json.dumps({
                    "status": "PRIVATE_ORDER_WS_UNHEALTHY_HARD_HALT",
                    "error_type": stream.error_type,
                    "campaigns": campaigns,
                    "attempts": attempts,
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
                clean = await _ensure_clean()
                while not clean:
                    await asyncio.sleep(CLEANUP_RETRY_SECONDS)
                    clean = await _ensure_clean()
                continue

            status = str(result.get("status") or "")
            await asyncio.sleep(EMPTY_SLEEP_SECONDS if status.startswith("NO_EXACT_") else NORMAL_SLEEP_SECONDS)
    finally:
        restore_transport()
        await stream.close()


if __name__ == "__main__":
    asyncio.run(main())
