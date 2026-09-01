"""Bounded q=1 dynamic-complement maker child for Gemini short crypto.

This is the execution child for the next ETH5 incentive-MM probe. It preserves
completion budget at entry, then reacts to the first full q=1 maker fill by
canceling the stale missing-side quote and progressively improving ONLY that side
while a hard fee-adjusted complete-set edge remains.

Taker rescue is deliberately NOT implemented here. The research policy may model
it separately, but this child submits only BUY LIMIT MOC maker orders.

Hard bounds:
- exactly q=1 maximum per outcome;
- one campaign per process;
- raw entry must preserve >=5c after maker fees;
- completed pair after any reprice must preserve >=2c after maker fees;
- initial two-sided wait <=60s;
- after first fill, naked rescue window <=30s;
- <=6 replacement maker orders on the missing side;
- cancel-before-repost, never overlapping missing-side maker orders;
- stop >=20s before expiry;
- projected rewards/rebates are excluded from all edge calculations and P&L.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
import hashlib
import os
import time
from typing import Any, Mapping

from src import gemini_short_crypto_btc5m_maker_canary_q1 as base
from src.gemini_short_crypto_btc5m_maker_canary_q1_v3 import maker_client_ids
from src.gemini_short_crypto_quantity import quantity_supported
from src import gemini_short_crypto_inventory_local_budget as budget
from src import gemini_incentive_mm_dynamic_complement as policy

D = Decimal
ONE = D("1")
ZERO = D("0")
ENTRY_PAIR_EDGE = D("0.05")
FINAL_PAIR_EDGE = D("0.02")
MIN_SECONDS_TO_EXPIRY = 180.0
STOP_BEFORE_EXPIRY_SECONDS = 20.0
INITIAL_WAIT_SECONDS = 60.0
RESCUE_SECONDS = 30.0
REPRICE_INTERVAL_SECONDS = 2.0
MAX_REPRICES = 6
MIN_PRICE = D("0.20")
MAX_PRICE = D("0.80")

LIVE_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_LIVE_Q1"
CANARY_ID_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ID"
CANARY_ARM_ENV = "GEMINI_SHORT_CRYPTO_INVENTORY_CANARY_ARM"


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    cid = os.getenv(CANARY_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and cid and os.getenv(CANARY_ARM_ENV, "").strip() == cid)


def emit(stage: str, **fields: Any) -> None:
    base.emit(stage, dynamic_complement=True, **fields)


def safe_inventory(yes_fill: D, no_fill: D) -> bool:
    return ZERO <= D(str(yes_fill)) <= ONE and ZERO <= D(str(no_fill)) <= ONE


def inventory_trade_id(event_ticker: str, canary_id: str) -> str:
    ticker = str(event_ticker or "").strip().upper()
    family = "eth5m" if ticker.startswith("ETH05M") else "btc5m" if ticker.startswith("BTC05M") else "shortcrypto"
    return f"crypto-gem-{family}-dyn-{canary_id}"[:64]


def replacement_client_id(trade_id: str, outcome: str, sequence: int) -> str:
    outcome = str(outcome).strip().lower()
    if outcome not in {"yes", "no"}:
        raise ValueError("replacement outcome must be YES or NO")
    if not 1 <= int(sequence) <= MAX_REPRICES:
        raise ValueError("replacement sequence outside bound")
    digest = hashlib.sha256(str(trade_id).encode("utf-8")).hexdigest()[:12]
    return f"dcq1-{digest}-{outcome}-r{int(sequence)}"[:36]


def fill_price_from_event(item: Mapping[str, Any], fallback: D) -> D:
    raw = item.get("L") or item.get("p") or fallback
    price = D(str(raw))
    return price if ZERO < price < ONE else D(str(fallback))


def raw_pair_model(bid: D, ask: D) -> dict[str, D]:
    return dict(base.pair_model(D(str(bid)), D(str(ask))))


def entry_eligible(model: Mapping[str, Any]) -> bool:
    try:
        yes = D(str(model["yes_quote"]))
        no = D(str(model["no_quote"]))
        edge = D(str(model["modeled_edge"]))
    except Exception:
        return False
    return MIN_PRICE <= yes <= MAX_PRICE and MIN_PRICE <= no <= MAX_PRICE and edge >= ENTRY_PAIR_EDGE


async def preflight() -> tuple[base.Event | None, dict[str, Any]]:
    event = await base.discover_btc5m()
    terms = await base._gemini_terms_ok()
    available = await base._gemini_available_usd()
    if event is None:
        return None, {
            "terms_ok": terms, "available_usd": str(available), "market": None,
            "armed": live_armed(), "entry_pair_edge_floor": str(ENTRY_PAIR_EDGE),
            "final_pair_edge_floor": str(FINAL_PAIR_EDGE), "order_entry": False,
        }
    bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
    model = raw_pair_model(bid, ask)
    q1_supported = quantity_supported(
        quantity=ONE, minimum=event.quantity_minimum, increment=event.quantity_increment,
    )
    return event, {
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
        "yes_bid": str(bid), "yes_bid_size": str(bid_size),
        "yes_ask": str(ask), "yes_ask_size": str(ask_size),
        **{k: str(v) for k, v in model.items()},
        "entry_pair_edge_floor": str(ENTRY_PAIR_EDGE),
        "final_pair_edge_floor": str(FINAL_PAIR_EDGE),
        "passes_entry_floor": bool(q1_supported and entry_eligible(model)),
        "strategy": "MAKER_FIRST_DYNAMIC_COMPLEMENT",
        "order_entry": False,
    }


async def _post_maker(
    maker: base.GeminiMakerSession,
    *, symbol: str, outcome: str, price: D, client_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, float]:
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "LIMIT",
        "timeInForce": "MOC",
        "price": str(D(str(price))),
        "quantity": "1",
        "eventOutcome": str(outcome).upper(),
        "clientOrderId": client_id,
    }
    started = time.monotonic()
    ack = await maker.request("order.place", params, timeout=4.0)
    ack_ms = (time.monotonic() - started) * 1000.0
    row = None
    if int(ack.get("status", 0)) == 200:
        row = await base._find_row(client_id, symbol)
    return ack, row, ack_ms


async def _cancel(
    maker: base.GeminiMakerSession,
    *, client_id: str, symbol: str, row: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], float, dict[str, Any] | None, D]:
    row = await base._order_by_client(client_id, symbol) or (dict(row) if row else None)
    started = time.monotonic()
    result = await base._cancel_maker(maker, row=row)
    cancel_ms = (time.monotonic() - started) * 1000.0
    await asyncio.sleep(0.12)
    terminal = await base._order_by_client(client_id, symbol)
    return result, cancel_ms, terminal, base._filled(terminal)


async def _aggregate_ids(client_ids: list[str], symbol: str) -> tuple[D, list[str]]:
    total = ZERO
    missing: list[str] = []
    for cid in client_ids:
        row = await base._order_by_client(cid, symbol)
        if row is None:
            missing.append(cid)
            continue
        total += base._filled(row)
    return total, missing


async def _wait_for_first_fill(
    maker: base.GeminiMakerSession,
    *, yes_id: str, no_id: str, yes_price: D, no_price: D, deadline: float,
) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        timeout = min(0.5, max(0.05, deadline - time.monotonic()))
        try:
            item = await asyncio.wait_for(maker.events.get(), timeout=timeout)
        except asyncio.TimeoutError:
            continue
        if not isinstance(item, Mapping):
            continue
        cid = str(item.get("c") or "")
        if cid not in {yes_id, no_id}:
            continue
        status = str(item.get("X") or "").upper()
        qty = D(str(item.get("Z") or "0"))
        if status not in {"PARTIALLY_FILLED", "FILLED"} or qty <= ZERO:
            continue
        outcome = "YES" if cid == yes_id else "NO"
        fallback = yes_price if outcome == "YES" else no_price
        return {
            "client_id": cid,
            "outcome": outcome,
            "quantity": qty,
            "price": fill_price_from_event(item, fallback),
            "status": status,
        }
    return None


async def _wait_for_specific_fill(
    maker: base.GeminiMakerSession, *, client_id: str, deadline: float,
) -> D:
    while time.monotonic() < deadline:
        timeout = min(0.35, max(0.05, deadline - time.monotonic()))
        try:
            item = await asyncio.wait_for(maker.events.get(), timeout=timeout)
        except asyncio.TimeoutError:
            continue
        if not isinstance(item, Mapping) or str(item.get("c") or "") != client_id:
            continue
        status = str(item.get("X") or "").upper()
        qty = D(str(item.get("Z") or "0"))
        if status in {"PARTIALLY_FILLED", "FILLED"} and qty > ZERO:
            return qty
    return ZERO


async def _dynamic_rescue(
    maker: base.GeminiMakerSession,
    *, event: base.Event, trade_id: str, filled_outcome: str, filled_price: D,
    complement_initial_id: str, complement_initial_price: D,
    complement_initial_row: Mapping[str, Any] | None,
    yes_ids: list[str], no_ids: list[str],
) -> dict[str, Any]:
    missing = "NO" if filled_outcome == "YES" else "YES"
    cancel, cancel_ms, terminal, race_fill = await _cancel(
        maker, client_id=complement_initial_id, symbol=event.symbol, row=complement_initial_row,
    )
    emit(
        "DYNAMIC_COMPLEMENT_CANCEL",
        missing_outcome=missing,
        client_order_id=complement_initial_id,
        cancel=cancel,
        cancel_roundtrip_ms=round(cancel_ms, 3),
        cancel_race_fill=str(race_fill),
        order_entry=True,
    )
    if race_fill > ONE:
        return {"status": "Q1_BOUND_VIOLATION", "paired": False, "hard_halt": True}
    if race_fill == ONE:
        return {"status": "COMPLEMENT_FILLED_DURING_CANCEL", "paired": True, "hard_halt": False}
    if race_fill > ZERO:
        return {"status": "FRACTIONAL_CANCEL_RACE_FILL", "paired": False, "hard_halt": True}
    if not cancel.get("ok"):
        return {"status": "COMPLEMENT_CANCEL_AMBIGUOUS", "paired": False, "hard_halt": True}

    current_price = D(str(complement_initial_price))
    current_id: str | None = None
    current_row: Mapping[str, Any] | None = terminal
    sequence = 0
    rescue_deadline = min(
        time.monotonic() + RESCUE_SECONDS,
        time.monotonic() + max(1.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS),
    )
    next_reprice_check = 0.0

    while time.monotonic() < rescue_deadline:
        now = time.monotonic()
        if current_id is None or (sequence < MAX_REPRICES and now >= next_reprice_check):
            bid, _, ask, _ = await base.fresh_top(event.symbol)
            plan = policy.dynamic_complement_plan(
                filled_outcome=filled_outcome,
                filled_price=filled_price,
                current_complement_price=current_price,
                yes_bid=bid,
                yes_ask=ask,
                min_final_edge=FINAL_PAIR_EDGE,
                increment=event.price_increment,
                min_price=MIN_PRICE,
                max_price=MAX_PRICE,
            )
            target = plan.get("target_price")
            target_d = None if target is None else D(str(target))

            # If an order is already resting, reprice only for a strictly better safe
            # maker target. Never lower/repost merely because the book moved against us.
            if current_id is not None and (plan.get("action") != "REPRICE_MAKER_COMPLEMENT" or target_d is None or target_d <= current_price):
                next_reprice_check = now + REPRICE_INTERVAL_SECONDS
            else:
                if current_id is not None:
                    c, c_ms, _, c_fill = await _cancel(
                        maker, client_id=current_id, symbol=event.symbol, row=current_row,
                    )
                    emit(
                        "DYNAMIC_REPRICE_CANCEL",
                        missing_outcome=missing,
                        client_order_id=current_id,
                        cancel=c,
                        cancel_roundtrip_ms=round(c_ms, 3),
                        cancel_race_fill=str(c_fill),
                        order_entry=True,
                    )
                    if c_fill == ONE:
                        return {"status": "COMPLEMENT_FILLED_DURING_REPRICE_CANCEL", "paired": True, "hard_halt": False}
                    if c_fill > ZERO or not c.get("ok"):
                        return {"status": "REPRICE_CANCEL_AMBIGUOUS", "paired": False, "hard_halt": True}
                    current_id = None
                    current_row = None

                # After canceling the old order, re-read the book and recompute. The
                # target must remain maker-only and edge-safe at mutation time.
                bid, _, ask, _ = await base.fresh_top(event.symbol)
                plan = policy.dynamic_complement_plan(
                    filled_outcome=filled_outcome,
                    filled_price=filled_price,
                    current_complement_price=current_price,
                    yes_bid=bid,
                    yes_ask=ask,
                    min_final_edge=FINAL_PAIR_EDGE,
                    increment=event.price_increment,
                    min_price=MIN_PRICE,
                    max_price=MAX_PRICE,
                )
                target = plan.get("target_price")
                target_d = None if target is None else D(str(target))
                if plan.get("action") != "REPRICE_MAKER_COMPLEMENT" or target_d is None or target_d <= current_price:
                    next_reprice_check = time.monotonic() + REPRICE_INTERVAL_SECONDS
                    await asyncio.sleep(min(0.25, max(0.05, rescue_deadline - time.monotonic())))
                    continue

                sequence += 1
                if sequence > MAX_REPRICES:
                    break
                rid = replacement_client_id(trade_id, missing, sequence)
                ack, row, ack_ms = await _post_maker(
                    maker, symbol=event.symbol, outcome=missing, price=target_d, client_id=rid,
                )
                if missing == "YES":
                    yes_ids.append(rid)
                else:
                    no_ids.append(rid)
                emit(
                    "DYNAMIC_COMPLEMENT_REPOST",
                    missing_outcome=missing,
                    sequence=sequence,
                    client_order_id=rid,
                    prior_price=str(current_price),
                    target_price=str(target_d),
                    modeled_final_edge=str(plan.get("final_edge")),
                    ack_status=ack.get("status"),
                    ack_latency_ms=round(ack_ms, 3),
                    accepted=int(ack.get("status", 0)) == 200,
                    maker_only_tif="MOC",
                    order_entry=True,
                )
                if int(ack.get("status", 0)) != 200:
                    return {"status": "DYNAMIC_COMPLEMENT_REPOST_REJECTED", "paired": False, "hard_halt": True}
                current_id = rid
                current_row = row
                current_price = target_d
                next_reprice_check = time.monotonic() + REPRICE_INTERVAL_SECONDS

        if current_id is not None:
            fill = await _wait_for_specific_fill(
                maker, client_id=current_id,
                deadline=min(rescue_deadline, time.monotonic() + 0.35),
            )
            if fill == ONE:
                return {"status": "DYNAMIC_COMPLEMENT_FILLED", "paired": True, "hard_halt": False, "reprices": sequence}
            if fill > ZERO:
                return {"status": "DYNAMIC_COMPLEMENT_FRACTIONAL_FILL", "paired": False, "hard_halt": True}
        else:
            await asyncio.sleep(min(0.25, max(0.05, rescue_deadline - time.monotonic())))

    if current_id is not None:
        c, c_ms, _, c_fill = await _cancel(
            maker, client_id=current_id, symbol=event.symbol, row=current_row,
        )
        emit(
            "DYNAMIC_RESCUE_TIMEOUT_CANCEL",
            missing_outcome=missing,
            client_order_id=current_id,
            cancel=c,
            cancel_roundtrip_ms=round(c_ms, 3),
            cancel_race_fill=str(c_fill),
            order_entry=True,
        )
        if c_fill == ONE:
            return {"status": "COMPLEMENT_FILLED_AT_RESCUE_TIMEOUT", "paired": True, "hard_halt": False, "reprices": sequence}
        if c_fill > ZERO or not c.get("ok"):
            return {"status": "RESCUE_TIMEOUT_CANCEL_AMBIGUOUS", "paired": False, "hard_halt": True, "reprices": sequence}
    return {"status": "DYNAMIC_RESCUE_EXPIRED_ONE_SIDED", "paired": False, "hard_halt": False, "reprices": sequence}


async def run() -> None:
    event, report = await preflight()
    emit("PREFLIGHT", **report)
    if event is None:
        emit("STOP", reason="NO_SHORT_CRYPTO_EVENT", order_entry=False)
        return
    if not live_armed():
        emit("DISARMED", reason="DYNAMIC_COMPLEMENT_DUAL_KEY_NOT_ARMED", order_entry=False)
        return
    if not report.get("terms_ok"):
        raise RuntimeError("Gemini Prediction Markets terms are not accepted")
    if D(str(report.get("available_usd") or "0")) < D("2"):
        raise RuntimeError("Gemini available USD below conservative $2 inventory reserve")
    if not report.get("q1_supported"):
        raise RuntimeError("current short-crypto contract does not support exact q=1")
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE_FOR_DYNAMIC_COMPLEMENT", order_entry=False)
        return

    bid, bid_size, ask, ask_size = await base.fresh_top(event.symbol)
    model = raw_pair_model(bid, ask)
    if not entry_eligible(model):
        emit(
            "STOP", reason="FRESH_RAW_ENTRY_EDGE_BELOW_5C_OR_OUTSIDE_BAND",
            model={k: str(v) for k, v in model.items()}, order_entry=False,
        )
        return
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE_AFTER_FRESH_BOOK", order_entry=False)
        return

    cid = base._clean_id(os.getenv(CANARY_ID_ENV, ""))
    trade_id = inventory_trade_id(event.ticker, cid)
    yes_id, no_id = maker_client_ids(trade_id)
    yes_ids = [yes_id]
    no_ids = [no_id]

    await budget.reserve(
        symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id,
        yes_quote=str(model["yes_quote"]), no_quote=str(model["no_quote"]),
        modeled_pair_edge=str(model["modeled_edge"]),
    )
    reserved = True
    maker = base.GeminiMakerSession()
    yes_row = None
    no_row = None
    try:
        await maker.connect()
        yes_ack, yes_row, yes_ack_ms = await _post_maker(
            maker, symbol=event.symbol, outcome="YES", price=model["yes_quote"], client_id=yes_id,
        )
        emit(
            "MAKER_ACK", outcome="YES", client_order_id=yes_id,
            price=str(model["yes_quote"]), ack_status=yes_ack.get("status"),
            ack_latency_ms=round(yes_ack_ms, 3), accepted=int(yes_ack.get("status", 0)) == 200,
            approximate_pre_submit_queue_qty_at_quote=str(bid_size), order_entry=True,
        )
        if int(yes_ack.get("status", 0)) != 200:
            await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="DYNAMIC_YES_REJECTED")
            reserved = False
            return

        no_ack, no_row, no_ack_ms = await _post_maker(
            maker, symbol=event.symbol, outcome="NO", price=model["no_quote"], client_id=no_id,
        )
        emit(
            "MAKER_ACK", outcome="NO", client_order_id=no_id,
            price=str(model["no_quote"]), ack_status=no_ack.get("status"),
            ack_latency_ms=round(no_ack_ms, 3), accepted=int(no_ack.get("status", 0)) == 200,
            approximate_pre_submit_queue_qty_at_quote=str(ask_size), order_entry=True,
        )
        if int(no_ack.get("status", 0)) != 200:
            cancel, cancel_ms, _, yes_fill = await _cancel(
                maker, client_id=yes_id, symbol=event.symbol, row=yes_row,
            )
            if yes_fill == ZERO and cancel.get("ok"):
                await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="DYNAMIC_NO_REJECTED_ZERO_FILL")
                reserved = False
                return
            await budget.result(
                symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id,
                status="SECOND_POST_FAILED_DYNAMIC_HARD_HALT",
                yes_fill=str(yes_fill), no_fill="0",
                metadata={
                    "yes_client_order_ids": yes_ids,
                    "no_client_order_ids": no_ids,
                    "order_execution_modes": {yes_id: "MAKER", no_id: "MAKER"},
                    "yes_cancel": cancel, "yes_cancel_roundtrip_ms": round(cancel_ms, 3),
                    "manual_review_required": True,
                },
            )
            return

        resting_started = time.monotonic()
        initial_deadline = min(
            resting_started + INITIAL_WAIT_SECONDS,
            resting_started + max(1.0, event.seconds_to_expiry - STOP_BEFORE_EXPIRY_SECONDS),
        )
        emit(
            "DYNAMIC_BOTH_MAKERS_POSTED",
            symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id,
            yes_price=str(model["yes_quote"]), no_price=str(model["no_quote"]),
            entry_modeled_edge=str(model["modeled_edge"]),
            entry_pair_edge_floor=str(ENTRY_PAIR_EDGE), final_pair_edge_floor=str(FINAL_PAIR_EDGE),
            initial_wait_seconds=INITIAL_WAIT_SECONDS, rescue_seconds=RESCUE_SECONDS,
            order_entry=True,
        )
        first = await _wait_for_first_fill(
            maker,
            yes_id=yes_id, no_id=no_id,
            yes_price=model["yes_quote"], no_price=model["no_quote"],
            deadline=initial_deadline,
        )

        rescue: dict[str, Any] | None = None
        if first is None:
            yes_cancel, _, _, yes_fill = await _cancel(maker, client_id=yes_id, symbol=event.symbol, row=yes_row)
            no_cancel, _, _, no_fill = await _cancel(maker, client_id=no_id, symbol=event.symbol, row=no_row)
            if yes_fill == ZERO and no_fill == ZERO and yes_cancel.get("ok") and no_cancel.get("ok"):
                await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="DYNAMIC_INITIAL_WINDOW_ZERO_FILL")
                reserved = False
                emit("COMPLETE", status="NO_FILL_RELEASED", order_entry=True)
                return
            first = {
                "outcome": "YES" if yes_fill > ZERO else "NO",
                "quantity": yes_fill if yes_fill > ZERO else no_fill,
                "price": model["yes_quote"] if yes_fill > ZERO else model["no_quote"],
                "status": "REST_RECONCILED_FILL",
            }
        emit(
            "DYNAMIC_FIRST_FILL",
            outcome=first["outcome"], quantity=str(first["quantity"]),
            price=str(first["price"]), ws_order_status=first["status"], order_entry=True,
        )
        if D(str(first["quantity"])) != ONE:
            # Do not attempt dynamic reposts from fractional/ambiguous first inventory.
            await _cancel(maker, client_id=yes_id, symbol=event.symbol, row=yes_row)
            await _cancel(maker, client_id=no_id, symbol=event.symbol, row=no_row)
            rescue = {"status": "FRACTIONAL_FIRST_FILL_HARD_HALT", "paired": False, "hard_halt": True}
        else:
            complement_id = no_id if first["outcome"] == "YES" else yes_id
            complement_price = model["no_quote"] if first["outcome"] == "YES" else model["yes_quote"]
            complement_row = no_row if first["outcome"] == "YES" else yes_row
            rescue = await _dynamic_rescue(
                maker,
                event=event, trade_id=trade_id,
                filled_outcome=str(first["outcome"]), filled_price=D(str(first["price"])),
                complement_initial_id=complement_id,
                complement_initial_price=D(str(complement_price)),
                complement_initial_row=complement_row,
                yes_ids=yes_ids, no_ids=no_ids,
            )

        await asyncio.sleep(0.2)
        yes_fill, yes_missing = await _aggregate_ids(yes_ids, event.symbol)
        no_fill, no_missing = await _aggregate_ids(no_ids, event.symbol)
        emit(
            "DYNAMIC_RECONCILED",
            rescue_status=rescue.get("status") if rescue else None,
            yes_fill=str(yes_fill), no_fill=str(no_fill),
            yes_client_order_ids=yes_ids, no_client_order_ids=no_ids,
            missing_terminal_ids=yes_missing + no_missing,
            order_entry=True,
        )
        if not safe_inventory(yes_fill, no_fill):
            raise RuntimeError("dynamic complement aggregate inventory exceeded q=1 bound")

        status = str(rescue.get("status") if rescue else "DYNAMIC_UNKNOWN")
        if yes_fill == no_fill == ONE:
            status = "DYNAMIC_MATCHED_INVENTORY_NOT_SETTLED"
        elif yes_fill != no_fill:
            status = "DYNAMIC_ONE_SIDED_INVENTORY_NOT_SETTLED"
        elif yes_fill == no_fill == ZERO and (yes_missing or no_missing):
            status = "DYNAMIC_HISTORY_RECONCILIATION_PENDING"

        await budget.result(
            symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id,
            status=status, yes_fill=str(yes_fill), no_fill=str(no_fill),
            metadata={
                "yes_client_order_ids": yes_ids,
                "no_client_order_ids": no_ids,
                "order_execution_modes": {cid: "MAKER" for cid in yes_ids + no_ids},
                "entry_yes_price": str(model["yes_quote"]),
                "entry_no_price": str(model["no_quote"]),
                "entry_modeled_edge": str(model["modeled_edge"]),
                "entry_pair_edge_floor": str(ENTRY_PAIR_EDGE),
                "final_pair_edge_floor": str(FINAL_PAIR_EDGE),
                "dynamic_rescue": rescue,
                "missing_terminal_ids": yes_missing + no_missing,
                "settlement_required": True,
                "taker_rescue_enabled": False,
            },
        )
    finally:
        await maker.close()
        if reserved:
            pass


if __name__ == "__main__":
    asyncio.run(run())
