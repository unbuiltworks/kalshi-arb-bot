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
from typing import Any, Dict, List, Mapping, Optional, Set

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
GEMINI_BBO_RECORD_SECONDS = 1.0   # ~1 Hz: resolves L5's 1-4 s behaviour

# WINDOW-AWARE SUBSCRIPTION LIFECYCLE.
#
# THE DEFECT THIS FIXES, measured: across 756 canonicalised 5m windows the
# tape carried a MEDIAN OF 170 two-sided observations per market and a MEDIAN
# OF ZERO SECONDS inside the market's own 300 s window, with a median maximum
# gap of 875.8 s. 69.3% of windows had no usable in-window quote at all.
#
# CAUSE, in two halves. The events feed returns windows as "active" long
# before they open, and `subscribed` only ever GREW -- nothing was ever
# unsubscribed. So each symbol was streamed from well before its open until
# the process died, and almost every row is a pre-open or post-expiry
# placeholder quote (a static 0.4800/0.5000 x 500).
#
# FIX: subscribe no later than expiry - LEAD, drop at expiry + RETAIN. The
# connection then spends its bandwidth on windows that are actually trading.
GEMINI_SUBSCRIBE_LEAD_S = 330.0    # >= 300 s window + margin
GEMINI_RETAIN_AFTER_S = 30.0       # keep through settlement prints
GEMINI_HEALTH_SECONDS = 30.0


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
                expiry = contract.get("expiryDate") or row.get("expiryDate")
                symbols[symbol] = {
                    "event": ticker,
                    "asset": match.group(1),
                    "window_minutes": int(match.group(2)),
                    "expiry": expiry,
                    "expiry_t": _expiry_epoch(expiry),
                }
    return symbols


