"""Read-only delayed revalidation for cross-exchange arbitrage signals.

This service answers one question: after a fresh Polymarket US / Kalshi spread is
observed, does the displayed price and depth survive long enough that the exact
same two limit orders would still be marketable a short time later?

It NEVER submits, previews, cancels, or modifies an order.  It only reads the
same websocket-maintained books used by the generic shadow scanner and records
conservative fillability checkpoints at fixed delays.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import os
import signal
import time
from typing import Any

import httpx
from polymarket_us import PolymarketUS

from src import generic_cross_market_shadow as generic
from src import polymarket_kalshi_shadow as calc
from src.eventing_books import BookUpdate, EventingKalshiCrossBooks, EventingPolyBookStore
from src.polymarket_kalshi_shadow_v2 import run_poly_ws

logger = logging.getLogger("kalshi_arb.execution_shadow_revalidation")


def _side(quote: Any, side: str) -> tuple[Decimal, Decimal]:
    if side == "OVER":
        return quote.yes_ask, quote.yes_depth
    if side == "UNDER":
        return quote.no_ask, quote.no_depth
    raise ValueError(f"Unsupported side {side!r}")


async def _probe(
    *,
    pair: generic.PairSpec,
    opened_ms: int,
    plan: calc.ShadowPlan,
    poly: EventingPolyBookStore,
    kalshi: EventingKalshiCrossBooks,
    poly_fee_rate: Decimal,
    kalshi_fee_rate: Decimal,
    delays_ms: tuple[int, ...],
    recheck_max_age_ms: int,
    strict_fresh_ms: int,
    counters: dict[str, int],
) -> None:
    """Re-check the exact original quantity and price limits at each delay."""
    previous = 0
    probe_id = f"{pair.pair_id}:{opened_ms}"
    for delay_ms in delays_ms:
        await asyncio.sleep(max(0, delay_ms - previous) / 1000)
        previous = delay_ms
        now_ms = int(time.time() * 1000)

        pq = poly.quote(pair.poly_slug, max_age_ms=recheck_max_age_ms)
        kq = await kalshi.quote(pair.kalshi_ticker, max_age_ms=recheck_max_age_ms)
        payload: dict[str, Any] = {
            "kind": "CHECK",
            "probe_id": probe_id,
            "pair_id": pair.pair_id,
            "category": pair.category,
            "settlement_status": pair.settlement_status,
            "delay_ms": delay_ms,
            "quantity": plan.quantity,
            "direction": plan.direction,
            "original_poly_price": str(plan.poly_price),
            "original_kalshi_price": str(plan.kalshi_price),
            "original_profit": str(plan.profit),
            "original_roi": str(plan.roi),
            "both_fillable": False,
            "strict_fresh": False,
        }

        if pq is None or kq is None or not poly.connected:
            payload["reason"] = "BOOK_UNAVAILABLE_OR_STALE"
            counters["checks"] += 1
            logger.info("EXECUTION_SHADOW %s", json.dumps(payload, separators=(",", ":")))
            continue

        poly_px, poly_depth = _side(pq, plan.poly_side)
        kalshi_px, kalshi_depth = _side(kq, plan.kalshi_side)
        poly_age_ms = max(0, now_ms - pq.received_ms)
        kalshi_age_ms = max(0, now_ms - kq.received_ms)
        skew_ms = abs(pq.received_ms - kq.received_ms)

        poly_price_ok = poly_px <= plan.poly_price
        kalshi_price_ok = kalshi_px <= plan.kalshi_price
        poly_depth_ok = poly_depth >= Decimal(plan.quantity)
        kalshi_depth_ok = kalshi_depth >= Decimal(plan.quantity)
        both_fillable = poly_price_ok and kalshi_price_ok and poly_depth_ok and kalshi_depth_ok
        strict_fresh = max(poly_age_ms, kalshi_age_ms) <= strict_fresh_ms and skew_ms <= strict_fresh_ms

        current_cost = None
        current_profit = None
        if both_fillable:
            pm_fee = calc.polymarket_taker_fee(plan.quantity, poly_px, poly_fee_rate)
            k_fee = calc.kalshi_taker_fee(plan.quantity, kalshi_px, kalshi_fee_rate)
            current_cost = Decimal(plan.quantity) * (poly_px + kalshi_px) + pm_fee + k_fee
            current_profit = Decimal(plan.quantity) - current_cost

        payload.update({
            "poly_price": str(poly_px),
            "poly_depth": str(poly_depth),
            "kalshi_price": str(kalshi_px),
            "kalshi_depth": str(kalshi_depth),
            "poly_price_ok": poly_price_ok,
            "kalshi_price_ok": kalshi_price_ok,
            "poly_depth_ok": poly_depth_ok,
            "kalshi_depth_ok": kalshi_depth_ok,
            "poly_age_ms": poly_age_ms,
            "kalshi_age_ms": kalshi_age_ms,
            "cross_receive_skew_ms": skew_ms,
            "both_fillable": both_fillable,
            "strict_fresh": strict_fresh,
            "current_cost": str(current_cost) if current_cost is not None else None,
            "current_profit": str(current_profit) if current_profit is not None else None,
        })
        counters["checks"] += 1
        if both_fillable:
            counters["survived"] += 1
        if both_fillable and strict_fresh:
            counters["survived_strict"] += 1
        logger.info("EXECUTION_SHADOW %s", json.dumps(payload, separators=(",", ":")))


async def run(args: argparse.Namespace) -> None:
    pairs = generic.load_pairs(args.config)
    key_id = os.getenv("POLYMARKET_KEY_ID", "").strip()
    secret = os.getenv("POLYMARKET_SECRET_KEY", "").strip()
    if not key_id or not secret:
        raise RuntimeError("Polymarket US websocket credentials are required")

    bankroll = Decimal(args.bankroll)
    poly_fee = Decimal(os.getenv("POLYMARKET_TAKER_FEE_RATE", args.poly_fee_rate))
    kalshi_fee = Decimal(os.getenv("KALSHI_TAKER_FEE_RATE", args.kalshi_fee_rate))
    delays_ms = tuple(sorted({int(x) for x in args.delays_ms.split(",") if x.strip()}))
    if not delays_ms or delays_ms[0] < 0:
        raise ValueError("--delays-ms must contain non-negative millisecond delays")

    logger.info("EXECUTION_SHADOW_START %s", json.dumps({
        "read_only": True,
        "order_entry": False,
        "pair_count": len(pairs),
        "bankroll": str(bankroll),
        "initial_max_age_ms": args.initial_max_age_ms,
        "recheck_max_age_ms": args.recheck_max_age_ms,
        "strict_fresh_ms": args.strict_fresh_ms,
        "delays_ms": delays_ms,
    }, separators=(",", ":")))

    async with httpx.AsyncClient(timeout=15.0) as http:
        poly = EventingPolyBookStore()
        seeded = await generic._seed_poly(http, poly, [p.poly_slug for p in pairs])
        live_pairs = [p for p in pairs if poly.quote(p.poly_slug, max_age_ms=60_000) is not None]
        if not live_pairs:
            raise RuntimeError("No configured pairs currently have a two-sided Polymarket book")

        by_poly = {p.poly_slug: p for p in live_pairs}
        by_kalshi = {p.kalshi_ticker: p for p in live_pairs}
        kalshi = EventingKalshiCrossBooks(list(by_kalshi))
        client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=10.0)
        poly_task = asyncio.create_task(run_poly_ws(client, poly, list(by_poly)), name="execshadow_poly_ws")
        kalshi_task = asyncio.create_task(kalshi.run(), name="execshadow_kalshi_ws")

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if poly_task.done():
                raise generic._task_error("Polymarket", poly_task)
            if kalshi_task.done():
                raise generic._task_error("Kalshi", kalshi_task)
            if poly.connected and kalshi.initialized_count == len(live_pairs):
                break
            await asyncio.sleep(0.025)
        else:
            raise RuntimeError(
                f"Startup timeout poly_connected={poly.connected} "
                f"kalshi={kalshi.initialized_count}/{len(live_pairs)} seeded={seeded}/{len(pairs)}"
            )

        generic._drain(poly.updates)
        generic._drain(kalshi.updates)
        logger.info("EXECUTION_SHADOW_READY %s", json.dumps({
            "configured": len(pairs), "live_pairs": len(live_pairs), "rest_is_signal_source": False
        }, separators=(",", ":")))

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass

        active: set[str] = set()
        probes: set[asyncio.Task[Any]] = set()
        counters = {"fresh_opens": 0, "checks": 0, "survived": 0, "survived_strict": 0}

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(10)
                logger.info("EXECUTION_SHADOW_SUMMARY %s", json.dumps({
                    **counters,
                    "active_pairs": len(active),
                    "pending_probes": len(probes),
                    "poly_dropped_updates": poly.dropped_updates,
                    "kalshi_dropped_updates": kalshi.dropped_updates,
                }, separators=(",", ":")))

        hb = asyncio.create_task(heartbeat())
        try:
            while not stop.is_set():
                update_task = asyncio.create_task(generic._next_update(poly, kalshi))
                stop_task = asyncio.create_task(stop.wait())
                done, _ = await asyncio.wait(
                    {update_task, stop_task, poly_task, kalshi_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if poly_task in done:
                    update_task.cancel(); stop_task.cancel()
                    await asyncio.gather(update_task, stop_task, return_exceptions=True)
                    raise generic._task_error("Polymarket", poly_task)
                if kalshi_task in done:
                    update_task.cancel(); stop_task.cancel()
                    await asyncio.gather(update_task, stop_task, return_exceptions=True)
                    raise generic._task_error("Kalshi", kalshi_task)
                if stop_task in done and stop_task.result():
                    update_task.cancel()
                    await asyncio.gather(update_task, return_exceptions=True)
                    break
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
                trigger: BookUpdate = update_task.result()
                pair = by_poly.get(trigger.market_id) if trigger.venue == "polymarket" else by_kalshi.get(trigger.market_id)
                if pair is None:
                    continue

                now_ms = int(time.time() * 1000)
                pq = poly.quote(pair.poly_slug, max_age_ms=args.initial_max_age_ms)
                kq = await kalshi.quote(pair.kalshi_ticker, max_age_ms=args.initial_max_age_ms)
                plan = None
                if pq is not None and kq is not None and poly.connected:
                    skew_ms = abs(pq.received_ms - kq.received_ms)
                    if skew_ms <= args.initial_max_age_ms:
                        plan = calc.best_plan(
                            pq, kq, bankroll=bankroll,
                            poly_fee_rate=poly_fee, kalshi_fee_rate=kalshi_fee,
                        )

                if plan is None or plan.profit <= 0:
                    active.discard(pair.pair_id)
                    continue
                if pair.pair_id in active:
                    continue

                active.add(pair.pair_id)
                counters["fresh_opens"] += 1
                logger.info("EXECUTION_SHADOW %s", json.dumps({
                    "kind": "OPEN",
                    "probe_id": f"{pair.pair_id}:{now_ms}",
                    "pair_id": pair.pair_id,
                    "category": pair.category,
                    "settlement_status": pair.settlement_status,
                    "poly_slug": pair.poly_slug,
                    "kalshi_ticker": pair.kalshi_ticker,
                    **plan.as_dict(),
                    "trigger_venue": trigger.venue,
                    "poly_age_ms": max(0, now_ms - pq.received_ms),
                    "kalshi_age_ms": max(0, now_ms - kq.received_ms),
                    "cross_receive_skew_ms": abs(pq.received_ms - kq.received_ms),
                }, separators=(",", ":")))

                task = asyncio.create_task(_probe(
                    pair=pair,
                    opened_ms=now_ms,
                    plan=plan,
                    poly=poly,
                    kalshi=kalshi,
                    poly_fee_rate=poly_fee,
                    kalshi_fee_rate=kalshi_fee,
                    delays_ms=delays_ms,
                    recheck_max_age_ms=args.recheck_max_age_ms,
                    strict_fresh_ms=args.strict_fresh_ms,
                    counters=counters,
                ))
                probes.add(task)
                task.add_done_callback(probes.discard)
        finally:
            hb.cancel(); poly_task.cancel(); kalshi_task.cancel()
            for task in probes:
                task.cancel()
            await kalshi.close()
            try:
                client.close()
            except Exception:
                pass
            await asyncio.gather(hb, poly_task, kalshi_task, *probes, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/cross_market_shadow_pairs.json")
    p.add_argument("--bankroll", default="50")
    p.add_argument("--poly-fee-rate", default="0.05")
    p.add_argument("--kalshi-fee-rate", default="0.07")
    p.add_argument("--initial-max-age-ms", type=int, default=250)
    p.add_argument("--recheck-max-age-ms", type=int, default=1500)
    p.add_argument("--strict-fresh-ms", type=int, default=250)
    p.add_argument("--delays-ms", default="50,100,250,500")
    return p.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
