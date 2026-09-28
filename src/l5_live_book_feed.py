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

import asyncio
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
#:
#: NOT WEAKENED BY RECONNECT. While the feed is down no row is appended, so
#: `age_s()` keeps growing and every freshness gate refuses. Reconnecting
#: restores a real book or it restores nothing; it never manufactures one.
MAX_AGE_S = 2.0

#: RUNG A 2026-09-28 1600z: the venue sent `1001 (going away) server shutting
#: down` mid-window. `_run` ran exactly one session, so the close ended the
#: feed thread, the book went 212.7s stale and the freshness gate refused all
#: 38 remaining actions. The gate was right; the feed was wrong.
#:
#: Backoff for one reconnect attempt, by consecutive failure count. The window
#: is 900s and a stale book refuses everything, so recovery is fast and the
#: tail is short.
RECONNECT_BACKOFF_S = (0.5, 1.0, 2.0, 4.0, 5.0)

#: at the 5s tail this is ~5 minutes of trying inside a 15-minute window.
MAX_RECONNECTS = 60


def _row(slug: str, s: Dict[str, Any], now: float) -> Dict[str, Any]:
    """One book state, in the shape the decision path reads.

    The shape and its status vocabulary are `l5_window_actions.book_row`,
    the same builder the continuous recorder's `live_books` uses, so the two
    feeds cannot drift into disagreeing about what "usable" means.
    """
    from src import l5_window_actions as WA
    return WA.book_row(t=now, bid=s.get("bid"), ask=s.get("ask"),
                       bid_qty=s.get("bid_qty"), ask_qty=s.get("ask_qty"))


class AuthenticatedBookFeed:
    """One market, one authenticated subscription, read-only."""

    def __init__(self, market: str, *, clock=time.time,
                 max_reconnects: int = MAX_RECONNECTS,
                 deadline: Optional[float] = None,
                 backoff_s: Any = RECONNECT_BACKOFF_S) -> None:
        self.market = market
        self.clock = clock
        #: reconnect policy. Injectable so a test can drive the supervisor
        #: without waiting out a real backoff.
        self.max_reconnects = int(max_reconnects)
        self.backoff_s = tuple(backoff_s) or (0.0,)
        #: stop reconnecting once the window this feed serves is over.
        self.deadline = deadline
        #: how many sessions were started, and how many were retries.
        self.sessions = 0
        self.reconnects = 0
        self.last_error: Optional[str] = None
        self.state: Dict[str, Any] = {}
        self.history: Deque[Dict[str, Any]] = deque(maxlen=HISTORY)
        self.frames = 0
        self.errors: List[str] = []
        self.connected = False
        #: why the session is no longer running, so an early stop can be
        #: told apart from a healthy one without reading the volume.
        self.stop_reason = "not_started"
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
                "stop_reason": self.stop_reason,
                "sessions": self.sessions, "reconnects": self.reconnects,
                "last_error": self.last_error,
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
        self.stop_reason = "running"
        self._thread = threading.Thread(target=self._supervise, daemon=True,
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

    def past_deadline(self) -> bool:
        return self.deadline is not None and self.clock() >= self.deadline

    def _backoff(self, attempt: int) -> float:
        return self.backoff_s[min(attempt, len(self.backoff_s) - 1)]

    def _supervise(self) -> None:
        """Run sessions until told to stop. ONE session is never enough.

        `_run` is one attempt and is left exactly as it was; recovery lives
        here, above it, so a venue-side close is a reconnect rather than the
        end of the feed.

        What this deliberately does NOT do: append a row, extend a timestamp,
        or touch `fresh()`. A gap in the book stays a gap, so every freshness
        gate keeps refusing until a REAL frame arrives from a live session.
        `stop_reason` reads `reconnecting:...` while the gap is open, so a
        refusal during recovery can be told apart from a dead feed.
        """
        attempt = 0
        while not self._stop.is_set():
            if self.past_deadline():
                self.stop_reason = "deadline"
                return
            delivered_before = self.frames
            self.sessions += 1
            self._run()                       # one attempt; never raises
            self.connected = False
            if self._stop.is_set():
                self.stop_reason = "local_cancellation"
                return
            if self.past_deadline():
                self.stop_reason = "deadline"
                return
            # a session that actually delivered frames earns a fresh budget:
            # a long healthy stream interrupted once is not a flapping feed.
            if self.frames > delivered_before:
                attempt = 0
            if self.reconnects >= self.max_reconnects:
                self.stop_reason = "reconnects_exhausted"
                return
            self.reconnects += 1
            self.stop_reason = "reconnecting:%s" % (
                self.last_error or self.stop_reason or "remote_close")
            if self._stop.wait(self._backoff(attempt)):
                self.stop_reason = "local_cancellation"
                return
            attempt += 1
        self.stop_reason = "local_cancellation"

    def _run(self) -> None:                                # pragma: no cover
        try:
            asyncio.run(self._session())
        except BaseException as exc:                       # noqa: BLE001
            self.errors.append("%s: %s" % (type(exc).__name__, str(exc)[:160]))
            self.last_error = self.errors[-1]
            self.stop_reason = "exception:%s" % type(exc).__name__
            self.connected = False
        else:
            # the loop ended without raising: either we asked it to stop, or
            # the far end went away and recv stopped yielding.
            self.stop_reason = ("local_cancellation" if self._stop.is_set()
                                else "remote_close")

    async def _session(self) -> None:
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
