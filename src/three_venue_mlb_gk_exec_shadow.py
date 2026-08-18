"""Read-only event-driven MLB Gemini<->Kalshi execution-intent shadow.

Uses Gemini Predictions public bookTicker WebSocket and Kalshi authenticated
orderbook_delta WebSocket. Exact normal-event counterparts come from the existing
three-venue MLB discovery layer. Every q=1 after-fee lock produces the exact FOK
payloads that a future live probe would use plus explicit blockers. This module
contains no order transport and cannot submit a trade.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any
import uuid

import httpx
import websockets

from src.eventing_books import BookUpdate, EventingKalshiCrossBooks
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    evaluate_pair,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_kalshi_fees import kalshi_quadratic_taker_fee
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.three_venue_mlb_hot import HotContract, discover_hot_universe

logger = logging.getLogger("kalshi_arb.three_venue_mlb_gk_exec_shadow")
D = Decimal
ONE = D("1")


class GeminiBookTickerStore:
    def __init__(self, symbols: list[str], *, max_queue: int = 20000) -> None:
        self.symbols = sorted(set(symbols))
        self.books: dict[str, TopOfBook] = {}
        self.updates: asyncio.Queue[BookUpdate] = asyncio.Queue(maxsize=max_queue)
        self.connected = False
        self.messages = 0
        self.book_updates = 0
        self.dropped_updates = 0
        self.last_message_ms = 0

    def _notify(self, symbol: str, received_ms: int) -> None:
        event = BookUpdate("gemini", symbol, received_ms, time.perf_counter_ns(), "bookTicker")
        try:
            self.updates.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self.updates.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped_updates += 1
            try:
                self.updates.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_updates += 1

    async def run(self) -> None:
        if not self.symbols:
            raise RuntimeError("no Gemini symbols to subscribe")
        backoff = 1.0
        wanted = {s.lower(): s for s in self.symbols}
        while True:
            try:
                async with websockets.connect(
                    GEMINI_WS,
                    open_timeout=5.0,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    await ws.send(json.dumps({
                        "id": "mlb-gk-exec-shadow",
                        "method": "SUBSCRIBE",
                        "params": [f"{s}@bookTicker" for s in self.symbols],
                    }, separators=(",", ":")))
                    self.connected = True
                    backoff = 1.0
                    async for raw in ws:
                        received_ms = int(time.time() * 1000)
                        self.last_message_ms = received_ms
                        self.messages += 1
                        payload = json.loads(raw)
                        if payload.get("status") is not None:
                            if int(payload.get("status")) >= 400:
                                raise RuntimeError(f"Gemini subscription failed status={payload.get('status')}")
                            continue
                        raw_symbol = str(payload.get("s") or "")
                        symbol = wanted.get(raw_symbol.lower())
                        if symbol is None:
                            continue
                        try:
                            book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                        except (KeyError, ValueError, TypeError):
                            continue
                        self.books[symbol] = book
                        self.book_updates += 1
                        self._notify(symbol, received_ms)
            except asyncio.CancelledError:
                self.connected = False
                raise
            except Exception as exc:
                self.connected = False
                logger.warning(
                    "MLB_GK_EXEC_GEMINI_WS_RECONNECT error_type=%s retry_s=%.1f",
                    type(exc).__name__, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    def quote(self, symbol: str, *, max_age_ms: int) -> TopOfBook | None:
        row = self.books.get(symbol)
        if row is None:
            return None
        if int(time.time() * 1000) - row.ts_ms > max_age_ms:
            return None
        return row


def _raw_kalshi_top(q: Any, ticker: str) -> TopOfBook:
    # Kalshi store exposes executable YES ask and NO ask. Transform NO ask back
    # into the resting YES bid that a V2 ask order would hit.
    return TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=int(q.received_ms),
        yes_bid=ONE - D(str(q.no_ask)),
        yes_bid_size=D(str(q.no_depth)),
        yes_ask=D(str(q.yes_ask)),
        yes_ask_size=D(str(q.yes_depth)),
    )


def _orient_kalshi(raw: TopOfBook, source_side: str) -> TopOfBook:
    if source_side == "YES":
        return raw
    if source_side != "NO":
        raise ValueError(f"unsupported Kalshi normalized source_side={source_side}")
    return TopOfBook(
        venue="kalshi",
        market_id=raw.market_id,
        ts_ms=raw.ts_ms,
        yes_bid=ONE - raw.yes_ask,
        yes_bid_size=raw.yes_ask_size,
        yes_ask=ONE - raw.yes_bid,
        yes_ask_size=raw.yes_bid_size,
    )


def _payloads(
    *,
    direction: str,
    gemini: TopOfBook,
    kalshi_raw: TopOfBook,
    kalshi_source_side: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build exact raw-venue q=1 FOK payloads for a canonical-direction lock."""
    if direction == "GEMINI_YES_KALSHI_NO":
        gemini_outcome = "YES"
        gemini_price = gemini.yes_ask
        canonical_k_outcome = "NO"
    elif direction == "KALSHI_YES_GEMINI_NO":
        gemini_outcome = "NO"
        gemini_price = ONE - gemini.yes_bid
        canonical_k_outcome = "YES"
    else:
        raise ValueError(f"unsupported direction={direction}")

    if kalshi_source_side == "YES":
        raw_k_outcome = canonical_k_outcome
    elif kalshi_source_side == "NO":
        raw_k_outcome = "NO" if canonical_k_outcome == "YES" else "YES"
    else:
        raise ValueError(f"unsupported Kalshi normalized source_side={kalshi_source_side}")

    if raw_k_outcome == "YES":
        kalshi_side = "bid"
        kalshi_yes_space_price = kalshi_raw.yes_ask
    else:
        kalshi_side = "ask"
        kalshi_yes_space_price = kalshi_raw.yes_bid

    client_id = f"mlb-gk-shadow-{uuid.uuid4().hex[:16]}"
    gemini_payload = {
        "symbol": gemini.market_id,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "FOK",
        "price": str(gemini_price),
        "quantity": "1",
        "eventOutcome": gemini_outcome,
        "clientOrderId": client_id + "-g",
    }
    kalshi_payload = {
        "ticker": kalshi_raw.market_id,
        "client_order_id": client_id + "-k",
        "side": kalshi_side,
        "count": "1.00",
        "price": str(kalshi_yes_space_price),
        "time_in_force": "fill_or_kill",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    return gemini_payload, kalshi_payload, raw_k_outcome


def _payload_profit(
    gemini_payload: dict[str, Any],
    kalshi_payload: dict[str, Any],
    *,
    kalshi_fee_multiplier: Decimal,
) -> Decimal:
    gp = D(str(gemini_payload["price"]))
    kp_yes = D(str(kalshi_payload["price"]))
    k_cost = kp_yes if kalshi_payload["side"] == "bid" else ONE - kp_yes
    return (
        ONE
        - gp
        - k_cost
        - gemini_predictions_taker_fee(gp, ONE)
        - kalshi_quadratic_taker_fee(kp_yes, ONE, fee_multiplier=kalshi_fee_multiplier)
    )


def _credentials_present() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip() and os.getenv("GEMINI_API_SECRET", "").strip())


