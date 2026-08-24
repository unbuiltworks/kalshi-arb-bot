"""Autonomous q=1 maker->FOK executor for same-Gemini crypto HI ladders.

Only exact same-event HI threshold siblings with identical source + expiry are
eligible. For low strike L < high strike H, BUY YES(L)+BUY NO(H) has a deterministic
$1 payout floor before fees. The live lane further requires both contracts to trade
in whole-contract increments, a maker quote that remains >=3c/contract after a
one-hedge-tick adverse move and current Gemini maker+taker fees, and durable global
crypto exposure reservation before mutation.

The service rotates confirmed zero-fill maker attempts. The first actual maker fill
freezes the campaign, cancels any remainder, submits exactly q=1 sibling FOK, records
FILLED_NOT_SETTLED, and exits. Any ambiguity or failed hedge flattens where possible
and hard-stops. Nothing is realized until settlement reconciliation.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import json
import os
import time
from typing import Any, Mapping
import uuid

import httpx
import websockets

from src import gemini_same_venue_crypto_budget as budget
from src import gemini_same_venue_crypto_ladder_scan as scan
from src.gemini_kalshi_execution_shadow import (
    TopOfBook,
    gemini_predictions_maker_fee,
    gemini_predictions_taker_fee,
)
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok
from src.gemini_kalshi_maker_shadow import MakerStats
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_kalshi_xrp_maker_live_canary import (
    GeminiMakerSession,
    MakerFill,
    _cancel_maker,
    _order_by_client,
)
from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker

D = Decimal
ONE = D("1")
TARGET = D("0.03")
MIN_AVAILABLE_USD = D("1.05")
MIN_SECONDS_TO_EXPIRY = 300.0
DISCOVERY_PAUSE_SECONDS = 15.0
REST_SECONDS = 90.0
MAX_BOOK_AGE_MS = 500
LIVE_ENV = "GEMINI_SAME_VENUE_LADDER_LIVE_Q1"
CANARY_ID_ENV = "GEMINI_SAME_VENUE_LADDER_CANARY_ID"
CANARY_ARM_ENV = "GEMINI_SAME_VENUE_LADDER_CANARY_ARM"


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_SAME_VENUE_LADDER_Q1 " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    cid = os.getenv(CANARY_ID_ENV, "").strip()
    arm = os.getenv(CANARY_ARM_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and cid and arm == cid)


def _clean_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:40]
    return safe or "disarmed"


def _qty(contract: Mapping[str, Any], name: str, default: str = "1") -> D:
    value = contract.get(name)
    if value is None:
        snake = "quantity_" + ("minimum" if name == "quantityMinimum" else "increment")
        value = contract.get(snake)
    return D(str(value if value is not None else default))


def whole_contract_only(contract: Mapping[str, Any]) -> bool:
    return _qty(contract, "quantityMinimum") == ONE and _qty(contract, "quantityIncrement") == ONE


def _contract_map(events: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        for contract in scan.ladder_contracts(event):
            out[scan._symbol(contract)] = dict(contract)
    return out


def locked_profit(maker_price: D, hedge_price: D) -> D:
    return (
        ONE - maker_price - hedge_price
        - gemini_predictions_maker_fee(maker_price, ONE)
        - gemini_predictions_taker_fee(hedge_price, ONE)
    )


def robust_frontier(*, maker_ask: D | None, observed_hedge_ask: D,
                    maker_tick: D, hedge_tick: D) -> tuple[D, D, D] | None:
    """Highest post-only maker quote preserving >=3c after one adverse hedge tick."""
    hedge_limit = min(D("0.99"), observed_hedge_ask + hedge_tick)
    if not (D("0") < hedge_limit < ONE):
        return None
    cap = (maker_ask - maker_tick) if maker_ask is not None else (ONE - maker_tick)
    if cap <= 0:
        return None
    steps = (cap / maker_tick).to_integral_value(rounding=ROUND_FLOOR)
    while steps > 0:
        quote = steps * maker_tick
        profit = locked_profit(quote, hedge_limit)
        if quote > 0 and profit >= TARGET:
            return quote, hedge_limit, profit
        steps -= 1
    return None


@dataclass(frozen=True)
class Candidate:
    event_ticker: str
    event_title: str
    expiry: str
    seconds_to_expiry: float
    source: str
    low_symbol: str
    low_strike: str
    high_symbol: str
    high_strike: str
    maker_symbol: str
    maker_outcome: str
    hedge_symbol: str
    hedge_outcome: str
    maker_quote: D
    maker_tick: D
    maker_ask: D | None
    existing_maker_bid: D
    hedge_observed_ask: D
    hedge_limit: D
    hedge_tick: D
    hedge_depth: D
    locked_profit: D
    action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_ticker": self.event_ticker,
            "event_title": self.event_title,
            "expiry": self.expiry,
            "seconds_to_expiry": self.seconds_to_expiry,
            "source": self.source,
            "low_symbol": self.low_symbol,
            "low_strike": self.low_strike,
            "high_symbol": self.high_symbol,
            "high_strike": self.high_strike,
            "maker_symbol": self.maker_symbol,
            "maker_outcome": self.maker_outcome,
            "hedge_symbol": self.hedge_symbol,
            "hedge_outcome": self.hedge_outcome,
            "maker_quote": str(self.maker_quote),
            "maker_tick": str(self.maker_tick),
            "maker_ask": None if self.maker_ask is None else str(self.maker_ask),
            "existing_maker_bid": str(self.existing_maker_bid),
            "hedge_observed_ask": str(self.hedge_observed_ask),
            "hedge_limit": str(self.hedge_limit),
            "hedge_tick": str(self.hedge_tick),
            "hedge_depth": str(self.hedge_depth),
            "locked_profit": str(self.locked_profit),
            "action": self.action,
            "classification": budget.CLASSIFICATION,
            "guaranteed_payout_floor_before_fees": "1",
            "realized": False,
        }


def candidate_from_row(row: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Any]]) -> Candidate | None:
    maker_symbol = str(row["maker_symbol"])
    hedge_symbol = str(row["hedge_symbol"])
    maker = contracts.get(maker_symbol)
    hedge = contracts.get(hedge_symbol)
    if not maker or not hedge or not whole_contract_only(maker) or not whole_contract_only(hedge):
        return None
    maker_tick = scan._tick(maker)
    hedge_tick = scan._tick(hedge)
    if maker_tick is None or hedge_tick is None:
        return None
    maker_ask = D(str(row["maker_ask"])) if row.get("maker_ask") is not None else None
    existing_bid = D(str(row.get("existing_maker_bid") or "0"))
    hedge_ask = D(str(row["hedge_ask"]))
    hedge_depth = D(str(row["hedge_depth"]))
    if hedge_depth < ONE:
        return None
    robust = robust_frontier(
        maker_ask=maker_ask,
        observed_hedge_ask=hedge_ask,
        maker_tick=maker_tick,
        hedge_tick=hedge_tick,
    )
    if robust is None:
        return None
    quote, hedge_limit, profit = robust
    action = scan.maker_action(existing_bid=existing_bid, safe_quote=quote, tick=maker_tick)
    if action not in {"SEED_FIRST_BID", "IMPROVE_TO_SAFE_FRONTIER", "JOIN_SAFE_BID"}:
        return None
    secs = float(row.get("seconds_to_expiry") or 0.0)
    if secs <= MIN_SECONDS_TO_EXPIRY:
        return None
    return Candidate(
        event_ticker=str(row["event_ticker"]),
        event_title=str(row.get("event_title") or ""),
        expiry=str(row["expiry"]),
        seconds_to_expiry=secs,
        source=str(row["source"]),
        low_symbol=str(row["low_symbol"]),
        low_strike=str(row["low_strike"]),
        high_symbol=str(row["high_symbol"]),
        high_strike=str(row["high_strike"]),
        maker_symbol=maker_symbol,
        maker_outcome=str(row["maker_outcome"]),
        hedge_symbol=hedge_symbol,
        hedge_outcome=str(row["hedge_outcome"]),
        maker_quote=quote,
        maker_tick=maker_tick,
        maker_ask=maker_ask,
        existing_maker_bid=existing_bid,
        hedge_observed_ask=hedge_ask,
        hedge_limit=hedge_limit,
        hedge_tick=hedge_tick,
        hedge_depth=hedge_depth,
        locked_profit=profit,
        action=action,
    )


async def discover() -> tuple[Candidate | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        contracts = _contract_map(events)
        # Fetch only whole-contract ladder symbols; fractional candidates can never
        # satisfy our first-fill guarantee because Gemini rounds fees to whole cents.
        whole_symbols = {symbol for symbol, c in contracts.items() if whole_contract_only(c)}
        books, statuses = await scan._books(http, whole_symbols)
    # score() expects any missing fractional books to be absent and simply skips them.
    actionable, joins, telemetry = scan.score(events, books)
    rows = actionable + joins
    candidates = [c for row in rows if (c := candidate_from_row(row, contracts)) is not None]
    candidates.sort(key=lambda c: (
        0 if c.seconds_to_expiry <= 86400 else 1,
        (c.maker_ask - c.maker_quote) if c.maker_ask is not None else D("1"),
        c.seconds_to_expiry,
        -c.hedge_depth,
    ))
    diag = {
        **telemetry,
        "whole_contract_symbols": len(whole_symbols),
        "whole_books_ok": sum(1 for s in whole_symbols if statuses.get(s) == 200),
        "robust_whole_contract_candidates": len(candidates),
        "top": [c.as_dict() for c in candidates[:5]],
        "read_only": not live_armed(),
        "order_entry": False,
        "realized": False,
    }
    return (candidates[0] if candidates else None), diag


async def _book_stream(symbol: str, kind: str, queue: asyncio.Queue) -> None:
    backoff = 0.25
    while True:
        try:
            async with websockets.connect(GEMINI_WS, open_timeout=5.0) as ws:
                await ws.send(json.dumps({
                    "id": f"same-gemini-{kind}",
                    "method": "SUBSCRIBE",
                    "params": [f"{symbol}@bookTicker"],
                }))
                backoff = 0.25
                async for raw in ws:
                    received_ms = int(time.time() * 1000)
                    payload = json.loads(raw)
                    if payload.get("status") is not None:
                        if int(payload.get("status")) >= 400:
                            raise RuntimeError(f"Gemini book subscription failed: {payload}")
                        continue
                    if str(payload.get("s") or "").lower() != symbol.lower():
                        continue
                    try:
                        top = parse_gemini_book_ticker(payload, received_ms=received_ms)
                    except (KeyError, ValueError, TypeError):
                        continue
                    await queue.put((kind, top))
            raise RuntimeError(f"Gemini public WS ended for {symbol}")
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(backoff)
            backoff = min(5.0, backoff * 2)


def _outcome_top(top: TopOfBook, outcome: str) -> tuple[D, D, D, D]:
    if outcome == "YES":
        return D(top.yes_bid), D(top.yes_bid_size), D(top.yes_ask), D(top.yes_ask_size)
    return D(top.no_bid), D(top.yes_ask_size), D(top.no_ask), D(top.yes_bid_size)


async def _fresh_pair(queue: asyncio.Queue, *, maker_top: TopOfBook | None = None,
                      hedge_top: TopOfBook | None = None, timeout: float = 5.0) -> tuple[TopOfBook, TopOfBook]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kind, top = await asyncio.wait_for(queue.get(), timeout=min(0.5, max(0.05, deadline-time.monotonic())))
        except asyncio.TimeoutError:
            continue
        if kind == "maker":
            maker_top = top
        elif kind == "hedge":
            hedge_top = top
        if maker_top is None or hedge_top is None:
            continue
        now_ms = int(time.time() * 1000)
        if max(now_ms-int(maker_top.ts_ms), now_ms-int(hedge_top.ts_ms)) <= MAX_BOOK_AGE_MS:
            return maker_top, hedge_top
    raise RuntimeError("no fresh same-Gemini maker/hedge books")


def _live_floor(candidate: Candidate, maker_top: TopOfBook, hedge_top: TopOfBook) -> tuple[bool, dict[str, str]]:
    _, _, maker_ask, _ = _outcome_top(maker_top, candidate.maker_outcome)
    _, _, hedge_ask, hedge_depth = _outcome_top(hedge_top, candidate.hedge_outcome)
    robust = robust_frontier(
        maker_ask=maker_ask,
        observed_hedge_ask=hedge_ask,
        maker_tick=candidate.maker_tick,
        hedge_tick=candidate.hedge_tick,
    )
    current_profit = locked_profit(candidate.maker_quote, min(D("0.99"), hedge_ask + candidate.hedge_tick))
    ok = bool(hedge_depth >= ONE and robust is not None and current_profit >= TARGET)
    return ok, {
        "maker_ask": str(maker_ask),
        "hedge_ask": str(hedge_ask),
        "hedge_depth": str(hedge_depth),
        "one_tick_cushioned_locked": str(current_profit),
    }


async def _flatten(symbol: str, outcome: str, trade_id: str) -> dict[str, Any]:
    session = GeminiTradingSession()
    try:
        await session.connect()
        result = await session.place_fok({
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "timeInForce": "IOC",
            "quantity": "1",
            "eventOutcome": outcome,
            "clientOrderId": f"{trade_id}-flat"[:36],
        }, terminal_timeout=3.0)
        return result.as_dict()
    finally:
        await session.close()


async def _attempt(candidate: Candidate, attempt_no: int) -> dict[str, Any]:
    cid = _clean_id(os.getenv(CANARY_ID_ENV, ""))
    trade_id = f"crypto-gem-ladder-{cid}-{attempt_no:03d}"[:64]
    cdict = candidate.as_dict()
    await budget.reserve(candidate=cdict, trade_id=trade_id)
    reserved = True
    maker = GeminiMakerSession()
    public_tasks: list[asyncio.Task] = []
    try:
        await maker.connect()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        public_tasks = [
            asyncio.create_task(_book_stream(candidate.maker_symbol, "maker", queue)),
            asyncio.create_task(_book_stream(candidate.hedge_symbol, "hedge", queue)),
        ]
        maker_top, hedge_top = await _fresh_pair(queue)
        ok, live = _live_floor(candidate, maker_top, hedge_top)
        if not ok:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="PRE_POST_FLOOR_LOST")
            reserved = False
            return {"status": "PRE_POST_FLOOR_LOST", "order_entry": False, "live": live}

        # Recompute the robust frontier from the exact fresh books. Never send the
        # older discovery quote if a more restrictive safe price is now required.
        maker_bid, _, maker_ask, _ = _outcome_top(maker_top, candidate.maker_outcome)
        _, _, hedge_ask, hedge_depth = _outcome_top(hedge_top, candidate.hedge_outcome)
        robust = robust_frontier(
            maker_ask=maker_ask,
            observed_hedge_ask=hedge_ask,
            maker_tick=candidate.maker_tick,
            hedge_tick=candidate.hedge_tick,
        )
        if robust is None:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="NO_FRESH_ROBUST_FRONTIER")
            reserved = False
            return {"status": "NO_FRESH_ROBUST_FRONTIER", "order_entry": False}
        quote, hedge_limit, modeled = robust
        action = scan.maker_action(existing_bid=maker_bid, safe_quote=quote, tick=candidate.maker_tick)
        if action not in {"SEED_FIRST_BID", "IMPROVE_TO_SAFE_FRONTIER", "JOIN_SAFE_BID"} or hedge_depth < ONE:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="FRESH_TOP_NOT_MAKER_ACTIONABLE")
            reserved = False
            return {"status": "FRESH_TOP_NOT_MAKER_ACTIONABLE", "order_entry": False, "action": action}
        cdict.update({"maker_quote": str(quote), "hedge_limit": str(hedge_limit), "locked_profit": str(modeled)})

        client_id = f"{trade_id}-mk"[:36]
        params = {
            "symbol": candidate.maker_symbol,
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "MOC",
            "price": str(quote),
            "quantity": "1",
            "eventOutcome": candidate.maker_outcome,
            "clientOrderId": client_id,
        }
        ack = await maker.request("order.place", params, timeout=4.0)
        if int(ack.get("status", 0)) != 200:
            await budget.release(candidate=cdict, trade_id=trade_id, reason="MAKER_REJECTED_ZERO_FILL")
            reserved = False
            return {"status": "MAKER_REJECTED_ZERO_FILL", "order_entry": True, "ack_status": int(ack.get("status", 0))}

        row = None
        for _ in range(12):
            row = await _order_by_client(client_id, candidate.maker_symbol)
            if row is not None:
                break
            await asyncio.sleep(0.05)
        emit("MAKER_POSTED", trade_id=trade_id, maker_symbol=candidate.maker_symbol,
             maker_outcome=candidate.maker_outcome, maker_price=str(quote),
             hedge_symbol=candidate.hedge_symbol, hedge_outcome=candidate.hedge_outcome,
             hedge_limit_reserved=str(hedge_limit), locked_profit=str(modeled), order_entry=True)

        fills = MakerFill()
        maker_top = hedge_top = None
        cancel_reason = "REST_TIMEOUT"
        started = time.monotonic()
        while time.monotonic() - started < REST_SECONDS:
            public_get = asyncio.create_task(queue.get())
            fill_get = asyncio.create_task(maker.events.get())
            done, pending = await asyncio.wait({public_get, fill_get}, timeout=0.25, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if not done:
                continue
            for task in done:
                item = task.result()
                if isinstance(item, tuple) and len(item) == 2:
                    kind, top = item
                    if kind == "maker":
                        maker_top = top
                    elif kind == "hedge":
                        hedge_top = top
                    if maker_top is not None and hedge_top is not None:
                        now_ms = int(time.time() * 1000)
                        if max(now_ms-int(maker_top.ts_ms), now_ms-int(hedge_top.ts_ms)) > MAX_BOOK_AGE_MS:
                            cancel_reason = "BOOK_FRESHNESS_LOST"
                            break
                        ok, _ = _live_floor(candidate, maker_top, hedge_top)
                        # Evaluate the actual posted quote, not candidate discovery price.
                        _, _, h_ask, h_depth = _outcome_top(hedge_top, candidate.hedge_outcome)
                        cushion_profit = locked_profit(quote, min(D("0.99"), h_ask + candidate.hedge_tick))
                        if not ok or h_depth < ONE or cushion_profit < TARGET:
                            cancel_reason = "HEDGE_FLOOR_DROPPED"
                            break
                elif isinstance(item, Mapping):
                    if str(item.get("c") or "") != client_id:
                        continue
                    before = fills.quantity
                    fills.add(item)
                    if fills.quantity > before:
                        cancel_reason = "FIRST_FILL_FREEZE"
                        break
            if cancel_reason != "REST_TIMEOUT":
                break

        cancel = await _cancel_maker(maker, row=row)
        await asyncio.sleep(0.05)
        while not maker.events.empty():
            event = maker.events.get_nowait()
            if isinstance(event, Mapping) and str(event.get("c") or "") == client_id:
                fills.add(event)
        final_row = await _order_by_client(client_id, candidate.maker_symbol)
        rest_filled = D(str((final_row or {}).get("filledQuantity") or "0"))
        filled = max(rest_filled, fills.quantity)

        if filled <= 0:
            if not cancel.get("ok"):
                status = "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT"
                await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=0, metadata={"cancel": cancel})
                return {"status": status, "order_entry": True, "cancel": cancel}
            await budget.release(candidate=cdict, trade_id=trade_id, reason=cancel_reason)
            reserved = False
            return {"status": "MAKER_CANCELED_ZERO_FILL", "order_entry": True, "cancel_reason": cancel_reason}

        if filled != ONE:
            status = "NON_WHOLE_MAKER_FILL_HARD_HALT"
            flatten = await _flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                          metadata={"filled_quantity": str(filled), "flatten": flatten})
            return {"status": status, "filled_quantity": str(filled), "flatten": flatten, "order_entry": True}

        # Get an exact current sibling top after the confirmed whole maker fill.
        maker_top, hedge_top = await _fresh_pair(queue, maker_top=maker_top, hedge_top=hedge_top, timeout=2.0)
        _, _, live_hedge_ask, live_hedge_depth = _outcome_top(hedge_top, candidate.hedge_outcome)
        if live_hedge_depth < ONE:
            flatten = await _flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
            status = "HEDGE_DEPTH_LOST_FLATTENED_HARD_HALT"
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                          metadata={"live_hedge_ask": str(live_hedge_ask), "flatten": flatten})
            return {"status": status, "flatten": flatten, "order_entry": True}

        # FOK exactly at current top. If price moves away, FOK cancels rather than
        # overpaying beyond the modeled >=3c floor.
        actual_maker_fee = fills.fees if fills.executions > 0 else gemini_predictions_maker_fee(quote, ONE)
        hedge_fee = gemini_predictions_taker_fee(live_hedge_ask, ONE)
        prehedge_locked = ONE - quote - live_hedge_ask - actual_maker_fee - hedge_fee
        if prehedge_locked < TARGET:
            flatten = await _flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
            status = "POST_FILL_EDGE_BELOW_3C_FLATTENED_HARD_HALT"
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                          metadata={"prehedge_locked": str(prehedge_locked), "flatten": flatten})
            return {"status": status, "prehedge_locked": str(prehedge_locked), "flatten": flatten, "order_entry": True}

        hedge_session = GeminiTradingSession()
        try:
            await hedge_session.connect()
            hedge = await hedge_session.place_fok({
                "symbol": candidate.hedge_symbol,
                "side": "BUY",
                "type": "LIMIT",
                "timeInForce": "FOK",
                "price": str(live_hedge_ask),
                "quantity": "1",
                "eventOutcome": candidate.hedge_outcome,
                "clientOrderId": f"{trade_id}-hg"[:36],
            }, terminal_timeout=3.0)
        finally:
            await hedge_session.close()

        if hedge.status != "FILLED" or D(str(hedge.filled_quantity or "0")) != ONE:
            flatten = await _flatten(candidate.maker_symbol, candidate.maker_outcome, trade_id)
            status = "SIBLING_FOK_FAILED_MAKER_FLATTENED_HARD_HALT"
            await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=1,
                                          metadata={"hedge": hedge.as_dict(), "flatten": flatten})
            return {"status": status, "hedge": hedge.as_dict(), "flatten": flatten, "order_entry": True}

        hedge_price = D(str(hedge.average_fill_price or live_hedge_ask))
        hedge_fee_actual = D(str(hedge.fee_paid or gemini_predictions_taker_fee(hedge_price, ONE)))
        maker_notional = fills.notional if fills.notional > 0 else quote
        maker_fee_actual = fills.fees if fills.executions > 0 else gemini_predictions_maker_fee(quote, ONE)
        locked = ONE - maker_notional - maker_fee_actual - hedge_price - hedge_fee_actual
        status = "BOTH_FILLED_RECONCILED_NOT_SETTLED"
        await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=2, metadata={
            "maker_fill_price": str(maker_notional),
            "maker_fee": str(maker_fee_actual),
            "hedge_fill_price": str(hedge_price),
            "hedge_fee": str(hedge_fee_actual),
            "locked_profit_before_settlement": str(locked),
            "counts_as_realized_arb": False,
            "settlement_pending": True,
        })
        return {
            "status": status,
            "order_entry": True,
            "filled_quantity": "1",
            "locked_profit_before_settlement": str(locked),
            "counts_as_realized_arb": False,
            "realized": False,
        }
    except Exception as exc:
        status = "HARD_HALT_EXCEPTION"
        if reserved:
            try:
                await budget.execution_result(candidate=cdict, trade_id=trade_id, status=status, legs_filled=0,
                                              metadata={"error_type": type(exc).__name__, "error": str(exc)[:180]})
            except Exception:
                pass
        return {"status": status, "error": f"{type(exc).__name__}:{str(exc)[:220]}", "order_entry": False}
    finally:
        for task in public_tasks:
            task.cancel()
        await asyncio.gather(*public_tasks, return_exceptions=True)
        await maker.close()


async def run() -> dict[str, Any]:
    armed = live_armed()
    terms = await _gemini_terms_ok()
    available = await _gemini_available_usd()
    preflight = {
        "armed": armed,
        "terms_accepted": bool(terms),
        "funded_ge_1_05": available >= MIN_AVAILABLE_USD,
        "target_per_contract": str(TARGET),
        "whole_contract_only": True,
        "classification": budget.CLASSIFICATION,
        "order_entry": False,
        "realized": False,
    }
    if not terms:
        preflight["status"] = "TERMS_NOT_ACCEPTED"
        emit("RESULT", **preflight)
        return preflight
    if armed and available < MIN_AVAILABLE_USD:
        preflight["status"] = "INSUFFICIENT_GEMINI_AVAILABLE_USD"
        emit("RESULT", **preflight)
        return preflight

    if not armed:
        candidate, diag = await discover()
        out = {**preflight, "status": "READ_ONLY_READY" if candidate else "READ_ONLY_NO_CANDIDATE", "candidate": None if candidate is None else candidate.as_dict(), "diagnostic": diag}
        emit("RESULT", **out)
        return out

    # A fixed canary id is intentionally single-fill. Zero-fill attempts can rotate;
    # any executed/ambiguous result exits. Command Center blocks overlapping crypto.
    attempt = 0
    while True:
        candidate, diag = await discover()
        if candidate is None:
            emit("WAITING", **preflight, diagnostic=diag)
            await asyncio.sleep(DISCOVERY_PAUSE_SECONDS)
            continue
        attempt += 1
        emit("CANDIDATE", attempt=attempt, candidate=candidate.as_dict(), order_entry=False, realized=False)
        result = await _attempt(candidate, attempt)
        emit("ATTEMPT_RESULT", attempt=attempt, candidate=candidate.as_dict(), **result)
        if result.get("status") == "MAKER_CANCELED_ZERO_FILL":
            await asyncio.sleep(1.0)
            continue
        emit("RESULT", **preflight, attempt=attempt, candidate=candidate.as_dict(), result=result)
        return {**preflight, "attempt": attempt, "candidate": candidate.as_dict(), "result": result}


if __name__ == "__main__":
    asyncio.run(run())
