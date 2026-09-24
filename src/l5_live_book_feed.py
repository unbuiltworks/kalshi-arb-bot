"""The authenticated PMUS book, for a process that is not the recorder.

The one-shot live probe runs in its own service, so it cannot see the
recorder's in-process websocket state. Its first version read the book over
REST instead, and that was the wrong source: `PmusTransport.book()` calls
`/v1/markets/{slug}/book` on `pmus_auth.GATEWAY`, and `ReadOnlyTransport`
signs a request only when `base == API` -- so those reads went out
unauthenticated, to the public gateway that has separately been shown not to
serve this series reliably. A decision taken against that book would be
taken against a weaker and staler view than the one every gate was validated
on.

So this subscribes to the same authenticated stream the recorder uses, for
ONE market, in the probe's own process. Nothing here re-implements the feed:

    WS_URL, WS_PATH   from pmus_ws_evidence_recorder
    auth_headers      its Ed25519 signature over `{ts}GET{path}`
    subscribe_frames  its subscribe frame builder
    guard_outbound    its outbound frame guard, unchanged
    apply_events      its event fold

What is added is the smallest consumer that did not already exist: a reader
for one market that keeps a short history of book states with the time each
arrived, because the decision path matches an action to the book at or
before it and bounds that at 2.0s. A single "latest" value cannot answer
that question.

This subscribes and heartbeats. It sends nothing else, and every frame it
does send passes through the recorder's own outbound guard.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

#: how many book states to keep. The decision path looks backwards a few
#: seconds at most; this is generous against a busy market.
HISTORY = 2000

#: a book older than this is not usable for a decision. Pinned by test to
#: the frozen rule's own bound so the two cannot drift apart.
MAX_AGE_S = 2.0


def _row(slug: str, s: Dict[str, Any], now: float) -> Dict[str, Any]:
    bid, ask = s.get("bid"), s.get("ask")
    return {"t": now, "bid": bid, "ask": ask,
            "bid_qty": s.get("bid_qty"), "ask_qty": s.get("ask_qty"),
            "status": ("OK" if bid is not None and ask is not None
                       else "NO_BOOK" if bid is None and ask is None
                       else "ONE_SIDED")}


class AuthenticatedBookFeed:
    """One market, one authenticated subscription, read-only."""

    def __init__(self, market: str, *, clock=time.time) -> None:
        self.market = market
        self.clock = clock
        self.state: Dict[str, Any] = {}
        self.history: Deque[Dict[str, Any]] = deque(maxlen=HISTORY)
        self.frames = 0
        self.errors: List[str] = []
        self.connected = False
        self.started_at: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- reading

    def books(self) -> List[Dict[str, Any]]:
        """Book states oldest first, in the shape the wiring expects."""
        with self._lock:
            return list(self.history)

    def latest(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self.history[-1]) if self.history else None

    def age_s(self) -> Optional[float]:
        last = self.latest()
        return None if last is None else self.clock() - last["t"]

    def fresh(self) -> bool:
        age = self.age_s()
        return age is not None and age <= MAX_AGE_S

    def health(self) -> Dict[str, Any]:
        return {"market": self.market, "connected": self.connected,
                "frames": self.frames, "book_states": len(self.history),
                "last_book_age_s": (None if self.age_s() is None
                                    else round(self.age_s(), 3)),
                "fresh": self.fresh(), "errors": self.errors[-3:],
                "source": "AUTHENTICATED_WS"}

    # ------------------------------------------------------------ the feed

    def ingest(self, msg: Any) -> None:
        """Fold one frame, using the recorder's own event fold."""
        from src import pmus_ws_evidence_recorder as R
        now = self.clock()
        before = dict(self.state.get(self.market) or {})
        R.apply_events(self.state, msg, now)
        after = self.state.get(self.market)
        if after is None:
            return
        self.frames += 1
        if (after.get("bid"), after.get("ask"),
                after.get("bid_qty"), after.get("ask_qty")) == (
                before.get("bid"), before.get("ask"),
                before.get("bid_qty"), before.get("ask_qty")) and before:
            return                      # nothing about the book changed
        with self._lock:
            self.history.append(_row(self.market, after, now))

    def start(self) -> None:
        self.started_at = self.clock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="l5-book-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def wait_ready(self, timeout_s: float = 60.0,
                   sleep=time.sleep) -> bool:
        """Block until a first book arrives, or give up and say so."""
        until = self.clock() + timeout_s
        while self.clock() < until:
            if self.latest() is not None:
                return True
            if self._stop.is_set():
                break
            sleep(0.25)
        return self.latest() is not None

    def _run(self) -> None:                                # pragma: no cover
        import asyncio
        try:
            asyncio.run(self._session())
        except BaseException as exc:                       # noqa: BLE001
            self.errors.append("%s: %s" % (type(exc).__name__, str(exc)[:160]))
            self.connected = False

    async def _session(self) -> None:                      # pragma: no cover
        import websockets

        from src import pmus_ws_evidence_recorder as R
        headers = R.auth_headers(os.environ[R.KEY_ENV].strip(),
                                 os.environ[R.SECRET_ENV].strip())
        async with websockets.connect(R.WS_URL, additional_headers=headers,
                                      max_size=8 * 1024 * 1024) as ws:
            self.connected = True
            for frame in R.subscribe_frames([self.market]):
                await ws.send(json.dumps(R.guard_outbound(frame)))
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                try:
                    self.ingest(json.loads(raw))
                except ValueError:
                    continue
        self.connected = False
