"""The live two-token book, from the PROVEN websocket. No second stack.

`src/l5_clob_ws.py` is the proven independent two-token collector --
live-verified, both tokens streaming, 11,438 and 11,433 frames on
`wss://ws-subscriptions-clob.polymarket.com/ws/market`. It is the TIMING
AUTHORITY for this cohort and it is reused as-is: this module starts one
collector per pinned market, keeps the latest top-of-book per token, and
answers two questions the provider asks. It parses no new feed, opens no
REST book, and derives no complement.

TWO TOKENS, INDEPENDENTLY. `health_fn` reports the last time EACH token id
was seen, so "both tokens streaming" is a fact about two token ids and not
an inference from one. A price for DOWN is never computed as 1 - UP: the
provider refuses a snapshot whose health claims a derived complement.

NOTHING HERE ORDERS. This module has no credentials, no POST and no import
of any order path.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from src import l5_clob_ws as WS

_LOCK = threading.Lock()
_FEEDS: Dict[str, "TwoTokenFeed"] = {}


class TwoTokenFeed:
    """One collector for one pinned market, holding the latest top of book.

    Deliberately small: the collector already does the hard part (framing,
    subscription proof, strict receive timestamps, reconnects). This keeps
    the newest bid/ask per token and the time each token was last seen.
    """

    def __init__(self, pin: Dict[str, Any], root: str) -> None:
        self.pin = dict(pin)
        self.market = str(pin["slug"])
        self.tokens = {str(pin["token_up"]): "UP",
                       str(pin["token_down"]): "DOWN"}
        self.root = root
        self.top: Dict[str, Dict[str, Any]] = {}
        self.last_seen: Dict[str, float] = {}
        self.frames = 0
        self.ws_url = WS.DEFAULT_WS_URL
        self._collector: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- ingest
    def observe(self, item: Any) -> None:
        """One websocket message -> the newest top of book for its token."""
        if not isinstance(item, dict):
            return
        tok = str(item.get("asset_id") or item.get("token_id") or "")
        if tok not in self.tokens:
            return
        bids = item.get("bids") or item.get("buys") or []
        asks = item.get("asks") or item.get("sells") or []

        def _best(levels: Any, reverse: bool) -> Optional[Dict[str, Any]]:
            rows = []
            for lv in (levels if isinstance(levels, list) else []):
                if not isinstance(lv, dict):
                    continue
                try:
                    rows.append((float(lv.get("price")),
                                 float(lv.get("size") or 0)))
                except (TypeError, ValueError):
                    continue
            if not rows:
                return None
            rows.sort(key=lambda r: r[0], reverse=reverse)
            return {"price": rows[0][0], "size": rows[0][1],
                    "levels": len(rows)}
        bb, ba = _best(bids, True), _best(asks, False)
        cur = dict(self.top.get(tok) or {})
        if bb is not None:
            cur["best_bid"] = "%.2f" % bb["price"]
            cur["size_at_our_price"] = str(bb["size"])
            cur["size_ahead_of_us"] = str(bb["size"])
        if ba is not None:
            cur["best_ask"] = "%.2f" % ba["price"]
        cur["token_id"] = tok
        cur["book_unix"] = time.time()
        cur["recv_monotonic_ns"] = time.monotonic_ns()
        self.top[tok] = cur
        self.last_seen[tok] = time.time()
        self.frames += 1

    # -------------------------------------------------------------- start
    def start(self, *, collector_factory: Any = None) -> None:
        if self._thread is not None:
            return
        make = collector_factory or (
            lambda: WS.ClobWsCollector(self.root, market=self.market,
                                       tokens=self.tokens))
        self._collector = make()
        feed = self

        original_record = getattr(self._collector, "record", None)

        def record(item: Any, nbytes: Any = 0) -> Any:
            feed.observe(item)
            if original_record is not None:
                return original_record(item, nbytes)
            return None

        try:
            self._collector.record = record            # type: ignore
        except Exception:                              # noqa: BLE001
            pass

        def run() -> None:
            while not self._stop.is_set():
                try:
                    self._collector.session(
                        deadline=time.monotonic() + 60.0)
                except Exception:                      # noqa: BLE001
                    time.sleep(1.0)

        self._thread = threading.Thread(target=run, daemon=True,
                                        name="e1-two-token-feed")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ answers
    def book(self) -> Dict[str, Any]:
        up = dict(self.top.get(str(self.pin["token_up"])) or {})
        down = dict(self.top.get(str(self.pin["token_down"])) or {})
        newest = max([v.get("book_unix", 0) for v in (up, down)] or [0])
        return {"market": self.market, "up": up, "down": down,
                "book_unix": newest or None,
                "recv_monotonic_ns": max(
                    [v.get("recv_monotonic_ns", 0)
                     for v in (up, down)] or [0]) or None,
                "timing_authority": WS.TIMING_WEBSOCKET,
                "derived_complement": False}

    def health(self) -> Dict[str, Any]:
        return {"market": self.market,
                "timing_authority": WS.TIMING_WEBSOCKET,
                "both_tokens_streaming": (
                    str(self.pin["token_up"]) in self.last_seen
                    and str(self.pin["token_down"]) in self.last_seen),
                "tokens_last_seen_unix": dict(self.last_seen),
                "frames": self.frames,
                "ws_url": self.ws_url,
                "derived_complement": False,
                "rest_role": WS.TIMING_REST}


def feed_for(pin: Dict[str, Any], *, root: str = "/state/pmus-ws/e1_cohort",
             collector_factory: Any = None) -> TwoTokenFeed:
    """One feed per pinned market, started on first use."""
    key = str(pin["slug"])
    with _LOCK:
        f = _FEEDS.get(key)
        if f is None:
            f = TwoTokenFeed(pin, root)
            _FEEDS[key] = f
            f.start(collector_factory=collector_factory)
        return f


def book_fn(pin: Dict[str, Any]) -> Dict[str, Any]:
    return feed_for(pin).book()


def health_fn(pin: Dict[str, Any]) -> Dict[str, Any]:
    return feed_for(pin).health()
