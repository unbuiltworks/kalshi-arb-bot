"""Read-only forward-window ETH5 tape collector.

The older retained collector selected only the nearest active contract per family.
Gemini ETH5 often trades a future contract before it becomes the nearest expiry,
which left those prints outside our virtual-quote chronology. This collector fixes
that measurement blind spot by subscribing to every open ETH05M UP contract inside
a bounded forward window.

Public REST + public WebSocket only. No credentials, account state, or order entry.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

import httpx
import websockets

from src.gemini_l5_tape_dynamic_shadow import (
    MarketState,
    WS_URL,
    _iso,
    _parse_book_ticker,
    _parse_trade,
    _write,
)
from src.gemini_l5_threshold_shadow import Event, book

REST = "https://api.gemini.com"
ETH5_RE = re.compile(r"^ETH05M\d{10}$")


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


def events_from_rows(
    rows: list[Any],
    *,
    now_ms: int,
    max_seconds_to_expiry: float,
) -> dict[str, Event]:
    found: dict[str, Event] = {}
    max_ms = int(max_seconds_to_expiry * 1000)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ETH5_RE.match(ticker):
            continue
        for contract in row.get("contracts") or []:
            if not isinstance(contract, Mapping):
                continue
            symbol = str(contract.get("instrumentSymbol") or "").strip()
            state = str(contract.get("marketState") or contract.get("status") or "").lower()
            if not symbol.upper().endswith("-UP") or state not in {"open", "active"}:
                continue
            expiry_ms = _iso_ms(contract.get("expiryDate") or row.get("expiryDate"))
            if expiry_ms is None:
                continue
            remaining = expiry_ms - now_ms
            if remaining <= 0 or remaining > max_ms:
                continue
            event = Event(
                asset="ETH",
                horizon="5m",
                ticker=ticker,
                symbol=symbol,
                expiry_ms=expiry_ms,
                price_increment=D(str(contract.get("priceIncrement") or "0.01")),
            )
            found[symbol.upper()] = event
    return found


async def discover_forward(
    http: httpx.AsyncClient,
    *,
    max_seconds_to_expiry: float,
) -> dict[str, Event]:
    rows_all: list[Any] = []
    offset = 0
    while offset < 5000:
        response = await http.get(
            f"{REST}/v1/prediction-markets/events",
            params=[("status", "active"), ("category", "Crypto"), ("limit", "500"), ("offset", str(offset))],
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        rows_all.extend(rows)
        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = int((pagination or {}).get("total") or len(rows))
        offset += len(rows)
        if not rows or offset >= total:
            break
    return events_from_rows(
        rows_all,
        now_ms=int(time.time() * 1000),
        max_seconds_to_expiry=max_seconds_to_expiry,
    )


async def capture(
    *,
    seconds: float,
    discover_s: float,
    max_seconds_to_expiry: float,
    output: Path,
    summary_output: Path | None = None,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, seconds)
    states: dict[str, MarketState] = {}
    subscribed: set[str] = set()
    stats = {
        "messages": 0,
        "book_tickers": 0,
        "trades": 0,
        "errors": 0,
        "discoveries": 0,
        "subscriptions": 0,
        "rest_seeds": 0,
        "control_messages": 0,
        "subscription_errors": 0,
    }
    last_discover = 0.0

    def summary(status: str) -> dict[str, Any]:
        eth5_trade_markets = sum(1 for state in states.values() if state.trade_events > 0)
        return {
            "type": "GEMINI_ETH5_FORWARD_TAPE_SHADOW_V1",
            "classification": "READ_ONLY_PUBLIC_TAPE_CAPTURE",
            "order_entry": False,
            "credentials_loaded": False,
            "status": status,
            "seconds": seconds,
            "max_seconds_to_expiry": max_seconds_to_expiry,
            "markets_seen": len(states),
            "markets_with_public_trades": eth5_trade_markets,
            "stats": stats,
            "markets": [state.summary() for state in states.values()],
        }

    async with httpx.AsyncClient(
        timeout=8.0,
        follow_redirects=True,
        headers={"User-Agent": "arb-gemini-eth5-forward-shadow/1.0"},
    ) as http:
        async with websockets.connect(
            WS_URL,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=2**20,
        ) as ws:
            with output.open("w", encoding="utf-8") as fh:
                _write(fh, {
                    "type": "ETH5_FORWARD_CAPTURE_START",
                    "observed_at": _iso(),
                    "classification": "READ_ONLY_PUBLIC_TAPE_CAPTURE",
                    "seconds": seconds,
                    "max_seconds_to_expiry": max_seconds_to_expiry,
                    "credentials_loaded": False,
                })
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now - last_discover >= discover_s or not subscribed:
                        try:
                            events = await discover_forward(http, max_seconds_to_expiry=max_seconds_to_expiry)
                            stats["discoveries"] += 1
                            desired: list[str] = []
                            for event in events.values():
                                key = event.symbol.upper()
                                if key not in states:
                                    state = MarketState(event)
                                    states[key] = state
                                    try:
                                        bid, _, ask, _ = await book(http, event.symbol)
                                        for row in state.on_book(bid, ask, _iso()):
                                            _write(fh, row)
                                        stats["rest_seeds"] += 1
                                    except Exception as exc:
                                        stats["errors"] += 1
                                        _write(fh, {
                                            "type": "REST_SEED_ERROR",
                                            "observed_at": _iso(),
                                            "ticker": event.ticker,
                                            "symbol": event.symbol,
                                            "asset": "ETH",
                                            "horizon": "5m",
                                            "error_type": type(exc).__name__,
                                            "error": str(exc)[:240],
                                        })
                                desired.extend([f"{event.symbol}@bookTicker", f"{event.symbol}@trade"])
                            new_streams = [stream for stream in desired if stream not in subscribed]
                            if new_streams:
                                request_id = f"eth5-forward-{stats['subscriptions'] + 1}"
                                await ws.send(json.dumps({"id": request_id, "method": "SUBSCRIBE", "params": new_streams}))
                                _write(fh, {"type": "WS_SUBSCRIBE_SENT", "observed_at": _iso(), "id": request_id, "streams": new_streams})
                                subscribed.update(new_streams)
                                stats["subscriptions"] += len(new_streams)
                            last_discover = now
                        except Exception as exc:
                            stats["errors"] += 1
                            _write(fh, {"type": "DISCOVERY_ERROR", "observed_at": _iso(), "error_type": type(exc).__name__, "error": str(exc)[:240]})

                    timeout = min(1.0, max(0.05, deadline - time.monotonic()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        continue
                    try:
                        message = json.loads(raw)
                    except Exception:
                        stats["errors"] += 1
                        continue
                    if not isinstance(message, dict):
                        continue
                    stats["messages"] += 1
                    symbol = str(message.get("s") or "").upper()
                    state = states.get(symbol)
                    if state is None:
                        stats["control_messages"] += 1
                        status = message.get("status")
                        if isinstance(status, int) and status >= 400:
                            stats["subscription_errors"] += 1
                        _write(fh, {"type": "WS_CONTROL", "observed_at": _iso(), "message": message})
                        continue
                    observed_at = _iso()
                    parsed_trade = _parse_trade(message)
                    if parsed_trade is not None and "t" in message:
                        stats["trades"] += 1
                        for row in state.on_trade(*parsed_trade, observed_at):
                            _write(fh, row)
                        _write(fh, {
                            "type": "PUBLIC_TRADE",
                            "observed_at": observed_at,
                            "ticker": state.event.ticker,
                            "symbol": state.event.symbol,
                            "asset": "ETH",
                            "horizon": "5m",
                            "price": str(parsed_trade[0]),
                            "quantity": str(parsed_trade[1]),
                            "buyer_maker": parsed_trade[2],
                            "trade_id": message.get("t"),
                        })
                        continue
                    parsed_book = _parse_book_ticker(message)
                    if parsed_book is not None:
                        stats["book_tickers"] += 1
                        for row in state.on_book(*parsed_book, observed_at):
                            _write(fh, row)

                final = summary("COMPLETED")
                _write(fh, {"type": "ETH5_FORWARD_CAPTURE_END", "observed_at": _iso(), "summary": final})
                if summary_output:
                    summary_output.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(json.dumps(final, indent=2, sort_keys=True), flush=True)
                return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=360.0)
    parser.add_argument("--discover-s", type=float, default=10.0)
    parser.add_argument("--max-seconds-to-expiry", type=float, default=1800.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    asyncio.run(capture(
        seconds=args.seconds,
        discover_s=args.discover_s,
        max_seconds_to_expiry=args.max_seconds_to_expiry,
        output=args.output,
        summary_output=args.summary_output,
    ))


if __name__ == "__main__":
    main()
