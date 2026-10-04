"""Authenticated WS -> executor pricing boundary. READ-ONLY.

This module changes ONE thing in the execution path: where the executor
reads market state. It does not touch strategy, signing, submission, REST
reconciliation, risk controls, authorization or deadman behaviour, and it
contains no order path of any kind.

It does not reimplement the socket. Every primitive is IMPORTED from the
recorder that has been running this stream in production for weeks --
`apply_events` (book/trade folding), `auth_headers` (the Ed25519 scheme),
`subscribe_frames`, `guard_outbound` (the read-only frame allowlist) and
`WS_URL`. A second parser would be a second thing to be wrong.

THE RULE THIS EXISTS TO ENFORCE: during an armed run there is **no
public-gateway pricing fallback**. Missing, stale or one-sided WS state
raises, and the executor's caller turns that into "no new order". The
gateway reader still exists for unarmed diagnostics and is unreachable
from an armed path.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, Mapping, Optional

from src.pmus_ws_evidence_recorder import (            # noqa: F401
    WS_URL, WS_PATH, apply_events, auth_headers, guard_outbound,
    has_credentials, subscribe_frames,
)

#: a quote older than this is not a quote. Default 2.0 s: the measured
#: median book inter-arrival on this stream is 0.101 s, so 2 s is ~20
#: updates of slack before the executor is told to stand down.
MAX_AGE_ENV = "PMUS_WS_BBO_MAX_AGE_S"
DEFAULT_MAX_AGE_S = 2.0

#: set to the slug set the executor may price. Empty disables the feed, so
#: the adapter is opt-in and the default build behaves exactly as before.
ENABLE_ENV = "PMUS_WS_BBO_SLUGS"


class StaleWsState(RuntimeError):
    """No usable authenticated quote. NEVER caught to fall back to HTTP."""


def max_age_s() -> float:
    try:
        v = float(os.getenv(MAX_AGE_ENV, "").strip() or DEFAULT_MAX_AGE_S)
    except ValueError:
        return DEFAULT_MAX_AGE_S
    return v if v > 0 else DEFAULT_MAX_AGE_S


def enabled_slugs() -> list:
    raw = os.getenv(ENABLE_ENV, "").strip()
    return [s for s in (x.strip() for x in raw.split(",")) if s]


def normalize(state: Mapping[str, Any], slug: str, now: float,
              max_age: Optional[float] = None) -> Dict[str, Any]:
    """Recorder state -> the executor's BBO shape, or raise.

    The returned dict is exactly what the gateway reader returned --
    `{"bid": str|None, "ask": str|None}` -- plus provenance, so the call
    site downstream is unchanged. Prices stay in the venue's LONG-SIDE
    terms, which is what `l5_order_body.venue_price` expects; this adapter
    performs no outcome inversion of its own.
    """
    s = state.get(slug)
    if not s:
        raise StaleWsState("NO_WS_STATE: %s" % slug)
    bid, ask = s.get("bid"), s.get("ask")
    if bid is None or ask is None:
        raise StaleWsState("ONE_SIDED_WS_BOOK: %s bid=%r ask=%r"
                           % (slug, bid, ask))
    if not s.get("both_sides_served"):
        raise StaleWsState("WS_DEPTH_NEVER_SERVED_BOTH_SIDES: %s" % slug)
    t = s.get("last_t")
    if t is None:
        raise StaleWsState("NO_WS_TIMESTAMP: %s" % slug)
    age = now - float(t)
    lim = max_age_s() if max_age is None else max_age
    if age > lim:
        raise StaleWsState("STALE_WS_BOOK: %s age=%.3fs > %.3fs"
                           % (slug, age, lim))
    return {"bid": str(bid), "ask": str(ask),
            "source": "authenticated_ws", "age_s": round(age, 4),
            "book_updates": s.get("book_updates")}


class WsBboFeed:
    """Background authenticated subscription. Read-only by construction."""

    def __init__(self, slugs, max_age: Optional[float] = None):
        self.slugs = list(slugs)
        self.max_age = max_age
        self.state: Dict[str, Any] = {}
        self._stop = threading.Event()
        self._thr: Optional[threading.Thread] = None
        self.connected = False
        self.frames = 0
        self.last_error: Optional[str] = None

    # -- the only write this class performs is to its own dict
    def _on_message(self, msg: Any) -> None:
        now = time.time()
        apply_events(self.state, msg, now)
        for s in self.state.values():
            if isinstance(s, dict) and s.get("book_updates"):
                s.setdefault("last_t", now)
        for slug in self.slugs:
            s = self.state.get(slug)
            if isinstance(s, dict):
                s["last_t"] = now
        self.frames += 1

    def _run(self) -> None:
        import json
        import websockets.sync.client as wsc
        while not self._stop.is_set():
            try:
                headers = auth_headers(os.environ["PMUS_API_KEY_ID"].strip(),
                                       os.environ["PMUS_API_SECRET_KEY"].strip())
                with wsc.connect(WS_URL, additional_headers=headers,
                                 open_timeout=20, close_timeout=5) as ws:
                    for fr in subscribe_frames(self.slugs):
                        ws.send(json.dumps(guard_outbound(fr)))
                    self.connected = True
                    while not self._stop.is_set():
                        self._on_message(json.loads(ws.recv(timeout=30)))
            except Exception as e:                            # noqa: BLE001
                self.connected = False
                self.last_error = "%s: %s" % (type(e).__name__, e)
                time.sleep(2)

    def start(self) -> "WsBboFeed":
        if not has_credentials():
            raise StaleWsState("NO_CREDENTIALS: the authenticated stream "
                               "cannot be opened without PMUS_API_KEY_ID / "
                               "PMUS_API_SECRET_KEY")
        self._thr = threading.Thread(target=self._run, daemon=True)
        self._thr.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def bbo(self, slug: str, now: Optional[float] = None) -> Dict[str, Any]:
        """The executor's decision input. Raises rather than degrading."""
        return normalize(self.state, slug,
                         time.time() if now is None else now, self.max_age)
