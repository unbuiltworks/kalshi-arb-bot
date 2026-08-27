"""One-campaign Gemini BTC 5m q=1 two-sided maker canary.

Purpose: validate real Gemini queue/fill/cancel/settlement plumbing for the closest
available analogue to the short-duration reference-wallet strategy. This is NOT a
claim that Polymarket economics transfer to Gemini.

Live mutation requires a dual-key arm:
  GEMINI_SHORT_CRYPTO_LIVE_Q1=YES
  GEMINI_SHORT_CRYPTO_CANARY_ID=<nonempty>
  GEMINI_SHORT_CRYPTO_CANARY_ARM=<same value>

Hard bounds:
- BTC 5m only;
- quantity exactly 1 and contract metadata must support whole-contract q=1;
- exactly one campaign per process;
- both orders are BUY LIMIT MOC (maker-only), YES and NO on the same event;
- modeled pair edge after conservative current Gemini maker fees must be >=3c;
- first observed fill freezes the campaign and cancels both resting orders;
- if both q=1 orders filled, status is FILLED_NOT_SETTLED (never realized here);
- any one-sided q=1 fill is flattened immediately with MARKET IOC and hard-stops;
- any fractional/ambiguous fill or cancel ambiguity hard-stops and is not called profit.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import re
import time
from typing import Any, Mapping

import httpx
import websockets

from src import gemini_short_crypto_binary_budget as budget
from src.gemini_kalshi_execution_shadow import gemini_predictions_maker_fee
from src.gemini_kalshi_live_service import _gemini_available_usd, _gemini_terms_ok
from src.gemini_kalshi_probe_transport import GeminiTradingSession
from src.gemini_kalshi_xrp_maker_live_canary import GeminiMakerSession, _cancel_maker, _order_by_client

D = Decimal
ONE = D("1")
TARGET = D("0.03")
REST = "https://api.gemini.com"
WS = "wss://ws.gemini.com"
EVENT_RE = re.compile(r"^BTC05M(\d{10})$")
LIVE_ENV = "GEMINI_SHORT_CRYPTO_LIVE_Q1"
CANARY_ID_ENV = "GEMINI_SHORT_CRYPTO_CANARY_ID"
CANARY_ARM_ENV = "GEMINI_SHORT_CRYPTO_CANARY_ARM"
MIN_SECONDS_TO_EXPIRY = 75.0
REST_SECONDS = 45.0


def emit(stage: str, **fields: Any) -> None:
    print("GEMINI_BTC5M_MAKER_Q1 " + json.dumps({"stage": stage, **fields}, separators=(",", ":"), default=str), flush=True)


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().upper() in {"1", "TRUE", "YES", "ON"}


def live_armed() -> bool:
    cid = os.getenv(CANARY_ID_ENV, "").strip()
    return bool(_enabled(LIVE_ENV) and cid and os.getenv(CANARY_ARM_ENV, "").strip() == cid)


def _clean_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.strip())[:40]
    return safe or "disarmed"


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


@dataclass(frozen=True)
class Event:
    ticker: str
    symbol: str
    expiry_ms: int
    quantity_minimum: D
    quantity_increment: D
    price_increment: D

    @property
    def seconds_to_expiry(self) -> float:
        return (self.expiry_ms - int(time.time() * 1000)) / 1000.0


async def discover_btc5m() -> Event | None:
    found: list[Event] = []
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True, headers={"User-Agent": "arb-gemini-btc5m-canary/1.0"}) as http:
        offset = 0
        while offset < 5000:
            response = await http.get(
                f"{REST}/v1/prediction-markets/events",
                params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") or [] if isinstance(payload, dict) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ticker = str(row.get("ticker") or "").strip().upper()
                if not EVENT_RE.match(ticker):
                    continue
                for contract in row.get("contracts") or []:
                    if not isinstance(contract, Mapping):
                        continue
                    symbol = str(contract.get("instrumentSymbol") or "").strip()
                    state = str(contract.get("marketState") or contract.get("status") or "").lower()
                    if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                        continue
                    expiry = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
                    if expiry is None or expiry <= int(time.time() * 1000):
                        continue
                    found.append(Event(
                        ticker=ticker,
                        symbol=symbol,
                        expiry_ms=expiry,
                        quantity_minimum=D(str(contract.get("quantityMinimum") or "1")),
                        quantity_increment=D(str(contract.get("quantityIncrement") or "1")),
                        price_increment=D(str(contract.get("priceIncrement") or "0.01")),
                    ))
            pagination = payload.get("pagination") if isinstance(payload, dict) else None
            total = int((pagination or {}).get("total") or len(rows))
            offset += len(rows)
            if not rows or offset >= total:
                break
    eligible = [e for e in found if e.seconds_to_expiry >= MIN_SECONDS_TO_EXPIRY]
    return min(eligible, key=lambda e: e.expiry_ms) if eligible else None


def _top(data: Any) -> tuple[D, D, D, D] | None:
    if not isinstance(data, Mapping):
        return None
    bids = data.get("bids")
    asks = data.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    pb: list[tuple[D, D]] = []
    pa: list[tuple[D, D]] = []
    for row in bids:
        try:
            p, q = D(str(row[0])), D(str(row[1]))
        except Exception:
            continue
        if D("0") < p < ONE and q > 0:
            pb.append((p, q))
    for row in asks:
        try:
            p, q = D(str(row[0])), D(str(row[1]))
        except Exception:
            continue
        if D("0") < p < ONE and q > 0:
            pa.append((p, q))
    if not pb or not pa:
        return None
    bid, bid_size = max(pb, key=lambda x: x[0])
    ask, ask_size = min(pa, key=lambda x: x[0])
    if not D("0") < bid < ask < ONE:
        return None
    return bid, bid_size, ask, ask_size


async def fresh_top(symbol: str, timeout: float = 4.0) -> tuple[D, D, D, D]:
    async with websockets.connect(WS, open_timeout=5.0, ping_interval=15, ping_timeout=8, max_queue=5000) as ws:
        await ws.send(json.dumps({"id": f"btc5m-{symbol}", "method": "SUBSCRIBE", "params": [f"{symbol}@depth5@100ms"]}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            try:
                data = json.loads(raw)
            except Exception:
                continue
            top = _top(data)
            if top is not None:
                return top
    raise RuntimeError("no fresh Gemini BTC5m depth")


def pair_model(bid: D, ask: D) -> dict[str, D]:
    yes_quote = bid
    no_quote = ONE - ask
    yes_fee = gemini_predictions_maker_fee(yes_quote, ONE)
    no_fee = gemini_predictions_maker_fee(no_quote, ONE)
    pair_cost = yes_quote + no_quote
    edge = ONE - pair_cost - yes_fee - no_fee
    return {
        "yes_quote": yes_quote,
        "no_quote": no_quote,
        "yes_fee": yes_fee,
        "no_fee": no_fee,
        "pair_cost": pair_cost,
        "modeled_edge": edge,
        "spread": ask - bid,
    }


async def _find_row(client_id: str, symbol: str) -> dict[str, Any] | None:
    for _ in range(20):
        row = await _order_by_client(client_id, symbol)
        if row is not None:
            return row
        await asyncio.sleep(0.05)
    return None


def _filled(row: Mapping[str, Any] | None) -> D:
    if not row:
        return D("0")
    return D(str(row.get("filledQuantity") or "0"))


async def _reconcile_pair(yes_id: str, no_id: str, symbol: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, D, D]:
    yes_row = await _order_by_client(yes_id, symbol)
    no_row = await _order_by_client(no_id, symbol)
    return yes_row, no_row, _filled(yes_row), _filled(no_row)


async def _flatten(params: dict[str, Any]) -> dict[str, Any]:
    session = GeminiTradingSession()
    try:
        await session.connect()
        result = await session.emergency_flatten_market(params, timeout=3.0)
        return result.as_dict()
    finally:
        await session.close()


async def preflight() -> tuple[Event | None, dict[str, Any]]:
    event = await discover_btc5m()
    terms = await _gemini_terms_ok()
    available = await _gemini_available_usd()
    if event is None:
        return None, {"terms_ok": terms, "available_usd": str(available), "market": None, "armed": live_armed(), "order_entry": False}
    bid, bid_size, ask, ask_size = await fresh_top(event.symbol)
    model = pair_model(bid, ask)
    q1_supported = event.quantity_minimum == ONE and event.quantity_increment == ONE
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
        "passes_3c_pair_floor": model["modeled_edge"] >= TARGET,
        "order_entry": False,
        "realized_pnl": 0,
    }
    return event, report


async def run() -> None:
    event, report = await preflight()
    emit("PREFLIGHT", **report)
    if event is None:
        emit("STOP", reason="NO_BTC5M_EVENT", order_entry=False)
        return
    if not live_armed():
        emit("DISARMED", reason="DUAL_KEY_NOT_ARMED", order_entry=False)
        return
    if not report.get("terms_ok"):
        raise RuntimeError("Gemini Prediction Markets terms are not accepted")
    if D(str(report.get("available_usd") or "0")) < D("2"):
        raise RuntimeError("Gemini available USD below conservative $2 canary reserve")
    if not report.get("q1_supported"):
        raise RuntimeError("current BTC5m contract does not support whole-contract q=1")
    if not report.get("passes_3c_pair_floor"):
        emit("STOP", reason="PAIR_FLOOR_BELOW_3C", order_entry=False)
        return

    # Re-read the book immediately before mutation; stale discovery prices are never sent.
    bid, _, ask, _ = await fresh_top(event.symbol)
    model = pair_model(bid, ask)
    if model["modeled_edge"] < TARGET:
        emit("STOP", reason="FRESH_PAIR_FLOOR_BELOW_3C", model={k: str(v) for k, v in model.items()}, order_entry=False)
        return
    if event.seconds_to_expiry < MIN_SECONDS_TO_EXPIRY:
        emit("STOP", reason="EXPIRY_TOO_CLOSE", seconds_to_expiry=event.seconds_to_expiry, order_entry=False)
        return

    cid = _clean_id(os.getenv(CANARY_ID_ENV, ""))
    trade_id = f"crypto-gem-btc5m-{cid}"[:64]
    yes_id = f"{trade_id}-yes"[:36]
    no_id = f"{trade_id}-no"[:36]
    yes_params = {
        "symbol": event.symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC",
        "price": str(model["yes_quote"]), "quantity": "1", "eventOutcome": "YES", "clientOrderId": yes_id,
    }
    no_params = {
        "symbol": event.symbol, "side": "BUY", "type": "LIMIT", "timeInForce": "MOC",
        "price": str(model["no_quote"]), "quantity": "1", "eventOutcome": "NO", "clientOrderId": no_id,
    }

    await budget.reserve(
        symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id,
        yes_quote=str(model["yes_quote"]), no_quote=str(model["no_quote"]), modeled_pair_edge=str(model["modeled_edge"]),
    )
    reserved = True
    maker = GeminiMakerSession()
    try:
        await maker.connect()
        yes_ack = await maker.request("order.place", yes_params, timeout=4.0)
        if int(yes_ack.get("status", 0)) != 200:
            await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="YES_MAKER_REJECTED")
            reserved = False
            emit("STOP", reason="YES_MAKER_REJECTED", ack_status=yes_ack.get("status"), order_entry=True)
            return
        yes_row = await _find_row(yes_id, event.symbol)

        no_ack = await maker.request("order.place", no_params, timeout=4.0)
        if int(no_ack.get("status", 0)) != 200:
            yes_cancel = await _cancel_maker(maker, row=yes_row)
            yrow, _, yfill, _ = await _reconcile_pair(yes_id, no_id, event.symbol)
            if yfill == 0 and yes_cancel.get("ok"):
                await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="NO_MAKER_REJECTED_ZERO_FILL")
                reserved = False
                emit("STOP", reason="NO_MAKER_REJECTED_ZERO_FILL", order_entry=True)
                return
            flatten = await _flatten(yes_params) if yfill == ONE else None
            status = "SECOND_POST_FAILED_AFTER_FIRST_MUTATION_HARD_HALT"
            await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status,
                                legs_filled=1 if yfill > 0 else 0, metadata={"yes_fill": str(yfill), "yes_cancel": yes_cancel, "flatten": flatten})
            emit("HARD_HALT", status=status, yes_fill=str(yfill), flatten=flatten, order_entry=True)
            return
        no_row = await _find_row(no_id, event.symbol)
        emit("BOTH_MAKERS_POSTED", symbol=event.symbol, yes_price=str(model["yes_quote"]), no_price=str(model["no_quote"]),
             modeled_pair_edge=str(model["modeled_edge"]), order_entry=True)

        deadline = min(time.monotonic() + REST_SECONDS, time.monotonic() + max(1.0, event.seconds_to_expiry - 30.0))
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

        # Freeze campaign after first fill or timeout. Both orders are cancelled before reconciliation.
        yes_row = await _order_by_client(yes_id, event.symbol) or yes_row
        no_row = await _order_by_client(no_id, event.symbol) or no_row
        yes_cancel, no_cancel = await asyncio.gather(
            _cancel_maker(maker, row=yes_row),
            _cancel_maker(maker, row=no_row),
        )
        await asyncio.sleep(0.15)
        yes_row, no_row, yes_fill, no_fill = await _reconcile_pair(yes_id, no_id, event.symbol)
        emit("RECONCILED", yes_fill=str(yes_fill), no_fill=str(no_fill), yes_cancel=yes_cancel, no_cancel=no_cancel, order_entry=True)

        if yes_fill == 0 and no_fill == 0:
            if not yes_cancel.get("ok") or not no_cancel.get("ok"):
                status = "ZERO_FILL_CANCEL_AMBIGUOUS_HARD_HALT"
                await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status, legs_filled=0,
                                    metadata={"yes_cancel": yes_cancel, "no_cancel": no_cancel})
                emit("HARD_HALT", status=status, order_entry=True)
                return
            await budget.release(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, reason="ZERO_FILL_TIMEOUT")
            reserved = False
            emit("COMPLETE", status="NO_FILL_RELEASED", order_entry=True, realized=False)
            return

        if yes_fill == ONE and no_fill == ONE:
            status = "FILLED_NOT_SETTLED"
            await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status, legs_filled=2,
                                metadata={"yes_price": str(model["yes_quote"]), "no_price": str(model["no_quote"]),
                                          "modeled_pair_edge": str(model["modeled_edge"]), "settlement_required": True})
            emit("COMPLETE", status=status, order_entry=True, realized=False)
            return

        if yes_fill == ONE and no_fill == 0:
            flatten = await _flatten(yes_params)
            status = "ONE_SIDED_YES_FILLED_FLATTENED_HARD_HALT"
            await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status, legs_filled=1,
                                metadata={"yes_fill": "1", "no_fill": "0", "flatten": flatten})
            emit("HARD_HALT", status=status, flatten=flatten, order_entry=True)
            return

        if no_fill == ONE and yes_fill == 0:
            flatten = await _flatten(no_params)
            status = "ONE_SIDED_NO_FILLED_FLATTENED_HARD_HALT"
            await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status, legs_filled=1,
                                metadata={"yes_fill": "0", "no_fill": "1", "flatten": flatten})
            emit("HARD_HALT", status=status, flatten=flatten, order_entry=True)
            return

        # q=1 whole-contract markets should not land here. Do not guess how to flatten an unexpected fractional state.
        status = "UNEXPECTED_FILL_SHAPE_HARD_HALT"
        await budget.result(symbol=event.symbol, event_ticker=event.ticker, trade_id=trade_id, status=status,
                            legs_filled=int(yes_fill > 0) + int(no_fill > 0),
                            metadata={"yes_fill": str(yes_fill), "no_fill": str(no_fill), "manual_review_required": True})
        emit("HARD_HALT", status=status, yes_fill=str(yes_fill), no_fill=str(no_fill), order_entry=True)
    finally:
        await maker.close()
        if reserved:
            # Filled/ambiguous states intentionally keep the durable reservation until explicit reconciliation.
            pass


if __name__ == "__main__":
    asyncio.run(run())
