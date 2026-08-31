"""Public tape + top-of-book collector for Kalshi and Gemini short crypto windows.

Writes JSONL events (one line per print / periodic BBO / window metadata) so the
frozen policy-D shadow evaluation is reproducible offline. Bounded runtime.

Event schema (superset; kind-specific fields):
  {"t": epoch_s, "venue": "kalshi"|"gemini", "kind": "print"|"bbo"|"window",
   "market": ticker_or_symbol, ...}

- Kalshi prints come from REST /markets/trades polling (deduped by trade_id),
  with taker_side yes/no and *_dollars prices (legacy integer fields are gone).
- Gemini prints come from the public ws "<symbol>@trade" stream: p price of the
  UP contract, q quantity, m = buyer-is-maker (True => taker SOLD => Down flow).
- BBO snapshots are periodic, not tick-by-tick: the evaluator joins quotes at
  the latest snapshot at-or-before each print.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx
import websockets

KALSHI_REST = "https://api.elections.kalshi.com/trade-api/v2"
GEMINI_REST = "https://api.gemini.com"
GEMINI_WS = "wss://ws.gemini.com"

KALSHI_SERIES = ("KXBTC15M", "KXETH15M")
GEMINI_WINDOW_RE = re.compile(r"^(BTC|ETH)(05|15)M(\d{10})$")

DISCOVER_SECONDS = 45.0
KALSHI_TRADES_POLL_SECONDS = 3.0
KALSHI_BBO_POLL_SECONDS = 6.0
GEMINI_BBO_RECORD_SECONDS = 5.0


def _now() -> float:
    return time.time()


class Sink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, event: Dict[str, Any]) -> None:
        line = json.dumps(event, separators=(",", ":"), default=str)
        async with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


async def kalshi_task(sink: Sink, stop: asyncio.Event) -> None:
    seen_trades: Set[str] = set()
    known_windows: Set[str] = set()
    active: Dict[str, Dict[str, Any]] = {}
    last_discover = 0.0
    last_bbo: Dict[str, float] = {}
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
        while not stop.is_set():
            now = _now()
            try:
                if now - last_discover >= DISCOVER_SECONDS:
                    last_discover = now
                    fresh: Dict[str, Dict[str, Any]] = {}
                    for series in KALSHI_SERIES:
                        response = await http.get(
                            f"{KALSHI_REST}/markets",
                            params={"series_ticker": series, "status": "open", "limit": 100},
                        )
                        if response.status_code != 200:
                            continue
                        for market in response.json().get("markets", []):
                            ticker = market.get("ticker")
                            if not ticker:
                                continue
                            fresh[ticker] = {"series": series, "close_time": market.get("close_time")}
                            if ticker not in known_windows:
                                known_windows.add(ticker)
                                await sink.write({
                                    "t": now, "venue": "kalshi", "kind": "window",
                                    "market": ticker, "series": series,
                                    "open_time": market.get("open_time"),
                                    "close_time": market.get("close_time"),
                                    "strike": market.get("cap_strike") or market.get("floor_strike")
                                    or market.get("cap_strike_dollars") or market.get("floor_strike_dollars"),
                                })
                    active = fresh
                for ticker, meta in list(active.items()):
                    response = await http.get(
                        f"{KALSHI_REST}/markets/trades", params={"ticker": ticker, "limit": 100}
                    )
                    if response.status_code == 200:
                        for trade in response.json().get("trades", []):
                            tid = trade.get("trade_id")
                            if not tid or tid in seen_trades:
                                continue
                            seen_trades.add(tid)
                            await sink.write({
                                "t": now, "venue": "kalshi", "kind": "print", "market": ticker,
                                "trade_id": tid,
                                "created_time": trade.get("created_time"),
                                "taker_side": trade.get("taker_side"),
                                "yes_price": trade.get("yes_price_dollars"),
                                "count": trade.get("count_fp"),
                            })
                    if now - last_bbo.get(ticker, 0.0) >= KALSHI_BBO_POLL_SECONDS:
                        last_bbo[ticker] = now
                        response = await http.get(
                            f"{KALSHI_REST}/markets/{ticker}/orderbook", params={"depth": 1}
                        )
                        if response.status_code == 200:
                            book = (response.json() or {}).get("orderbook_fp") or {}
                            await sink.write({
                                "t": now, "venue": "kalshi", "kind": "bbo", "market": ticker,
                                "yes_bids": book.get("yes_dollars"),
                                "no_bids": book.get("no_dollars"),
                            })
                if len(seen_trades) > 200_000:
                    seen_trades = set(list(seen_trades)[-100_000:])
            except Exception as exc:
                await sink.write({"t": _now(), "venue": "kalshi", "kind": "error", "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=KALSHI_TRADES_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


async def _gemini_active_symbols(http: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    symbols: Dict[str, Dict[str, Any]] = {}
    response = await http.get(
        f"{GEMINI_REST}/v1/prediction-markets/events",
        params=[("status", "active"), ("category", "Crypto"), ("limit", "500")],
    )
    if response.status_code != 200:
        return symbols
    for row in response.json().get("data", []):
        ticker = str(row.get("ticker") or "").upper()
        match = GEMINI_WINDOW_RE.match(ticker)
        if not match:
            continue
        for contract in row.get("contracts", []) or []:
            symbol = str(contract.get("instrumentSymbol") or "")
            state = str(contract.get("marketState") or contract.get("status") or "").lower()
            if symbol.upper().endswith("-UP") and state in {"open", "active"}:
                symbols[symbol] = {
                    "event": ticker,
                    "asset": match.group(1),
                    "window_minutes": int(match.group(2)),
                    "expiry": contract.get("expiryDate") or row.get("expiryDate"),
                }
    return symbols


GEMINI_VOLUME_POLL_SECONDS = 20.0


async def gemini_volume_task(sink: Sink, stop: asyncio.Event) -> None:
    """Poll cumulative per-window volume from the events REST feed.

    This is the robust flow signal for the burst census: deltas between polls
    give minute-by-minute volume velocity per window, independent of websocket
    trade-capture gaps."""
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
        last: Dict[str, float] = {}
        while not stop.is_set():
            try:
                response = await http.get(
                    f"{GEMINI_REST}/v1/prediction-markets/events",
                    params=[("status", "active"), ("category", "Crypto"), ("limit", "500")],
                )
                if response.status_code == 200:
                    now = _now()
                    for row in response.json().get("data", []):
                        ticker = str(row.get("ticker") or "").upper()
                        if not GEMINI_WINDOW_RE.match(ticker):
                            continue
                        try:
                            volume = float(row.get("volume") or 0.0)
                        except (TypeError, ValueError):
                            continue
                        if last.get(ticker) != volume:
                            await sink.write({
                                "t": now, "venue": "gemini", "kind": "vol",
                                "market": ticker, "volume": volume,
                                "expiry": row.get("expiryDate"),
                            })
                            last[ticker] = volume
                    for gone in [k for k in last if k not in {
                        str(r.get("ticker") or "").upper() for r in response.json().get("data", [])
                    }]:
                        last.pop(gone, None)
            except Exception as exc:
                await sink.write({"t": _now(), "venue": "gemini", "kind": "error",
                                  "error": f"vol:{type(exc).__name__}"})
            try:
                await asyncio.wait_for(stop.wait(), timeout=GEMINI_VOLUME_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


async def gemini_task(sink: Sink, stop: asyncio.Event) -> None:
    known: Set[str] = set()
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
        while not stop.is_set():
            try:
                symbols = await _gemini_active_symbols(http)
                for symbol, meta in symbols.items():
                    if symbol not in known:
                        known.add(symbol)
                        await sink.write({"t": _now(), "venue": "gemini", "kind": "window",
                                          "market": symbol, **meta})
                if symbols:
                    await _gemini_ws_session(sink, stop, list(symbols))
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                await sink.write({"t": _now(), "venue": "gemini", "kind": "error", "error": type(exc).__name__})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


GEMINI_WS_SESSION_SECONDS = 600.0  # hold one connection; subscribe rolled windows live


async def _gemini_ws_session(sink: Sink, stop: asyncio.Event, symbols: List[str]) -> None:
    """One long-lived ws session for trades+depth.

    Reconnect churn every DISCOVER_SECONDS was dropping exactly the bursts we
    need to time (all big overnight windows had zero captured prints), so the
    session now persists GEMINI_WS_SESSION_SECONDS and re-discovers newly
    rolled windows in-place, subscribing them on the same connection.
    """
    subscribed = set(symbols)
    last_top_write: Dict[str, float] = {}
    last_discover = time.monotonic()
    session_deadline = time.monotonic() + GEMINI_WS_SESSION_SECONDS
    async with websockets.connect(GEMINI_WS, open_timeout=5, ping_interval=15, ping_timeout=8, max_queue=5000) as ws:
        streams = [f"{s}@trade" for s in subscribed] + [f"{s}@depth5@100ms" for s in subscribed]
        await ws.send(json.dumps({"id": "laneD", "method": "SUBSCRIBE", "params": streams}))
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
            while not stop.is_set() and time.monotonic() < session_deadline:
                if time.monotonic() - last_discover >= DISCOVER_SECONDS:
                    last_discover = time.monotonic()
                    try:
                        fresh = await _gemini_active_symbols(http)
                        new = [s for s in fresh if s not in subscribed]
                        if new:
                            subscribed.update(new)
                            for symbol in new:
                                await sink.write({"t": _now(), "venue": "gemini",
                                                  "kind": "window", "market": symbol,
                                                  **fresh[symbol]})
                            await ws.send(json.dumps({
                                "id": "laneD-add", "method": "SUBSCRIBE",
                                "params": [f"{s}@trade" for s in new] + [f"{s}@depth5@100ms" for s in new],
                            }))
                    except (httpx.HTTPError, websockets.WebSocketException):
                        pass
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                symbol = str(msg.get("s") or msg.get("symbol") or "")
                if "p" in msg and "q" in msg and "m" in msg:
                    await sink.write({
                        "t": _now(), "venue": "gemini", "kind": "print",
                        "market": symbol.upper(),
                        "event_ns": msg.get("E"), "trade_id": msg.get("t"),
                        "price": msg.get("p"), "qty": msg.get("q"),
                        "buyer_is_maker": bool(msg.get("m")),
                    })
                elif "bids" in msg and "asks" in msg:
                    now = _now()
                    key = symbol.upper()
                    if now - last_top_write.get(key, 0.0) >= GEMINI_BBO_RECORD_SECONDS:
                        last_top_write[key] = now
                        await sink.write({
                            "t": now, "venue": "gemini", "kind": "bbo", "market": key,
                            "bids": (msg.get("bids") or [])[:2],
                            "asks": (msg.get("asks") or [])[:2],
                        })


async def run(out_dir: Path, hours: float) -> None:
    stop = asyncio.Event()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(out_dir / f"tape_{stamp}.jsonl")
    await sink.write({"t": _now(), "kind": "collector_start", "venue": "meta",
                      "bounded_hours": hours, "kalshi_series": list(KALSHI_SERIES)})
    tasks = [
        asyncio.create_task(kalshi_task(sink, stop)),
        asyncio.create_task(gemini_task(sink, stop)),
        asyncio.create_task(gemini_volume_task(sink, stop)),
    ]
    try:
        await asyncio.sleep(hours * 3600)
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sink.write({"t": _now(), "kind": "collector_end", "venue": "meta"})
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Lane-2 tape collector")
    parser.add_argument("--hours", type=float, default=5.0)
    parser.add_argument("--out-dir", default="data/policy_d_port")
    parser.add_argument("--series", default="",
                        help="comma-separated Kalshi series override "
                             "(e.g. KXSOL15M,KXXRP15M); default keeps the "
                             "standard BTC/ETH pair")
    args = parser.parse_args()
    if args.series:
        global KALSHI_SERIES
        KALSHI_SERIES = tuple(s.strip() for s in args.series.split(",") if s.strip())
    asyncio.run(run(Path(args.out_dir), args.hours))


if __name__ == "__main__":
    main()
