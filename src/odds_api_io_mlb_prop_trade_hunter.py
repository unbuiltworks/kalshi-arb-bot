"""Read-only MLB sportsbook/Gemini prop trade hunter using executable depth sweeps.

A sportsbook quote is only promoted after a fresh Gemini L2 partial-depth snapshot.
Gemini prediction-market public depth is normalized in YES price space, so a BUY
NO sweep consumes YES bids from highest to lowest and transforms each price P to
(1-P), preserving the displayed contract quantity at each level.

The hunter computes matched sportsbook/Gemini payout quantity, full depth-weighted
Gemini raw cost, conservative per-fill taker fees, explicit sportsbook stake/profit/
total return, and worst-case P/L. It has no order transport and remains rule-blocked.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import time
from typing import Any, Mapping, Sequence

import websockets

from src import odds_api_io_mlb_prop_exact_shadow as exact
from src import odds_api_io_mlb_hot_shadow as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_live_shadow import GEMINI_WS

logger = logging.getLogger("kalshi_arb.odds_api_io_mlb_prop_trade_hunter")
D = Decimal
ONE = D("1")
MIN_SNAPSHOT_EDGE = D("0.002")
MAX_VALIDATE_CONCURRENCY = 8
VALIDATE_COOLDOWN_MS = 1000
BOOK_TIMEOUT_S = 3.0
DEPTH_LEVELS = 10
MAX_MATCHED_PAYOUT = D("50")

_orig_ingest = base.OddsIoStore.ingest
_orig_run_session = base.run_session
_sem = asyncio.Semaphore(MAX_VALIDATE_CONCURRENCY)
_last_validation_ms: dict[tuple[str, str], int] = {}
_validation_tasks: set[asyncio.Task[Any]] = set()
_stats: Counter[str] = Counter()
_best_profit = D("0")


@dataclass(frozen=True)
class NoFill:
    no_price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class NoSweep:
    quantity: Decimal
    raw_cost: Decimal
    fee: Decimal
    worst_limit_price: Decimal
    fills: tuple[NoFill, ...]

    @property
    def total_cost(self) -> Decimal:
        return self.raw_cost + self.fee

    @property
    def average_raw_price(self) -> Decimal:
        return self.raw_cost / self.quantity


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
        if price.is_finite() and qty.is_finite() and D("0") <= price <= ONE and qty > 0:
            out.append((price, qty))
    return out


def buy_no_sweep_from_yes_bids(
    yes_bids: Sequence[tuple[Decimal, Decimal]], quantity: Decimal,
) -> NoSweep | None:
    """Sweep BUY NO quantity against a Gemini YES-space depth snapshot.

    Highest YES bid maps to the cheapest executable NO ask (1-YES bid). Fees are
    conservatively rounded per consumed price level rather than once across fills.
    """
    if quantity <= 0:
        return None
    remaining = quantity
    raw_cost = D("0")
    fee = D("0")
    fills: list[NoFill] = []
    for yes_price, level_qty in sorted(yes_bids, key=lambda x: x[0], reverse=True):
        if remaining <= 0:
            break
        take = min(remaining, level_qty)
        if take <= 0:
            continue
        no_price = ONE - yes_price
        if not (D("0") < no_price < ONE):
            continue
        fills.append(NoFill(no_price=no_price, quantity=take))
        raw_cost += no_price * take
        fee += gemini_predictions_taker_fee(no_price, take)
        remaining -= take
    if remaining > 0 or not fills:
        return None
    return NoSweep(
        quantity=quantity,
        raw_cost=raw_cost,
        fee=fee,
        worst_limit_price=max(fill.no_price for fill in fills),
        fills=tuple(fills),
    )


def _candidate_quantities(yes_bids: Sequence[tuple[Decimal, Decimal]]) -> list[Decimal]:
    total = D("0")
    out: set[Decimal] = set()
    for _, qty in sorted(yes_bids, key=lambda x: x[0], reverse=True):
        if total >= MAX_MATCHED_PAYOUT:
            break
        total = min(MAX_MATCHED_PAYOUT, total + qty)
        if total > 0:
            out.add(total)
    if total >= ONE:
        out.add(ONE)
    return sorted(out)


def _hedge_payload(retail: exact.RetailProp, sweep: NoSweep) -> dict[str, Decimal] | None:
    qty = sweep.quantity
    sportsbook_stake = qty / retail.decimal_odds
    sportsbook_profit = qty - sportsbook_stake
    sportsbook_total_return = qty
    gemini_total_cost = sweep.total_cost
    total_capital = sportsbook_stake + gemini_total_cost
    pnl_if_prop_hits = sportsbook_total_return - total_capital
    pnl_if_prop_misses = qty - total_capital
    worst_case = min(pnl_if_prop_hits, pnl_if_prop_misses)
    if worst_case <= 0 or total_capital <= 0:
        return None
    remaining_for_sportsbook = qty - gemini_total_cost
    min_decimal = qty / remaining_for_sportsbook if remaining_for_sportsbook > 0 else D("Infinity")
    return {
        "quantity": qty,
        "sportsbook_stake": sportsbook_stake,
        "sportsbook_profit": sportsbook_profit,
        "sportsbook_total_return": sportsbook_total_return,
        "gemini_raw_cost": sweep.raw_cost,
        "gemini_fee": sweep.fee,
        "gemini_total_cost": gemini_total_cost,
        "total_capital": total_capital,
        "pnl_if_prop_hits": pnl_if_prop_hits,
        "pnl_if_prop_misses": pnl_if_prop_misses,
        "worst_case_locked_profit": worst_case,
        "roi_on_total_capital": worst_case / total_capital,
        "minimum_sportsbook_decimal": min_decimal,
    }


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
        retail_age_ms = now_ms - retail.received_ms
        gemini_age_ms = now_ms - depth_received_ms
        _stats["gemini_depth_validations"] += 1
        if not yes_bids:
            _stats["no_executable_gemini_depth"] += 1
            return

        best: tuple[NoSweep, dict[str, Decimal]] | None = None
        for qty in _candidate_quantities(yes_bids):
            sweep = buy_no_sweep_from_yes_bids(yes_bids, qty)
            if sweep is None:
                continue
            hedge = _hedge_payload(retail, sweep)
            if hedge is None:
                continue
            if best is None or hedge["worst_case_locked_profit"] > best[1]["worst_case_locked_profit"]:
                best = (sweep, hedge)

        if best is None:
            _stats["depth_revalidated_nonpositive"] += 1
            return

        sweep, hedge = best
        locked_profit = hedge["worst_case_locked_profit"]
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
            "sportsbook_stake": str(hedge["sportsbook_stake"]),
            "sportsbook_profit_if_hits": str(hedge["sportsbook_profit"]),
            "sportsbook_total_return_if_hits": str(hedge["sportsbook_total_return"]),
            "gemini_symbol": symbol,
            "gemini_outcome": "NO",
            "gemini_depth_basis": "PUBLIC_L2_YES_SPACE_DEPTH10_100MS",
            "gemini_best_yes_bid": str(max(p for p, _ in yes_bids)),
            "gemini_best_yes_ask": None if not yes_asks else str(min(p for p, _ in yes_asks)),
            "gemini_no_best_ask": str(ONE - max(p for p, _ in yes_bids)),
            "gemini_worst_no_limit_price": str(sweep.worst_limit_price),
            "gemini_fill_levels": [
                {"no_price": str(fill.no_price), "quantity": str(fill.quantity)}
                for fill in sweep.fills
            ],
            "gemini_raw_cost": str(hedge["gemini_raw_cost"]),
            "gemini_conservative_fee": str(hedge["gemini_fee"]),
            "gemini_total_cost": str(hedge["gemini_total_cost"]),
            "matched_contracts_and_payout": str(hedge["quantity"]),
            "total_capital": str(hedge["total_capital"]),
            "pnl_if_prop_hits": str(hedge["pnl_if_prop_hits"]),
            "pnl_if_prop_misses": str(hedge["pnl_if_prop_misses"]),
            "worst_case_locked_profit": str(locked_profit),
            "roi_on_total_capital": str(hedge["roi_on_total_capital"]),
            "minimum_sportsbook_decimal_for_positive_lock": str(hedge["minimum_sportsbook_decimal"]),
            "snapshot_candidate_profit_per_1": str(snapshot_profit),
            "retail_age_ms_at_validation": retail_age_ms,
            "gemini_depth_age_ms": gemini_age_ms,
            "gemini_snapshot_roundtrip_ms": now_ms - started_ms,
            "price_semantics_gate": "L2_DEPTH_SWEEP_REQUIRED",
            "settlement_blocker": "FANDUEL_VS_GEMINI_SUSPENSION_RESCHEDULE_RULES_NOT_IDENTICAL",
            "execution_eligible": False,
            "manual_actionable": False,
            "read_only": True,
            "order_entry": False,
        }
        logger.info("PROP_TRADE_HUNTER_DEPTH_POSITIVE %s", json.dumps(payload, separators=(",", ":"), default=str))


def _schedule_validation(symbol: str, retail: exact.RetailProp, snapshot_profit: Decimal) -> None:
    key = (symbol, retail.book)
    now_ms = int(time.time() * 1000)
    if now_ms - _last_validation_ms.get(key, 0) < VALIDATE_COOLDOWN_MS:
        _stats["cooldown_skips"] += 1
        return
    _last_validation_ms[key] = now_ms
    task = asyncio.create_task(_validate(symbol, retail, snapshot_profit), name=f"prop_depth_validate_{len(_validation_tasks)}")
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
        logger.info("PROP_TRADE_HUNTER_DEPTH_SUMMARY %s", json.dumps({
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
    hb = asyncio.create_task(_hunter_heartbeat(), name="trade_hunter_depth_heartbeat")
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
