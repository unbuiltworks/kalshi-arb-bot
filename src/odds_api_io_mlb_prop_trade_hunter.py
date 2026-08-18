"""Read-only MLB sportsbook/Gemini prop trade hunter ranked by real top-book dollars.

Builds on the existing prop candidate/exact shadow stack but fixes two research
limitations: finished sportsbook events are rejected, and every fresh positive
sportsbook candidate gets an on-demand Gemini bookTicker snapshot so quiet books
do not fail freshness merely because their price did not change. The output ranks
opportunities by executable top-level depth after Gemini taker fees. No order
transport is imported and every result remains settlement-rule blocked.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

from src import odds_api_io_mlb_prop_exact_shadow as exact
from src import odds_api_io_mlb_hot_shadow as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_live_shadow import gemini_book_ticker_once

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_prop_trade_hunter")
D = Decimal
ONE = D("1")
MIN_SNAPSHOT_EDGE = D("0.002")
MAX_VALIDATE_CONCURRENCY = 8
VALIDATE_COOLDOWN_MS = 1000
BOOK_TIMEOUT_S = 3.0

_orig_ingest = base.OddsIoStore.ingest
_orig_run_session = base.run_session
_sem = asyncio.Semaphore(MAX_VALIDATE_CONCURRENCY)
_last_validation_ms: dict[tuple[str, str], int] = {}
_validation_tasks: set[asyncio.Task[Any]] = set()
_stats: Counter[str] = Counter()
_best_profit = D("0")


def _live_event(self: base.OddsIoStore, event_id: str) -> bool:
    event = self.events.get(event_id)
    if not isinstance(event, Mapping):
        return False
    return str(event.get("status") or "").strip().lower() == "live"


async def _validate(symbol: str, retail: exact.RetailProp, snapshot_profit: Decimal) -> None:
    global _best_profit
    key = (symbol, retail.book)
    async with _sem:
        started_ms = int(time.time() * 1000)
        try:
            tob = await gemini_book_ticker_once(symbol, timeout_s=BOOK_TIMEOUT_S)
        except Exception as exc:
            _stats["gemini_snapshot_errors"] += 1
            logger.warning(
                "PROP_TRADE_HUNTER_GEMINI_ERROR symbol=%s book=%s type=%s",
                symbol, retail.book, type(exc).__name__,
            )
            return

        now_ms = int(time.time() * 1000)
        retail_age_ms = now_ms - retail.received_ms
        no_price = tob.no_ask
        no_depth = tob.no_ask_size
        _stats["gemini_validations"] += 1
        if no_depth <= 0 or not (D("0") < no_price < ONE):
            _stats["no_executable_gemini_depth"] += 1
            return

        qty = no_depth
        gemini_fee = gemini_predictions_taker_fee(no_price, qty)
        sportsbook_stake = qty / retail.decimal_odds
        gemini_raw_cost = qty * no_price
        total_cost = sportsbook_stake + gemini_raw_cost + gemini_fee
        locked_profit = qty - total_cost
        if locked_profit <= 0:
            _stats["revalidated_nonpositive"] += 1
            return

        q1_profit = None
        if qty >= ONE:
            q1_fee = gemini_predictions_taker_fee(no_price, ONE)
            q1_profit = ONE - (ONE / retail.decimal_odds) - no_price - q1_fee

        remaining_for_fd = qty - gemini_raw_cost - gemini_fee
        min_fd_decimal = None if remaining_for_fd <= 0 else qty / remaining_for_fd
        _stats["positive_depth_validations"] += 1
        if locked_profit > _best_profit:
            _best_profit = locked_profit

        payload = {
            "ts_ms": now_ms,
            "book": retail.book,
            "event_id": retail.event_id,
            "player": retail.player,
            "stat": retail.stat,
            "line": str(retail.line),
            "sportsbook_over_decimal": str(retail.decimal_odds),
            "gemini_symbol": symbol,
            "gemini_no_price": str(no_price),
            "gemini_no_top_depth": str(no_depth),
            "gemini_fee_at_top_depth": str(gemini_fee),
            "max_matched_payout": str(qty),
            "sportsbook_stake_for_top_depth": str(sportsbook_stake),
            "gemini_raw_cost_for_top_depth": str(gemini_raw_cost),
            "total_capital_for_top_depth": str(total_cost),
            "locked_profit_top_depth": str(locked_profit),
            "roi_on_total_capital": str(locked_profit / total_cost),
            "q1_locked_profit": None if q1_profit is None else str(q1_profit),
            "minimum_sportsbook_decimal_at_this_depth": None if min_fd_decimal is None else str(min_fd_decimal),
            "snapshot_candidate_profit_per_1": str(snapshot_profit),
            "retail_age_ms_at_validation": retail_age_ms,
            "gemini_snapshot_roundtrip_ms": now_ms - started_ms,
            "settlement_blocker": "FANDUEL_VS_GEMINI_SUSPENSION_RESCHEDULE_RULES_NOT_IDENTICAL",
            "execution_eligible": False,
            "read_only": True,
            "order_entry": False,
        }
        logger.info("PROP_TRADE_HUNTER_POSITIVE %s", json.dumps(payload, separators=(",", ":"), default=str))


def _schedule_validation(symbol: str, retail: exact.RetailProp, snapshot_profit: Decimal) -> None:
    key = (symbol, retail.book)
    now_ms = int(time.time() * 1000)
    if now_ms - _last_validation_ms.get(key, 0) < VALIDATE_COOLDOWN_MS:
        _stats["cooldown_skips"] += 1
        return
    _last_validation_ms[key] = now_ms
    task = asyncio.create_task(_validate(symbol, retail, snapshot_profit), name=f"prop_validate_{len(_validation_tasks)}")
    _validation_tasks.add(task)
    task.add_done_callback(_validation_tasks.discard)


def _hunter_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    accepted = _orig_ingest(self, message, freshness_ms=freshness_ms)
    if str(message.get("type") or "") not in {"created", "updated"}:
        return accepted
    event_id = str(message.get("id") or "")
    if not _live_event(self, event_id):
        _stats["nonlive_event_updates_rejected"] += 1
        return accepted
    for symbol, retail, snapshot_profit in exact._candidate_rows(self, message, freshness_ms):
        if snapshot_profit < MIN_SNAPSHOT_EDGE:
            continue
        _stats["validation_candidates"] += 1
        _schedule_validation(symbol, retail, snapshot_profit)
    return accepted


async def _hunter_heartbeat() -> None:
    while True:
        await asyncio.sleep(10.0)
        logger.info("PROP_TRADE_HUNTER_SUMMARY %s", json.dumps({
            "stats": dict(_stats),
            "validation_tasks_inflight": len(_validation_tasks),
            "best_locked_profit_top_depth": str(_best_profit),
            "min_snapshot_edge": str(MIN_SNAPSHOT_EDGE),
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


async def _run_hunter(args: Any) -> None:
    hb = asyncio.create_task(_hunter_heartbeat(), name="trade_hunter_heartbeat")
    try:
        await _orig_run_session(args)
    finally:
        hb.cancel()
        for task in list(_validation_tasks):
            task.cancel()
        await asyncio.gather(hb, *_validation_tasks, return_exceptions=True)


base.OddsIoStore.ingest = _hunter_ingest
base.run_session = _run_hunter

if __name__ == "__main__":
    base.main()
