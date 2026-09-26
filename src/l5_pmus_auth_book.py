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
#: an open window with no book forces a FRESH CONNECTION this often.
#: MEASURED 2026-09-24. A market-data subscription placed before a window
#: opens stays silent ~3 min (0015z +184.5s, 2345z +186.7s, 0115z +184.3s).
#: Re-sending the subscribe on the SAME connection does not help: 0115z got
#: 51 re-subscribes from +0.1s and still no book until +184.3s. A FRESH
#: connection subscribing to an already-live market gets the full snapshot
#: at once, three for three: 0000z +300.5s, 0030z +236.5s, 0100z +279.1s,
#: each the instant the restarted service connected.
#: 0415z: a reconnect at +0.1s landed before the market was live and went
#: silent; the 10s retry got the book at +10.8s, after trading began at
#: +6.9s. Retrying every 3s bounds that to ~+3s.
RESUB_EVERY_S = 3.0
#: after this many fruitless reconnects in one window, slow to RESUB_SLOW_S.
#: 2026-09-24 10:00Z (venue weekly maintenance, Thu 06:00-08:00 ET) produced
#: no frames at all and 3s reconnects ran ~12/min on the shared key; a
#: healthy window needs 1-2, so 10 is far outside normal and never delays one.
RESUB_FAST_MAX = 10
RESUB_SLOW_S = 30.0
#: a connected socket silent this long while the open window already has a
#: book is treated as dead and replaced. 2026-09-24 1215z, after the venue's
#: post-maintenance server restarts ("1001 server shutting down" x3), our
#: socket stayed open but delivered 26 book updates in the window while the
#: independent recorder received 1,747; 115 L5 actions were refused STALE.
#: A legitimately quiet market was measured silent ~2.8s, far below this.
STALL_S = 10.0


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
        #: DIAGNOSTIC ONLY: compact shape of the frames a market received
        #: before its first two-sided book, so a late first book can be
        #: attributed to the venue (one-sided / pre-open) or to our fold
        self.pre_book: Dict[str, List[dict]] = {}
        self.pre_book_n: Dict[str, int] = {}
        self.first_state: Dict[str, Dict[str, float]] = {}
        self.forced_reconnect = False
        self.stall_reconnects = 0
        self.last_stall_t = 0.0
        #: slug -> [first forced-reconnect t, last, count]
        self.resub: Dict[str, List[float]] = {}
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
    @staticmethod
    def market_states(msg: Any, out: Optional[Dict[str, str]] = None
                      ) -> Dict[str, str]:
        """{slug: MARKET_STATE_*} wherever a frame carries one. The recorder's
        parser folds prices and depth only; the venue's own trading state is
        read here from the same frames (docs: marketData.state)."""
        out = {} if out is None else out
        if isinstance(msg, list):
            for m in msg:
                AuthBook.market_states(m, out)
        elif isinstance(msg, dict):
            slug = msg.get("marketSlug") or msg.get("market_slug")
            st = msg.get("state")
            if slug and isinstance(st, str) and st.startswith("MARKET_STATE_"):
                out[str(slug)] = st
            for v in msg.values():
                if isinstance(v, (dict, list)):
                    AuthBook.market_states(v, out)
        return out

    @staticmethod
    def frame_shapes(msg: Any, out: Optional[List[dict]] = None) -> List[dict]:
        """Every market-data dict in a frame, reduced to its shape."""
        out = [] if out is None else out
        if isinstance(msg, list):
            for m in msg:
                AuthBook.frame_shapes(m, out)
        elif isinstance(msg, dict):
            slug = msg.get("marketSlug") or msg.get("market_slug")
            if slug and any(k in msg for k in ("bids", "offers", "asks",
                                               "bestBid", "bestAsk", "state")):
                def top(v):
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        return {k: v[0].get(k) for k in ("px", "price", "qty")}
                    return v
                out.append({"slug": str(slug), "keys": sorted(msg)[:14],
                            "n_bids": (len(msg["bids"]) if isinstance(
                                msg.get("bids"), list) else None),
                            "n_offers": (len(msg.get("offers") or
                                             msg.get("asks") or [])
                                         if isinstance(msg.get("offers",
                                                       msg.get("asks")), list)
                                         else None),
                            "bid0": top(msg.get("bids")),
                            "offer0": top(msg.get("offers", msg.get("asks"))),
                            "bestBid": msg.get("bestBid"),
                            "bestAsk": msg.get("bestAsk"),
                            "state": msg.get("state")})
            for v in msg.values():
                if isinstance(v, (dict, list)):
                    AuthBook.frame_shapes(v, out)
        return out

    def ingest(self, msg: Any, now: float) -> None:
        """Fold one frame. Pure bookkeeping, separated for tests."""
        with self.lock:
            self.last_frame_t = now
            R.apply_events(self.state, msg, now)
            for sh in self.frame_shapes(msg):
                slug = sh.pop("slug")
                if sh.get("state"):
                    self.first_state.setdefault(slug, {}).setdefault(
                        sh["state"], now)
                if slug in self.first_book:
                    continue
                n = self.pre_book_n.get(slug, 0) + 1
                self.pre_book_n[slug] = n
                if n <= 6 or (n % 25 == 0 and n <= 300):
                    sh["n"] = n
                    sh["t"] = round(now, 3)
                    self.pre_book.setdefault(slug, []).append(sh)
            for slug, st in self.market_states(msg).items():
                self.state.setdefault(slug, {"bid": None, "bid_qty": None,
                                             "ask": None, "ask_qty": None,
                                             "both_sides_served": False,
                                             "trades": [], "book_updates": 0})
                self.state[slug]["market_state"] = st
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
                    "market_state": s.get("market_state"),
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
                "pre_book_frames": self.pre_book_n.get(slug, 0),
                "book_resubscribes": (
                    {"first_offset_s": round(self.resub[slug][0] - ws, 1),
                     "count": self.resub[slug][2]}
                    if slug in self.resub and ws else None),
                "pre_book_sample": [
                    dict(f, t_offset_s=round(f["t"] - ws, 1) if ws else None)
                    for f in self.pre_book.get(slug, [])],
                "state_first_seen_offset_s": {
                    k: round(v - ws, 1) if ws else None
                    for k, v in self.first_state.get(slug, {}).items()},
            }

    def resub_due(self, now: float) -> List[str]:
        """Open windows we subscribed to that still have no book."""
        out = []
        for slug in self.subscribed:
            st = slug_start(slug)
            if st is None or now < st or now >= st + WINDOW_S:
                continue
            if slug in self.first_book:
                continue
            r = self.resub.get(slug)
            every = RESUB_EVERY_S if (r is None or r[2] < RESUB_FAST_MAX) \
                else RESUB_SLOW_S
            if r is None or now - r[1] >= every:
                out.append(slug)
        return out

    def stall_due(self, now: float) -> bool:
        """The open window has a book, yet the socket has gone silent."""
        if not self.connected or self.last_frame_t is None:
            return False
        if now - self.last_frame_t < STALL_S:
            return False
        if now - self.last_stall_t < (STALL_S if self.stall_reconnects < RESUB_FAST_MAX
                                      else RESUB_SLOW_S):
            return False
        cur = slug_for(window_start(now))
        return cur in self.first_book

    def note_resub(self, slug: str, now: float) -> None:
        r = self.resub.get(slug)
        self.resub[slug] = [now, now, 1] if r is None else [r[0], now, r[2] + 1]

    @staticmethod
    def market_data_frame(slug: str) -> dict:
        """A MARKET-DATA-only subscribe: the trade subscription already
        works from the first second and is not duplicated."""
        import uuid
        return {"subscribe": {"request_id": "mkre-%s" % uuid.uuid4().hex[:10],
                              "subscription_type": R.SUB_MARKET_DATA,
                              "market_slugs": [slug]}}

    def status(self) -> Dict[str, Any]:
        now = self.clock()
        return {"connected": self.connected, "connects": self.connects,
                "stall_reconnects": self.stall_reconnects,
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
                        now = self.clock()
                        if self.stall_due(now):
                            self.stall_reconnects += 1
                            self.last_stall_t = now
                            self.forced_reconnect = True
                            break                  # dead socket: replace it
                        due = self.resub_due(now)
                        if due:
                            for slug in due:
                                self.note_resub(slug, now)
                            self.forced_reconnect = True
                            break                  # fresh connection, below
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
            if self.forced_reconnect:
                self.forced_reconnect = False
                continue                           # no backoff: planned
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
