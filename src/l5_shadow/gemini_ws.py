"""Gemini order book from the WEBSOCKET, maintained, not sampled.

WHY THIS REPLACES THE REST PATH. Measured 2026-09-15 13:33Z on
GEMI-BTC15M2609151345-UP: eight consecutive REST book calls, 0.26-2.0s apart,
returned SEVEN different books. The best ask moved 0.20 -> 0.26 and the touch
size flickered between 1.0 and 702.0 within the same second. REST is not stale
-- level timestamps were 0.12-1.17s old and no cache header is served -- it is
simply a sample of something that changes faster than we can poll.

The websocket shows why. `wss://api.gemini.com/v1/marketdata/{symbol}` delivers
an `initial` snapshot on connect and then per-order deltas at ~35 messages a
second, and the deltas are dominated by quote churn:

    {"delta":"-1.0","price":"0.39","reason":"cancel","remaining":"0.0",...}
    {"delta":"1.0", "price":"0.39","reason":"place", "remaining":"1.0", ...}

placed and cancelled 25 MILLISECONDS apart. A REST snapshot that lands between
those two messages sees a 1-share touch; one that lands after sees nothing
there at all. Neither reading is wrong and neither is the book.

WHAT THIS BUYS US, beyond freshness:

  * `reason` separates `trade` from `place`/`cancel`, so a fill is evidenced by
    an actual trade event rather than inferred from a polled trade list;
  * millisecond venue timestamps on every change;
  * level LIFETIME becomes measurable -- how long size actually rests at a
    price is the statistic that decides whether a taker can reach it, and no
    sequence of snapshots can measure it.

FAIL CLOSED. `book()` returns None until the `initial` snapshot has arrived and
whenever the socket is not connected. An empty book and an absent one are
different facts, and a stale maintained book is worse than no book: it looks
authoritative. `age_s` is therefore the age of the last APPLIED message, and a
caller that ignores it is reading history.

READ ONLY. The public market-data socket takes no credentials and carries no
order path.
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Optional

WS_URL = "wss://api.gemini.com/v1/marketdata/{symbol}"
CA_BUNDLE = "/root/.ccr/ca-bundle.crt"
# a book with no applied message for this long is reported as UNKNOWN rather
# than served: the venue is quiet or we are disconnected, and we cannot tell
# which from the book alone.
STALE_AFTER_S = 30.0


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    try:
        c.load_verify_locations(CA_BUNDLE)
    except OSError:
        pass
    return c


class GeminiBook:
    """One symbol's live book, maintained from the v1 marketdata socket.

    Thread-safe for a single writer (the socket thread) and many readers.
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._lock = threading.Lock()
        self._bids: Dict[D, D] = {}
        self._asks: Dict[D, D] = {}
        self.ready = False
        self.connected = False
        # when the CURRENT continuous view began: the moment `initial` landed,
        # reset by any gap or reconnect. A trade list is only complete for an
        # interval that starts at or after this instant, and claiming
        # completeness over a gap is how a missed print becomes a "no fill".
        self.ready_since: Optional[float] = None
        self.last_msg_t: Optional[float] = None
        self.last_venue_ms: Optional[int] = None
        self.socket_sequence: Optional[int] = None
        self.counts: Dict[str, int] = {"initial": 0, "place": 0, "cancel": 0,
                                       "trade": 0, "top": 0, "other": 0}
        self.trades: List[dict] = []          # evidenced prints, newest last
        self.gaps = 0                          # socket_sequence discontinuities
        self.reconnects = 0
        self.errors: List[str] = []

    # ------------------------------------------------------------ maintenance

    def apply(self, msg: dict, *, now: Optional[float] = None) -> None:
        t = time.time() if now is None else now
        seq = msg.get("socket_sequence")
        with self._lock:
            if seq is not None:
                if (self.socket_sequence is not None
                        and seq != self.socket_sequence + 1):
                    # A GAP MEANS THE BOOK IS WRONG, not merely late. Recorded
                    # and the book is invalidated; silently continuing would
                    # serve a book missing whatever the gap contained.
                    self.gaps += 1
                    self.ready = False
                    self.ready_since = None
                    self._bids.clear()
                    self._asks.clear()
                self.socket_sequence = seq
            ms = msg.get("timestampms")
            if ms:
                self.last_venue_ms = int(ms)
            for ev in msg.get("events") or []:
                self._apply_event(ev, t)
            self.last_msg_t = t

    def _apply_event(self, ev: dict, t: float) -> None:
        kind = str(ev.get("type"))
        reason = str(ev.get("reason") or "")
        if kind == "trade":
            self.counts["trade"] += 1
            try:
                self.trades.append({
                    "t": t,
                    "venue_ms": self.last_venue_ms,
                    "price": D(str(ev.get("price"))),
                    "size": D(str(ev.get("amount"))),
                    # Gemini names the side of the RESTING order that was hit,
                    # so a "bid" trade is someone SELLING into a bid.
                    "resting_side": str(ev.get("makerSide") or ev.get("side")
                                        or ""),
                    "venue": "GEMINI", "asset": self.symbol})
            except Exception:                                 # noqa: BLE001
                pass
            if len(self.trades) > 5000:
                self.trades = self.trades[-2000:]
            return
        if kind != "change":
            self.counts["other"] += 1
            return
        self.counts[reason if reason in self.counts else "other"] += 1
        if reason == "initial":
            if not self.ready:
                self.ready_since = t
            self.ready = True
        try:
            px = D(str(ev.get("price")))
            rem = D(str(ev.get("remaining")))
        except Exception:                                     # noqa: BLE001
            return
        side = str(ev.get("side"))
        book = self._bids if side == "bid" else self._asks
        if rem <= 0:
            book.pop(px, None)
        else:
            book[px] = rem

    # ----------------------------------------------------------------- reading

    def snapshot(self, *, now: Optional[float] = None) -> Optional[dict]:
        """Best-first ladders, or None when the book cannot be trusted."""
        t = time.time() if now is None else now
        with self._lock:
            if not (self.ready and self.connected):
                return None
            if self.last_msg_t is None or t - self.last_msg_t > STALE_AFTER_S:
                return None
            bids = sorted(self._bids.items(), key=lambda kv: -kv[0])
            asks = sorted(self._asks.items(), key=lambda kv: kv[0])
            return {
                "venue": "GEMINI", "asset_id": self.symbol, "source": "websocket",
                "bids": [{"price": str(p), "amount": str(s)} for p, s in bids],
                "asks": [{"price": str(p), "amount": str(s)} for p, s in asks],
                "price_key": "price", "size_key": "amount",
                "source_ts": self.last_msg_t,
                "venue_ts_ms": self.last_venue_ms,
                "observed_at": t,
                "age_s": round(t - self.last_msg_t, 4),
                "socket_sequence": self.socket_sequence,
                "gaps": self.gaps, "reconnects": self.reconnects,
                "ready_since": self.ready_since,
                "counts": dict(self.counts),
            }

    def prints_since(self, t0: float) -> Optional[List[dict]]:
        """Trades observed strictly after `t0`, or None if we cannot vouch for
        the interval.

        None when the socket's current continuous view began AFTER `t0`: we
        would be reporting an empty or partial trade list for a period we were
        not watching, and "we saw nothing" would be indistinguishable from "we
        were not there". That distinction is the whole difference between an
        evidenced no-fill and an unknown."""
        with self._lock:
            if not self.ready or self.ready_since is None:
                return None
            if self.ready_since > t0:
                return None
            return [dict(x) for x in self.trades if x["t"] > t0]


