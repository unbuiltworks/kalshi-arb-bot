"""Safer one-shot Gemini BTC 5m q=1 maker canary.

This entrypoint keeps the corrected q=1 quantity-grid test from v2 and closes a
live-safety gap: q=1 orders on Gemini's 0.01 share grid can fill fractionally.
Any non-paired fill shape now attempts to flatten the exact observed quantity via
MARKET IOC. Incomplete flattening hard-stops with durable reservation retained.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import os
import time
import uuid
from typing import Any, Mapping

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v2 import corrected_preflight

D = Decimal


def _q(value: Any) -> D:
    return D(str(value or "0"))


def _qty_text(value: D) -> str:
    # Plain decimal notation; Gemini accepts quantities on the contract grid.
    return format(value.normalize(), "f")


async def flatten_exact(original_params: dict[str, Any], quantity: D) -> dict[str, Any]:
    quantity = _q(quantity)
    if not (D("0") < quantity <= base.ONE):
        return {
            "requested_quantity": str(quantity),
            "filled_quantity": "0",
            "status": "INVALID_FLATTEN_QUANTITY",
            "complete": False,
        }

    params = {
        "symbol": original_params["symbol"],
        "side": "SELL",
        "type": "MARKET",
        "timeInForce": "IOC",
        "quantity": _qty_text(quantity),
        "eventOutcome": original_params["eventOutcome"],
        "clientOrderId": f"flat-b5-{uuid.uuid4().hex[:20]}",
    }
    session = base.GeminiTradingSession()
    try:
        await session.connect()
        result = await session.place_fok(params, terminal_timeout=3.0)
        out = result.as_dict()
    except Exception as exc:
        out = {
            "status": "FLATTEN_EXCEPTION",
            "reason": type(exc).__name__,
            "filled_quantity": "0",
        }
    finally:
        await session.close()

    filled = _q(out.get("filled_quantity"))
    out["requested_quantity"] = str(quantity)
    out["complete"] = filled == quantity
    return out


async def flatten_observed(
    *,
    yes_params: dict[str, Any],
    no_params: dict[str, Any],
    yes_fill: D,
    no_fill: D,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    complete = True
    if yes_fill > 0:
        results["yes"] = await flatten_exact(yes_params, yes_fill)
        complete = complete and bool(results["yes"].get("complete"))
    if no_fill > 0:
        results["no"] = await flatten_exact(no_params, no_fill)
        complete = complete and bool(results["no"].get("complete"))
    results["complete"] = complete
    return results


async def run() -> None:
    event, report = await corrected_preflight()
    base.emit("PREFLIGHT", **report)
    if event is None:
        base.emit("STOP", reason="NO_BTC5M_EVENT", order_entry=False)
        return
    if not base.live_armed():
        base.emit("DISARMED", reason="DUAL_KEY_NOT_ARMED", order_entry=False)
        return
    if not report.get("terms_ok"):
        raise RuntimeError("Gemini Prediction Markets terms are not accepted")
    if D(str(report.get("available_usd") or "0")) < D("2"):
        raise RuntimeError("Gemini available USD below conservative $2 canary reserve")
    if not report.get("q1_supported"):
        raise RuntimeError("current BTC5m contract does not support exact q=1")
    if not report.get("passes_3c_pair_floor"):
        base.emit("STOP", reason="PAIR_FLOOR_BELOW_3C", order_entry=False)
        return

    # Never send discovery prices. Re-read immediately before any ledger mutation/order entry.
    bid, _, ask, _ = await base.fresh_top(event.symbol)
    model = base.pair_model(bid, ask)
    if model["modeled_edge"] < base.TARGET:
        base.emit(
            "STOP",
            reason="FRESH_PAIR_FLOOR_BELOW_3C",
            model={k: str(v) for k, v in model.items()},
            order_entry=False,
        )
        return
    if event.seconds_to_expiry < base.MIN_SECONDS_TO_EXPIRY:
        base.emit("STOP", reason="EXPIRY_TOO_CLOSE", seconds_to_expiry=event.seconds_to_expiry, order_entry=False)
        return

    cid = base._clean_id(os.getenv(base.CANARY_ID_ENV, ""))
    trade_id = f"crypto-gem-btc5m-{cid}"[:64]
    yes_id = f"{trade_id}-yes"[:36]
    no_id = f"{trade_id}-no"[:36]
    yes_params = {
        "symbol": event.symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(model["yes_quote"]),
        "quantity": "1",
        "eventOutcome": "YES",
        "clientOrderId": yes_id,
    }
    no_params = {
        "symbol": event.symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(model["no_quote"]),
        "quantity": "1",
        "eventOutcome": "NO",
        "clientOrderId": no_id,
    }

    # Fail closed before connecting the order-entry session if ledger authorization fails.
    await base.budget.reserve(
        symbol=event.symbol,
        event_ticker=event.ticker,
        trade_id=trade_id,
        yes_quote=str(model["yes_quote"]),
        no_quote=str(model["no_quote"]),
        modeled_pair_edge=str(model["modeled_edge"]),
    )
    reserved = True
    maker = base.GeminiMakerSession()
    try:
        await maker.connect()
        yes_ack = await maker.request("order.place", yes_params, timeout=4.0)
        if int(yes_ack.get("status", 0)) != 200:
            await base.budget.release(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                reason="YES_MAKER_REJECTED",
            )
            reserved = False
            base.emit("STOP", reason="YES_MAKER_REJECTED", ack_status=yes_ack.get("status"), order_entry=True)
            return
        yes_row = await base._find_row(yes_id, event.symbol)

        no_ack = await maker.request("order.place", no_params, timeout=4.0)
        if int(no_ack.get("status", 0)) != 200:
            yes_cancel = await base._cancel_maker(maker, row=yes_row)
            _, _, yfill, _ = await base._reconcile_pair(yes_id, no_id, event.symbol)
            if yfill == 0 and yes_cancel.get("ok"):
                await base.budget.release(
                    symbol=event.symbol,
                    event_ticker=event.ticker,
                    trade_id=trade_id,
                    reason="NO_MAKER_REJECTED_ZERO_FILL",
                )
                reserved = False
                base.emit("STOP", reason="NO_MAKER_REJECTED_ZERO_FILL", order_entry=True)
                return

            flattened = await flatten_observed(
                yes_params=yes_params,
                no_params=no_params,
                yes_fill=yfill,
                no_fill=D("0"),
            ) if yfill > 0 else {"complete": False, "reason": "AMBIGUOUS_ZERO_FILL_CANCEL"}
            status = (
                "SECOND_POST_FAILED_FLATTENED_HARD_HALT"
                if flattened.get("complete")
                else "SECOND_POST_FAILED_FLATTEN_INCOMPLETE_HARD_HALT"
            )
            await base.budget.result(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                status=status,
                legs_filled=1 if yfill > 0 else 0,
                metadata={"yes_fill": str(yfill), "yes_cancel": yes_cancel, "flatten": flattened},
            )
            base.emit("HARD_HALT", status=status, yes_fill=str(yfill), flatten=flattened, order_entry=True)
            return

        no_row = await base._find_row(no_id, event.symbol)
        base.emit(
            "BOTH_MAKERS_POSTED",
            symbol=event.symbol,
            yes_price=str(model["yes_quote"]),
            no_price=str(model["no_quote"]),
            modeled_pair_edge=str(model["modeled_edge"]),
            order_entry=True,
        )

        deadline = min(
            time.monotonic() + base.REST_SECONDS,
            time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0),
        )
        first_fill_seen = False
        while time.monotonic() < deadline and not first_fill_seen:
            try:
                item = await asyncio.wait_for(maker.events.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            if not isinstance(item, Mapping):
                continue
            client_id = str(item.get("c") or "")
            if client_id not in {yes_id, no_id}:
                continue
            status = str(item.get("X") or "").upper()
            qty = D(str(item.get("Z") or "0"))
            if status in {"PARTIALLY_FILLED", "FILLED"} and qty > 0:
                first_fill_seen = True

        # Freeze after first observed execution or timeout, then reconcile authoritative order state.
        yes_row = await base._order_by_client(yes_id, event.symbol) or yes_row
        no_row = await base._order_by_client(no_id, event.symbol) or no_row
        yes_cancel, no_cancel = await asyncio.gather(
            base._cancel_maker(maker, row=yes_row),
            base._cancel_maker(maker, row=no_row),
        )
        await asyncio.sleep(0.15)
        _, _, yes_fill, no_fill = await base._reconcile_pair(yes_id, no_id, event.symbol)
        base.emit(
            "RECONCILED",
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            yes_cancel=yes_cancel,
            no_cancel=no_cancel,
            order_entry=True,
        )

        if yes_fill == 0 and no_fill == 0:
            if not yes_cancel.get("ok") or not no_cancel.get("ok"):
                status = "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT"
                await base.budget.result(
                    symbol=event.symbol,
                    event_ticker=event.ticker,
                    trade_id=trade_id,
                    status=status,
                    legs_filled=0,
                    metadata={"yes_cancel": yes_cancel, "no_cancel": no_cancel},
                )
                base.emit("HARD_HALT", status=status, order_entry=True)
                return
            await base.budget.release(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                reason="ZERO_FILL_TIMEOUT",
            )
            reserved = False
            base.emit("COMPLETE", status="NO_FILL_RELEASED", order_entry=True, realized=False)
            return

        if yes_fill == base.ONE and no_fill == base.ONE:
            status = "FILLED_NOT_SETTLED"
            await base.budget.result(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                status=status,
                legs_filled=2,
                metadata={
                    "yes_price": str(model["yes_quote"]),
                    "no_price": str(model["no_quote"]),
                    "modeled_pair_edge": str(model["modeled_edge"]),
                    "settlement_required": True,
                },
            )
            base.emit("COMPLETE", status=status, order_entry=True, realized=False)
            return

        # Any one-sided or fractional shape is risk, not arb. Flatten exact observed inventory.
        flatten = await flatten_observed(
            yes_params=yes_params,
            no_params=no_params,
            yes_fill=yes_fill,
            no_fill=no_fill,
        )
        status = (
            "NONPAIRED_FILL_FLATTENED_HARD_HALT"
            if flatten.get("complete")
            else "NONPAIRED_FILL_FLATTEN_INCOMPLETE_MANUAL_REVIEW"
        )
        await base.budget.result(
            symbol=event.symbol,
            event_ticker=event.ticker,
            trade_id=trade_id,
            status=status,
            legs_filled=int(yes_fill > 0) + int(no_fill > 0),
            metadata={
                "yes_fill": str(yes_fill),
                "no_fill": str(no_fill),
                "flatten": flatten,
                "manual_review_required": not bool(flatten.get("complete")),
            },
        )
        base.emit(
            "HARD_HALT",
            status=status,
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            flatten=flatten,
            order_entry=True,
        )
    finally:
        await maker.close()
        if reserved:
            # Filled/ambiguous/flattened states retain the durable reservation until reconciliation.
            pass


if __name__ == "__main__":
    asyncio.run(run())
