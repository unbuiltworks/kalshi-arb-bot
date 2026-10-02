"""INFORMATION-EDGE lane collector: Gemini BTC05M contract + high-frequency
BTC reference, with BOTH venue-event and local-receive timestamps.

READ-ONLY. Public market data only. Unauthenticated. No order path exists in
this file. This lane is SEPARATE from the closed passive-maker lane and does
not touch it.

WHY THIS EXISTS (see P0_INVENTORY_AND_PREREGISTRATION.md):
  data/ref_lag/ already pairs Gemini prediction books with a BTC spot feed,
  but its spot source is GEMINI's own BTCUSD, whose trade gaps are p50
  1.259 s / p75 5.446 s / p90 12.2 s. Only 34.2% of gaps are under 250 ms, so
  the 100/250/500 ms horizons the hypothesis asks about are NOT RESOLVABLE on
  it -- a test there would measure our sampling sparsity, not the market.
  Measured alternative: Coinbase BTC-USD matches run ~4.7 trades/s (~76x),
  carry venue `sequence` and `time`, and are reachable here. Binance is
  geo-blocked (HTTP 451) from this environment.

STREAMS
  Gemini prediction (wss://ws.gemini.com)
    {sym}@depth    every book DIFF, with U/u sequence ids and E (ns)
    {sym}@trade    every print, with E (ns), trade id, p, q, m
    {sym}@depth20  periodic full snapshot, to seed and resync
  Gemini events REST
    window metadata: strike, strike_at, SOURCE, expiry, effective, volume
    settlement: settlement_value, resolution_side (ground truth)
  Coinbase (wss://ws-feed.exchange.coinbase.com)
    matches        every trade: price, size, side, sequence, venue time
    ticker         best_bid/best_ask with sizes -- EXECUTABLE reference, not mid

CLOCK DISCIPLINE -- the leakage control
  Every row carries `t_ns` (our monotonic-ish receive stamp) and, where the
  venue supplies one, the venue's own event time. A dedicated `clock_probe`
  row records local time against Coinbase's server time so the skew is
  MEASURED rather than assumed. In data/ref_lag/ the receive-minus-event skew
  ran p10 -149.6 ms / p50 -20.9 ms / p90 +571.5 ms, i.e. comparable to or
  larger than the horizons under test, which is itself a reason that cohort
  cannot answer the sub-second question.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Set

import httpx
import websockets

GEMINI_REST = "https://api.gemini.com"
EVENTS = f"{GEMINI_REST}/v1/prediction-markets/events"
GEMINI_WS = "wss://ws.gemini.com"
CB_WS = "wss://ws-feed.exchange.coinbase.com"
CB_REST = "https://api.exchange.coinbase.com"

SUBSCRIBE_LEAD_S = 420.0      # > 300 s window + margin, to see pre-window
RETAIN_AFTER_S = 120.0        # keep through settlement publication
DISCOVER_S = 30.0
META_POLL_S = 10.0
CLOCK_PROBE_S = 60.0
WS_SESSION_S = 900.0
CCR_README = Path("/root/.ccr/README.md")
CA = "/root/.ccr/ca-bundle.crt"
_PORT = re.compile(r"http://127\.0\.0\.1:(\d+)")


def now_ns() -> int:
    return time.time_ns()


def proxy_url():
    try:
        m = _PORT.search(CCR_README.read_text())
        if m:
            return f"http://127.0.0.1:{m.group(1)}"
    except Exception:
        pass
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


def ssl_ctx():
    try:
        return ssl.create_default_context(cafile=CA)
    except Exception:
        return None


class Sink:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", buffering=1)
        self.path, self.n = path, 0

    def write(self, row: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        self.n += 1

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc).timestamp()


async def gemini_meta(sink: Sink, stop: asyncio.Event, known: Dict[str, Any],
                      series_re: re.Pattern):
    """Window metadata + settlement ground truth from the events REST feed."""
    seen_settle: Set[str] = set()
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=25.0, proxy=proxy_url(),
                                         verify=CA) as http:
                r = await http.get(EVENTS)
                data = r.json().get("data", [])
            t = now_ns()
            for ev in data:
                tick = ev.get("ticker") or ""
                if not series_re.match(tick):
                    continue
                for c in ev.get("contracts", []):
                    sym = (c.get("instrumentSymbol") or "").upper()
                    if not sym:
                        continue
                    strike = c.get("strike") or {}
                    row = {"t_ns": t, "kind": "window", "market": sym,
                           "ticker": tick,
                           "strike": strike.get("value"),
                           "strike_at": strike.get("availableAt"),
                           "strike_type": strike.get("type"),
                           "source": c.get("source"),
                           "expiry": c.get("expiryDate"),
                           "effective": c.get("effectiveDate"),
                           "start": ev.get("startTime"),
                           "status": ev.get("status"),
                           "marketState": c.get("marketState"),
                           "volume": ev.get("volume"),
                           "price_increment": c.get("priceIncrement"),
                           "qty_increment": c.get("quantityIncrement"),
                           "prices": c.get("prices")}
                    if sym not in known:
                        try:
                            known[sym] = {"expiry_t": parse_ts(c["expiryDate"]),
                                          "start_t": parse_ts(ev["startTime"])}
                        except Exception:
                            pass
                        sink.write(row)
                    else:
                        # price/state snapshot each poll: cheap, and it is the
                        # only record of the venue's own quoted prices
                        sink.write({**row, "kind": "window_poll"})
                    sv = c.get("settlementValue") or ev.get("settlementValue")
                    rs = c.get("resolutionSide") or ev.get("resolutionSide")
                    if (sv is not None or rs is not None) and sym not in seen_settle:
                        seen_settle.add(sym)
                        sink.write({"t_ns": now_ns(), "kind": "settlement",
                                    "market": sym, "ticker": tick,
                                    "settlement_value": sv,
                                    "resolution_side": rs,
                                    "source": c.get("source"),
                                    "expiry": c.get("expiryDate")})
        except Exception as exc:
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "meta",
                        "error": type(exc).__name__})
        try:
            await asyncio.wait_for(stop.wait(), timeout=META_POLL_S)
        except asyncio.TimeoutError:
            pass


async def clock_probe(sink: Sink, stop: asyncio.Event):
    """Measure local-vs-venue clock skew instead of assuming it."""
    while not stop.is_set():
        try:
            t0 = now_ns()
            async with httpx.AsyncClient(timeout=20.0, proxy=proxy_url(),
                                         verify=CA) as http:
                r = await http.get(f"{CB_REST}/time")
                t1 = now_ns()
                j = r.json()
            sink.write({"t_ns": t1, "kind": "clock_probe", "venue": "coinbase",
                        "local_before_ns": t0, "local_after_ns": t1,
                        "rtt_ms": (t1 - t0) / 1e6,
                        "venue_epoch": j.get("epoch"), "venue_iso": j.get("iso"),
                        "http_date": r.headers.get("date")})
        except Exception as exc:
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "clock",
                        "error": type(exc).__name__})
        try:
            await asyncio.wait_for(stop.wait(), timeout=CLOCK_PROBE_S)
        except asyncio.TimeoutError:
            pass


async def coinbase_task(sink: Sink, stop: asyncio.Event, product="BTC-USD"):
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(CB_WS, proxy=proxy_url(), ssl=ssl_ctx(),
                                          open_timeout=15, ping_interval=20,
                                          ping_timeout=15, max_queue=20000) as ws:
                await ws.send(json.dumps({"type": "subscribe",
                                          "product_ids": [product],
                                          "channels": ["matches", "ticker"]}))
                sink.write({"t_ns": now_ns(), "kind": "cb_open", "product": product})
                backoff = 1.0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        continue
                    t = now_ns()
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    ty = m.get("type")
                    if ty == "match" or ty == "last_match":
                        sink.write({"t_ns": t, "kind": "cb_match",
                                    "seq": m.get("sequence"),
                                    "trade_id": m.get("trade_id"),
                                    "price": m.get("price"), "size": m.get("size"),
                                    "side": m.get("side"),
                                    "venue_time": m.get("time")})
                    elif ty == "ticker":
                        sink.write({"t_ns": t, "kind": "cb_ticker",
                                    "seq": m.get("sequence"),
                                    "price": m.get("price"),
                                    "best_bid": m.get("best_bid"),
                                    "best_bid_size": m.get("best_bid_size"),
                                    "best_ask": m.get("best_ask"),
                                    "best_ask_size": m.get("best_ask_size"),
                                    "venue_time": m.get("time")})
                    elif ty in ("error", "subscriptions"):
                        sink.write({"t_ns": t, "kind": "cb_ctrl", "raw": m})
        except Exception as exc:
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "coinbase",
                        "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def gemini_pred_task(sink: Sink, stop: asyncio.Event,
                           known: Dict[str, Any]):
    backoff = 1.0
    while not stop.is_set():
        try:
            subscribed: Set[str] = set()
            deadline = time.monotonic() + WS_SESSION_S
            async with websockets.connect(GEMINI_WS, proxy=proxy_url(),
                                          ssl=ssl_ctx(), open_timeout=15,
                                          ping_interval=15, ping_timeout=10,
                                          max_queue=20000) as ws:
                sink.write({"t_ns": now_ns(), "kind": "gem_open"})
                backoff = 1.0
                while not stop.is_set() and time.monotonic() < deadline:
                    tnow = time.time()
                    add = [s for s, m in list(known.items())
                           if s not in subscribed
                           and (m["expiry_t"] - SUBSCRIBE_LEAD_S) <= tnow
                           <= (m["expiry_t"] + RETAIN_AFTER_S)]
                    drop = [s for s in list(subscribed)
                            if tnow > known.get(s, {}).get("expiry_t", 0)
                            + RETAIN_AFTER_S]
                    if add:
                        params = []
                        for s in add:
                            params += [f"{s}@depth", f"{s}@trade", f"{s}@depth20"]
                        await ws.send(json.dumps({"id": "ie-add",
                                                  "method": "SUBSCRIBE",
                                                  "params": params}))
                        subscribed.update(add)
                        sink.write({"t_ns": now_ns(), "kind": "subscribe",
                                    "markets": add})
                    if drop:
                        params = []
                        for s in drop:
                            params += [f"{s}@depth", f"{s}@trade", f"{s}@depth20"]
                        await ws.send(json.dumps({"id": "ie-drop",
                                                  "method": "UNSUBSCRIBE",
                                                  "params": params}))
                        for s in drop:
                            subscribed.discard(s)
                        sink.write({"t_ns": now_ns(), "kind": "unsubscribe",
                                    "markets": drop})
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    t = now_ns()
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(m, dict):
                        continue
                    if "status" in m or "result" in m or "error" in m:
                        sink.write({"t_ns": t, "kind": "gem_ctrl", "raw": m})
                        continue
                    if m.get("e") == "depthUpdate":
                        sink.write({"t_ns": t, "kind": "p_depth",
                                    "market": (m.get("s") or "").upper(),
                                    "E_ns": m.get("E"), "U": m.get("U"),
                                    "u": m.get("u"), "b": m.get("b"),
                                    "a": m.get("a")})
                    elif "lastUpdateId" in m:
                        sink.write({"t_ns": t, "kind": "p_snapshot",
                                    "market": (m.get("symbol") or "").upper(),
                                    "lastUpdateId": m.get("lastUpdateId"),
                                    "bids": m.get("bids"), "asks": m.get("asks")})
                    elif "p" in m and "q" in m and "m" in m:
                        sink.write({"t_ns": t, "kind": "p_trade",
                                    "market": (m.get("s") or "").upper(),
                                    "E_ns": m.get("E"), "trade_id": m.get("t"),
                                    "price": m.get("p"), "qty": m.get("q"),
                                    "buyer_is_maker": m.get("m")})
                    else:
                        sink.write({"t_ns": t, "kind": "p_other", "raw": m})
        except Exception as exc:
            sink.write({"t_ns": now_ns(), "kind": "error", "where": "gemini_pred",
                        "error": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def main_async(args):
    series_re = re.compile(args.series_regex)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(Path(args.outdir) / f"ie_{stamp}.jsonl")
    sink.write({"t_ns": now_ns(), "kind": "collector_start",
                "series_regex": args.series_regex,
                "gemini_streams": ["@depth(every diff)", "@trade", "@depth20"],
                "reference": "coinbase BTC-USD matches+ticker",
                "reference_rate_hint": "~4.7 trades/s measured",
                "subscribe_lead_s": SUBSCRIBE_LEAD_S,
                "meta_poll_s": META_POLL_S,
                "read_only": True, "authenticated": False, "places_orders": False})
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sg in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sg, stop.set)
        except NotImplementedError:
            pass
    if args.duration_s:
        loop.call_later(args.duration_s, stop.set)
    known: Dict[str, Any] = {}
    await asyncio.gather(
        gemini_meta(sink, stop, known, series_re),
        gemini_pred_task(sink, stop, known),
        coinbase_task(sink, stop, args.product),
        clock_probe(sink, stop),
    )
    sink.write({"t_ns": now_ns(), "kind": "collector_stop", "rows": sink.n})
    sink.close()
    print(f"wrote {sink.path} ({sink.n} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="data/gemini_infoedge")
    ap.add_argument("--series-regex", default=r"^BTC05M\d{10}$")
    ap.add_argument("--product", default="BTC-USD")
    ap.add_argument("--duration-s", type=float, default=0.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
