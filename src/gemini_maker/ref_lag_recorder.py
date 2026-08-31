"""Gemini Reference-Lag Recorder (read-only, strategy brief section 12).

Records, with nanosecond receive timestamps, everything needed to test the
reference-price premise: does the prediction book lag a knowable fair value?

Structural fact that shapes the fair-value model: settlement uses
GRR-KAIKO_RFR_<ASSET>USD_60S - a 60-SECOND ROLLING reference rate. The index
is a trailing average, so it lags live spot by construction (~30s). The
recorder therefore captures live spot trades and maintains a trailing-60s
proxy of the RFR; the lag study measures prediction-book response to spot
moves at 10ms..1s horizons, requiring causal executability (quote existed
AFTER the signal, at recorded depth).

Streams recorded (one JSONL sink, t_ns = local receive time):
  {"kind":"spot_trade", "sym":"BTCUSD", "price", "qty"}            all trades
  {"kind":"spot_rfr",   "sym":"BTCUSD", "rfr", "n"}                1/s proxy
  {"kind":"pbook", "market": "...-UP", "b","B","a","A","c","C","u","E"}
                                            every bookTicker update (tick-level)
  {"kind":"ptrade", "market", "price","qty","buyer_is_maker","E","tid"}
  {"kind":"window", "market", "strike", "strike_at", "source", "expiry",
                    "effective"}                     REST metadata per window
  {"kind":"vol", "market", "volume"}                 cumulative, on change

No credentials, no orders, public endpoints only.
Run: python3 -m src.gemini_maker.ref_lag_recorder --hours 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

import httpx
import websockets

GEMINI_REST = "https://api.gemini.com"
PRED_WS = "wss://ws.gemini.com"
SPOT_WS = "wss://api.gemini.com/v2/marketdata"

SPOT_SYMBOLS = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD")
WINDOW_RE = re.compile(r"^(BTC|ETH|SOL|XRP)(05|15)M(\d{10})$")

DISCOVER_SECONDS = 30.0
RFR_WINDOW_S = 60.0
RFR_EMIT_S = 1.0
PRED_WS_SESSION_S = 600.0


def _now_ns() -> int:
    return time.time_ns()


class Sink:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, event: Dict[str, Any]) -> None:
        event.setdefault("t_ns", _now_ns())
        line = json.dumps(event, separators=(",", ":"), default=str)
        async with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        self._fh.close()


async def spot_task(sink: Sink, stop: asyncio.Event) -> None:
    """Live spot trades for all four assets + 1Hz trailing-60s RFR proxy."""
    trades: Dict[str, deque] = {s: deque() for s in SPOT_SYMBOLS}
    last_emit: Dict[str, float] = {s: 0.0 for s in SPOT_SYMBOLS}
    while not stop.is_set():
        try:
            async with websockets.connect(SPOT_WS, open_timeout=5, ping_interval=15,
                                          ping_timeout=8, max_queue=10000) as ws:
                await ws.send(json.dumps({"type": "subscribe", "subscriptions": [
                    {"name": "l2", "symbols": list(SPOT_SYMBOLS)}]}))
                last_msg = time.monotonic()
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        last_msg = time.monotonic()
                    except asyncio.TimeoutError:
                        # The feed can go silently half-dead (recv times out
                        # forever, no exception). Force a reconnect on silence.
                        if time.monotonic() - last_msg > 30.0:
                            await sink.write({"kind": "error", "where": "spot",
                                              "error": "SilentFeedReconnect"})
                            break
                        continue
                    t_ns = _now_ns()
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "trade":
                        sym = str(msg.get("symbol") or "").upper()
                        if sym not in trades:
                            continue
                        try:
                            price = float(msg.get("price"))
                            qty = float(msg.get("quantity") or 0)
                        except (TypeError, ValueError):
                            continue
                        await sink.write({"t_ns": t_ns, "kind": "spot_trade",
                                          "sym": sym, "price": price, "qty": qty,
                                          "ev_ms": msg.get("timestamp")})
                        dq = trades[sym]
                        now = t_ns / 1e9
                        dq.append((now, price))
                        while dq and now - dq[0][0] > RFR_WINDOW_S:
                            dq.popleft()
                        if now - last_emit[sym] >= RFR_EMIT_S and dq:
                            last_emit[sym] = now
                            rfr = sum(p for _, p in dq) / len(dq)
                            await sink.write({"t_ns": _now_ns(), "kind": "spot_rfr",
                                              "sym": sym, "rfr": round(rfr, 4),
                                              "n": len(dq)})
        except Exception as exc:
            await sink.write({"kind": "error", "where": "spot", "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass


async def _active_up_instruments(http: httpx.AsyncClient, sink: Sink,
                                 known: Set[str]) -> List[str]:
    symbols: List[str] = []
    response = await http.get(f"{GEMINI_REST}/v1/prediction-markets/events",
                              params=[("status", "active"), ("category", "Crypto"),
                                      ("limit", "500")])
    if response.status_code != 200:
        return symbols
    now = datetime.now(timezone.utc)
    for row in response.json().get("data", []):
        ticker = str(row.get("ticker") or "").upper()
        if not WINDOW_RE.match(ticker):
            continue
        vol = row.get("volume")
        for contract in row.get("contracts", []) or []:
            symbol = str(contract.get("instrumentSymbol") or "")
            state = str(contract.get("marketState") or "").lower()
            if not symbol.upper().endswith("-UP") or state not in ("open", "active"):
                continue
            # Only windows trading now or starting within 10 min: keeps the
            # stream count within the ws limit (bulk-subscribing every listed
            # future window gets the connection closed).
            try:
                eff = datetime.fromisoformat(str(contract.get("effectiveDate"))
                                             .replace("Z", "+00:00"))
                if (eff - now).total_seconds() > 600:
                    continue
            except (TypeError, ValueError):
                pass
            symbols.append(symbol)
            if symbol not in known:
                known.add(symbol)
                strike = contract.get("strike") or {}
                await sink.write({
                    "kind": "window", "market": symbol.upper(),
                    "strike": strike.get("value"),
                    "strike_at": strike.get("availableAt"),
                    "source": contract.get("source"),
                    "expiry": contract.get("expiryDate"),
                    "effective": contract.get("effectiveDate"),
                    "volume": vol,
                })
    return symbols


async def pred_task(sink: Sink, stop: asyncio.Event) -> None:
    """Prediction-side: tick-level bookTicker + trades for all -UP contracts."""
    known: Set[str] = set()
    async with httpx.AsyncClient(timeout=10.0,
                                 headers={"User-Agent": "arb-reflag-readonly/1.0"}) as http:
        while not stop.is_set():
            try:
                symbols = await _active_up_instruments(http, sink, known)
                if not symbols:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
                    continue
                await _pred_ws_session(sink, stop, http, symbols, known)
            except Exception as exc:
                await sink.write({"kind": "error", "where": "pred", "error": type(exc).__name__})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


async def _pred_ws_session(sink: Sink, stop: asyncio.Event, http: httpx.AsyncClient,
                           symbols: List[str], known: Set[str]) -> None:
    subscribed = set(symbols)
    deadline = time.monotonic() + PRED_WS_SESSION_S
    last_discover = time.monotonic()
    async with websockets.connect(PRED_WS, open_timeout=5, ping_interval=15,
                                  ping_timeout=8, max_queue=10000) as ws:
        streams = [f"{s}@trade" for s in subscribed] + [f"{s}@bookTicker" for s in subscribed]
        for i in range(0, len(streams), 20):
            await ws.send(json.dumps({"id": f"reflag{i}", "method": "SUBSCRIBE",
                                      "params": streams[i:i + 20]}))
            await asyncio.sleep(0.2)
        while not stop.is_set() and time.monotonic() < deadline:
            if time.monotonic() - last_discover >= DISCOVER_SECONDS:
                last_discover = time.monotonic()
                try:
                    fresh = await _active_up_instruments(http, sink, known)
                    new = [s for s in fresh if s not in subscribed]
                    if new:
                        subscribed.update(new)
                        await ws.send(json.dumps({
                            "id": "reflag-add", "method": "SUBSCRIBE",
                            "params": [f"{s}@trade" for s in new] + [f"{s}@bookTicker" for s in new]}))
                except (httpx.HTTPError, websockets.WebSocketException):
                    pass
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            t_ns = _now_ns()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            symbol = str(msg.get("s") or "").upper()
            if "b" in msg and "a" in msg and "u" in msg:
                await sink.write({"t_ns": t_ns, "kind": "pbook", "market": symbol,
                                  "b": msg.get("b"), "B": msg.get("B"),
                                  "a": msg.get("a"), "A": msg.get("A"),
                                  "c": msg.get("c"), "C": msg.get("C"),
                                  "u": msg.get("u"), "E": msg.get("E")})
            elif "p" in msg and "q" in msg and "m" in msg:
                await sink.write({"t_ns": t_ns, "kind": "ptrade", "market": symbol,
                                  "price": msg.get("p"), "qty": msg.get("q"),
                                  "buyer_is_maker": bool(msg.get("m")),
                                  "E": msg.get("E"), "tid": msg.get("t")})


async def settlement_task(sink: Sink, stop: asyncio.Event) -> None:
    """Ground-truth labels: settled events expose settlementValue (the exact
    reference index at settlement) and resolutionSide. These, not the spot
    proxy, are the labels the calibration study must use (audit fix)."""
    seen: Set[str] = set()
    async with httpx.AsyncClient(timeout=10.0,
                                 headers={"User-Agent": "arb-reflag-readonly/1.0"}) as http:
        while not stop.is_set():
            try:
                response = await http.get(f"{GEMINI_REST}/v1/prediction-markets/events",
                                          params=[("status", "settled"),
                                                  ("category", "Crypto"),
                                                  ("limit", "100")])
                if response.status_code == 200:
                    for row in response.json().get("data", []):
                        ticker = str(row.get("ticker") or "").upper()
                        if not WINDOW_RE.match(ticker) or ticker in seen:
                            continue
                        for contract in row.get("contracts", []) or []:
                            sym = str(contract.get("instrumentSymbol") or "")
                            if not sym.upper().endswith("-UP"):
                                continue
                            seen.add(ticker)
                            await sink.write({
                                "kind": "settlement", "market": sym.upper(),
                                "ticker": ticker,
                                "settlement_value": contract.get("settlementValue"),
                                "resolution_side": contract.get("resolutionSide"),
                                "expiry": contract.get("expiryDate"),
                                "source": contract.get("source"),
                            })
            except Exception as exc:
                await sink.write({"kind": "error", "where": "settlement",
                                  "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass


async def vol_task(sink: Sink, stop: asyncio.Event) -> None:
    last: Dict[str, float] = {}
    async with httpx.AsyncClient(timeout=10.0,
                                 headers={"User-Agent": "arb-reflag-readonly/1.0"}) as http:
        while not stop.is_set():
            try:
                response = await http.get(f"{GEMINI_REST}/v1/prediction-markets/events",
                                          params=[("status", "active"), ("category", "Crypto"),
                                                  ("limit", "500")])
                if response.status_code == 200:
                    for row in response.json().get("data", []):
                        ticker = str(row.get("ticker") or "").upper()
                        if not WINDOW_RE.match(ticker):
                            continue
                        try:
                            volume = float(row.get("volume") or 0.0)
                        except (TypeError, ValueError):
                            continue
                        if last.get(ticker) != volume:
                            last[ticker] = volume
                            await sink.write({"kind": "vol", "market": ticker,
                                              "volume": volume})
            except Exception as exc:
                await sink.write({"kind": "error", "where": "vol", "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=20.0)
            except asyncio.TimeoutError:
                pass


async def run(out_dir: Path, hours: float) -> None:
    stop = asyncio.Event()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(out_dir / f"reflag_{stamp}.jsonl")
    await sink.write({"kind": "recorder_start", "bounded_hours": hours,
                      "spot_symbols": list(SPOT_SYMBOLS)})
    tasks = [
        asyncio.create_task(spot_task(sink, stop)),
        asyncio.create_task(pred_task(sink, stop)),
        asyncio.create_task(vol_task(sink, stop)),
        asyncio.create_task(settlement_task(sink, stop)),
    ]
    try:
        await asyncio.sleep(hours * 3600)
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sink.write({"kind": "recorder_end"})
        sink.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Gemini reference-lag recorder")
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--out-dir", default="data/ref_lag")
    args = parser.parse_args()
    asyncio.run(run(Path(args.out_dir), args.hours))


if __name__ == "__main__":
    main()
