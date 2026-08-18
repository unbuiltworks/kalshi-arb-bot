"""Promote positive MLB sportsbook/Gemini prop candidates to exact Gemini bookTicker validation.

Imports the candidate sieve, then opens a small dedicated Gemini public bookTicker
watcher only for symbols whose snapshot edge is >= 1 cent. The single Odds-API.io
WebSocket remains owned by the base service. Exact outputs are still read-only and
rule-blocked; no order transport is present.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import websockets

from src import odds_api_io_mlb_prop_candidate_shadow as cand
from src import odds_api_io_mlb_hot_shadow as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_prop_exact_shadow")
D = Decimal
ONE = D("1")
WATCH_MIN_SNAPSHOT_PROFIT = D("0.01")
MAX_WATCHERS = 24
MAX_RETAIL_AGE_MS = 3000
MAX_EXACT_SKEW_MS = 1000


@dataclass(frozen=True)
class RetailProp:
    book: str
    event_id: str
    player: str
    stat: str
    line: Decimal
    decimal_odds: Decimal
    cost: Decimal
    received_ms: int
    updated_ms: int


_watch_tasks: dict[str, asyncio.Task[Any]] = {}
_gemini_books: dict[str, Any] = {}
_latest_retail: dict[str, dict[str, RetailProp]] = {}
_active: dict[tuple[str, str], dict[str, Any]] = {}
_fingerprints: dict[tuple[str, str], tuple[str, str]] = {}
_orig_ingest = base.OddsIoStore.ingest
_orig_run_session = base.run_session


def _candidate_rows(self: base.OddsIoStore, message: Mapping[str, Any], freshness_ms: int) -> list[tuple[str, RetailProp, Decimal]]:
    if str(message.get("type") or "") not in {"created", "updated"}:
        return []
    event_id = str(message.get("id") or "")
    event = self.events.get(event_id)
    if not isinstance(event, Mapping):
        return []
    event_ident = base._event_identity(event)
    if event_ident is None:
        return []
    book = base._retail_book_name(message.get("bookie") or message.get("bookmaker"))
    if not book:
        return []
    received_ms = int(time.time() * 1000)
    out: list[tuple[str, RetailProp, Decimal]] = []
    markets = message.get("markets")
    if not isinstance(markets, list):
        return out
    for market in markets:
        if not isinstance(market, Mapping) or cand._norm(market.get("name")) != "player props":
            continue
        updated_ms = base._market_updated_ms(market.get("updatedAt"))
        if updated_ms is None or abs(received_ms - updated_ms) > freshness_ms:
            continue
        rows = market.get("odds")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            parts = cand._label_parts(row.get("label"))
            line = cand._d(row.get("hdp"))
            decimal_odds = cand._d(row.get("over"))
            if parts is None or line is None or decimal_odds is None or decimal_odds <= ONE:
                continue
            player, stat = parts
            key = cand._semantic(
                teams=event_ident.participants,
                start_ms=event_ident.scheduled_start_ms,
                player=player,
                stat=stat,
                line=line,
            )
            gem = cand._gemini_props.get(key)
            if gem is None:
                continue
            cost = ONE / decimal_odds
            snapshot_profit = ONE - cost - gem.no_cost - gem.no_fee
            if snapshot_profit < WATCH_MIN_SNAPSHOT_PROFIT:
                continue
            out.append((gem.symbol, RetailProp(
                book=book, event_id=event_id, player=player, stat=stat, line=line,
                decimal_odds=decimal_odds, cost=cost, received_ms=received_ms,
                updated_ms=updated_ms,
            ), snapshot_profit))
    return out


def _ensure_watch(symbol: str) -> None:
    if symbol in _watch_tasks or len(_watch_tasks) >= MAX_WATCHERS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_watch_symbol(symbol), name=f"prop_gemini_{len(_watch_tasks)}")
    _watch_tasks[symbol] = task
    logger.info("ODDS_API_IO_PROP_PROMOTE %s", json.dumps({
        "gemini_symbol": symbol,
        "watchers": len(_watch_tasks),
        "reason": "SNAPSHOT_EDGE_AT_LEAST_1C",
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def _evaluate(symbol: str, book: str) -> None:
    retail = _latest_retail.get(symbol, {}).get(book)
    gem = _gemini_books.get(symbol)
    key = (symbol, book)
    now_ms = int(time.time() * 1000)
    if retail is None or gem is None:
        return
    retail_age = now_ms - retail.received_ms
    gem_age = now_ms - int(gem.ts_ms)
    skew = abs(retail.received_ms - int(gem.ts_ms))
    no_cost = ONE - D(str(gem.yes_bid))
    no_fee = gemini_predictions_taker_fee(no_cost, ONE)
    profit = ONE - retail.cost - no_cost - no_fee
    threshold_den = ONE - no_cost - no_fee
    min_decimal = None if threshold_den <= 0 else ONE / threshold_den
    valid_freshness = (
        retail_age <= MAX_RETAIL_AGE_MS
        and gem_age <= MAX_RETAIL_AGE_MS
        and skew <= MAX_EXACT_SKEW_MS
    )

    if profit <= 0 or not valid_freshness:
        prior = _active.pop(key, None)
        if prior is not None:
            logger.info("ODDS_API_IO_PROP_EXACT_CLOSE %s", json.dumps({
                **prior,
                "event": "CLOSE",
                "closed_ms": now_ms,
                "close_profit": str(profit),
                "close_retail_age_ms": retail_age,
                "close_gemini_age_ms": gem_age,
                "close_quote_skew_ms": skew,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":"), default=str))
        return

    fingerprint = (str(retail.decimal_odds), str(no_cost))
    if _fingerprints.get(key) == fingerprint:
        return
    _fingerprints[key] = fingerprint
    prior = _active.get(key)
    payload = {
        "event": "OPEN" if prior is None else "MOVE",
        "ts_ms": now_ms,
        "book": book,
        "event_id": retail.event_id,
        "player": retail.player,
        "stat": retail.stat,
        "line": str(retail.line),
        "sportsbook_over_decimal": str(retail.decimal_odds),
        "sportsbook_cost_per_1_payout": str(retail.cost),
        "minimum_sportsbook_decimal_for_positive_lock": None if min_decimal is None else str(min_decimal),
        "gemini_symbol": symbol,
        "gemini_no_live_cost": str(no_cost),
        "gemini_no_fee": str(no_fee),
        "exact_locked_profit_per_1_payout": str(profit),
        "retail_age_ms": retail_age,
        "gemini_age_ms": gem_age,
        "quote_receive_skew_ms": skew,
        "freshness_gate": f"retail<=3000ms,gemini<=3000ms,skew<={MAX_EXACT_SKEW_MS}ms",
        "settlement_rule_audit_required": True,
        "execution_eligible": False,
        "read_only": True,
        "order_entry": False,
    }
    if prior is None:
        payload["opened_ms"] = now_ms
    else:
        payload["opened_ms"] = prior["opened_ms"]
    _active[key] = payload
    logger.info("ODDS_API_IO_PROP_EXACT_%s %s", payload["event"], json.dumps(payload, separators=(",", ":"), default=str))


async def _watch_symbol(symbol: str) -> None:
    backoff = 0.5
    while True:
        try:
            async with websockets.connect(
                GEMINI_WS, open_timeout=5.0, ping_interval=20, ping_timeout=20,
                max_size=2 * 1024 * 1024,
            ) as ws:
                await ws.send(json.dumps({
                    "id": f"prop-{abs(hash(symbol)) % 10000000}",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@bookTicker"],
                }, separators=(",", ":")))
                backoff = 0.5
                logger.info("ODDS_API_IO_PROP_GEMINI_LIVE %s", json.dumps({
                    "gemini_symbol": symbol, "read_only": True, "order_entry": False,
                }, separators=(",", ":")))
                async for raw in ws:
                    received_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"subscription status={payload.get('status')}")
                        continue
                    if str(payload.get("s") or "").lower() != symbol.lower():
                        continue
                    try:
                        book = parse_gemini_book_ticker(payload, received_ms=received_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    _gemini_books[symbol] = book
                    for retail_book in list(_latest_retail.get(symbol, {})):
                        _evaluate(symbol, retail_book)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("ODDS_API_IO_PROP_GEMINI_RECONNECT symbol=%s type=%s retry_s=%.1f", symbol, type(exc).__name__, backoff)
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2.0)


def _exact_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    accepted = _orig_ingest(self, message, freshness_ms=freshness_ms)
    for symbol, retail, snapshot_profit in _candidate_rows(self, message, freshness_ms):
        _latest_retail.setdefault(symbol, {})[retail.book] = retail
        _ensure_watch(symbol)
        logger.info("ODDS_API_IO_PROP_WATCH_CANDIDATE %s", json.dumps({
            "gemini_symbol": symbol,
            "book": retail.book,
            "player": retail.player,
            "stat": retail.stat,
            "line": str(retail.line),
            "sportsbook_over_decimal": str(retail.decimal_odds),
            "snapshot_profit": str(snapshot_profit),
            "read_only": True,
        }, separators=(",", ":")))
        if symbol in _gemini_books:
            _evaluate(symbol, retail.book)
    return accepted


async def _exact_heartbeat() -> None:
    while True:
        await asyncio.sleep(10.0)
        now_ms = int(time.time() * 1000)
        for symbol, books in list(_latest_retail.items()):
            for book in list(books):
                _evaluate(symbol, book)
        logger.info("ODDS_API_IO_PROP_EXACT_SUMMARY %s", json.dumps({
            "watchers": len(_watch_tasks),
            "live_gemini_books": len(_gemini_books),
            "retail_symbols": len(_latest_retail),
            "active_exact_signals": len(_active),
            "max_watchers": MAX_WATCHERS,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


async def _run_exact(args: Any) -> None:
    hb = asyncio.create_task(_exact_heartbeat(), name="prop_exact_heartbeat")
    try:
        await _orig_run_session(args)
    finally:
        hb.cancel()
        for task in _watch_tasks.values():
            task.cancel()
        await asyncio.gather(hb, *_watch_tasks.values(), return_exceptions=True)


base.OddsIoStore.ingest = _exact_ingest
base.run_session = _run_exact

if __name__ == "__main__":
    base.main()
