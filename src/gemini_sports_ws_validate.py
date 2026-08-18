"""Read-only Gemini sports bookTicker validator.

The existing MLB hot scanner uses Gemini's active-events response as a q=1 price
source. This validator subscribes to the actual Gemini prediction-market
``bookTicker`` stream for those same symbols and compares executable live bid/ask
to the active-events values. It has no private auth and no order-entry imports.
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


async def run(seconds: float, max_symbols: int) -> None:
    async with httpx.AsyncClient(timeout=10.0) as http:
        events = await fetch_gemini_mlb(http)
    contracts = _gemini_contracts(events)
    symbols = sorted(contracts)[:max_symbols]
    if not symbols:
        raise RuntimeError("No active Gemini MLB prediction symbols discovered")

    snapshot = {symbol: _snapshot_prices(contracts[symbol]) for symbol in symbols}
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
            # bookTicker is a YES-space order book. Executable YES buy = ask;
            # executable NO buy = 1 - YES bid.
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
        "discovered_symbols": len(symbols),
        "symbols_with_book_updates": len(seen),
        "comparisons": comparisons,
        "max_yes_abs_gap": str(max_yes_gap),
        "max_no_abs_gap": str(max_no_gap),
        "read_only": True,
        "order_entry": False,
    }, separators=(",", ":")))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--max-symbols", type=int, default=150)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(args.seconds, max(1, args.max_symbols)))


if __name__ == "__main__":
    main()
