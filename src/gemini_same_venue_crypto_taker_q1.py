"""Immediate q=1 FOK/FOK execution for same-Gemini crypto HI ladders.

For adjacent thresholds L < H inside the exact same event/source/expiry,
BUY YES(L) + BUY NO(H) pays at least $1 before fees in every settlement state.
This lane executes only when fresh 100ms Gemini depth shows both q1 asks and exact
Gemini taker fees leave >=3c locked net. The harder/shallower leg is sent first; if
it fills and the sibling FOK does not, the first leg is flattened and the service
hard-stops. Nothing is counted realized before settlement.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import time
from typing import Any, Mapping

import httpx

from src import gemini_same_venue_crypto_budget as budget
from src import gemini_same_venue_crypto_ladder_scan as scan
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src import gemini_same_venue_crypto_maker_q1_depth as depth
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_fast_overlap import _gemini_active_crypto
from src.gemini_kalshi_probe_transport import GeminiTradingSession

D = Decimal
ONE = D("1")
TARGET = D("0.03")
STRATEGY = "crypto_gemini_ladder_taker_q1"
CLASSIFICATION = "SAME_GEMINI_EVENT_MONOTONE_HI_LADDER_TAKER_TAKER_STRUCTURAL_ARBITRAGE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "-" for ch in value)[:190]


async def _post(event: dict[str, Any]) -> None:
    await budget._post(event)


async def reserve(candidate: Mapping[str, Any], trade_id: str) -> None:
    rows = await budget.trades()
    blocked = budget.unresolved_crypto(rows)
    if blocked is not None:
        raise RuntimeError(f"unresolved LIVE crypto exposure blocks taker pair: {blocked.get('trade_id')}")
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-reserved"), "trade_id": trade_id,
        "event_type": "CAPITAL_RESERVED", "event_ts": _now(), "strategy": STRATEGY,
        "mode": "LIVE_PROBE", "market": f"{candidate['low_symbol']}|{candidate['high_symbol']}",
        "asset": str(candidate.get("event_ticker") or "CRYPTO"), "venues": ["gemini"],
        "side": "SAME_EVENT_FOK_YES_LOW_FOK_NO_HIGH", "status": "CAPITAL_RESERVED",
        "amount": "1", "legs_required": 2, "legs_filled": 0,
        "metadata": {"reserved_usd": "1", "q": 1, "classification": CLASSIFICATION,
                     "same_event": True, "same_source": True, "same_expiry": True,
                     "source": candidate.get("source"), "expiry": candidate.get("expiry"),
                     "modeled_locked_profit": candidate.get("locked_profit"), "realized": False},
    })


async def release(candidate: Mapping[str, Any], trade_id: str, reason: str) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:capital-released"), "trade_id": trade_id,
        "event_type": "CAPITAL_RELEASED", "event_ts": _now(), "strategy": STRATEGY,
        "mode": "LIVE_PROBE", "market": f"{candidate['low_symbol']}|{candidate['high_symbol']}",
        "asset": str(candidate.get("event_ticker") or "CRYPTO"), "venues": ["gemini"],
        "side": "SAME_EVENT_FOK_YES_LOW_FOK_NO_HIGH", "status": "CAPITAL_RELEASED",
        "amount": "0", "legs_required": 2, "legs_filled": 0,
        "metadata": {"reserved_usd": "0", "classification": CLASSIFICATION,
                     "release_reason": reason, "realized": False},
    })


async def result(candidate: Mapping[str, Any], trade_id: str, status: str, legs_filled: int,
                 metadata: Mapping[str, Any]) -> None:
    await _post({
        "event_id": _safe_id(f"{trade_id}:execution-result:{status}"), "trade_id": trade_id,
        "event_type": "EXECUTION_RESULT", "event_ts": _now(), "strategy": STRATEGY,
        "mode": "LIVE_PROBE", "market": f"{candidate['low_symbol']}|{candidate['high_symbol']}",
        "asset": str(candidate.get("event_ticker") or "CRYPTO"), "venues": ["gemini"],
        "side": "SAME_EVENT_FOK_YES_LOW_FOK_NO_HIGH", "status": status,
        "quantity": "1", "legs_required": 2, "legs_filled": legs_filled,
        "metadata": {"classification": CLASSIFICATION, "guaranteed_payout_floor_before_fees": "1",
                     "same_event": True, "same_source": True, "same_expiry": True,
                     "realized": False, **dict(metadata)},
    })


def _taker_profit(low_yes: D, high_no: D) -> D:
    return ONE - low_yes - high_no - gemini_predictions_taker_fee(low_yes, ONE) - gemini_predictions_taker_fee(high_no, ONE)


async def discover() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20.0) as http:
        events = await _gemini_active_crypto(http)
        strict = [(event, scan.ladder_contracts(event)) for event in events]
        symbols = {scan._symbol(c) for _, contracts in strict for c in contracts if live.base.whole_contract_only(c)}
        books, statuses = await scan._books(http, symbols)
    rows: list[dict[str, Any]] = []
    pair_count = 0
    for event, contracts0 in strict:
        contracts = [c for c in contracts0 if live.base.whole_contract_only(c)]
        if len(contracts) < 2:
            continue
        for low, high in zip(contracts, contracts[1:]):
            pair_count += 1
            ls, hs = scan._symbol(low), scan._symbol(high)
            lb, hb = books.get(ls), books.get(hs)
            if not isinstance(lb, Mapping) or not isinstance(hb, Mapping):
                continue
            lo = scan.outcome_book(scan.top(lb), "YES")
            hi = scan.outcome_book(scan.top(hb), "NO")
            la, ld = lo.get("ask"), lo.get("ask_qty")
            ha, hd = hi.get("ask"), hi.get("ask_qty")
            if not all(isinstance(x, D) for x in (la, ld, ha, hd)):
                continue
            assert isinstance(la, D) and isinstance(ld, D) and isinstance(ha, D) and isinstance(hd, D)
            if ld < ONE or hd < ONE:
                continue
            locked = _taker_profit(la, ha)
            if locked < TARGET:
                continue
            rows.append({
                "event_ticker": str(event.get("ticker") or ""), "event_title": str(event.get("title") or ""),
                "expiry": str(low.get("_expiry") or ""), "source": str(low.get("_source") or ""),
                "low_symbol": ls, "high_symbol": hs, "low_strike": str(low.get("_strike")),
                "high_strike": str(high.get("_strike")), "low_yes_ask": str(la), "low_yes_depth": str(ld),
                "high_no_ask": str(ha), "high_no_depth": str(hd), "locked_profit": str(locked),
                "event_volume": str(event.get("volume") or "0"), "event_liquidity": str(event.get("liquidity") or "0"),
                "classification": CLASSIFICATION, "realized": False,
            })
    rows.sort(key=lambda r: (-D(str(r["locked_profit"])), -min(D(str(r["low_yes_depth"])), D(str(r["high_no_depth"]))),
                             -D(str(r.get("event_liquidity") or "0")), -D(str(r.get("event_volume") or "0"))))
    diag = {"strict_adjacent_pairs": pair_count, "books_requested": len(symbols),
            "books_ok": sum(1 for s in symbols if statuses.get(s) == 200),
            "taker_taker_3c_candidates": len(rows), "top": rows[:5], "order_entry": False, "realized": False}
    live.emit("TAKER_TAKER_SCAN", **diag)
    return (rows[0] if rows else None), diag


async def execute(candidate: dict[str, Any]) -> dict[str, Any]:
    cid = live.base._clean_id(os.getenv(live.base.CANARY_ID_ENV, ""))
    trade_id = f"crypto-gem-tt-{cid}-{int(time.time()*1000)}"[:64]
    await reserve(candidate, trade_id)
    reserved = True
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    live.base._book_stream = depth._depth_book_stream
    tasks = [asyncio.create_task(live.base._book_stream(str(candidate["low_symbol"]), "maker", queue)),
             asyncio.create_task(live.base._book_stream(str(candidate["high_symbol"]), "hedge", queue))]
    try:
        low_top, high_top = await live.base._fresh_pair(queue, timeout=3.0)
        _, _, low_yes, low_depth = live.base._outcome_top(low_top, "YES")
        _, _, high_no, high_depth = live.base._outcome_top(high_top, "NO")
        locked = _taker_profit(low_yes, high_no)
        if low_depth < ONE or high_depth < ONE or locked < TARGET:
            await release(candidate, trade_id, "FRESH_TAKER_PAIR_LOST")
            reserved = False
            return {"status": "FRESH_TAKER_PAIR_LOST", "order_entry": False, "locked_profit": str(locked)}
        candidate.update({"low_yes_ask": str(low_yes), "low_yes_depth": str(low_depth),
                          "high_no_ask": str(high_no), "high_no_depth": str(high_depth), "locked_profit": str(locked)})
        legs = [
            {"name": "LOW_YES", "symbol": candidate["low_symbol"], "outcome": "YES", "price": low_yes, "depth": low_depth},
            {"name": "HIGH_NO", "symbol": candidate["high_symbol"], "outcome": "NO", "price": high_no, "depth": high_depth},
        ]
        # Send the shallower/harder leg first so a first fill is followed by the
        # deeper sibling. Both are FOK at the exact prevalidated limit.
        legs.sort(key=lambda x: (x["depth"], x["price"]))
        session = GeminiTradingSession()
        try:
            await session.connect()
            fills = []
            for idx, leg in enumerate(legs):
                client_id = f"{trade_id}-t{idx+1}"[:36]
                live.emit("TAKER_TAKER_SUBMIT", trade_id=trade_id, leg=idx+1, leg_name=leg["name"],
                          symbol=leg["symbol"], outcome=leg["outcome"], limit=str(leg["price"]), order_entry=True, realized=False)
                execution = await session.place_fok({
                    "symbol": leg["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
                    "price": str(leg["price"]), "quantity": "1", "eventOutcome": leg["outcome"],
                    "clientOrderId": client_id,
                }, terminal_timeout=3.0)
                fills.append((leg, execution))
                if execution.status != "FILLED" or D(str(execution.filled_quantity or "0")) != ONE:
                    if idx == 0:
                        await release(candidate, trade_id, "FIRST_FOK_ZERO_FILL")
                        reserved = False
                        return {"status": "FIRST_FOK_ZERO_FILL", "order_entry": True, "first": execution.as_dict(), "realized": False}
                    first_leg, first_exec = fills[0]
                    flatten = await live.base._flatten(str(first_leg["symbol"]), str(first_leg["outcome"]), trade_id)
                    status = "SECOND_FOK_FAILED_FIRST_FLATTENED_HARD_HALT"
                    await result(candidate, trade_id, status, 1, {"first": first_exec.as_dict(), "second": execution.as_dict(), "flatten": flatten})
                    return {"status": status, "order_entry": True, "first": first_exec.as_dict(), "second": execution.as_dict(), "flatten": flatten, "realized": False}
        finally:
            await session.close()

        (leg1, ex1), (leg2, ex2) = fills
        p1 = D(str(ex1.average_fill_price or leg1["price"]))
        p2 = D(str(ex2.average_fill_price or leg2["price"]))
        f1 = D(str(ex1.fee_paid or gemini_predictions_taker_fee(p1, ONE)))
        f2 = D(str(ex2.fee_paid or gemini_predictions_taker_fee(p2, ONE)))
        actual_locked = ONE - p1 - p2 - f1 - f2
        status = "BOTH_FILLED_RECONCILED_NOT_SETTLED"
        await result(candidate, trade_id, status, 2, {
            "first_leg": leg1["name"], "first_fill_price": str(p1), "first_fee": str(f1),
            "second_leg": leg2["name"], "second_fill_price": str(p2), "second_fee": str(f2),
            "locked_profit_before_settlement": str(actual_locked), "settlement_pending": True,
            "counts_as_realized_arb": False,
        })
        return {"status": status, "order_entry": True, "locked_profit_before_settlement": str(actual_locked),
                "settlement_pending": True, "counts_as_realized_arb": False, "realized": False}
    except Exception as exc:
        status = "TAKER_TAKER_HARD_HALT_EXCEPTION"
        if reserved:
            try:
                await result(candidate, trade_id, status, 0, {"error_type": type(exc).__name__, "error": str(exc)[:180]})
            except Exception:
                pass
        return {"status": status, "error": f"{type(exc).__name__}:{str(exc)[:220]}", "order_entry": False, "realized": False}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def maybe_execute() -> dict[str, Any]:
    candidate, diag = await discover()
    if candidate is None:
        return {"status": "NO_TAKER_TAKER_3C", "diagnostic": diag, "order_entry": False, "realized": False}
    return await execute(candidate)