def _expiry_epoch(value: Any) -> Optional[float]:
    """Expiry as epoch seconds, or None. Never raises."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _canonical_bbo(market: str, meta: Mapping[str, Any], t: float,
                   bids: Any, asks: Any) -> Optional[Dict[str, Any]]:
    """One canonical trajectory row. Returns None when the quote is not
    two-sided -- a one-sided book is recorded raw but is not a state."""
    def _top(levels):
        if not isinstance(levels, list) or not levels:
            return None, None
        lv = levels[0]
        if not isinstance(lv, (list, tuple)) or len(lv) < 2:
            return None, None
        try:
            return float(lv[0]), float(lv[1])
        except (TypeError, ValueError):
            return None, None
    bid, bid_sz = _top(bids)
    ask, ask_sz = _top(asks)
    if bid is None or ask is None:
        return None
    exp = meta.get("expiry_t")
    return {"t": t, "kind": "bbo", "market": market,
            "asset": meta.get("asset"),
            "window_minutes": meta.get("window_minutes"),
            "expiry_t": exp, "expiry_source": "gemini_events_rest",
            "window_start_t": (None if exp is None
                               else exp - 60.0 * (meta.get("window_minutes") or 5)),
            "tte_s": (None if exp is None else exp - t),
            "bid": bid, "ask": ask, "bid_sz": bid_sz, "ask_sz": ask_sz,
            "spread": round(ask - bid, 6),
            "depth": {"bids": (bids or [])[:5], "asks": (asks or [])[:5]}}


GEMINI_DIGEST_MAX_OBS = 400   # hard cap per window: a window that never
                              # drops must not grow the buffer without bound
GEMINI_DIGEST_CHUNK = 50      # observations per SERIES line; long log lines
                              # are truncated by the log transport


def _win_dur_s(meta: Mapping[str, Any]) -> float:
    return 60.0 * float(meta.get("window_minutes") or 5)


def _p50(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    v = sorted(xs)
    n = len(v)
    return round(v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0, 6)


def _window_digest(market: str, meta: Mapping[str, Any],
                   series: List[Any], prints: int,
                   one_sided: int) -> Optional[Dict[str, Any]]:
    """Summarise ONE closed window from its in-window canonical observations.

    WHY THIS EXISTS: the canonical trajectory rows land on a Railway volume
    that only the collector container can read, so a window that completes
    is otherwise invisible to any analysis running elsewhere. The digest is
    the read path -- it carries exactly the fields the coverage/habitat
    scorers need, computed with the SAME conventions as
    analysis/l5/gemini_trajectory/habitat_expanded.py (two-sided means
    bid > 0 and ask > 0; depth at touch is min(bid_sz, ask_sz)).

    `one_sided` is the count of in-window samples whose book was NOT
    two-sided, so two_sided_pct has an honest denominator rather than one
    that silently drops what _canonical_bbo refused.
    """
    if len(series) < 2:
        return None
    ser = sorted(series, key=lambda o: -o[0])     # descending tte == chronological
    tte = [o[0] for o in ser]
    span = round(tte[0] - tte[-1], 2)
    dur = _win_dur_s(meta)
    two = sum(1 for o in ser if o[1] > 0 and o[2] > 0)
    tot = len(ser) + int(one_sided)
    depth = [min(o[3], o[4]) for o in ser
             if o[3] is not None and o[4] is not None]
    return {
        "market": market, "asset": meta.get("asset"),
        "expiry_t": meta.get("expiry_t"),
        "window_minutes": meta.get("window_minutes"),
        "obs": len(ser),
        "tte_first": tte[0], "tte_last": tte[-1], "span_s": span,
        "obs_per_min": (None if span <= 0
                        else round(len(ser) / (span / 60.0), 2)),
        "two_sided_pct": (None if not tot else round(100.0 * two / tot, 2)),
        "one_sided_obs": int(one_sided),
        "prints": int(prints),
        "spread_p50": _p50([round(o[2] - o[1], 6) for o in ser]),
        "depth_p50": _p50(depth),
        # A COMPLETE window spans >= 290 s of its own 0..dur tte range.
        "complete": bool(span >= dur - 10.0),
    }


def _digest_lines(digest: Mapping[str, Any], series: List[Any],
                  chunk: int = GEMINI_DIGEST_CHUNK) -> List[str]:
    """One summary line plus chunked series lines, reassemblable by
    (market, part). Each observation is [tte, bid, ask, bid_sz, ask_sz]."""
    out = ["GEMINI_WINDOW_CLOSE " + json.dumps(digest, separators=(",", ":"))]
    ser = sorted(series, key=lambda o: -o[0])
    parts = max(1, -(-len(ser) // max(1, chunk)))
    for i in range(parts):
        out.append("GEMINI_WINDOW_SERIES " + json.dumps(
            {"market": digest["market"], "part": i + 1, "parts": parts,
             "s": [list(o) for o in ser[i * chunk:(i + 1) * chunk]]},
            separators=(",", ":")))
    return out


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


async def gemini_task(sink: Sink, stop: asyncio.Event,
                      canon: Optional[Sink] = None,
                      health: Optional[Dict[str, Any]] = None) -> None:
    known: Set[str] = set()
    h = health if health is not None else {}
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
                    # A session END is a COLLECTION GAP until the next one
                    # opens. It is recorded as such so no consumer can
                    # forward-fill across it as if the market were observed.
                    await _gemini_ws_session(sink, stop, list(symbols),
                                             canon=canon, health=h)
                    gap = {"t": _now(), "venue": "gemini", "kind": "connection",
                           "event": "ws_close", "reconnects": h.get("reconnects")}
                    await sink.write(gap)
                    if canon is not None:
                        await canon.write({"t": _now(), "kind": "gap",
                                           "reason": "ws_session_end"})
                else:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                h["errors"] = int(h.get("errors") or 0) + 1
                await sink.write({"t": _now(), "venue": "gemini", "kind": "error",
                                  "error": type(exc).__name__})
                if canon is not None:
                    await canon.write({"t": _now(), "kind": "gap",
                                       "reason": f"error:{type(exc).__name__}"})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass


GEMINI_WS_SESSION_SECONDS = 600.0  # hold one connection; subscribe rolled windows live


async def _gemini_ws_session(sink: Sink, stop: asyncio.Event, symbols: List[str],
                             canon: Optional[Sink] = None,
                             health: Optional[Dict[str, Any]] = None) -> None:
    """One long-lived ws session for trades+depth.

    Reconnect churn every DISCOVER_SECONDS was dropping exactly the bursts we
    need to time (all big overnight windows had zero captured prints), so the
    session now persists GEMINI_WS_SESSION_SECONDS and re-discovers newly
    rolled windows in-place, subscribing them on the same connection.
    """
    # `known` holds every discovered window with its metadata; `subscribed`
    # holds only those inside their own trading interval. A window moves
    # known -> subscribed at expiry - LEAD and subscribed -> dropped at
    # expiry + RETAIN, so the connection is not spent on dead symbols.
    known: Dict[str, Dict[str, Any]] = {}
    subscribed: Set[str] = set()
    last_top_write: Dict[str, float] = {}
    # per-window digest buffers, cleared when the window is unsubscribed
    closed: Dict[str, List[Any]] = {}
    prints_by: Dict[str, int] = {}
    one_sided_by: Dict[str, int] = {}
    last_discover = time.monotonic()
    last_health = 0.0
    session_deadline = time.monotonic() + GEMINI_WS_SESSION_SECONDS
    h = health if health is not None else {}

    def _due(meta: Mapping[str, Any], now: float) -> bool:
        e = meta.get("expiry_t")
        if e is None:
            return False          # no expiry -> never eligible; UNKNOWN, not assumed
        return (e - GEMINI_SUBSCRIBE_LEAD_S) <= now <= (e + GEMINI_RETAIN_AFTER_S)

    def _expired(meta: Mapping[str, Any], now: float) -> bool:
        e = meta.get("expiry_t")
        return e is not None and now > e + GEMINI_RETAIN_AFTER_S

    async with websockets.connect(GEMINI_WS, open_timeout=5, ping_interval=15, ping_timeout=8, max_queue=5000) as ws:
        h["reconnects"] = int(h.get("reconnects") or 0) + 1
        await sink.write({"t": _now(), "venue": "gemini", "kind": "connection",
                          "event": "ws_open", "reconnects": h["reconnects"]})
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "arb-laneD-readonly/1.0"}) as http:
            while not stop.is_set() and time.monotonic() < session_deadline:
                if time.monotonic() - last_discover >= DISCOVER_SECONDS:
                    last_discover = time.monotonic()
                    try:
                        fresh = await _gemini_active_symbols(http)
                        for sym, meta in fresh.items():
                            if sym not in known:
                                known[sym] = meta
                                # window metadata is provenance: persist it the
                                # moment it is learned, not at subscribe time
                                await sink.write({"t": _now(), "venue": "gemini",
                                                  "kind": "window", "market": sym,
                                                  **meta})
                                if canon is not None:
                                    await canon.write({"t": _now(), "kind": "window",
                                                       "market": sym, **meta})
                    except httpx.HTTPError:
                        pass
                    # roll the subscription set on the window clock
                    now_w = _now()
                    add = [s for s, m in known.items()
                           if s not in subscribed and _due(m, now_w)]
                    drop = [s for s in subscribed
                            if _expired(known.get(s, {}), now_w)]
                    try:
                        if add:
                            subscribed.update(add)
                            await ws.send(json.dumps({
                                "id": "laneD-add", "method": "SUBSCRIBE",
                                "params": [f"{x}@trade" for x in add]
                                          + [f"{x}@depth5@100ms" for x in add]}))
                        if drop:
                            for x in drop:
                                subscribed.discard(x)
                                last_top_write.pop(x, None)
                            await ws.send(json.dumps({
                                "id": "laneD-drop", "method": "UNSUBSCRIBE",
                                "params": [f"{x}@trade" for x in drop]
                                          + [f"{x}@depth5@100ms" for x in drop]}))
                        for x in known:
                            known[x].setdefault("_pruned", False)
                        for x in list(known):
                            if _expired(known[x], now_w - 3600):
                                known.pop(x, None)   # bounded memory
                    except websockets.WebSocketException:
                        pass
                    # A dropped window is a CLOSED window: emit its digest
                    # now, while its metadata is still known, then release
                    # the buffers.
                    for x in drop:
                        ser = closed.pop(x, None) or []
                        d = _window_digest(x, known.get(x) or {}, ser,
                                           prints_by.pop(x, 0),
                                           one_sided_by.pop(x, 0))
                        if d is None:
                            continue
                        for line in _digest_lines(d, ser):
                            print(line, flush=True)
                    h["subscribed"] = len(subscribed)
                    h["known"] = len(known)
                    h["expected_active"] = sum(1 for m in known.values()
                                               if _due(m, now_w))
                if health is not None and _now() - last_health >= GEMINI_HEALTH_SECONDS:
                    last_health = _now()
                    await sink.write({"t": _now(), "venue": "gemini",
                                      "kind": "health", **h})
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
                    # PRINTS ARE NEVER SAMPLED AWAY: they are the scarce
                    # evidence (735 in the entire historical tape) and the
                    # only thing that can support a STRICT fill claim.
                    key = symbol.upper()
                    now = _now()
                    row = {"t": now, "venue": "gemini", "kind": "print",
                           "market": key,
                           "event_ns": msg.get("E"), "trade_id": msg.get("t"),
                           "price": msg.get("p"), "qty": msg.get("q"),
                           "buyer_is_maker": bool(msg.get("m"))}
                    await sink.write(row)
                    h["prints"] = int(h.get("prints") or 0) + 1
                    h["last_print_t"] = now
                    _pm = known.get(key) or {}
                    _pe = _pm.get("expiry_t")
                    if _pe is not None and 0.0 <= _pe - now <= _win_dur_s(_pm):
                        prints_by[key] = prints_by.get(key, 0) + 1
                    if canon is not None:
                        meta = known.get(key, {})
                        exp = meta.get("expiry_t")
                        await canon.write({
                            "t": now, "kind": "print", "market": key,
                            "asset": meta.get("asset"),
                            "expiry_t": exp,
                            "tte_s": (None if exp is None else exp - now),
                            "price": msg.get("p"), "qty": msg.get("q"),
                            "buyer_is_maker": bool(msg.get("m")),
                            "trade_id": msg.get("t")})
                elif "bids" in msg and "asks" in msg:
                    now = _now()
                    key = symbol.upper()
                    if now - last_top_write.get(key, 0.0) >= GEMINI_BBO_RECORD_SECONDS:
                        last_top_write[key] = now
                        await sink.write({
                            "t": now, "venue": "gemini", "kind": "bbo", "market": key,
                            "bids": (msg.get("bids") or [])[:5],
                            "asks": (msg.get("asks") or [])[:5],
                        })
                        h["bbo"] = int(h.get("bbo") or 0) + 1
                        h["last_bbo_t"] = now
                        _m = known.get(key) or {}
                        c = _canonical_bbo(key, _m, now,
                                           msg.get("bids"), msg.get("asks"))
                        if c is not None and canon is not None:
                            await canon.write(c)
                        # Buffer for the window-close digest. A sample the
                        # canonicaliser REFUSED (one-sided book) is still
                        # counted, so two_sided_pct is not flattered by the
                        # refusal.
                        _e = _m.get("expiry_t")
                        _tte = None if _e is None else _e - now
                        if _tte is not None and 0.0 <= _tte <= _win_dur_s(_m):
                            if c is None:
                                one_sided_by[key] = one_sided_by.get(key, 0) + 1
                            else:
                                _b = closed.setdefault(key, [])
                                if len(_b) < GEMINI_DIGEST_MAX_OBS:
                                    _b.append((round(_tte, 2), c["bid"], c["ask"],
                                               c["bid_sz"], c["ask_sz"]))


async def run(out_dir: Path, hours: float, gemini_only: bool = False) -> None:
    """`hours <= 0` runs CONTINUOUSLY: the read-only collector is meant to be
    up 24/7, and a bounded session is what produced a tape whose median
    in-window coverage was zero seconds."""
    stop = asyncio.Event()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sink = Sink(out_dir / f"tape_{stamp}.jsonl")
    canon = Sink(out_dir / f"canonical_{stamp}.jsonl")
    health: Dict[str, Any] = {"rows": 0, "reconnects": 0, "errors": 0}
    await sink.write({"t": _now(), "kind": "collector_start", "venue": "meta",
                      "bounded_hours": hours, "continuous": hours <= 0,
                      "gemini_only": gemini_only,
                      "read_only": True, "orders": "none: this process has no "
                      "order path and no credentials",
                      "kalshi_series": [] if gemini_only else list(KALSHI_SERIES)})
    tasks = [asyncio.create_task(gemini_task(sink, stop, canon=canon,
                                             health=health)),
             asyncio.create_task(gemini_volume_task(sink, stop))]
    if not gemini_only:
        tasks.append(asyncio.create_task(kalshi_task(sink, stop)))
    tasks.append(asyncio.create_task(_health_task(sink, stop, health, out_dir)))
    try:
        if hours > 0:
            await asyncio.sleep(hours * 3600)
        else:
            await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await sink.write({"t": _now(), "kind": "collector_end", "venue": "meta"})
        sink.close()
        canon.close()


async def _health_task(sink: Sink, stop: asyncio.Event,
                       health: Dict[str, Any], out_dir: Path) -> None:
    """Fail VISIBLY. A collector quietly gathering zero usable windows is the
    failure mode this exists to make impossible to miss."""
    import shutil
    while not stop.is_set():
        try:
            du = shutil.disk_usage(str(out_dir))
            now = _now()
            line = {"t": now, "kind": "collector_health", "venue": "meta",
                    "subscribed": health.get("subscribed"),
                    "known_windows": health.get("known"),
                    "expected_active": health.get("expected_active"),
                    "bbo_rows": health.get("bbo"), "print_rows": health.get("prints"),
                    "last_bbo_t": health.get("last_bbo_t"),
                    "last_print_t": health.get("last_print_t"),
                    "bbo_age_s": (None if not health.get("last_bbo_t")
                                  else round(now - health["last_bbo_t"], 1)),
                    "print_age_s": (None if not health.get("last_print_t")
                                    else round(now - health["last_print_t"], 1)),
                    "reconnects": health.get("reconnects"),
                    "errors": health.get("errors"),
                    "disk_free_gb": round(du.free / 1e9, 2),
                    "disk_used_pct": round(du.used / du.total * 100, 1),
                    "alarm": _health_alarm(health, now, du)}
            print("GEMINI_COLLECTOR_HEALTH " + json.dumps(line), flush=True)
            await sink.write(line)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=GEMINI_HEALTH_SECONDS)
        except asyncio.TimeoutError:
            pass


def _health_alarm(health: Mapping[str, Any], now: float, du: Any) -> Optional[str]:
    if health.get("expected_active") and not health.get("subscribed"):
        return "ACTIVE_WINDOWS_BUT_NONE_SUBSCRIBED"
    last = health.get("last_bbo_t")
    if last and now - last > 120.0:
        return "NO_BBO_FOR_120S"
    if not last and (health.get("reconnects") or 0) > 2:
        return "CONNECTED_BUT_NEVER_RECEIVED_A_QUOTE"
    if du.free < 2e9:
        return "DISK_BELOW_2GB"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Lane-2 tape collector")
    parser.add_argument("--hours", type=float, default=5.0,
                        help="<= 0 runs continuously (24/7)")
    parser.add_argument("--gemini-only", action="store_true",
                        help="skip the Kalshi tasks; Gemini BTC/ETH 5m only")
    parser.add_argument("--out-dir", default="data/policy_d_port")
    parser.add_argument("--series", default="",
                        help="comma-separated Kalshi series override "
                             "(e.g. KXSOL15M,KXXRP15M); default keeps the "
                             "standard BTC/ETH pair")
    args = parser.parse_args()
    if args.series:
        global KALSHI_SERIES
        KALSHI_SERIES = tuple(s.strip() for s in args.series.split(",") if s.strip())
    asyncio.run(run(Path(args.out_dir), args.hours,
                    gemini_only=bool(args.gemini_only)))


if __name__ == "__main__":
    main()
