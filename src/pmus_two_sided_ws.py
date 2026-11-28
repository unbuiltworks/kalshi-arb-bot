"""Realtime top-of-book feed for the PMUS sports two-sided maker bot.

OWNER 2026-11-14: "react to price changes in real time, stop being picked
off". Evidence: the first leg of a pair filled a median 19s after placement
and 41% within 15s -- stale quotes hit right after a game event, while the
bot polled the public gateway BBO only every LOOP_S=5s.

This module is a READ-ONLY market-data client. It opens the same
authenticated market stream the evidence recorder uses
(`src/pmus_ws_evidence_recorder.py`: WS_URL, Ed25519 `{ts}GET{path}` handshake
headers, `subscribe` frame with subscription_type 1 = market data, venue
heartbeat echo) and keeps a thread-safe {slug: bid, ask, t}. It sends only
`subscribe` / `heartbeat` frames (guard_outbound) and has no order path; the
bot's main thread does every cancel/place.

Signing is `src/pmus_auth.signed_headers` (the order-free signer, tested
byte-identical to the executor); `tests/test_pmus_sports_two_sided.py`
asserts these headers, the subscribe frame and the top-of-book parse match
the recorder's so the two clients cannot drift silently.

Inert unless the bot runs with PMUS_TWO_SIDED_WS=1.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal as D
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

WS_URL = "wss://api.polymarket.us/v1/ws/markets"      # == recorder.WS_URL
WS_PATH = "/v1/ws/markets"                              # == recorder.WS_PATH
SUB_MARKET_DATA = 1                                     # == recorder value
OUTBOUND_FRAME_KEYS = frozenset({"subscribe", "heartbeat"})

FRESH_S = float(os.getenv("PMUS_TWO_SIDED_WS_FRESH_S", "3"))
IDLE_HEARTBEAT_S = float(os.getenv("PMUS_TWO_SIDED_WS_HEARTBEAT_S", "20"))
SILENT_RECONNECT_S = float(os.getenv("PMUS_TWO_SIDED_WS_SILENT_S", "60"))
BACKOFF_MAX_S = float(os.getenv("PMUS_TWO_SIDED_WS_BACKOFF_MAX_S", "30"))
STALE_LOG_S = 60.0
# a long connection accumulates subscriptions for markets the bot released;
# beyond this many unwanted seats, reconnect with a fresh subscription set
MAX_DEAD_SUBS = 16


def _log(kind: str, **kw: Any) -> None:
    print("PMUS_TWO_SIDED " + json.dumps(
        {"kind": kind, "t": datetime.now(timezone.utc).isoformat(), **kw},
        default=str, separators=(",", ":")), flush=True)


# ------------------------------------------------------------- wire format

def ws_headers(now_ms: Optional[int] = None) -> dict:
    """Handshake headers: the recorder's scheme via the order-free signer.
    Content-Type is an HTTP-body header the recorder never sends."""
    from src import pmus_auth
    h = pmus_auth.signed_headers("GET", WS_PATH, now_ms)
    h.pop("Content-Type", None)
    return h


def subscribe_frame(slugs) -> dict:
    return {"subscribe": {"request_id": f"tsws-{uuid.uuid4().hex[:10]}",
                          "subscription_type": SUB_MARKET_DATA,
                          "market_slugs": sorted(slugs)}}


def guard_outbound(frame: Mapping[str, Any]) -> Mapping[str, Any]:
    bad = set(frame) - OUTBOUND_FRAME_KEYS
    if bad:
        raise RuntimeError(f"blocked outbound frame keys: {bad}")
    return frame


def _px(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        return value.get("value") or value.get("px")
    return None if value is None else str(value)


def _top(levels: Any) -> Optional[str]:
    if isinstance(levels, list) and levels and isinstance(levels[0], Mapping):
        return _px(levels[0].get("px") or levels[0].get("price"))
    return None


def parse_tops(msg: Any, out: Optional[list] = None,
               slug_hint: str = "") -> List[Tuple[str, Optional[str],
                                                  Optional[str]]]:
    """(slug, best_bid, best_ask) for every book event in a frame. Same
    tolerant walk as recorder._walk_market_events/apply_events: explicit
    bestBid/bestAsk first, else the first level of bids / offers(asks);
    None = this frame did not carry that side."""
    if out is None:
        out = []
    if isinstance(msg, list):
        for item in msg:
            parse_tops(item, out, slug_hint)
        return out
    if not isinstance(msg, Mapping):
        return out
    slug = str(msg.get("marketSlug") or msg.get("market_slug")
               or msg.get("slug") or slug_hint or "")
    if slug and ("bids" in msg or "offers" in msg or "asks" in msg
                 or "bestBid" in msg or "best_bid" in msg
                 or "bestAsk" in msg or "best_ask" in msg):
        asks = msg.get("offers") if "offers" in msg else msg.get("asks")
        bid = _px(msg.get("bestBid") or msg.get("best_bid")) or \
            _top(msg.get("bids"))
        ask = _px(msg.get("bestAsk") or msg.get("best_ask")) or _top(asks)
        out.append((slug, bid, ask))
    for v in msg.values():
        parse_tops(v, out, slug)
    return out


def _dec(v: Optional[str]) -> Optional[D]:
    try:
        return None if v is None else D(str(v))
    except Exception:
        return None


# ------------------------------------------------------------- the feed

ChangeFn = Callable[[str, Dict[str, D], Dict[str, D], float], None]


class TwoSidedWs:
    """Thread-safe latest top of book per slug, fed by a background socket.

    `on_change(slug, prev, new, t_mono)` is called from the socket thread
    whenever a slug's best bid or ask changes; the consumer must only
    enqueue there (the bot's state stays single-threaded)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 fresh_s: Optional[float] = None,
                 log: Callable[..., None] = _log):
        self.clock = clock
        self.fresh_s = FRESH_S if fresh_s is None else fresh_s
        self.log = log
        self._lock = threading.Lock()
        self._top: Dict[str, Dict[str, Any]] = {}
        self._want: set = set()
        self.on_change: Optional[ChangeFn] = None
        self.conn_id = 0
        self.connected = False
        self.last_frame_t: Optional[float] = None
        self.reconnects = 0
        self.frames = 0
        self.updates = 0
        self.ws_hits = 0
        self.fallbacks = 0
        # REPORTING ONLY (2026-11-28 request audit): why the caller was sent
        # to the gateway. "no_book" = this socket never carried the market
        # (a candidate, a combo leg); "other_connection" = its last top came
        # on an earlier connection and the market is no longer subscribed;
        # "not_two_sided" = a one-sided or crossed top; "stale" = a
        # subscribed market on a socket that has gone quiet.
        self.fallback_why: Dict[str, int] = {}
        self.last_error: Optional[str] = None
        self._stale_logged: Dict[str, float] = {}
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------- consumer side
    def set_slugs(self, slugs) -> None:
        with self._lock:
            self._want = set(slugs)

    def wanted(self) -> set:
        with self._lock:
            return set(self._want)

    def fresh_bbo(self, slug: str, now: Optional[float] = None
                  ) -> Optional[Dict[str, D]]:
        """{'bid','ask'} when the WS view is current, else None (caller
        falls back to the gateway poll). Current = a two-sided, uncrossed
        top that either changed within fresh_s, or arrived on the socket
        that is open now and that socket delivered a frame within fresh_s
        (a book that did not change on a live stream is still the book)."""
        now = self.clock() if now is None else now
        with self._lock:
            top = self._top.get(slug)
            live = (self.connected and self.last_frame_t is not None and
                    now - self.last_frame_t <= self.fresh_s)
            ok = bool(top and top["bid"] is not None and
                      top["ask"] is not None and top["ask"] > top["bid"] and
                      (now - top["t"] <= self.fresh_s or
                       (live and top["conn"] == self.conn_id)))
            if ok:
                self.ws_hits += 1
                return {"bid": top["bid"], "ask": top["ask"]}
            self.fallbacks += 1
            why = ("no_book" if top is None else
                   "not_two_sided" if (top["bid"] is None or
                                       top["ask"] is None or
                                       top["ask"] <= top["bid"]) else
                   "other_connection" if top["conn"] != self.conn_id
                   else "stale")
            self.fallback_why[why] = self.fallback_why.get(why, 0) + 1
        if top is not None and \
                now - self._stale_logged.get(slug, -1e9) >= STALE_LOG_S:
            self._stale_logged[slug] = now
            self.log("ws_stale", slug=slug, age_s=round(now - top["t"], 2),
                     connected=self.connected, fallback="gateway_poll")
        return None

    def status(self) -> Dict[str, Any]:
        now = self.clock()
        with self._lock:
            return {"connected": self.connected, "conn_id": self.conn_id,
                    "reconnects": self.reconnects, "frames": self.frames,
                    "book_updates": self.updates,
                    "subscribed_want": len(self._want),
                    "last_frame_age_s": (None if self.last_frame_t is None
                                         else round(now - self.last_frame_t, 2)),
                    "ws_bbo_hits": self.ws_hits,
                    "gateway_fallbacks": self.fallbacks,
                    "gateway_fallbacks_by_cause": dict(self.fallback_why),
                    "last_error": self.last_error}

    # ---------------------------------------------------- feed side
    def ingest(self, msg: Any, now: Optional[float] = None) -> int:
        """Fold one received frame; fire on_change per moved slug."""
        now = self.clock() if now is None else now
        tape = getattr(self, "tape_fn", None)
        if tape is not None:
            try:
                tape(msg)                 # recording only; never raises
            except Exception:             # noqa: BLE001
                pass
        changes = []
        with self._lock:
            self.frames += 1
            self.last_frame_t = now
            for slug, b, a in parse_tops(msg):
                bid, ask = _dec(b), _dec(a)
                cur = self._top.get(slug)
                prev = None if cur is None else {"bid": cur["bid"],
                                                 "ask": cur["ask"]}
                nb = bid if bid is not None else (cur or {}).get("bid")
                na = ask if ask is not None else (cur or {}).get("ask")
                self._top[slug] = {"bid": nb, "ask": na, "t": now,
                                   "conn": self.conn_id}
                self.updates += 1
                if prev and None not in (prev["bid"], prev["ask"], nb, na) \
                        and (prev["bid"] != nb or prev["ask"] != na):
                    changes.append((slug, prev, {"bid": nb, "ask": na}))
        cb = self.on_change
        if cb is not None:
            for slug, prev, new in changes:
                try:
                    cb(slug, prev, new, now)
                except Exception as exc:                  # noqa: BLE001
                    self.log("ws_callback_error", err=str(exc)[:160])
        return len(changes)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._thread_main,
                                             name="pmus-two-sided-ws",
                                             daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop = True

    def _thread_main(self) -> None:
        import asyncio
        try:
            asyncio.run(self._run())
        except Exception as exc:                          # noqa: BLE001
            self.log("ws_thread_exit", err=f"{type(exc).__name__}:"
                                           f"{str(exc)[:160]}")

    async def _run(self) -> None:
        import asyncio
        import websockets
        backoff = 1.0
        while not self._stop:
            if not self.wanted():
                await asyncio.sleep(1.0)
                continue
            err = None
            try:
                async with websockets.connect(
                        WS_URL, additional_headers=ws_headers(),
                        max_size=8 * 1024 * 1024, open_timeout=10) as ws:
                    with self._lock:
                        self.conn_id += 1
                        self.connected = True
                        self.last_frame_t = self.clock()
                    backoff = 1.0
                    subscribed: set = set()
                    last_hb = self.clock()
                    self.log("ws_connected", conn_id=self.conn_id,
                             want=len(self.wanted()))
                    while not self._stop:
                        want = self.wanted()
                        add = want - subscribed
                        if add:
                            await ws.send(json.dumps(guard_outbound(
                                subscribe_frame(add))))
                            subscribed |= add
                        if len(subscribed - want) > MAX_DEAD_SUBS:
                            err = "resubscribe_fresh_set"
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            now = self.clock()
                            if now - last_hb >= IDLE_HEARTBEAT_S:
                                await ws.send(json.dumps(
                                    guard_outbound({"heartbeat": {}})))
                                last_hb = now
                            if self.last_frame_t is not None and \
                                    now - self.last_frame_t > SILENT_RECONNECT_S:
                                err = "silent_stream"
                                self.log("ws_stale", reason="silent_stream",
                                         silent_s=round(now - self.last_frame_t))
                                break
                            continue
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(msg, dict) and "heartbeat" in msg:
                            await ws.send(json.dumps(
                                guard_outbound({"heartbeat": {}})))
                            last_hb = self.clock()
                        if isinstance(msg, dict) and msg.get("error"):
                            self.log("ws_error_frame",
                                     frame=json.dumps(msg)[:300])
                        self.ingest(msg)
            except Exception as exc:                      # noqa: BLE001
                err = f"{type(exc).__name__}:{str(exc)[:160]}"
            finally:
                with self._lock:
                    self.connected = False
            if self._stop:
                break
            self.reconnects += 1
            self.last_error = err
            self.log("ws_reconnect", attempt=self.reconnects,
                     backoff_s=backoff, err=err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_S)
