"""Hardened immediate q=1 same-Gemini taker/taker execution.

Adds unique/recoverable client IDs, fail-closed handling for UNKNOWN FOK state, and
startup reconciliation for an interrupted taker pair. A first-leg fill is never
classified as zero-fill merely because transport state is ambiguous.
"""
from __future__ import annotations

from decimal import Decimal
import os
import time
from typing import Any, Mapping

from src import gemini_same_venue_crypto_budget as budget
from src import gemini_same_venue_crypto_maker_q1_wsfirst as live
from src import gemini_same_venue_crypto_taker_q1 as base
from src.gemini_kalshi_execution_shadow import gemini_predictions_taker_fee
from src.gemini_kalshi_probe_transport import GeminiTradingSession

D = Decimal
ONE = D("1")
TARGET = D("0.03")
TRADE_PREFIX = "crypto-gem-tt-"
CLIENT_PREFIX = "cgtt-"
_SEQ = 0
_RUN_NONCE = f"{int(time.time() * 1000):x}"[-6:]


def _next_ids() -> tuple[str, str, str]:
    global _SEQ
    _SEQ += 1
    cid = live.base._clean_id(os.getenv(live.base.CANARY_ID_ENV, ""))[:8]
    key = f"{cid}-{_RUN_NONCE}-{_SEQ:03d}"
    return f"{TRADE_PREFIX}{key}"[:64], f"{CLIENT_PREFIX}{key}-1"[:36], f"{CLIENT_PREFIX}{key}-2"[:36]


def _ids_for_trade(trade_id: str) -> tuple[str, str]:
    key = trade_id[len(TRADE_PREFIX):] if trade_id.startswith(TRADE_PREFIX) else trade_id
    return f"{CLIENT_PREFIX}{key}-1"[:36], f"{CLIENT_PREFIX}{key}-2"[:36]


def _row_fill(row: Mapping[str, Any] | None) -> D:
    return live._filled(row)


def _terminal_zero(status: str, qty: D) -> bool:
    return status in {"CANCELED", "CANCELLED", "REJECTED"} and qty == 0


async def reconcile_unresolved_taker() -> bool | None:
    """Return True if a prior taker reservation is safely cleared, False if it must block,
    and None when the unresolved crypto row belongs to another strategy.
    """
    rows = await budget.trades()
    blocked = budget.unresolved_crypto(rows)
    if blocked is None:
        return True
    if str(blocked.get("strategy") or "").lower() != base.STRATEGY:
        return None
    trade_id = str(blocked.get("trade_id") or "")
    market = str(blocked.get("market") or "")
    symbols = market.split("|")
    if not trade_id.startswith(TRADE_PREFIX) or len(symbols) != 2 or not all(symbols):
        live.emit("TAKER_STARTUP_RECOVERY_BAD_LEDGER_HARD_HALT", trade_id=trade_id, market=market,
                  order_entry=False, realized=False)
        return False
    c1, c2 = _ids_for_trade(trade_id)
    low_symbol, high_symbol = symbols
    r1, e1 = await live._bounded_lookup(c1, low_symbol, prefer_terminal=True)
    r2, e2 = await live._bounded_lookup(c2, high_symbol, prefer_terminal=True)
    q1, q2 = _row_fill(r1), _row_fill(r2)
    s1, s2 = live._status(r1), live._status(r2)
    candidate = {
        "event_ticker": str(blocked.get("asset") or "CRYPTO"),
        "low_symbol": low_symbol,
        "high_symbol": high_symbol,
        "source": (blocked.get("metadata") or {}).get("source") if isinstance(blocked.get("metadata"), Mapping) else None,
        "expiry": (blocked.get("metadata") or {}).get("expiry") if isinstance(blocked.get("metadata"), Mapping) else None,
    }
    if q1 == ONE and q2 == ONE:
        p1 = D(str((r1 or {}).get("avgExecutionPrice") or "0"))
        p2 = D(str((r2 or {}).get("avgExecutionPrice") or "0"))
        f1 = gemini_predictions_taker_fee(p1, ONE) if p1 > 0 else D("0.01")
        f2 = gemini_predictions_taker_fee(p2, ONE) if p2 > 0 else D("0.01")
        locked = ONE - p1 - p2 - f1 - f2 if p1 > 0 and p2 > 0 else D("0")
        await base.result(candidate, trade_id, "BOTH_FILLED_RECONCILED_NOT_SETTLED", 2, {
            "first_client_id": c1, "second_client_id": c2,
            "first_fill_price": str(p1), "second_fill_price": str(p2),
            "locked_profit_before_settlement": str(locked), "settlement_pending": True,
            "counts_as_realized_arb": False, "startup_reconciled": True,
        })
        live.emit("TAKER_STARTUP_RECOVERY_BOTH_FILLED", trade_id=trade_id, locked_profit_before_settlement=str(locked),
                  order_entry=False, realized=False)
        return False
    if q1 > 0 or q2 > 0:
        if q1 > 0 and q2 == 0:
            flatten = await live.base._flatten(low_symbol, "YES", trade_id)
            filled_leg = "LOW_YES"
        elif q2 > 0 and q1 == 0:
            flatten = await live.base._flatten(high_symbol, "NO", trade_id)
            filled_leg = "HIGH_NO"
        else:
            flatten = {"status": "AMBIGUOUS_MULTI_FILL"}
            filled_leg = "BOTH_PARTIAL_OR_NON_Q1"
        await base.result(candidate, trade_id, "INTERRUPTED_TAKER_PAIR_FLATTENED_HARD_HALT", 1, {
            "first_client_id": c1, "second_client_id": c2, "q1": str(q1), "q2": str(q2),
            "filled_leg": filled_leg, "flatten": flatten, "recovery_errors": (e1 + e2)[-12:],
        })
        live.emit("TAKER_STARTUP_RECOVERY_ONE_LEG_FILL", trade_id=trade_id, q1=str(q1), q2=str(q2),
                  flatten=flatten, order_entry=False, realized=False)
        return False
    # If one FOK was never sent, its row may legitimately be absent. Release only when
    # every row that exists is authoritative terminal zero-fill and no row shows active/unknown.
    bad = []
    for client, row, status, qty in ((c1, r1, s1, q1), (c2, r2, s2, q2)):
        if row is not None and not _terminal_zero(status, qty):
            bad.append({"client": client, "status": status, "qty": str(qty)})
    if not bad and (r1 is not None or r2 is not None):
        await base.release(candidate, trade_id, "STARTUP_AUTHORITATIVE_TAKER_ZERO_FILL")
        live.emit("TAKER_STARTUP_RECOVERY_RELEASED_ZERO_FILL", trade_id=trade_id, first_status=s1,
                  second_status=s2, order_entry=False, realized=False)
        return True
    live.emit("TAKER_STARTUP_RECOVERY_AMBIGUOUS_HARD_HALT", trade_id=trade_id, first_status=s1,
              second_status=s2, q1=str(q1), q2=str(q2), errors=(e1 + e2)[-12:], bad=bad,
              order_entry=False, realized=False)
    return False


