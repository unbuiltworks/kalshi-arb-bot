"""Read-only target attribution with connection-conserving maker-shadow feeds.

The base maker shadow intentionally mixes several required-profit cohorts in its
aggregate fill counters. This wrapper adds per-target fill/profit/stale-hedge counters
and replaces its rate-limited per-asset market-data sockets with:

* one shared public Gemini Prediction WebSocket using the read-only ``depth`` RPC;
* public exact Kalshi REST orderbooks; and
* one shared public Kalshi REST completed-trade poller.

Trade prints still come from Kalshi's actual completed-trade feed. REST polling latency
is conservative because it can only make the observed hedge older. No order transport
is added. Quote generation, queue consumption, fee math, settlement classification,
and execution eligibility remain unchanged. All outputs remain
INDEX_MISMATCH_RESEARCH_ONLY.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
import json
import time
from typing import Any

import httpx
import websockets

from src import gemini_kalshi_maker_shadow as base

D = Decimal
_INSTALLED = False
_TARGET_STATS: dict[int, dict[str, dict[str, Decimal | int]]] = {}
KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
BOOK_POLL_S = 0.20
TRADE_POLL_S = 0.20
GEMINI_DEPTH = 5
GEMINI_REQUEST_SPACING_S = 0.05
MAX_BACKOFF_S = 2.0

_GEMINI_SUBSCRIBERS: dict[str, dict[int, tuple[asyncio.Queue, Any]]] = {}
_GEMINI_MANAGER: asyncio.Task | None = None
_GEMINI_CHANGE: asyncio.Event | None = None
_GEMINI_EMPTY_BOOK_LOG_AT: dict[str, float] = {}

_TRADE_SUBSCRIBERS: dict[str, dict[int, tuple[asyncio.Queue, Any]]] = {}
_TRADE_MANAGER: asyncio.Task | None = None
_TRADE_CHANGE: asyncio.Event | None = None
_SEEN_TRADE_IDS: set[str] = set()
_SEEN_TRADE_ORDER: deque[str] = deque()
MAX_SEEN_TRADES = 20_000


def _target_from_key(key: str) -> str | None:
    try:
        _direction, _policy, target = str(key).rsplit("|", 2)
        return str(D(target))
    except Exception:
        return None


def _bucket(stats: Any, target: str) -> dict[str, Decimal | int]:
    by_target = _TARGET_STATS.setdefault(id(stats), {})
    return by_target.setdefault(
        target,
        {"fills": 0, "profit": D("0"), "stale_hedges": 0},
    )


def _gemini_change_event() -> asyncio.Event:
    global _GEMINI_CHANGE
    if _GEMINI_CHANGE is None:
        _GEMINI_CHANGE = asyncio.Event()
    return _GEMINI_CHANGE


def _gemini_symbols() -> set[str]:
    return {symbol for symbol, subscribers in _GEMINI_SUBSCRIBERS.items() if subscribers}


def _best_level(rows: Any, *, highest: bool) -> tuple[D, D] | None:
    levels: list[tuple[D, D]] = []
    if not isinstance(rows, list):
        return None
    for row in rows:
        try:
            if isinstance(row, dict):
                price = D(str(row.get("price")))
                qty = D(str(row.get("quantity") or row.get("amount") or row.get("qty")))
            else:
                price = D(str(row[0]))
                qty = D(str(row[1]))
        except Exception:
            continue
        if D("0") <= price <= D("1") and qty > 0:
            levels.append((price, qty))
    if not levels:
        return None
    return (max if highest else min)(levels, key=lambda level: level[0])


def _gemini_top_from_depth(symbol: str, payload: dict[str, Any], quote_ts_ms: int):
    candidates = [payload.get("result"), payload.get("data"), payload]
    data = next(
        (
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and "bids" in candidate and "asks" in candidate
        ),
        None,
    )
    if data is None:
        return None
    bid = _best_level(data.get("bids"), highest=True)
    ask = _best_level(data.get("asks"), highest=False)
    if bid is None or ask is None:
        return None
    try:
        return base.parse_gemini_book_ticker(
            {
                "s": symbol,
                "b": str(bid[0]),
                "B": str(bid[1]),
                "a": str(ask[0]),
                "A": str(ask[1]),
            },
            received_ms=int(quote_ts_ms),
        )
    except Exception:
        return None


async def _shared_gemini_manager() -> None:
    backoff = 1.0
    seq = 0
    while True:
        symbols = sorted(_gemini_symbols())
        if not symbols:
            change = _gemini_change_event()
            change.clear()
            await change.wait()
            continue
        ws = None
        try:
            ws = await websockets.connect(base.GEMINI_WS, open_timeout=5.0)
            backoff = 1.0
            base.logger.info(
                "MAKER_SHADOW_GEMINI_SHARED_CONNECTED %s",
                json.dumps(
                    {
                        "symbols": symbols,
                        "connections": 1,
                        "transport": "PUBLIC_SHARED_WS_DEPTH_RPC",
                        "depth": GEMINI_DEPTH,
                        "request_spacing_s": GEMINI_REQUEST_SPACING_S,
                        "diagnostic_only": True,
                        "read_only": True,
                        "order_entry": False,
                    },
                    separators=(",", ":"),
                ),
            )
            while _gemini_symbols():
                for symbol in sorted(_gemini_symbols()):
                    subscribers = list(_GEMINI_SUBSCRIBERS.get(symbol, {}).values())
                    if not subscribers:
                        continue
                    seq += 1
                    request_id = f"maker-depth-{seq}-{symbol}"
                    request_start_ms = int(time.time() * 1000)
                    await ws.send(
                        json.dumps(
                            {
                                "id": request_id,
                                "method": "depth",
                                "params": {"symbol": symbol, "limit": GEMINI_DEPTH},
                            },
                            separators=(",", ":"),
                        )
                    )
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    payload = json.loads(raw)
                    if str(payload.get("id") or "") != request_id:
                        raise RuntimeError("Gemini maker depth RPC response id mismatch")
                    if int(payload.get("status", 200)) >= 400:
                        raise RuntimeError(f"Gemini maker depth RPC status={payload.get('status')}")
                    top = _gemini_top_from_depth(symbol, payload, request_start_ms)
                    if top is None:
                        # A prediction market can briefly expose a one-sided/empty book.
                        # That symbol is unusable for safe quote generation, but it is
                        # not evidence that the shared transport failed. Keep the socket
                        # alive and fail closed for this symbol until a two-sided depth
                        # response appears.
                        now = time.monotonic()
                        if now - _GEMINI_EMPTY_BOOK_LOG_AT.get(symbol, 0.0) >= 30.0:
                            base.logger.info(
                                "MAKER_SHADOW_GEMINI_BOOK_UNUSABLE symbol=%s action=skip_snapshot "
                                "socket_reconnect=false inferred_book=false read_only=true",
                                symbol,
                            )
                            _GEMINI_EMPTY_BOOK_LOG_AT[symbol] = now
                        await asyncio.sleep(GEMINI_REQUEST_SPACING_S)
                        continue
                    receive_ms = int(time.time() * 1000)
                    for queue, stats in subscribers:
                        base._enqueue(queue, ("gemini", receive_ms, top), stats)
                    await asyncio.sleep(GEMINI_REQUEST_SPACING_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            base.logger.warning(
                "MAKER_SHADOW_GEMINI_SHARED_RECONNECT type=%s error=%s backoff_s=%.1f",
                type(exc).__name__, str(exc)[:180], backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)
        finally:
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass


async def _gemini_shared_stream(symbol: str, queue: asyncio.Queue, stats: Any) -> None:
    global _GEMINI_MANAGER
    token = id(asyncio.current_task())
    _GEMINI_SUBSCRIBERS.setdefault(symbol, {})[token] = (queue, stats)
    _gemini_change_event().set()
    if _GEMINI_MANAGER is None or _GEMINI_MANAGER.done():
        _GEMINI_MANAGER = asyncio.create_task(
            _shared_gemini_manager(), name="maker-shadow-gemini-shared-depth"
        )
    try:
        await asyncio.Future()
    finally:
        subscribers = _GEMINI_SUBSCRIBERS.get(symbol)
        if subscribers is not None:
            subscribers.pop(token, None)
            if not subscribers:
                _GEMINI_SUBSCRIBERS.pop(symbol, None)
        _gemini_change_event().set()


def _trade_change_event() -> asyncio.Event:
    global _TRADE_CHANGE
    if _TRADE_CHANGE is None:
        _TRADE_CHANGE = asyncio.Event()
    return _TRADE_CHANGE


def _mark_trade_seen(trade_id: str) -> bool:
    if not trade_id or trade_id in _SEEN_TRADE_IDS:
        return False
    _SEEN_TRADE_IDS.add(trade_id)
    _SEEN_TRADE_ORDER.append(trade_id)
    while len(_SEEN_TRADE_ORDER) > MAX_SEEN_TRADES:
        old = _SEEN_TRADE_ORDER.popleft()
        _SEEN_TRADE_IDS.discard(old)
    return True


def _parse_created_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _rest_trade(row: dict[str, Any], receive_ms: int) -> base.KalshiTrade | None:
    try:
        ticker = str(row.get("ticker") or "")
        yes_price = D(str(row["yes_price_dollars"]))
        no_price = D(str(row["no_price_dollars"]))
        quantity = D(str(row.get("count_fp") or "0"))
        taker_side = str(row.get("taker_side") or row.get("taker_outcome_side") or "").lower()
    except Exception:
        return None
    if not ticker or taker_side not in {"yes", "no"} or quantity <= 0:
        return None
    ts_ms = _parse_created_ms(row.get("created_time")) or int(receive_ms)
    return base.KalshiTrade(
        ticker=ticker,
        yes_price=yes_price,
        no_price=no_price,
        quantity=quantity,
        taker_side=taker_side,
        ts_ms=ts_ms,
    )


def _rest_levels(rows: Any) -> dict[D, D]:
    out: dict[D, D] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        try:
            price = D(str(row[0]))
            qty = D(str(row[1]))
        except Exception:
            continue
        if D("0") <= price <= D("1") and qty > 0:
            out[price] = qty
    return out


def _rest_top(ticker: str, payload: dict[str, Any], quote_ts_ms: int):
    book = payload.get("orderbook_fp") or {}
    yes = _rest_levels(book.get("yes_dollars"))
    no = _rest_levels(book.get("no_dollars"))
    if not yes or not no:
        return None
    yes_bid, yes_bid_size = max(yes.items(), key=lambda item: item[0])
    no_bid, no_bid_size = max(no.items(), key=lambda item: item[0])
    yes_ask = D("1") - no_bid
    if not (D("0") <= yes_bid <= yes_ask <= D("1")):
        return None
    return base.TopOfBook(
        venue="kalshi",
        market_id=ticker,
        ts_ms=int(quote_ts_ms),
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=no_bid_size,
    )


async def _shared_trade_manager() -> None:
    backoff = TRADE_POLL_S
    async with httpx.AsyncClient(timeout=2.0) as http:
        while True:
            tickers = {ticker for ticker, subscribers in _TRADE_SUBSCRIBERS.items() if subscribers}
            if not tickers:
                change = _trade_change_event()
                change.clear()
                await change.wait()
                continue
            receive_ms = int(time.time() * 1000)
            try:
                response = await http.get(
                    f"{KALSHI_REST}/markets/trades",
                    params={
                        "limit": 1000,
                        "min_ts": max(0, int(time.time()) - 3),
                        "is_block_trade": "false",
                    },
                )
                if response.status_code == 429:
                    base.logger.warning(
                        "MAKER_SHADOW_KALSHI_REST_TRADES_429 backoff_s=%.3f",
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(MAX_BACKOFF_S, max(TRADE_POLL_S, backoff * 2))
                    continue
                if response.status_code != 200:
                    await asyncio.sleep(backoff)
                    backoff = min(MAX_BACKOFF_S, max(TRADE_POLL_S, backoff * 2))
                    continue
                backoff = TRADE_POLL_S
                rows = response.json().get("trades") or []
                for row in reversed(rows):
                    ticker = str(row.get("ticker") or "")
                    if ticker not in tickers:
                        continue
                    trade_id = str(row.get("trade_id") or "")
                    if not _mark_trade_seen(trade_id):
                        continue
                    trade = _rest_trade(row, receive_ms)
                    if trade is None:
                        continue
                    for queue, stats in list(_TRADE_SUBSCRIBERS.get(ticker, {}).values()):
                        stats.public_trades += 1
                        base._enqueue(queue, ("trade", receive_ms, trade), stats)
                await asyncio.sleep(TRADE_POLL_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                base.logger.warning(
                    "MAKER_SHADOW_KALSHI_REST_TRADES_ERROR type=%s error=%s backoff_s=%.3f",
                    type(exc).__name__, str(exc)[:180], backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF_S, max(TRADE_POLL_S, backoff * 2))


async def _kalshi_rest_stream(ticker: str, queue: asyncio.Queue, stats: Any) -> None:
    global _TRADE_MANAGER
    token = id(asyncio.current_task())
    _TRADE_SUBSCRIBERS.setdefault(ticker, {})[token] = (queue, stats)
    _trade_change_event().set()
    if _TRADE_MANAGER is None or _TRADE_MANAGER.done():
        _TRADE_MANAGER = asyncio.create_task(
            _shared_trade_manager(), name="maker-shadow-kalshi-rest-trades"
        )
    backoff = BOOK_POLL_S
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    try:
        async with httpx.AsyncClient(timeout=2.0, limits=limits) as http:
            while True:
                request_start_ms = int(time.time() * 1000)
                try:
                    response = await http.get(
                        f"{KALSHI_REST}/markets/{ticker}/orderbook",
                        params={"depth": 10},
                    )
                    if response.status_code == 429:
                        base.logger.warning(
                            "MAKER_SHADOW_KALSHI_REST_BOOK_429 asset=%s ticker=%s backoff_s=%.3f",
                            stats.asset, ticker, backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(MAX_BACKOFF_S, max(BOOK_POLL_S, backoff * 2))
                        continue
                    if response.status_code != 200:
                        await asyncio.sleep(backoff)
                        backoff = min(MAX_BACKOFF_S, max(BOOK_POLL_S, backoff * 2))
                        continue
                    top = _rest_top(ticker, response.json(), request_start_ms)
                    if top is not None:
                        receive_ms = int(time.time() * 1000)
                        base._enqueue(queue, ("kalshi", receive_ms, top), stats)
                    backoff = BOOK_POLL_S
                    await asyncio.sleep(BOOK_POLL_S)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    base.logger.warning(
                        "MAKER_SHADOW_KALSHI_REST_BOOK_ERROR asset=%s ticker=%s type=%s error=%s backoff_s=%.3f",
                        stats.asset, ticker, type(exc).__name__, str(exc)[:180], backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(MAX_BACKOFF_S, max(BOOK_POLL_S, backoff * 2))
    finally:
        subscribers = _TRADE_SUBSCRIBERS.get(ticker)
        if subscribers is not None:
            subscribers.pop(token, None)
            if not subscribers:
                _TRADE_SUBSCRIBERS.pop(ticker, None)
        _trade_change_event().set()


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_process_trade = base._process_trade
    original_payload = base.MakerStats.payload

    def process_trade_with_target_attribution(**kwargs):
        stats = kwargs["stats"]
        events = original_process_trade(**kwargs)
        for row in events:
            event = str(row.get("event") or "")
            target = row.get("target_profit")
            if target is None:
                target = _target_from_key(str(row.get("key") or ""))
            if target is None:
                continue
            target_s = str(D(str(target)))
            bucket = _bucket(stats, target_s)
            if event == "SIMULATED_FILL":
                bucket["fills"] = int(bucket["fills"]) + 1
                bucket["profit"] = D(str(bucket["profit"])) + D(
                    str(row.get("simulated_fill_profit") or "0")
                )
            elif event == "FILL_HEDGE_UNAVAILABLE":
                bucket["stale_hedges"] = int(bucket["stale_hedges"]) + 1
        return events

    def payload_with_target_attribution(self, active_quotes: int, queue_size: int):
        payload = original_payload(self, active_quotes, queue_size)
        rows = _TARGET_STATS.get(id(self), {})
        by_target = {
            target: {
                "fills": int(row["fills"]),
                "profit": str(row["profit"]),
                "stale_hedges": int(row["stale_hedges"]),
            }
            for target, row in sorted(rows.items(), key=lambda item: D(item[0]))
        }
        live_floor = D("0.03")
        payload["by_target"] = by_target
        payload["fills_at_or_above_3c_target"] = sum(
            int(row["fills"]) for target, row in rows.items() if D(target) >= live_floor
        )
        payload["stale_hedges_at_or_above_3c_target"] = sum(
            int(row["stale_hedges"]) for target, row in rows.items() if D(target) >= live_floor
        )
        payload["profit_at_or_above_3c_target"] = str(
            sum(
                (D(str(row["profit"])) for target, row in rows.items() if D(target) >= live_floor),
                D("0"),
            )
        )
        payload["target_attribution"] = "DIAGNOSTIC_ONLY"
        payload["live_floor_reference"] = "0.03"
        payload["gemini_transport"] = "ONE_PUBLIC_SHARED_WS_DEPTH_RPC"
        payload["kalshi_transport"] = "REST_EXACT_BOOK_PLUS_SHARED_REST_TRADES"
        payload["gemini_request_spacing_s"] = GEMINI_REQUEST_SPACING_S
        payload["rest_trade_poll_s"] = TRADE_POLL_S
        payload["rest_book_poll_s"] = BOOK_POLL_S
        payload["risk_gates_changed"] = False
        payload["execution_eligible"] = False
        payload["read_only"] = True
        payload["order_entry"] = False
        return payload

    base._process_trade = process_trade_with_target_attribution
    base.MakerStats.payload = payload_with_target_attribution
    base._gemini_stream = _gemini_shared_stream
    base._kalshi_stream = _kalshi_rest_stream
    _INSTALLED = True
    base.logger.warning(
        "MAKER_SHADOW_CONNECTION_CONSOLIDATION_INSTALLED gemini=one_public_shared_depth_rpc "
        "kalshi=rest_exact_book_plus_shared_rest_trades book_poll_s=%.2f trade_poll_s=%.2f "
        "one_sided_gemini_book=skip_without_reconnect target_attribution=true "
        "diagnostic_only=true read_only=true order_entry=false",
        BOOK_POLL_S,
        TRADE_POLL_S,
    )


def main() -> None:
    install()
    from src import gemini_kalshi_maker_shadow_service as service
    service.main()


if __name__ == "__main__":
    main()
