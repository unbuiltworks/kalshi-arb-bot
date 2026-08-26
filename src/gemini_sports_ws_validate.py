"""Read-only Gemini sports live-book validator.

Compares Gemini prediction-market active-events q=1 prices with the actual current
order book and then watches bookTicker updates for the same symbols. No private
authentication or order-entry code is used.
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import time
from typing import Any

import httpx
import websockets

from src.gemini_kalshi_live_shadow import GEMINI_WS, parse_gemini_book_ticker
from src.three_venue_mlb_price_scan import _gemini_contracts
from src.universal_three_venue_sports import fetch_gemini_mlb

D = Decimal
logger = logging.getLogger("kalshi_arb.gemini_sports_ws_validate")
MAX_WS_MESSAGE_BYTES = 8 * 1024 * 1024
GEMINI_REST = "https://api.gemini.com"


def _snapshot_prices(contract: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    prices = contract.get("prices") if isinstance(contract.get("prices"), dict) else {}
    buy = prices.get("buy") if isinstance(prices.get("buy"), dict) else {}
    try:
        yes, no = D(str(buy["yes"])), D(str(buy["no"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not (D("0") < yes <= D("1") and D("0") < no <= D("1")):
        return None
    return yes, no


def _select_symbols(contracts: dict[str, Any], *, max_symbols: int, symbol_contains: str) -> list[str]:
    symbols = sorted(contracts)
    needle = symbol_contains.strip().lower()
    if needle:
        symbols = [symbol for symbol in symbols if needle in symbol.lower()]
    return symbols[:max_symbols]


def _rest_top(body: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, Decimal] | None:
    bids = body.get("bids") if isinstance(body.get("bids"), list) else []
    asks = body.get("asks") if isinstance(body.get("asks"), list) else []
    if not bids or not asks:
        return None
    try:
        best_bid = max(bids, key=lambda row: D(str(row["price"])))
        best_ask = min(asks, key=lambda row: D(str(row["price"])))
        return (
            D(str(best_bid["price"])),
            D(str(best_bid.get("amount") or "0")),
            D(str(best_ask["price"])),
            D(str(best_ask.get("amount") or "0")),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _fetch_rest_book(http: httpx.AsyncClient, symbol: str) -> tuple[str, int, dict[str, Any] | None, str | None]:
    started_ms = int(time.time() * 1000)
    try:
        response = await http.get(
            f"{GEMINI_REST}/v1/book/{symbol}",
            params={"limit_bids": 5, "limit_asks": 5},
        )
        received_ms = int(time.time() * 1000)
        response.raise_for_status()
        body = response.json()
        return symbol, received_ms, body if isinstance(body, dict) else None, None
    except Exception as exc:
        return symbol, started_ms, None, f"{type(exc).__name__}:{str(exc)[:240]}"


async def _subscribe(symbols: list[str], queue: asyncio.Queue) -> None:
    async with websockets.connect(
        GEMINI_WS,
        open_timeout=5.0,
        max_size=MAX_WS_MESSAGE_BYTES,
    ) as ws:
        request = {
            "id": "sports-ws-validate",
            "method": "SUBSCRIBE",
            "params": [f"{symbol}@bookTicker" for symbol in symbols],
        }
        await ws.send(json.dumps(request, separators=(",", ":")))
        async for raw in ws:
            received_ms = int(time.time() * 1000)
            payload = json.loads(raw)
            if payload.get("status") is not None:
                if int(payload.get("status")) >= 400:
                    raise RuntimeError(f"Gemini subscription rejected: {payload}")
                logger.info("GEMINI_SPORTS_WS_SUBSCRIBED %s", json.dumps({
                    "symbols": len(symbols), "status": payload.get("status")
                }, separators=(",", ":")))
                continue
            symbol = str(payload.get("s") or "")
            if not symbol:
                continue
            try:
                top = parse_gemini_book_ticker(payload, received_ms=received_ms)
            except (KeyError, ValueError, TypeError):
                continue
            await queue.put((symbol, top))
    raise RuntimeError("Gemini sports bookTicker stream ended")


async def run(seconds: float, max_symbols: int, symbol_contains: str = "") -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        events = await fetch_gemini_mlb(http)
        contracts = _gemini_contracts(events)
        symbols = _select_symbols(contracts, max_symbols=max_symbols, symbol_contains=symbol_contains)
        if not symbols:
            raise RuntimeError(f"No active Gemini MLB prediction symbols matched filter={symbol_contains!r}")

        logger.info("GEMINI_SPORTS_WS_VALIDATE_START %s", json.dumps({
            "symbols": symbols,
            "symbol_count": len(symbols),
            "symbol_filter": symbol_contains,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))
        snapshot = {symbol: _snapshot_prices(contracts[symbol]) for symbol in symbols}
        rest_rows = await asyncio.gather(*(_fetch_rest_book(http, symbol) for symbol in symbols))

    rest_compared = 0
    for symbol, received_ms, body, error in rest_rows:
        baseline = snapshot.get(symbol)
        top = _rest_top(body or {}) if body is not None else None
        if baseline is None or top is None:
            logger.info("GEMINI_SPORTS_REST_COMPARE %s", json.dumps({
                "symbol": symbol,
                "snapshot": None if baseline is None else [str(x) for x in baseline],
                "book_available": top is not None,
                "error": error,
                "received_ms": received_ms,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
            continue
        snap_yes, snap_no = baseline
        yes_bid, bid_size, yes_ask, ask_size = top
        live_no_ask = D("1") - yes_bid
        rest_compared += 1
        logger.info("GEMINI_SPORTS_REST_COMPARE %s", json.dumps({
            "symbol": symbol,
            "snapshot_yes_buy": str(snap_yes),
            "snapshot_no_buy": str(snap_no),
            "live_yes_bid": str(yes_bid),
            "live_yes_bid_size": str(bid_size),
            "live_yes_ask": str(yes_ask),
            "live_yes_ask_size": str(ask_size),
            "live_no_ask": str(live_no_ask),
            "yes_abs_gap": str(abs(snap_yes - yes_ask)),
            "no_abs_gap": str(abs(snap_no - live_no_ask)),
            "received_ms": received_ms,
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

    queue: asyncio.Queue = asyncio.Queue(maxsize=100000)
    task = asyncio.create_task(_subscribe(symbols, queue))
    deadline = time.monotonic() + seconds
    seen: set[str] = set()
    comparisons = 0
    max_yes_gap = D("0")
    max_no_gap = D("0")
    try:
        while time.monotonic() < deadline:
            if task.done():
                exc = task.exception()
                raise RuntimeError(f"Gemini sports WS failed: {exc}")
            try:
                symbol, top = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            baseline = snapshot.get(symbol)
            if baseline is None:
                continue
            snap_yes, snap_no = baseline
            live_yes = D(top.yes_ask)
            live_no = D("1") - D(top.yes_bid)
            yes_gap = abs(snap_yes - live_yes)
            no_gap = abs(snap_no - live_no)
            max_yes_gap = max(max_yes_gap, yes_gap)
            max_no_gap = max(max_no_gap, no_gap)
            comparisons += 1
            seen.add(symbol)
            logger.info("GEMINI_SPORTS_WS_COMPARE %s", json.dumps({
                "symbol": symbol,
                "snapshot_yes_buy": str(snap_yes),
                "snapshot_no_buy": str(snap_no),
                "live_yes_ask": str(live_yes),
                "live_no_ask": str(live_no),
                "yes_abs_gap": str(yes_gap),
                "no_abs_gap": str(no_gap),
                "live_yes_bid_size": str(top.yes_bid_size),
                "live_yes_ask_size": str(top.yes_ask_size),
                "received_ms": top.ts_ms,
                "read_only": True,
                "order_entry": False,
            }, separators=(",", ":")))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    logger.info("GEMINI_SPORTS_WS_VALIDATE_SUMMARY %s", json.dumps({
        "selected_symbols": len(symbols),
        "rest_books_compared": rest_compared,
        "symbols_with_book_updates": len(seen),
        "ws_comparisons": comparisons,
        "max_yes_abs_gap": str(max_yes_gap),
        "max_no_abs_gap": str(max_no_gap),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--max-symbols", type=int, default=150)
    p.add_argument("--symbol-contains", default="")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args.seconds, max(1, args.max_symbols), args.symbol_contains))


if __name__ == "__main__":
    main()