async def execute(candidate: dict[str, Any]) -> dict[str, Any]:
    trade_id, c1, c2 = _next_ids()
    await base.reserve(candidate, trade_id)
    reserved = True
    queue = __import__("asyncio").Queue(maxsize=1000)
    base.live.base._book_stream = base.depth._depth_book_stream
    asyncio = __import__("asyncio")
    tasks = [asyncio.create_task(base.live.base._book_stream(str(candidate["low_symbol"]), "maker", queue)),
             asyncio.create_task(base.live.base._book_stream(str(candidate["high_symbol"]), "hedge", queue))]
    first_filled: tuple[dict[str, Any], Any] | None = None
    try:
        low_top, high_top = await base.live.base._fresh_pair(queue, timeout=3.0)
        _, _, low_yes, low_depth = base.live.base._outcome_top(low_top, "YES")
        _, _, high_no, high_depth = base.live.base._outcome_top(high_top, "NO")
        locked = base._taker_profit(low_yes, high_no)
        if low_depth < ONE or high_depth < ONE or locked < TARGET:
            await base.release(candidate, trade_id, "FRESH_TAKER_PAIR_LOST")
            reserved = False
            return {"status": "FRESH_TAKER_PAIR_LOST", "order_entry": False, "locked_profit": str(locked), "realized": False}
        legs = [
            {"name": "LOW_YES", "symbol": candidate["low_symbol"], "outcome": "YES", "price": low_yes, "depth": low_depth, "client": c1},
            {"name": "HIGH_NO", "symbol": candidate["high_symbol"], "outcome": "NO", "price": high_no, "depth": high_depth, "client": c2},
        ]
        # Harder/shallow leg first, but client IDs remain tied to semantic legs so startup recovery is deterministic.
        legs.sort(key=lambda x: (x["depth"], x["price"]))
        session = GeminiTradingSession()
        try:
            await session.connect()
            executions: list[tuple[dict[str, Any], Any]] = []
            for idx, leg in enumerate(legs):
                base.live.emit("TAKER_TAKER_SUBMIT", trade_id=trade_id, leg=idx + 1, leg_name=leg["name"],
                               client_id=leg["client"], symbol=leg["symbol"], outcome=leg["outcome"],
                               limit=str(leg["price"]), order_entry=True, realized=False)
                execution = await session.place_fok({
                    "symbol": leg["symbol"], "side": "BUY", "type": "LIMIT", "timeInForce": "FOK",
                    "price": str(leg["price"]), "quantity": "1", "eventOutcome": leg["outcome"],
                    "clientOrderId": leg["client"],
                }, terminal_timeout=3.0)
                executions.append((leg, execution))
                qty = D(str(execution.filled_quantity or "0"))
                if execution.status == "FILLED" and qty == ONE:
                    if idx == 0:
                        first_filled = (leg, execution)
                    continue
                if idx == 0:
                    if execution.status in {"CANCELED", "CANCELLED", "REJECTED"} and qty == 0:
                        await base.release(candidate, trade_id, "FIRST_FOK_ZERO_FILL")
                        reserved = False
                        return {"status": "FIRST_FOK_ZERO_FILL", "order_entry": True,
                                "first": execution.as_dict(), "realized": False}
                    await base.result(candidate, trade_id, "FIRST_FOK_AMBIGUOUS_HARD_HALT", 0, {
                        "first": execution.as_dict(), "first_client_id": leg["client"]})
                    return {"status": "FIRST_FOK_AMBIGUOUS_HARD_HALT", "order_entry": True,
                            "first": execution.as_dict(), "realized": False}
                # Second leg did not fill after first definitely did. Flatten the first.
                assert first_filled is not None
                first_leg, first_exec = first_filled
                flatten = await live.base._flatten(str(first_leg["symbol"]), str(first_leg["outcome"]), trade_id)
                status = "SECOND_FOK_FAILED_FIRST_FLATTENED_HARD_HALT"
                await base.result(candidate, trade_id, status, 1, {
                    "first": first_exec.as_dict(), "second": execution.as_dict(), "flatten": flatten,
                    "first_client_id": first_leg["client"], "second_client_id": leg["client"]})
                return {"status": status, "order_entry": True, "first": first_exec.as_dict(),
                        "second": execution.as_dict(), "flatten": flatten, "realized": False}
        finally:
            await session.close()

        by_name = {leg["name"]: (leg, ex) for leg, ex in executions}
        low_leg, low_ex = by_name["LOW_YES"]
        high_leg, high_ex = by_name["HIGH_NO"]
        p1 = D(str(low_ex.average_fill_price or low_leg["price"]))
        p2 = D(str(high_ex.average_fill_price or high_leg["price"]))
        f1 = D(str(low_ex.fee_paid or gemini_predictions_taker_fee(p1, ONE)))
        f2 = D(str(high_ex.fee_paid or gemini_predictions_taker_fee(p2, ONE)))
        actual_locked = ONE - p1 - p2 - f1 - f2
        status = "BOTH_FILLED_RECONCILED_NOT_SETTLED"
        await base.result(candidate, trade_id, status, 2, {
            "low_yes_client_id": c1, "high_no_client_id": c2,
            "low_yes_fill_price": str(p1), "low_yes_fee": str(f1),
            "high_no_fill_price": str(p2), "high_no_fee": str(f2),
            "locked_profit_before_settlement": str(actual_locked), "settlement_pending": True,
            "counts_as_realized_arb": False,
        })
        return {"status": status, "order_entry": True, "locked_profit_before_settlement": str(actual_locked),
                "settlement_pending": True, "counts_as_realized_arb": False, "realized": False}
    except Exception as exc:
        # A transport exception after a definite first fill must flatten immediately.
        if first_filled is not None:
            first_leg, first_exec = first_filled
            try:
                flatten = await live.base._flatten(str(first_leg["symbol"]), str(first_leg["outcome"]), trade_id)
            except Exception as fexc:
                flatten = {"status": "FLATTEN_EXCEPTION", "error": type(fexc).__name__}
            await base.result(candidate, trade_id, "TAKER_EXCEPTION_AFTER_FIRST_FILL_HARD_HALT", 1, {
                "first": first_exec.as_dict(), "flatten": flatten, "error_type": type(exc).__name__,
                "error": str(exc)[:180]})
            return {"status": "TAKER_EXCEPTION_AFTER_FIRST_FILL_HARD_HALT", "order_entry": True,
                    "flatten": flatten, "realized": False}
        # Before a definite fill, ambiguity is not a zero-fill proof. Keep reservation.
        await base.result(candidate, trade_id, "TAKER_TAKER_HARD_HALT_EXCEPTION", 0, {
            "error_type": type(exc).__name__, "error": str(exc)[:180], "first_client_id": c1, "second_client_id": c2})
        return {"status": "TAKER_TAKER_HARD_HALT_EXCEPTION", "error": f"{type(exc).__name__}:{str(exc)[:220]}",
                "order_entry": True, "realized": False}
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def maybe_execute() -> dict[str, Any]:
    candidate, diag = await base.discover()
    if candidate is None:
        return {"status": "NO_TAKER_TAKER_3C", "diagnostic": diag, "order_entry": False, "realized": False}
    return await execute(candidate)
