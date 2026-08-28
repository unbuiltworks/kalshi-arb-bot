"""One-shot Gemini short-crypto q=1 inventory-maker canary child.

The native discovery path is BTC 5m. A tightly bounded wrapper may substitute the
single event-discovery function (currently ETH 5m for the incentive-MM probe) while
reusing the same proven order, reservation, cancellation, and reconciliation path.

Strategy behavior:
- post passive YES and NO maker bids;
- allow bounded one-sided inventory;
- do NOT freeze/flatten merely because one side fills;
- let both maker orders keep working for a bounded window;
- cancel remaining orders before expiry and hold observed inventory to settlement;
- normal strategy path never uses a SELL exit;
- operational failures retain the durable reservation and may use the proven
  emergency exact-quantity flatten helper.

Hard live bounds for this child:
- max posted quantity 1 per outcome;
- initial conservative matched-pair edge must meet ENTRY_PAIR_EDGE;
- at least 180 seconds to expiry at entry;
- one campaign per process;
- any fill remains non-realized until authoritative Gemini settlement.

Execution-quality evidence is emitted explicitly: UTC stage timestamps, order-ack
latency, post-ack order-visibility latency, approximate top-of-book queue ahead,
first WebSocket fill-signal latency, total resting duration, and cancel round-trip.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import time
from typing import Any, Mapping

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import flatten_observed, maker_client_ids
from src.gemini_short_crypto_quantity import quantity_supported
from src import gemini_short_crypto_inventory_local_budget as budget

D = Decimal
ONE = D("1")
ENTRY_PAIR_EDGE = D("0.01")
MIN_SECONDS_TO_EXPIRY = 180.0
STOP_BEFORE_EXPIRY_SECONDS = 20.0
MAX_WORK_SECONDS = 180.0

LIVE_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1"
CANARY_ID_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID"
CANARY_ARM_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM"


def emit(stage: str, **fields: Any) -> None:
    print(
        "GEMINI_SHORT_CRYPTO_INVENTORY_Q1 "
        + json.dumps(
            {
                "stage": stage,
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                **fields,
            },
            separators=(",", ":"),
            default=str,
        ),
        flush=True,
    )


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    cid = os.getenv(CANARY_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and cid and os.getenv(CANARY_ARM_ENV, "").strip() == cid)


def safe_inventory(yes_fill: D, no_fill: D) -> bool:
    return D("0") <= yes_fill <= ONE and D("0") <= no_fill <= ONE


def inventory_status(yes_fill: D, no_fill: D, *, fill_signal_seen: bool = False) -> str:
    if yes_fill == 0 and no_fill == 0:
        if fill_signal_seen:
            return "FILL_SIGNAL_RECONCILIATION_PENDING"
        return "NO_FILL_RELEASED"
    if yes_fill == no_fill:
        return "MATCHED_INVENTORY_NOT_SETTLED"
    return "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED"


def inventory_trade_id(event_ticker: str, canary_id: str) -> str:
    ticker = str(event_ticker or "").strip().upper()
    if ticker.startswith("ETH05M"):
        family = "eth5m"
    elif ticker.startswith("BTC05M"):
        family = "btc5m"
    else:
        family = "shortcrypto"
    return f"crypto-gem-{family}-inventory-{canary_id}"[:64]


async def preflight() -> tuple[base.Event | None, dict[str, Any]]:
    event = await base.discover_btc5m()
    terms = await base._gemini_terms_ok()
    available = await base._gemini_available_usd()
    if event is None:
        return None, {
            "terms_ok": terms,
            "available_usd": str(available),
            "market": None,
            "armed": live_armed(),
            "order_entry": False,
            "realized_pnl": 0,
        }

    bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
    model = base.pair_model(bid, ask)
    q1_supported = quantity_supported(
        quantity=ONE,
        minimum=event.quantity_minimum,
        increment=event.quantity_increment,
    )
    report = {
        "terms_ok": terms,
        "available_usd": str(available),
        "armed": live_armed(),
        "market": event.ticker,
        "symbol": event.symbol,
        "seconds_to_expiry": event.seconds_to_expiry,
        "quantity_minimum": str(event.quantity_minimum),
        "quantity_increment": str(event.quantity_increment),
        "q1_supported": q1_supported,
        "price_increment": str(event.price_increment),
        "yes_bid": str(bid),
        "yes_bid_size": str(bid_size),
        "yes_ask": str(ask),
        "yes_ask_size": str(ask_size),
        **{k: str(v) for k, v in model.items()},
        "entry_pair_edge_floor": str(ENTRY_PAIR_EDGE),
        "passes_inventory_entry_floor": bool(q1_supported and model["modeled_edge"] >= ENTRY_PAIR_EDGE),
        "strategy": "MAKER_FIRST_TEMPORAL_INVENTORY",
        "one_sided_inventory_allowed": True,
        "normal_sell_exit": False,
        "order_entry": False,
        "realized_pnl": 0,
    }
    return event, report


async def _post_maker(
    maker: base.GeminiMakerSession,
    params: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, float, float]:
    request_started = time.monotonic()
    ack = await maker.request("order.place", params, timeout=4.0)
    ack_latency_ms = (time.monotonic() - request_started) * 1000.0
    row = None
    row_lookup_ms = 0.0
    if int(ack.get("status", 0)) == 200:
        lookup_started = time.monotonic()
        row = await base._find_row(str(params["clientOrderId"]), str(params["symbol"]))
        row_lookup_ms = (time.monotonic() - lookup_started) * 1000.0
    return ack, row, ack_latency_ms, row_lookup_ms


async def run() -> None:
    event, report = await preflight()
    emit("PREFLIGHT", **report)
    if event is None:
        emit("STOP", reason="NO_SHORT_CRYPTO_EVENT", order_entry=False)
        return
    if not live_armed():
        emit("DISARMED", reason="INVENTORY_DUAL_KEY_NOT_ARMED", order_entry=False)
        return
    if not report.get("terms_ok"):
        raise RuntimeError("Gemini Prediction Markets terms are not accepted")
    if D(str(report.get("available_usd") or "0")) < D("2"):
        raise RuntimeError("Gemini available USD below conservative $2 inventory reserve")
    if not report.get("q1_supported"):
        raise RuntimeError("current short-crypto contract does not support exact q=1")
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE_FOR_INVENTORY_WORK", seconds_to_expiry=event.seconds_to_expiry, order_entry=False)
        return

    # Never send discovery prices. Re-read immediately before reservation/order entry.
    bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
    model = base.pair_model(bid, ask)
    if model["modeled_edge"] < ENTRY_PAIR_EDGE:
        emit(
            "STOP",
            reason="FRESH_INVENTORY_ENTRY_EDGE_BELOW_FLOOR",
            model={k: str(v) for k, v in model.items()},
            entry_pair_edge_floor=str(ENTRY_PAIR_EDGE),
            order_entry=False,
        )
        return
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE_AFTER_FRESH_BOOK", seconds_to_expiry=event.seconds_to_expiry, order_entry=False)
        return

    cid = base._clean_id(os.getenv(CANARY_ID_ENV, ""))
    trade_id = inventory_trade_id(event.ticker, cid)
    yes_id, no_id = maker_client_ids(trade_id)
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

    await budget.reserve(
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
        yes_ack, yes_row, yes_ack_ms, yes_row_lookup_ms = await _post_maker(maker, yes_params)
        emit(
            "MAKER_ACK",
            outcome="YES",
            symbol=event.symbol,
            client_order_id=yes_id,
            price=str(model["yes_quote"]),
            ack_status=yes_ack.get("status"),
            accepted=int(yes_ack.get("status", 0)) == 200,
            ack_latency_ms=round(yes_ack_ms, 3),
            post_ack_order_visibility_ms=round(yes_row_lookup_ms, 3),
            approximate_pre_submit_queue_qty_at_quote=str(bid_size),
            maker_only_tif="MOC",
            order_entry=True,
        )
        if int(yes_ack.get("status", 0)) != 200:
            await budget.release(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                reason="YES_MAKER_REJECTED",
            )
            reserved = False
            emit("STOP", reason="YES_MAKER_REJECTED", ack_status=yes_ack.get("status"), order_entry=True)
            return

        no_ack, no_row, no_ack_ms, no_row_lookup_ms = await _post_maker(maker, no_params)
        emit(
            "MAKER_ACK",
            outcome="NO",
            symbol=event.symbol,
            client_order_id=no_id,
            price=str(model["no_quote"]),
            ack_status=no_ack.get("status"),
            accepted=int(no_ack.get("status", 0)) == 200,
            ack_latency_ms=round(no_ack_ms, 3),
            post_ack_order_visibility_ms=round(no_row_lookup_ms, 3),
            approximate_pre_submit_queue_qty_at_quote=str(ask_size),
            maker_only_tif="MOC",
            order_entry=True,
        )
        if int(no_ack.get("status", 0)) != 200:
            yes_row = await base._order_by_client(yes_id, event.symbol) or yes_row
            cancel_started = time.monotonic()
            yes_cancel = await base._cancel_maker(maker, row=yes_row)
            cancel_roundtrip_ms = (time.monotonic() - cancel_started) * 1000.0
            await asyncio.sleep(0.15)
            _, _, yes_fill, _ = await base._reconcile_pair(yes_id, no_id, event.symbol)
            if yes_fill == 0 and yes_cancel.get("ok"):
                await budget.release(
                    symbol=event.symbol,
                    event_ticker=event.ticker,
                    trade_id=trade_id,
                    reason="NO_MAKER_REJECTED_ZERO_FILL",
                )
                reserved = False
                emit(
                    "STOP",
                    reason="NO_MAKER_REJECTED_ZERO_FILL",
                    yes_cancel_roundtrip_ms=round(cancel_roundtrip_ms, 3),
                    order_entry=True,
                )
                return
            flatten = await flatten_observed(
                yes_params=yes_params,
                no_params=no_params,
                yes_fill=yes_fill,
                no_fill=D("0"),
            ) if yes_fill > 0 else {"complete": False, "reason": "AMBIGUOUS_SECOND_POST_FAILURE"}
            status = (
                "SECOND_POST_FAILED_FLATTENED_HARD_HALT"
                if flatten.get("complete")
                else "SECOND_POST_FAILED_MANUAL_REVIEW"
            )
            await budget.result(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                status=status,
                yes_fill=str(yes_fill),
                no_fill="0",
                metadata={
                    "yes_cancel": yes_cancel,
                    "yes_cancel_roundtrip_ms": round(cancel_roundtrip_ms, 3),
                    "flatten": flatten,
                    "operational_failure": True,
                },
            )
            emit(
                "HARD_HALT",
                status=status,
                yes_fill=str(yes_fill),
                yes_cancel_roundtrip_ms=round(cancel_roundtrip_ms, 3),
                flatten=flatten,
                order_entry=True,
            )
            return

        resting_started = time.monotonic()
        emit(
            "BOTH_INVENTORY_MAKERS_POSTED",
            symbol=event.symbol,
            event_ticker=event.ticker,
            trade_id=trade_id,
            yes_price=str(model["yes_quote"]),
            no_price=str(model["no_quote"]),
            modeled_pair_edge=str(model["modeled_edge"]),
            client_ids_distinct=yes_id != no_id,
            yes_ack_latency_ms=round(yes_ack_ms, 3),
            no_ack_latency_ms=round(no_ack_ms, 3),
            yes_post_ack_order_visibility_ms=round(yes_row_lookup_ms, 3),
            no_post_ack_order_visibility_ms=round(no_row_lookup_ms, 3),
            approximate_yes_queue_ahead_qty=str(bid_size),
            approximate_no_queue_ahead_qty=str(ask_size),
            maker_only_tif="MOC",
            normal_one_sided_fill_action="KEEP_WORKING_OPPOSITE_SIDE",
            order_entry=True,
        )

        # Unlike the old atomic canary, a first fill does NOT freeze the campaign.
        # Both maker orders remain eligible to fill until the bounded inventory work
        # window ends or we approach expiry.
        deadline = min(
            resting_started + MAX_WORK_SECONDS,
            resting_started + max(1.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS),
        )
        fill_signals: dict[str, D] = {}
        first_fill_latency_ms: float | None = None
        while time.monotonic() < deadline:
            timeout = min(0.5, max(0.05, deadline - time.monotonic()))
            try:
                item = await asyncio.wait_for(maker.events.get(), timeout=timeout)
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
                first_for_campaign = not fill_signals
                fill_signals[client_id] = max(fill_signals.get(client_id, D("0")), qty)
                if first_for_campaign:
                    first_fill_latency_ms = (time.monotonic() - resting_started) * 1000.0
                    emit(
                        "FIRST_FILL_CONTINUE_WORKING",
                        outcome="YES" if client_id == yes_id else "NO",
                        client_order_id=client_id,
                        reported_fill_quantity=str(qty),
                        ws_order_status=status,
                        first_fill_latency_ms=round(first_fill_latency_ms, 3),
                        action="DO_NOT_FLATTEN_KEEP_OTHER_MAKER_RESTING",
                        order_entry=True,
                    )

        resting_duration_ms = (time.monotonic() - resting_started) * 1000.0
        yes_row = await base._order_by_client(yes_id, event.symbol) or yes_row
        no_row = await base._order_by_client(no_id, event.symbol) or no_row
        cancel_started = time.monotonic()
        yes_cancel, no_cancel = await asyncio.gather(
            base._cancel_maker(maker, row=yes_row),
            base._cancel_maker(maker, row=no_row),
        )
        cancel_roundtrip_ms = (time.monotonic() - cancel_started) * 1000.0
        await asyncio.sleep(0.2)
        _, _, yes_fill, no_fill = await base._reconcile_pair(yes_id, no_id, event.symbol)
        emit(
            "RECONCILED",
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            yes_cancel=yes_cancel,
            no_cancel=no_cancel,
            ws_first_fill_latency_ms=None if first_fill_latency_ms is None else round(first_fill_latency_ms, 3),
            resting_duration_ms=round(resting_duration_ms, 3),
            cancel_roundtrip_ms=round(cancel_roundtrip_ms, 3),
            order_entry=True,
        )

        if not safe_inventory(yes_fill, no_fill):
            status = "INVENTORY_BOUND_VIOLATION_MANUAL_REVIEW"
            await budget.result(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                status=status,
                yes_fill=str(yes_fill),
                no_fill=str(no_fill),
                metadata={
                    "yes_cancel": yes_cancel,
                    "no_cancel": no_cancel,
                    "resting_duration_ms": round(resting_duration_ms, 3),
                    "cancel_roundtrip_ms": round(cancel_roundtrip_ms, 3),
                    "manual_review_required": True,
                },
            )
            emit("HARD_HALT", status=status, yes_fill=str(yes_fill), no_fill=str(no_fill), order_entry=True)
            return

        if (not yes_cancel.get("ok") and yes_fill < ONE) or (not no_cancel.get("ok") and no_fill < ONE):
            status = "CANCEL_AMBIGUOUS_WITH_POSSIBLE_RESTING_ORDER_MANUAL_REVIEW"
            await budget.result(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                status=status,
                yes_fill=str(yes_fill),
                no_fill=str(no_fill),
                metadata={
                    "yes_cancel": yes_cancel,
                    "no_cancel": no_cancel,
                    "resting_duration_ms": round(resting_duration_ms, 3),
                    "cancel_roundtrip_ms": round(cancel_roundtrip_ms, 3),
                    "manual_review_required": True,
                },
            )
            emit("HARD_HALT", status=status, yes_fill=str(yes_fill), no_fill=str(no_fill), order_entry=True)
            return

        status = inventory_status(yes_fill, no_fill, fill_signal_seen=bool(fill_signals))
        if status == "NO_FILL_RELEASED":
            await budget.release(
                symbol=event.symbol,
                event_ticker=event.ticker,
                trade_id=trade_id,
                reason="INVENTORY_WINDOW_ZERO_FILL",
            )
            reserved = False
            emit(
                "COMPLETE",
                status=status,
                yes_fill="0",
                no_fill="0",
                ws_first_fill_latency_ms=None,
                resting_duration_ms=round(resting_duration_ms, 3),
                cancel_roundtrip_ms=round(cancel_roundtrip_ms, 3),
                realized=False,
                order_entry=True,
            )
            return

        await budget.result(
            symbol=event.symbol,
            event_ticker=event.ticker,
            trade_id=trade_id,
            status=status,
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            metadata={
                "initial_yes_price": str(model["yes_quote"]),
                "initial_no_price": str(model["no_quote"]),
                "initial_modeled_pair_edge": str(model["modeled_edge"]),
                "approximate_pre_submit_yes_queue_qty_at_quote": str(bid_size),
                "approximate_pre_submit_no_queue_qty_at_quote": str(ask_size),
                "yes_ack_latency_ms": round(yes_ack_ms, 3),
                "no_ack_latency_ms": round(no_ack_ms, 3),
                "yes_post_ack_order_visibility_ms": round(yes_row_lookup_ms, 3),
                "no_post_ack_order_visibility_ms": round(no_row_lookup_ms, 3),
                "ws_first_fill_latency_ms": None if first_fill_latency_ms is None else round(first_fill_latency_ms, 3),
                "resting_duration_ms": round(resting_duration_ms, 3),
                "cancel_roundtrip_ms": round(cancel_roundtrip_ms, 3),
                "maker_only_tif": "MOC",
                "settlement_required": True,
                "one_sided_settlement_allowed": True,
                "normal_sell_exit_used": False,
                "reference_replica_probe": True,
                "websocket_fill_signal_seen": bool(fill_signals),
                "websocket_fill_signals": {key: str(value) for key, value in fill_signals.items()},
                "yes_client_order_id": yes_id,
                "no_client_order_id": no_id,
            },
        )
        emit(
            "COMPLETE",
            status=status,
            yes_fill=str(yes_fill),
            no_fill=str(no_fill),
            ws_first_fill_latency_ms=None if first_fill_latency_ms is None else round(first_fill_latency_ms, 3),
            resting_duration_ms=round(resting_duration_ms, 3),
            cancel_roundtrip_ms=round(cancel_roundtrip_ms, 3),
            realized=False,
            settlement_required=True,
            order_entry=True,
        )
    finally:
        await maker.close()
        if reserved:
            # Any executed inventory remains reserved until authoritative settlement
            # reconciliation. Ambiguous states also retain the reservation.
            pass


if __name__ == "__main__":
    asyncio.run(run())