class GeminiFeed:
    """Background socket per symbol. Reconnects; never blocks a reader."""

    def __init__(self) -> None:
        self._books: Dict[str, GeminiBook] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._stop = threading.Event()

    def watch(self, symbol: str) -> GeminiBook:
        b = self._books.get(symbol)
        if b is None:
            b = self._books[symbol] = GeminiBook(symbol)
            th = threading.Thread(target=self._run, args=(symbol,), daemon=True)
            self._threads[symbol] = th
            th.start()
        return b

    def book(self, symbol: str) -> Optional[dict]:
        b = self._books.get(symbol)
        return b.snapshot() if b else None

    def watched(self) -> List[str]:
        return sorted(self._books)

    def drop(self, symbol: str) -> None:
        """Stop following an expired window so sockets do not accumulate."""
        self._books.pop(symbol, None)

    def stop(self) -> None:
        self._stop.set()

    def _run(self, symbol: str) -> None:
        import asyncio
        try:
            import websockets
        except ImportError:
            bk = self._books.get(symbol)
            if bk:
                bk.errors.append("websockets library unavailable")
            return

        async def go() -> None:
            url = WS_URL.format(symbol=symbol)
            while not self._stop.is_set():
                bk = self._books.get(symbol)
                if bk is None:
                    return                     # dropped: let the thread die
                try:
                    async with websockets.connect(url, ssl=_ctx(),
                                                  open_timeout=15,
                                                  ping_interval=20) as ws:
                        bk.connected = True
                        while not self._stop.is_set():
                            if self._books.get(symbol) is None:
                                return
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                            try:
                                bk.apply(json.loads(raw))
                            except ValueError:
                                continue
                except Exception as exc:                      # noqa: BLE001
                    if bk:
                        bk.connected = False
                        bk.ready = False
                        bk.reconnects += 1
                        bk.errors.append(f"{type(exc).__name__}: {exc}"[:160])
                        bk.errors = bk.errors[-20:]
                    await asyncio.sleep(2.0)
        try:
            asyncio.run(go())
        except Exception:                                     # noqa: BLE001
            pass
