"""Read-only MLB sportsbook/Gemini prop hunter with guaranteed depth-safe sizing.

Every candidate is revalidated against a fresh Gemini L2 snapshot. Gemini public
prediction depth is normalized in YES space, so BUY NO liquidity is derived from
YES bids. The actual trade plan is selected by the pure hedge optimizer, which:
- works backward from executable Gemini depth;
- rounds sportsbook stake down to cents;
- rounds Gemini quantity down to 0.01 contracts;
- prices the entire Gemini FOK quantity at the worst submitted limit for the
  guaranteed P/L gate (price improvement is optional upside only);
- proves P/L independently for prop-hit and prop-miss outcomes.

The legacy exact/candidate modules are imported only for pure identity helpers and
Gemini REST discovery. Their monkeypatched run loops are deliberately bypassed so
legacy top-book positive logs cannot masquerade as qualified signals.

No order transport is imported. Results remain settlement-rule blocked and are
never marked manually actionable or execution eligible.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping

import websockets

# Capture the clean base service before importing prop helper modules, which have
# historical import-time monkeypatches for their standalone shadow entrypoints.
from src import odds_api_io_mlb_hot_shadow as base
_BASE_INIT = base.OddsIoStore.__init__
_BASE_INGEST = base.OddsIoStore.ingest
_BASE_RUN_SESSION = base.run_session

from src import odds_api_io_mlb_prop_exact_shadow as exact
from src import odds_api_io_mlb_prop_candidate_shadow as cand
from src.gemini_kalshi_live_shadow import GEMINI_WS
from src.gemini_prop_hedge_optimizer import best_guaranteed_buy_no_hedge

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_prop_trade_hunter")
D = Decimal
MIN_SNAPSHOT_EDGE = D("0.002")
MAX_VALIDATE_CONCURRENCY = 8
VALIDATE_COOLDOWN_MS = 1000
BOOK_TIMEOUT_S = 3.0
DEPTH_LEVELS = 10
MAX_MATCHED_PAYOUT = D("50")

_sem = asyncio.Semaphore(MAX_VALIDATE_CONCURRENCY)
_last_validation_ms: dict[tuple[str, str], int] = {}
_validation_tasks: set[asyncio.Task[Any]] = set()
_stats: Counter[str] = Counter()
_best_profit = D("0")


def _levels(raw: Any) -> list[tuple[Decimal, Decimal]]:
    if not isinstance(raw, list):
        return []
    out: list[tuple[Decimal, Decimal]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            price, qty = D(str(row[0])), D(str(row[1]))
        except Exception:
            continue
        if price.is_finite() and qty.is_finite() and D("0") <= price <= D("1") and qty > 0:
            out.append((price, qty))
    return out


async def gemini_depth_once(
    symbol: str, *, levels: int = DEPTH_LEVELS, timeout_s: float = BOOK_TIMEOUT_S,
) -> tuple[int, list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
    """Return one fresh Gemini partial-depth snapshot for a prediction symbol."""
    stream = f"{symbol}@depth{levels}@100ms"
    async with websockets.connect(
        GEMINI_WS, open_timeout=timeout_s, ping_interval=20, ping_timeout=20,
        max_size=2 * 1024 * 1024,
    ) as ws:
        await ws.send(json.dumps({"id": "depth-1", "method": "SUBSCRIBE", "params": [stream]}))
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no Gemini depth snapshot for {symbol}")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            payload = json.loads(raw)
            if payload.get("status") is not None:
                if int(payload.get("status")) >= 400:
                    raise RuntimeError(f"Gemini depth subscription status={payload.get('status')}")
                continue
            bids = _levels(payload.get("bids"))
            asks = _levels(payload.get("asks"))
            if bids or asks:
                return int(time.time() * 1000), bids, asks


def _live_event(self: base.OddsIoStore, event_id: str) -> bool:
    event = self.events.get(event_id)
    if not isinstance(event, Mapping):
        return False
    return str(event.get("status") or "").strip().lower() == "live"


async def _validate(symbol: str, retail: exact.RetailProp, snapshot_profit: Decimal) -> None:
    global _best_profit
    async with _sem:
        started_ms = int(time.time() * 1000)
        try:
            depth_received_ms, yes_bids, yes_asks = await gemini_depth_once(symbol)
        except Exception as exc:
            _stats["gemini_depth_errors"] += 1
            logger.warning(
                "PROP_TRADE_HUNTER_GEMINI_DEPTH_ERROR symbol=%s book=%s type=%s",
                symbol, retail.book, type(exc).__name__,
            )
            return

        now_ms = int(time.time() * 1000)
        _stats["gemini_depth_validations"] += 1
        if not yes_bids:
            _stats["no_executable_gemini_depth"] += 1
            return

        plan = best_guaranteed_buy_no_hedge(
            yes_bids=yes_bids,
            sportsbook_decimal_odds=retail.decimal_odds,
            max_matched_payout=MAX_MATCHED_PAYOUT,
            sportsbook_stake_increment=D("0.01"),
            gemini_quantity_increment=D("0.01"),
        )
        if plan is None:
            _stats["guaranteed_nonpositive"] += 1
            return

        _stats["guaranteed_positive_depth_validations"] += 1
        if plan.worst_case_locked_profit > _best_profit:
            _best_profit = plan.worst_case_locked_profit

        expected_improvement = (
            plan.gemini_guaranteed_max_total_debit - plan.expected_depth_total_cost
        )
        payload = {
            "ts_ms": now_ms,
            "book": retail.book,
            "event_id": retail.event_id,
            "player": retail.player,
            "stat": retail.stat,
            "line": str(retail.line),
            "sportsbook_over_decimal": str(retail.decimal_odds),
            "sportsbook_stake": str(plan.sportsbook_stake),
            "sportsbook_profit_if_hits": str(plan.sportsbook_total_return - plan.sportsbook_stake),
            "sportsbook_total_return_if_hits": str(plan.sportsbook_total_return),
            "sportsbook_stake_increment": "0.01",
            "gemini_symbol": symbol,
            "gemini_side": "BUY",
            "gemini_outcome": "NO",
            "gemini_quantity": str(plan.gemini_quantity),
            "gemini_quantity_increment_assumed": "0.01",
            "gemini_fok_limit_price": str(plan.gemini_no_limit_price),
            "gemini_available_depth_through_limit": str(plan.available_depth_through_limit),
            "gemini_guaranteed_max_raw_debit": str(plan.gemini_guaranteed_max_raw_debit),
            "gemini_guaranteed_max_fee": str(plan.gemini_guaranteed_max_fee),
            "gemini_guaranteed_max_total_debit": str(plan.gemini_guaranteed_max_total_debit),
            "gemini_expected_depth_raw_cost": str(plan.expected_depth_raw_cost),
            "gemini_expected_depth_fee": str(plan.expected_depth_fee),
            "gemini_expected_depth_total_cost": str(plan.expected_depth_total_cost),
            "gemini_expected_price_improvement_vs_limit_max": str(expected_improvement),
            "gemini_expected_fill_levels": [
                {"no_price": str(fill.no_price), "quantity": str(fill.quantity)}
                for fill in plan.expected_fills
            ],
            "gemini_best_yes_bid": str(max(p for p, _ in yes_bids)),
            "gemini_best_yes_ask": None if not yes_asks else str(min(p for p, _ in yes_asks)),
            "pnl_if_prop_hits_guaranteed": str(plan.pnl_if_prop_hits_guaranteed),
            "pnl_if_prop_misses_guaranteed": str(plan.pnl_if_prop_misses_guaranteed),
            "worst_case_locked_profit": str(plan.worst_case_locked_profit),
            "guaranteed_roi_on_capital": str(plan.guaranteed_roi_on_capital),
            "snapshot_candidate_profit_per_1": str(snapshot_profit),
            "retail_age_ms_at_validation": now_ms - retail.received_ms,
            "gemini_depth_age_ms": now_ms - depth_received_ms,
            "gemini_depth_roundtrip_ms": now_ms - started_ms,
            "pricing_gate": "FRESH_L2_DEPTH_PLUS_WORST_FOK_LIMIT",
            "settlement_blocker": "FANDUEL_VS_GEMINI_SUSPENSION_RESCHEDULE_RULES_NOT_IDENTICAL",
            "execution_eligible": False,
            "manual_actionable": False,
            "read_only": True,
            "order_entry": False,
        }
        logger.info(
            "PROP_TRADE_HUNTER_GUARANTEED_POSITIVE %s",
            json.dumps(payload, separators=(",", ":"), default=str),
        )


def _schedule_validation(symbol: str, retail: exact.RetailProp, snapshot_profit: Decimal) -> None:
    key = (symbol, retail.book)
    now_ms = int(time.time() * 1000)
    if now_ms - _last_validation_ms.get(key, 0) < VALIDATE_COOLDOWN_MS:
        _stats["cooldown_skips"] += 1
        return
    _last_validation_ms[key] = now_ms
    task = asyncio.create_task(
        _validate(symbol, retail, snapshot_profit),
        name=f"prop_guaranteed_validate_{len(_validation_tasks)}",
    )
    _validation_tasks.add(task)
    task.add_done_callback(_validation_tasks.discard)


def _hunter_ingest(self: base.OddsIoStore, message: Mapping[str, Any], *, freshness_ms: int) -> int:
    accepted = _BASE_INGEST(self, message, freshness_ms=freshness_ms)
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
        logger.info("PROP_TRADE_HUNTER_GUARANTEED_SUMMARY %s", json.dumps({
            "stats": dict(_stats),
            "validation_tasks_inflight": len(_validation_tasks),
            "best_worst_case_locked_profit": str(_best_profit),
            "min_snapshot_edge": str(MIN_SNAPSHOT_EDGE),
            "depth_levels": DEPTH_LEVELS,
            "max_matched_payout": str(MAX_MATCHED_PAYOUT),
            "manual_actionable": False,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))


async def _run_hunter(args: Any) -> None:
    # Start only the Gemini REST discovery map needed by exact._candidate_rows.
    # Do not enter cand/exact standalone shadow loops or their legacy positive logs.
    refresh = asyncio.create_task(cand._refresh_gemini_props(), name="gemini_prop_identity_refresh")
    hb = asyncio.create_task(_hunter_heartbeat(), name="trade_hunter_guaranteed_heartbeat")
    try:
        await _BASE_RUN_SESSION(args)
    finally:
        refresh.cancel()
        hb.cancel()
        for task in list(_validation_tasks):
            task.cancel()
        await asyncio.gather(refresh, hb, *_validation_tasks, return_exceptions=True)


# Undo helper-module import side effects, then install only this hunter's scoped hooks.
base.OddsIoStore.__init__ = _BASE_INIT
base.OddsIoStore.ingest = _hunter_ingest
base.run_session = _run_hunter

if __name__ == "__main__":
    base.main()