async def evaluate_key(
    key: str,
    contract: HotContract,
    gemini_store: GeminiBookTickerStore,
    kalshi_store: EventingKalshiCrossBooks,
    *,
    max_book_age_ms: int,
    max_quote_skew_ms: int,
    min_profit: Decimal,
    active: dict[str, dict[str, Any]],
    trigger: BookUpdate,
) -> None:
    g_side = contract.sides.get("gemini")
    k_side = contract.sides.get("kalshi")
    if g_side is None or k_side is None or contract.kalshi_fee_multiplier is None:
        return
    g = gemini_store.quote(g_side.venue_market_id, max_age_ms=max_book_age_ms)
    if g is None:
        return
    k_quote = await kalshi_store.quote(k_side.venue_market_id, max_age_ms=max_book_age_ms)
    if k_quote is None:
        return
    k_raw = _raw_kalshi_top(k_quote, k_side.venue_market_id)
    k = _orient_kalshi(k_raw, k_side.side)
    multiplier = D(str(contract.kalshi_fee_multiplier))

    def k_fee(price: Decimal, qty: Decimal) -> Decimal:
        return kalshi_quadratic_taker_fee(price, qty, fee_multiplier=multiplier)

    candidates = evaluate_pair(
        g, k,
        gemini_fee=gemini_predictions_taker_fee,
        kalshi_fee=k_fee,
        max_quote_skew_ms=max_quote_skew_ms,
        max_quantity=ONE,
    )
    candidates = [c for c in candidates if c.locked_profit >= min_profit]
    now_ms = int(time.time() * 1000)

    if not candidates:
        prior = active.pop(key, None)
        if prior is not None:
            logger.info("MLB_GK_EXEC_CLOSE %s", json.dumps({
                **prior,
                "event": "CLOSE",
                "closed_ms": now_ms,
                "lifetime_ms": now_ms - int(prior["opened_ms"]),
                "close_trigger_venue": trigger.venue,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
        return

    best = max(candidates, key=lambda c: c.locked_profit)
    g_payload, k_payload, raw_k_outcome = _payloads(
        direction=best.direction,
        gemini=g,
        kalshi_raw=k_raw,
        kalshi_source_side=k_side.side,
    )
    recomputed = _payload_profit(g_payload, k_payload, kalshi_fee_multiplier=multiplier)
    blockers: list[str] = []
    if best.quote_skew_ms > 50:
        blockers.append("QUOTE_SKEW_GT_50MS")
    if best.locked_profit < D("0.03"):
        blockers.append("AFTER_FEE_PROFIT_LT_3C")
    # The normal-event identity is proven, but venue-specific postponement,
    # cancellation, corrections and push semantics remain deliberately unaudited.
    blockers.append("FULL_RULE_FINGERPRINT_REQUIRED")
    if not _credentials_present():
        blockers.append("GEMINI_TRADING_CREDENTIALS_MISSING")
    blockers.append("LIVE_NOT_ARMED")
    if abs(recomputed - best.locked_profit) > D("0.000001"):
        blockers.append("PAYLOAD_ECONOMICS_MISMATCH")

    family = g_side.identity.market_family
    payload = {
        "event": "OPEN" if key not in active else "MOVE",
        "ts_ms": now_ms,
        "opened_ms": active.get(key, {}).get("opened_ms", now_ms),
        "settlement_key": key,
        "market_family": family,
        "gemini_label": g_side.source_label,
        "kalshi_label": k_side.source_label,
        "gemini_symbol": g_side.venue_market_id,
        "kalshi_ticker": k_side.venue_market_id,
        "kalshi_normalized_source_side": k_side.side,
        "direction": best.direction,
        "raw_kalshi_outcome_bought": raw_k_outcome,
        "modeled_locked_profit": str(best.locked_profit),
        "recomputed_payload_locked_profit": str(recomputed),
        "net_cost": str(best.net_cost),
        "roi_on_cost": str(best.roi_on_cost),
        "quote_skew_ms": best.quote_skew_ms,
        "gemini_age_ms": max(0, now_ms - g.ts_ms),
        "kalshi_age_ms": max(0, now_ms - k_raw.ts_ms),
        "trigger_venue": trigger.venue,
        "trigger_kind": trigger.kind,
        "gemini_fok_payload": g_payload,
        "kalshi_fok_payload": k_payload,
        "blockers": blockers,
        "sendable_now": not blockers,
        "read_only": True,
        "order_entry": False,
    }
    prior = active.get(key)
    fingerprint = json.dumps({
        "direction": payload["direction"],
        "g": g_payload["price"],
        "k": k_payload["price"],
        "profit": payload["modeled_locked_profit"],
        "blockers": blockers,
    }, sort_keys=True, separators=(",", ":"))
    if prior is not None and prior.get("fingerprint") == fingerprint:
        return
    payload["fingerprint"] = fingerprint
    active[key] = payload
    logger.info("MLB_GK_EXEC_%s %s", payload["event"], json.dumps(payload, separators=(",", ":"), default=str))


async def _next_update(gemini: GeminiBookTickerStore, kalshi: EventingKalshiCrossBooks) -> BookUpdate:
    gt = asyncio.create_task(gemini.updates.get())
    kt = asyncio.create_task(kalshi.updates.get())
    done, pending = await asyncio.wait((gt, kt), return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    return next(iter(done)).result()


async def run_session(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=30.0) as http:
        universe = await discover_hot_universe(http)
    contracts = {
        key: c for key, c in universe.contracts.items()
        if "gemini" in c.sides and "kalshi" in c.sides
    }
    if not contracts:
        raise RuntimeError("no exact Gemini/Kalshi MLB counterparts")
    symbols = sorted({c.sides["gemini"].venue_market_id for c in contracts.values()})
    tickers = sorted({c.sides["kalshi"].venue_market_id for c in contracts.values()})
    map_to_keys: dict[tuple[str, str], list[str]] = {}
    for key, c in contracts.items():
        for venue in ("gemini", "kalshi"):
            side = c.sides[venue]
            map_to_keys.setdefault((venue, side.venue_market_id), []).append(key)

    gemini = GeminiBookTickerStore(symbols)
    kalshi = EventingKalshiCrossBooks(tickers)
    tasks = [
        asyncio.create_task(gemini.run(), name="gemini_bookticker"),
        asyncio.create_task(kalshi.run(), name="kalshi_orderbook"),
    ]
    deadline = time.monotonic() + args.startup_timeout_seconds
    while time.monotonic() < deadline:
        for task in tasks:
            if task.done():
                exc = task.exception()
                raise RuntimeError(f"feed task failed at startup: {exc or task.get_name()}")
        if gemini.connected and gemini.books and kalshi.initialized_count > 0:
            break
        await asyncio.sleep(0.025)
    if not gemini.connected or not gemini.books or kalshi.initialized_count == 0:
        raise RuntimeError(
            f"startup timeout gemini_connected={gemini.connected} gemini_books={len(gemini.books)} "
            f"kalshi_initialized={kalshi.initialized_count}"
        )

    logger.info("MLB_GK_EXEC_READY %s", json.dumps({
        "contracts": len(contracts),
        "gemini_symbols": len(symbols),
        "gemini_seeded_books": len(gemini.books),
        "kalshi_tickers": len(tickers),
        "kalshi_initialized_books": kalshi.initialized_count,
        "max_quote_skew_ms": args.max_quote_skew_ms,
        "max_book_age_ms": args.max_book_age_ms,
        "min_profit": str(args.min_profit),
        "gemini_trading_credentials_present": _credentials_present(),
        "rule_gate": "FULL_RULE_FINGERPRINT_REQUIRED",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))

    active: dict[str, dict[str, Any]] = {}
    evaluations = positives = 0
    by_trigger: Counter[str] = Counter()
    started = time.monotonic()
    last_summary = started
    try:
        while True:
            for task in tasks:
                if task.done():
                    exc = task.exception()
                    raise RuntimeError(f"feed task exited: {exc or task.get_name()}")
            trigger = await _next_update(gemini, kalshi)
            by_trigger[trigger.venue] += 1
            for key in map_to_keys.get((trigger.venue, trigger.market_id), []):
                before = key in active
                await evaluate_key(
                    key, contracts[key], gemini, kalshi,
                    max_book_age_ms=args.max_book_age_ms,
                    max_quote_skew_ms=args.max_quote_skew_ms,
                    min_profit=args.min_profit,
                    active=active,
                    trigger=trigger,
                )
                evaluations += 1
                if key in active and not before:
                    positives += 1
            now = time.monotonic()
            if now - last_summary >= 10.0:
                last_summary = now
                logger.info("MLB_GK_EXEC_SUMMARY %s", json.dumps({
                    "uptime_s": round(now - started, 1),
                    "evaluations": evaluations,
                    "signal_opens": positives,
                    "active_signals": len(active),
                    "triggers": dict(by_trigger),
                    "gemini_messages": gemini.messages,
                    "gemini_book_updates": gemini.book_updates,
                    "gemini_cached_books": len(gemini.books),
                    "kalshi_initialized_books": kalshi.initialized_count,
                    "gemini_dropped_updates": gemini.dropped_updates,
                    "kalshi_dropped_updates": kalshi.dropped_updates,
                    "read_only": True,
                    "order_entry": False,
                }, separators=(",", ":")))
    finally:
        for task in tasks:
            task.cancel()
        await kalshi.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def supervise(args: argparse.Namespace) -> None:
    backoff = 1.0
    while True:
        try:
            await run_session(args)
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("MLB_GK_EXEC_SESSION_ERROR %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--startup-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-book-age-ms", type=int, default=1000)
    parser.add_argument("--max-quote-skew-ms", type=int, default=50)
    parser.add_argument("--min-profit", type=D, default=D("0"))
    args = parser.parse_args()
    if args.max_quote_skew_ms < 0 or args.max_book_age_ms <= 0:
        raise SystemExit("invalid freshness settings")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(supervise(args))


if __name__ == "__main__":
    main()
