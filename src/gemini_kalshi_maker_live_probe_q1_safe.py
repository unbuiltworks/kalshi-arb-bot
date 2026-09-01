"""Hardened entrypoint for the one-shot maker LIVE_PROBE.

This wrapper intentionally owns the mutating state machine. It reuses the pure
payload/arm/transport helpers from gemini_kalshi_maker_live_probe_q1 but fixes the
live-specific race semantics before the service can ever be armed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any

import httpx

from src import gemini_kalshi_maker_live_probe_q1 as base
from src.gemini_kalshi_maker_shadow import kalshi_maker_fee

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_kalshi_maker_live_probe_q1_safe")
MIN_EXPIRY_REMAINING_S = 30.0

# Re-export pure helpers so tests and operators have one canonical live entrypoint.
kalshi_maker_payload = base.kalshi_maker_payload
gemini_hedge_payload = base.gemini_hedge_payload
TARGET_PROFIT = base.TARGET_PROFIT
QTY = base.QTY
LIVE_ENV = base.LIVE_ENV
ARM_ENV = base.ARM_ENV
DEADLINE_ENV = base.DEADLINE_ENV
VOLUME_ENV = base.VOLUME_ENV
RUN_ID_ENV = base.RUN_ID_ENV
SETTLEMENT_STATUS = base.SETTLEMENT_STATUS
_live_software_authorized = base._live_software_authorized
_maybe_create_arm = base._maybe_create_arm
_claim_before_order = base._claim_before_order
_paths = base._paths


def candidate_from_books(*, gemini, kalshi, fee_meta, now_ms: int):
    """Only quote at/above displayed best bid while preserving the 3c target."""
    candidate = base.candidate_from_books(
        gemini=gemini, kalshi=kalshi, fee_meta=fee_meta, now_ms=now_ms
    )
    if candidate is None:
        return None
    # MAX_SAFE below the existing best bid is economically safe but not a useful
    # first live probe: every better bid is ahead of us and the fill model becomes
    # dependent on unseen deeper queue. Refuse that case.
    if candidate.maker_price < candidate.best_bid:
        return None
    return candidate


def _fee_mapping_gate(fee_meta) -> None:
    if fee_meta.scheduled_changes:
        raise RuntimeError("maker live probe refuses a series with scheduled fee changes")
    # Shared maker fee code fails closed on unknown fee types/multipliers.
    kalshi_maker_fee(
        D("0.50"), QTY,
        fee_type=fee_meta.fee_type,
        fee_multiplier=D(fee_meta.fee_multiplier),
    )


def _fresh_for_live_hedge(book, now_ms: int) -> bool:
    return bool(book is not None and 0 <= now_ms - int(book.ts_ms) <= base.MAX_RESTING_GEMINI_AGE_MS)


async def _cancel_and_reconcile(kalshi, order_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], Decimal]:
    try:
        cancel = await base._cancel_order(kalshi, order_id)
    except Exception as exc:
        cancel = {"error_type": type(exc).__name__, "error": str(exc)[:240]}
    # The fill endpoint is authoritative for the cancel/fill race.
    fills = await base._fills_for_order(kalshi, order_id)
    qty = min(QTY, base._filled_qty(fills))
    return cancel, fills, qty


async def _handle_confirmed_fill(
    *, gemini, kalshi, candidate, g, pair, k_payload, order_id: str,
    run_id: str, observed_qty: Decimal, is_maker: bool | None,
) -> dict[str, Any]:
    """Neutralize one confirmed Kalshi fill; partial fills fail closed."""
    observed_qty = min(QTY, D(observed_qty))
    if observed_qty <= 0:
        raise ValueError("confirmed fill quantity must be positive")

    # Fractional/partial maker fills are intentionally not the first hedge test.
    # Cancel the remainder, reconcile exact total fill, then flatten that Kalshi
    # position and hard-stop. This keeps the first experiment simple and bounded.
    if observed_qty < QTY:
        cancel, fills, total = await _cancel_and_reconcile(kalshi, order_id)
        if total <= 0:
            total = observed_qty
        if total < QTY:
            flatten = await base._flatten_kalshi_qty(kalshi, k_payload, total)
            return {
                "outcome": "PARTIAL_MAKER_FILL_FLATTENED",
                "observed_fill_qty": str(observed_qty),
                "reconciled_fill_qty": str(total),
                "is_maker": is_maker,
                "cancel": cancel,
                "fills": fills,
                "flatten": flatten,
                "hard_halt": True,
            }
        observed_qty = QTY

    now_ms = int(time.time() * 1000)
    if not _fresh_for_live_hedge(g, now_ms):
        flatten = await base._flatten_kalshi_qty(kalshi, k_payload, observed_qty)
        return {
            "outcome": "FILL_WITH_STALE_GEMINI_BOOK_FLATTENED",
            "observed_fill_qty": str(observed_qty),
            "is_maker": is_maker,
            "flatten": flatten,
            "hard_halt": True,
        }

    hedge = await base._hedge_after_fill(
        gemini=gemini,
        kalshi=kalshi,
        candidate=candidate,
        gemini_book=g,
        symbol=pair.gemini_symbol,
        kalshi_payload=k_payload,
        fill_qty=observed_qty,
        run_id=run_id,
    )
    # A post-only order reporting taker=true is an execution invariant breach even
    # if the hedge succeeds. Exposure is neutralized, then the run hard-stops.
    if is_maker is False:
        hedge = dict(hedge)
        hedge["post_only_invariant_breach"] = True
        hedge["hard_halt"] = True
        hedge["outcome"] = "POST_ONLY_ORDER_REPORTED_TAKER_AFTER_HEDGE"
    return hedge


async def run_once() -> dict[str, Any]:
    raw_run_id = os.getenv(RUN_ID_ENV, "").strip()
    run_id = base._clean_run_id(raw_run_id) if raw_run_id else "disarmed"
    armed = _live_software_authorized()
    logger.warning("MAKER_LIVE_PROBE_SAFE_BOOT %s", json.dumps({
        "mode": "LIVE_PROBE",
        "version": "q1-safe-v1",
        "asset": base.ASSET,
        "quantity_max": "1",
        "target_profit": str(TARGET_PROFIT),
        "policy": base.POLICY,
        "settlement_status": SETTLEMENT_STATUS,
        "live_software_authorized": armed,
        "order_entry": False,
    }, separators=(",", ":")))
    if not armed:
        return {
            "outcome": "DISARMED",
            "mode": "LIVE_PROBE",
            "order_entry": False,
            "settlement_status": SETTLEMENT_STATUS,
        }

    arm_path = _maybe_create_arm(run_id)
    if arm_path is None:
        raise RuntimeError("maker live probe could not create persistent arm")

    gemini = base.GeminiTradingSession()
    kalshi = base.KalshiV2Executor()
    market_tasks: list[asyncio.Task] = []
    account_task: asyncio.Task | None = None
    result_path = None
    try:
        await gemini.connect()
        preflight = await base._preflight(kalshi, live_requested=True, token_path=arm_path)
        if not bool(preflight.get("armed")):
            raise RuntimeError("maker live probe preflight is not armed")
        logger.warning("MAKER_LIVE_PROBE_PREFLIGHT %s", json.dumps({
            **preflight,
            "asset": base.ASSET,
            "mode": "LIVE_PROBE",
            "settlement_status": SETTLEMENT_STATUS,
            "order_entry": False,
        }, separators=(",", ":")))

        async with httpx.AsyncClient(timeout=20.0) as http:
            fee_meta = await base.fetch_series_fee_metadata(http, base.ASSET_SERIES[base.ASSET])
            _fee_mapping_gate(fee_meta)
            discovery = base.PairDiscoveryCache(ttl_s=1.0)
            pair = None
            pair_deadline = time.monotonic() + 30.0
            while pair is None and time.monotonic() < pair_deadline:
                pair = await discovery.current_pair(http, base.ASSET)
                if pair is None:
                    await asyncio.sleep(0.25)
            if pair is None:
                raise RuntimeError("no current XRP pair discovered")

        # Replace only candidate selection; all market-data parsing stays shared with
        # the already-running maker shadow.
        original_candidate = base.candidate_from_books
        base.candidate_from_books = candidate_from_books
        try:
            candidate, g, k, market_queue, market_tasks = await base._wait_candidate(
                pair=pair, fee_meta=fee_meta
            )
        finally:
            base.candidate_from_books = original_candidate
        if candidate is None:
            return {
                "outcome": "NO_QUALIFYING_MAKER_CANDIDATE",
                "mode": "LIVE_PROBE",
                "order_entry": False,
                "settlement_status": SETTLEMENT_STATUS,
            }
        expiry_left = pair.window_end.timestamp() - time.time()
        if expiry_left < MIN_EXPIRY_REMAINING_S:
            return {
                "outcome": "CANDIDATE_TOO_CLOSE_TO_EXPIRY",
                "seconds_to_expiry": expiry_left,
                "mode": "LIVE_PROBE",
                "order_entry": False,
                "settlement_status": SETTLEMENT_STATUS,
            }

        account_queue: asyncio.Queue = asyncio.Queue()
        account_ready = asyncio.Event()
        account_task = asyncio.create_task(
            base._account_stream(pair.kalshi_ticker, account_queue, account_ready),
            name="maker-live-account-ws",
        )
        await asyncio.wait_for(account_ready.wait(), timeout=5.0)

        kalshi_client_id = f"makerprobe-{run_id}-k"
        k_payload = kalshi_maker_payload(
            ticker=pair.kalshi_ticker,
            direction=candidate.direction,
            maker_price=candidate.maker_price,
            client_order_id=kalshi_client_id,
        )

        # Recheck current hedge economics immediately before claiming/mutating.
        now_ms = int(time.time() * 1000)
        if g is None or k is None:
            raise RuntimeError("maker books disappeared before post")
        if max(now_ms - int(g.ts_ms), now_ms - int(k.ts_ms)) > base.MAX_OPEN_BOOK_AGE_MS:
            return {"outcome": "PRE_POST_BOOK_STALE", "order_entry": False, "mode": "LIVE_PROBE", "settlement_status": SETTLEMENT_STATUS}
        current = base.quote_economics(
            direction=candidate.direction,
            maker_price=candidate.maker_price,
            gemini=g,
            fee_meta=fee_meta,
        )
        if current is None or current.locked_profit < TARGET_PROFIT:
            return {"outcome": "PRE_POST_TARGET_GONE", "order_entry": False, "mode": "LIVE_PROBE", "settlement_status": SETTLEMENT_STATUS}

        result_path = _claim_before_order(run_id)
        response = await kalshi.client.post("/portfolio/events/orders", json=k_payload)
        if response.status_code != 201:
            try:
                body: Any = response.json()
            except Exception:
                body = {"text": response.text[:300]}
            result = {
                "outcome": "KALSHI_POST_REJECTED",
                "http_status": response.status_code,
                "response": body,
                "mode": "LIVE_PROBE",
                "counts_as_realized_arb": False,
                "settlement_status": SETTLEMENT_STATUS,
            }
            base._write_json_atomic(result_path, result)
            return result

        created = response.json()
        order_id = str(created.get("order_id") or "")
        if not order_id:
            raise RuntimeError("Kalshi create-order response missing order_id")
        immediate_fill = min(QTY, D(str(created.get("fill_count") or "0")))
        logger.warning("MAKER_LIVE_PROBE_KALSHI_POSTED %s", json.dumps({
            "run_id": run_id,
            "order_id": order_id,
            "ticker": pair.kalshi_ticker,
            "direction": candidate.direction,
            "maker_price": str(candidate.maker_price),
            "modeled_locked_profit": str(current.locked_profit),
            "queue_ahead": str(candidate.queue_ahead),
            "post_only": True,
            "time_in_force": "good_till_canceled",
            "fill_count_on_ack": str(immediate_fill),
            "settlement_status": SETTLEMENT_STATUS,
        }, separators=(",", ":")))

        if immediate_fill > 0:
            hedge = await _handle_confirmed_fill(
                gemini=gemini, kalshi=kalshi, candidate=candidate, g=g, pair=pair,
                k_payload=k_payload, order_id=order_id, run_id=run_id,
                observed_qty=immediate_fill, is_maker=None,
            )
            result = {
                "outcome": hedge["outcome"], "kalshi_order": created, "hedge": hedge,
                "mode": "LIVE_PROBE", "counts_as_realized_arb": False,
                "settlement_status": SETTLEMENT_STATUS,
            }
            base._write_json_atomic(result_path, result)
            return result

        started = time.monotonic()
        cancel_reason = None
        while time.monotonic() - started < base.REST_SECONDS:
            if account_task.done():
                cancel_reason = "PRIVATE_ACCOUNT_WS_LOST"
                break
            if any(task.done() for task in market_tasks):
                cancel_reason = "PUBLIC_MARKET_WS_LOST"
                break

            market_get = asyncio.create_task(market_queue.get(), name="market-get")
            account_get = asyncio.create_task(account_queue.get(), name="account-get")
            done, pending = await asyncio.wait(
                {market_get, account_get}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
            )
            # If both became ready in the same scheduler turn, process the account
            # event first so a fill can never be discarded behind a market update.
            account_items = []
            market_items = []
            for task in done:
                if task is account_get:
                    account_items.append(task.result())
                else:
                    market_items.append(task.result())
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            confirmed_qty = D("0")
            confirmed_is_maker: bool | None = None
            external_cancel = False
            for evt in account_items:
                typ = str(evt.get("type") or "")
                msg = evt.get("msg") or {}
                if typ == "fill" and str(msg.get("order_id") or "") == order_id:
                    confirmed_qty = min(QTY, confirmed_qty + D(str(msg.get("count_fp") or "0")))
                    confirmed_is_maker = not bool(msg.get("is_taker"))
                elif typ == "user_order" and str(msg.get("client_order_id") or "") == kalshi_client_id:
                    confirmed_qty = max(confirmed_qty, min(QTY, D(str(msg.get("fill_count_fp") or "0"))))
                    status = str(msg.get("status") or "").lower()
                    if status in {"canceled", "cancelled", "executed", "filled"} and confirmed_qty <= 0:
                        external_cancel = True

            if confirmed_qty > 0:
                hedge = await _handle_confirmed_fill(
                    gemini=gemini, kalshi=kalshi, candidate=candidate, g=g, pair=pair,
                    k_payload=k_payload, order_id=order_id, run_id=run_id,
                    observed_qty=confirmed_qty, is_maker=confirmed_is_maker,
                )
                # Any unfilled remainder must not remain live after the first fill.
                try:
                    await base._cancel_order(kalshi, order_id)
                except Exception:
                    pass
                result = {
                    "outcome": hedge["outcome"], "kalshi_order": created,
                    "kalshi_fill_qty": str(confirmed_qty), "is_maker": confirmed_is_maker,
                    "hedge": hedge, "mode": "LIVE_PROBE",
                    "counts_as_realized_arb": False, "settlement_status": SETTLEMENT_STATUS,
                }
                base._write_json_atomic(result_path, result)
                return result
            if external_cancel:
                cancel_reason = "ORDER_CANCELED_EXTERNALLY"
                break

            for item in market_items:
                kind, receive_ms, payload = item
                if kind == "gemini":
                    g = payload
                elif kind == "kalshi":
                    k = payload

            now_ms = int(time.time() * 1000)
            if g is None or k is None:
                cancel_reason = "MISSING_BOOK"
                break
            if now_ms - int(g.ts_ms) > base.MAX_RESTING_GEMINI_AGE_MS:
                cancel_reason = "GEMINI_FEED_STALE"
                break
            if now_ms - int(k.ts_ms) > base.MAX_RESTING_KALSHI_AGE_MS:
                cancel_reason = "KALSHI_FEED_STALE"
                break
            econ = base.quote_economics(
                direction=candidate.direction,
                maker_price=candidate.maker_price,
                gemini=g,
                fee_meta=fee_meta,
            )
            if econ is None or econ.locked_profit < TARGET_PROFIT:
                cancel_reason = "TARGET_NO_LONGER_HOLDS"
                break

        cancel_reason = cancel_reason or "REST_TIMEOUT"
        cancel, fills, reconciled_qty = await _cancel_and_reconcile(kalshi, order_id)
        if reconciled_qty > 0:
            hedge = await _handle_confirmed_fill(
                gemini=gemini, kalshi=kalshi, candidate=candidate, g=g, pair=pair,
                k_payload=k_payload, order_id=order_id, run_id=run_id,
                observed_qty=reconciled_qty, is_maker=None,
            )
            result = {
                "outcome": hedge["outcome"], "cancel_reason": cancel_reason,
                "cancel": cancel, "fills": fills, "kalshi_fill_qty": str(reconciled_qty),
                "hedge": hedge, "mode": "LIVE_PROBE", "counts_as_realized_arb": False,
                "settlement_status": SETTLEMENT_STATUS,
            }
            base._write_json_atomic(result_path, result)
            return result

        result = {
            "outcome": "MAKER_ORDER_CANCELED_NO_FILL",
            "cancel_reason": cancel_reason,
            "cancel": cancel,
            "fills": fills,
            "kalshi_order": created,
            "mode": "LIVE_PROBE",
            "counts_as_realized_arb": False,
            "settlement_status": SETTLEMENT_STATUS,
        }
        base._write_json_atomic(result_path, result)
        return result
    finally:
        for task in market_tasks:
            task.cancel()
        await asyncio.gather(*market_tasks, return_exceptions=True)
        if account_task is not None:
            account_task.cancel()
            await asyncio.gather(account_task, return_exceptions=True)
        await gemini.close()
        await kalshi.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    result = asyncio.run(run_once())
    logger.warning("MAKER_LIVE_PROBE_SAFE_RESULT %s", json.dumps(result, separators=(",", ":"), default=str))
    if result.get("hedge", {}).get("hard_halt"):
        raise SystemExit(70)


if __name__ == "__main__":
    main()
