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

    THE FRAME SHAPES ARE THE REAL ONES. The first version of this class
    parsed a shape the venue does not send, and the consequence pointed the
    WRONG WAY: it dropped 99.3% of frames and then served a FROZEN book as
    healthy. The proven probe (deployment 90fa9013, market
    btc-updown-15m-1791372600) captured 11,523 frames, of which exactly TWO
    were book snapshots and 11,438 were `price_change` events. A
    `price_change` carries NO top-level `asset_id`, NO `bids` and NO
    `asks`: the payload is NESTED in `price_changes`, one entry PER TOKEN,
    each already carrying `best_bid` and `best_ask` directly.

      {"event_type": "price_change",
       "market": "0x436a83…c8d1",                 <- the CONDITION ID
       "price_changes": [
         {"asset_id": "3746189…1315", "best_ask": "0.68",
          "best_bid": "0.67", "price": "0.01", "side": "BUY",
          "size": "3133.01", "hash": "e5a17cfb…"},
         {"asset_id": "1880892…6534", "best_ask": "0.33",
          "best_bid": "0.32", "price": "0.99", "side": "SELL",
          "size": "3133."}]}

    So `price_change` is handled first-class, and `best_bid`/`best_ask` are
    read DIRECTLY from the entry rather than derived from ladder levels.
    The snapshot path is kept for frames that DO carry top-level
    `bids`/`asks` plus `asset_id`.

    SIZE IS NOT INVENTED. A snapshot's ladder gives the resting size at the
    best bid, so `size_at_our_price` / `size_ahead_of_us` are real there. A
    `price_change` entry's `size` is the size at the CHANGED price, which
    is usually not the best bid -- in the frame above the changed prices
    are 0.01 and 0.99 while the bests are 0.67 and 0.32. Using it as
    queue depth would be a fabrication, so it is used ONLY when the changed
    price IS the best bid, and otherwise the size is reported as unknown
    with its reason.

    THE MARKET IS VERIFIED. A frame whose top-level `market` is not the
    pinned condition id is REFUSED, so a stray subscription cannot update
    our cache.
    """

    def __init__(self, pin: Dict[str, Any], root: str) -> None:
        self.pin = dict(pin)
        self.market = str(pin["slug"])
        #: the venue's own identifier on the wire is the CONDITION ID, not
        #: the slug. Frames are matched against this.
        self.condition_id = str(pin.get("condition_id")
                                or pin.get("market_id") or "")
        self.tokens = {str(pin["token_up"]): "UP",
                       str(pin["token_down"]): "DOWN"}
        self.root = root
        self.top: Dict[str, Dict[str, Any]] = {}
        #: when each token's TOP OF BOOK was last updated. Distinct from
        #: `last_seen`: a frame that arrives and is refused, or that
        #: carries no usable top of book, does NOT advance this.
        self.last_update: Dict[str, float] = {}
        self.last_seen: Dict[str, float] = {}
        self.frames = 0
        self.frames_applied = 0
        self.frames_refused_other_market = 0
        self.frames_unrecognised = 0
        self.ws_url = WS.DEFAULT_WS_URL
        self._collector: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------- ingest
    def market_matches(self, item: Dict[str, Any]) -> bool:
        """A frame must name the PINNED market, or it is refused.

        Matched on the condition id, which is what the wire carries. A
        frame with no market field at all is accepted only because the
        per-entry `asset_id` is then checked against this window's two
        token ids, which is itself an identity check.
        """
        got = item.get("market") or item.get("condition_id")
        if got is None:
            return True
        if not self.condition_id:
            return True
        return str(got) == self.condition_id

    @staticmethod
    def _num(v: Any) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _set_top(self, tok: str, *, best_bid: Any, best_ask: Any,
                 size_at_best_bid: Optional[Any], source: str,
                 size_reason: Optional[str] = None) -> None:
        cur = dict(self.top.get(tok) or {})
        bb, ba = self._num(best_bid), self._num(best_ask)
        if bb is None and ba is None:
            return
        if bb is not None:
            cur["best_bid"] = "%.2f" % bb
        if ba is not None:
            cur["best_ask"] = "%.2f" % ba
        if size_at_best_bid is not None:
            cur["size_at_our_price"] = str(size_at_best_bid)
            cur["size_ahead_of_us"] = str(size_at_best_bid)
            cur["size_source"] = source
        else:
            # NOT invented. The field says it is unknown and why.
            cur["size_at_our_price"] = None
            cur["size_ahead_of_us"] = None
            cur["size_source"] = source
            cur["size_unknown_reason"] = size_reason or (
                "this frame carries no ladder at the best bid")
        cur["token_id"] = tok
        cur["outcome"] = self.tokens.get(tok)
        cur["frame_source"] = source
        cur["book_unix"] = time.time()
        cur["recv_monotonic_ns"] = time.monotonic_ns()
        self.top[tok] = cur
        self.last_update[tok] = cur["book_unix"]
        self.frames_applied += 1

    def _best_level(self, levels: Any, reverse: bool
                    ) -> Optional[Dict[str, float]]:
        rows = []
        for lv in (levels if isinstance(levels, list) else []):
            if not isinstance(lv, dict):
                continue
            px, sz = self._num(lv.get("price")), self._num(lv.get("size"))
            if px is None:
                continue
            rows.append((px, sz or 0.0))
        if not rows:
            return None
        rows.sort(key=lambda r: r[0], reverse=reverse)
        return {"price": rows[0][0], "size": rows[0][1]}

    def observe(self, item: Any) -> None:
        """One websocket message -> the newest top of book for its token.

        Two frame shapes, both real. Anything else is counted as
        unrecognised rather than guessed at.
        """
        if not isinstance(item, dict):
            return
        self.frames += 1
        if not self.market_matches(item):
            self.frames_refused_other_market += 1
            return

        # ---- shape 1: price_change. 11,438 of 11,523 real frames.
        changes = item.get("price_changes")
        if isinstance(changes, list) and changes:
            for entry in changes:
                if not isinstance(entry, dict):
                    continue
                tok = str(entry.get("asset_id")
                          or entry.get("token_id") or "")
                if tok not in self.tokens:
                    continue
                self.last_seen[tok] = time.time()
                bb = entry.get("best_bid")
                ba = entry.get("best_ask")
                # the entry's `size` is the size at the CHANGED price, and
                # that is usually not the best bid. Use it only when the
                # changed price IS the best bid.
                changed_px = self._num(entry.get("price"))
                best_bid_px = self._num(bb)
                size = None
                reason = None
                if (changed_px is not None and best_bid_px is not None
                        and abs(changed_px - best_bid_px) < 1e-9
                        and str(entry.get("side", "")).upper() == "BUY"):
                    size = entry.get("size")
                else:
                    reason = ("a price_change carries size only at the "
                              "CHANGED price (%s), not at the best bid "
                              "(%s)" % (entry.get("price"), bb))
                self._set_top(tok, best_bid=bb, best_ask=ba,
                              size_at_best_bid=size, source="price_change",
                              size_reason=reason)
            return

        # ---- shape 2: a book snapshot. Top-level asset_id plus ladders.
        tok = str(item.get("asset_id") or item.get("token_id") or "")
        if tok in self.tokens:
            self.last_seen[tok] = time.time()
            bids = item.get("bids") or item.get("buys")
            asks = item.get("asks") or item.get("sells")
            bb = self._best_level(bids, True)
            ba = self._best_level(asks, False)
            if bb is None and ba is None:
                self.frames_unrecognised += 1
                return
            self._set_top(
                tok,
                best_bid=(bb or {}).get("price"),
                best_ask=(ba or {}).get("price"),
                size_at_best_bid=(bb or {}).get("size"),
                source="book_snapshot")
            return
        self.frames_unrecognised += 1

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
        """Health is about the TOP OF BOOK being FRESH, not about frames
        arriving.

        STALENESS MUST NOT DEPEND ON SILENCE ALONE. The first version
        reported healthy whenever frames were seen, so a feed that updated
        ONE token while the other's top of book sat frozen read as healthy
        and a maker price was chosen from a stale book. So the reported
        `tokens_last_seen_unix` -- which the provider's silence bound reads
        -- is the time each token's TOP OF BOOK was last UPDATED, and a
        token whose book has not moved goes stale on its own schedule even
        while the other token is busy.
        """
        up, down = str(self.pin["token_up"]), str(self.pin["token_down"])
        fresh = {t: self.last_update.get(t) for t in (up, down)}
        return {"market": self.market,
                "condition_id": self.condition_id,
                "timing_authority": WS.TIMING_WEBSOCKET,
                "both_tokens_streaming": (fresh[up] is not None
                                          and fresh[down] is not None),
                # the provider bounds staleness on THIS, and it is the
                # last TOP-OF-BOOK UPDATE, not the last frame seen
                "tokens_last_seen_unix": {t: v for t, v in fresh.items()
                                          if v is not None},
                "tokens_last_book_update_unix": dict(self.last_update),
                "tokens_last_frame_unix": dict(self.last_seen),
                "staleness_is_per_token_book_update": True,
                "staleness_depends_on_silence_alone": False,
                "frames": self.frames,
                "frames_applied": self.frames_applied,
                "frames_refused_other_market":
                    self.frames_refused_other_market,
                "frames_unrecognised": self.frames_unrecognised,
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
