"""Authenticated WS market state for a LIVE controller. Read-only frames.

DEFECT 1 FIX. The canary previously made OPEN/HOLD/COMPLEMENT/TAKE decisions
from `GET /v1/markets/{slug}/bbo`, which is the PUBLIC gateway's ~30s cache:
measured at 1.43-2.07 distinct book states per minute against the
authenticated WS's ~590 (analysis/V1_PUBLIC_FEED_CEILING.md). Deciding a
queue-sensitive strategy off a 30-second-old book is not the strategy.

This module folds the SAME authenticated stream the evidence recorder uses
(`wss://api.polymarket.us/v1/ws/markets`, Ed25519 `auth_headers`,
`subscribe_frames`, every outbound frame through `guard_outbound`) into a
current market state, and refuses to answer when that state is stale.

It keeps the FULL ladder, not just the touch: a hold decision depends on the
depth resting at our own price, which top-of-book cannot express.

REST keeps exactly three jobs and no strategy role: the pre-submit cross
sanity check inside `place_limit_buy`, authoritative order/status
reconciliation, and settlement/account reads.

Nothing here can place an order. It holds no mutation path at all.
"""
from __future__ import annotations

import json
import os
import threading
import time
from decimal import Decimal as D
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import pmus_ws_evidence_recorder as R

#: a book older than this is not a basis for a money decision
DEFAULT_MAX_BOOK_AGE_S = float(os.getenv("PMUS_CANARY_MAX_BOOK_AGE_S", "3.0"))


class StaleFeed(RuntimeError):
    """The WS state is too old to decide on. Never falls back to REST."""


def _px(v: Any) -> Optional[D]:
    if isinstance(v, Mapping):
        v = v.get("value")
    if v in (None, ""):
        return None
    try:
        return D(str(v))
    except Exception:                                        # noqa: BLE001
        return None


def _levels(raw: Any) -> List[Tuple[D, D]]:
    """Every (price, qty) level, best first. Empty when absent."""
    out: List[Tuple[D, D]] = []
    if not isinstance(raw, list):
        return out
    for lv in raw:
        if not isinstance(lv, Mapping):
            continue
        p = _px(lv.get("px") or lv.get("price"))
        q = _px(lv.get("qty") or lv.get("quantity"))
        if p is not None and q is not None:
            out.append((p, q))
    return out


