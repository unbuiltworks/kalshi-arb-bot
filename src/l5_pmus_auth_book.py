"""The AUTHENTICATED Polymarket US BTC15m book, held live for the L5 engine.

WHY THIS EXISTS. The public gateway exposes a BTC15m market only partway into
its own window (09ecc33): the live window 404s while the authenticated stream
already carries it trading at +6.8s. The L5 engine was therefore structurally
blind exactly while L5 opens his sequence. This module reads the book over the
authenticated markets WebSocket instead, the same stream and the same parser
the evidence recorder uses, so what the engine prices against is what the
recorder records.

REUSED, NOT REIMPLEMENTED: `auth_headers` (Ed25519 over `{ts}GET{path}`),
`subscribe_frames`, `guard_outbound` and `apply_events` all come from
`pmus_ws_evidence_recorder`. Only subscription to the clock-derived BTC15m
windows is new: the gateway does not list series 375's events at all, so the
slug comes from the wall clock, and the NEXT window is subscribed LEAD_S before
it opens so its first book is never missed.

READ-ONLY. The markets stream carries book and trade data only; every outbound
frame passes `guard_outbound`, which admits subscribe/unsubscribe/heartbeat and
nothing else.

OBSERVABILITY IS MEASURED, NOT ASSUMED. For every window it records the receipt
time of the first two-sided book and of the first trade, against the window's
own start, so "observable from the beginning" is a number per window.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from src import pmus_ws_evidence_recorder as R

WINDOW_S = 900
#: subscribe the next window this long before it opens
LEAD_S = 120.0
#: reconnect with a fresh subscription set once this many slugs accumulate
MAX_SUBSCRIBED = 6


def window_start(now: Optional[float] = None) -> int:
    t = time.time() if now is None else now
    return int(t // WINDOW_S) * WINDOW_S


def slug_for(ws: int) -> str:
    d = dt.datetime.fromtimestamp(ws, dt.timezone.utc)
    return "cpc-btc-updown-15m-%s-%02d%02dz" % (
        d.strftime("%Y-%m-%d"), d.hour, d.minute)


def slug_start(slug: str) -> Optional[int]:
    try:
        d = dt.datetime.strptime(slug[-16:], "%Y-%m-%d-%H%Mz")
    except ValueError:
        return None
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp())


def wanted_slugs(now: Optional[float] = None, lead_s: float = LEAD_S) -> List[str]:
    """The window trading now, plus the next one once it is within lead_s."""
    t = time.time() if now is None else now
    ws = window_start(t)
    out = [slug_for(ws)]
    if (ws + WINDOW_S) - t <= lead_s:
        out.append(slug_for(ws + WINDOW_S))
    return out


class AuthBook:
    """Live authenticated book for the BTC15m windows, on a daemon thread."""

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 lead_s: float = LEAD_S) -> None:
        self.clock = clock
        self.lead_s = lead_s
        self.lock = threading.Lock()
        self.state: Dict[str, dict] = {}
        self.first_book: Dict[str, float] = {}
        self.first_trade: Dict[str, float] = {}
        self.connected = False
        self.last_frame_t: Optional[float] = None
        self.connects = 0
        self.errors: List[str] = []
        self.subscribed: List[str] = []
        #: slugs whose subscription went out BEFORE their window opened
        self.subscribed_at_open: set = set()
        self._thread: Optional[threading.Thread] = None
        self._stop = False

    # ------------------------------------------------------------ fold
    def ingest(self, msg: Any, now: float) -> None:
        """Fold one frame. Pure bookkeeping, separated for tests."""
        with self.lock:
            self.last_frame_t = now
            R.apply_events(self.state, msg, now)
            for slug, s in self.state.items():
                if (slug not in self.first_book and s.get("bid") is not None
                        and s.get("ask") is not None):
                    self.first_book[slug] = now
                if slug not in self.first_trade and s.get("trades"):
                    self.first_trade[slug] = s["trades"][0]["t"]

    # ------------------------------------------------------------ reads
    def snapshot(self, slug: str) -> Optional[Dict[str, Any]]:
        """The authenticated top of book NOW, in the frozen rule's shape.

        `stream_age_s` is the time since the socket last delivered ANY frame.
        A push stream sends nothing when nothing changes, so the age of the
        last CHANGE says nothing about freshness; the age of the last frame on
        a connected socket does. None unless connected and two-sided.
        """
        now = self.clock()
        with self.lock:
            s = self.state.get(slug)
            if not self.connected or not s:
                return None
            if s.get("bid") is None or s.get("ask") is None:
                return None
            hist = s.get("bbo_history") or []
            return {"bid": s["bid"], "ask": s["ask"],
                    "bid_shares": s.get("bid_qty"),
                    "ask_shares": s.get("ask_qty"),
                    "last_update_t": hist[-1]["t"] if hist else None,
                    "stream_age_s": (round(now - self.last_frame_t, 3)
                                     if self.last_frame_t else None),
                    "book_updates": s.get("book_updates"),
                    "source": "authenticated_ws"}

    def window_report(self, slug: str) -> Dict[str, Any]:
        ws = slug_start(slug)
        with self.lock:
            s = self.state.get(slug) or {}
            fb, ft = self.first_book.get(slug), self.first_trade.get(slug)
            return {
                "slug": slug,
                "first_book_offset_s": (round(fb - ws, 1)
                                        if fb is not None and ws else None),
                "first_trade_offset_s": (round(ft - ws, 1)
                                         if ft is not None and ws else None),
                "book_before_first_trade": (fb is not None and
                                            (ft is None or fb <= ft)),
                "book_updates": s.get("book_updates", 0),
                "trades_seen": len(s.get("trades") or []),
                "subscribed_before_open": slug in self.subscribed_at_open,
            }

    def status(self) -> Dict[str, Any]:
        now = self.clock()
        return {"connected": self.connected, "connects": self.connects,
                "subscribed": list(self.subscribed),
                "stream_age_s": (round(now - self.last_frame_t, 1)
                                 if self.last_frame_t else None),
                "errors": self.errors[-3:]}

    # ------------------------------------------------------------ socket
    def start(self) -> "AuthBook":
        self._thread = threading.Thread(target=self._run_thread,
                                        name="auth-book", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop = True

    def _run_thread(self) -> None:
        asyncio.run(self._run())

    async def _subscribe(self, ws, slugs: List[str]) -> None:
        for frame in R.subscribe_frames(slugs):
            await ws.send(json.dumps(R.guard_outbound(frame)))
        now = self.clock()
        for s in slugs:
            self.subscribed.append(s)
            st = slug_start(s)
            if st is not None and now < st:
                self.subscribed_at_open.add(s)

    async def _run(self) -> None:
        import websockets
        backoff = 1.0
        while not self._stop:
            try:
                headers = R.auth_headers(os.environ[R.KEY_ENV].strip(),
                                         os.environ[R.SECRET_ENV].strip())
                async with websockets.connect(
                        R.WS_URL, additional_headers=headers,
                        max_size=8 * 1024 * 1024) as ws:
                    self.connected = True
                    self.connects += 1
                    self.subscribed = []
                    backoff = 1.0
                    await self._subscribe(ws, wanted_slugs(self.clock(),
                                                           self.lead_s))
                    last_hb = time.monotonic()
                    while not self._stop:
                        want = [s for s in wanted_slugs(self.clock(),
                                                        self.lead_s)
                                if s not in self.subscribed]
                        if want:
                            if len(self.subscribed) + len(want) > MAX_SUBSCRIBED:
                                break                  # fresh, small set
                            await self._subscribe(ws, want)
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                        except asyncio.TimeoutError:
                            if time.monotonic() - last_hb >= R.IDLE_HEARTBEAT_S:
                                await ws.send(json.dumps(
                                    R.guard_outbound({"heartbeat": {}})))
                                last_hb = time.monotonic()
                            continue
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(msg, dict) and "heartbeat" in msg:
                            await ws.send(json.dumps(
                                R.guard_outbound({"heartbeat": {}})))
                            last_hb = time.monotonic()
                        self.ingest(msg, self.clock())
            except Exception as exc:                           # noqa: BLE001
                self.errors.append("%s: %s" % (type(exc).__name__,
                                               str(exc)[:160]))
                del self.errors[:-20]
            self.connected = False
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