class LiveState:
    """Current per-market state, folded from raw authenticated WS messages."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.markets: Dict[str, dict] = {}
        self.messages = 0

    def apply(self, msg: Any, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if not isinstance(msg, Mapping):
            return
        with self.lock:
            self.messages += 1
            md = msg.get("marketData")
            if isinstance(md, Mapping) and md.get("marketSlug"):
                s = self.markets.setdefault(md["marketSlug"], {})
                s["bids"] = _levels(md.get("bids"))
                s["asks"] = _levels(md.get("offers") or md.get("asks"))
                s["state"] = md.get("state")
                s["book_recv_t"] = now
                s["book_updates"] = s.get("book_updates", 0) + 1
            tr = msg.get("trade")
            if isinstance(tr, Mapping) and tr.get("marketSlug"):
                s = self.markets.setdefault(tr["marketSlug"], {})
                s["last_trade"] = {
                    "px": _px(tr.get("price")),
                    "qty": _px(tr.get("quantity")),
                    "venue_t": tr.get("tradeTime"),
                    "id": tr.get("id"),
                }
                s["trade_recv_t"] = now
                s["trades"] = s.get("trades", 0) + 1

    def snapshot(self, slug: str, *, now: Optional[float] = None,
                 max_age_s: float = DEFAULT_MAX_BOOK_AGE_S) -> dict:
        """The state a decision is allowed to use, or raise `StaleFeed`.

        Raising is the point: there is deliberately no REST fallback for a
        strategy decision, so an absent or stale feed stops the controller
        instead of quietly downgrading it to a cached book.
        """
        now = time.time() if now is None else now
        with self.lock:
            s = dict(self.markets.get(slug) or {})
        recv = s.get("book_recv_t")
        if recv is None:
            raise StaleFeed(f"no WS book received for {slug}")
        age = now - recv
        if age > max_age_s:
            raise StaleFeed(f"WS book for {slug} is {age:.2f}s old "
                            f"(max {max_age_s:.2f}s)")
        bids, asks = s.get("bids") or [], s.get("asks") or []
        lt = s.get("last_trade") or {}
        tr_recv = s.get("trade_recv_t")
        return {
            "slug": slug,
            "ws_recv_t": recv,
            "book_age_s": age,
            "bid": (bids[0][0] if bids else None),
            "bid_qty": (bids[0][1] if bids else None),
            "ask": (asks[0][0] if asks else None),
            "ask_qty": (asks[0][1] if asks else None),
            "bid_levels": len(bids),
            "ask_levels": len(asks),
            "depth": {"bids": [(str(p), str(q)) for p, q in bids[:5]],
                      "asks": [(str(p), str(q)) for p, q in asks[:5]]},
            "last_trade_px": lt.get("px"),
            "last_trade_qty": lt.get("qty"),
            "last_trade_venue_t": lt.get("venue_t"),
            "last_trade_age_s": ((now - tr_recv) if tr_recv else None),
            "book_updates": s.get("book_updates", 0),
            "trades_seen": s.get("trades", 0),
            "market_state": s.get("state"),
            "source": "AUTHENTICATED_WS",
        }

    def depth_at(self, slug: str, side: str, px: D) -> Optional[D]:
        """Displayed size at exactly `px` on `side` ('bids'/'asks')."""
        with self.lock:
            s = dict(self.markets.get(slug) or {})
        for p, q in (s.get(side) or []):
            if p == D(str(px)):
                return q
        return None


class WsFeed:
    """Background reader of the authenticated stream for ONE market.

    Uses the recorder's own auth and subscribe helpers, and passes every
    outbound frame through `guard_outbound`, so this cannot send anything
    but a read-only subscription.
    """

    def __init__(self, slug: str, state: Optional[LiveState] = None) -> None:
        self.slug = slug
        self.state = state or LiveState()
        self.thread: Optional[threading.Thread] = None
        self.stop = threading.Event()
        self.connected = False
        self.errors: List[str] = []

    def _run(self) -> None:                                  # pragma: no cover
        import asyncio

        import websockets

        async def pump() -> None:
            key = os.getenv("PMUS_API_KEY_ID", "").strip()
            sec = os.getenv("PMUS_API_SECRET_KEY", "").strip()
            if not (key and sec):
                self.errors.append("NO_CREDENTIALS")
                return
            while not self.stop.is_set():
                try:
                    headers = R.auth_headers(key, sec)
                    async with websockets.connect(
                            R.WS_URL, additional_headers=headers,
                            ping_interval=20, ping_timeout=20,
                            max_size=8 * 1024 * 1024) as ws:
                        self.connected = True
                        for frame in R.subscribe_frames([self.slug]):
                            await ws.send(json.dumps(R.guard_outbound(frame)))
                        while not self.stop.is_set():
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                            try:
                                self.state.apply(json.loads(raw))
                            except Exception:                # noqa: BLE001
                                continue
                except Exception as exc:                     # noqa: BLE001
                    self.connected = False
                    self.errors.append(str(exc)[:160])
                    if self.stop.is_set():
                        return
                    await asyncio.sleep(1.0)

        asyncio.run(pump())

    def start(self) -> "WsFeed":
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name="pmus-ws-live-state")
        self.thread.start()
        return self

    def close(self) -> None:
        self.stop.set()


def wait_ready(state: LiveState, slug: str, *, timeout_s: float = 20.0,
               max_age_s: float = DEFAULT_MAX_BOOK_AGE_S,
               sleep=time.sleep) -> dict:
    """Block until a fresh two-sided book exists, or raise `StaleFeed`."""
    until = time.time() + timeout_s
    last = "no book yet"
    while time.time() < until:
        try:
            snap = state.snapshot(slug, max_age_s=max_age_s)
            if snap["bid"] is not None and snap["ask"] is not None:
                return snap
            last = "one-sided book"
        except StaleFeed as exc:
            last = str(exc)
        sleep(0.25)
    raise StaleFeed(f"WS never became ready for {slug}: {last}")
