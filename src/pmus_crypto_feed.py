"""Background reader for the crypto reward lane's public market data.

Owner 2026-11-22: "A crypto gateway call that blocks the sports controller
for up to 10 seconds is unacceptable." The crypto lane lives in the sports
engine's loop, so NO crypto gateway read may run on that loop.

This feed is the only place the lane reads the public gateway. It runs in
its own daemon thread and keeps the latest answer of each read with the
time it was taken. The engine's loop only looks at that cache -- a lookup
under a lock, never a request -- and treats an old or missing entry as
"unknown" (opening quotes are pulled, nothing is placed). A slow, hung or
failing gateway therefore costs the crypto lane its data and costs the
sports loop nothing.

Reads (all public, unsigned, GET):
  events list + incentive programmes   every DISCOVER_S
  market spec (open / tick / min qty)  every SPEC_S per watched market
  depth                                every BOOK_S per watched market

No orders, no credentials, no signed transport. Nothing here can place or
cancel.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

from src import pmus_crypto_rewards as CR

UNKNOWN = object()          # never read successfully (distinct from closed)
SPEC_S = 60.0
REQUEST_GAP_S = 0.25        # <= 4 requests a second from this thread


class CryptoFeed:
    def __init__(self, get: Callable[[str], Any],
                 spec_fn: Callable[[str], Any],
                 log: Optional[Callable[..., None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._get, self._spec_fn = get, spec_fn
        self._log = log or (lambda *a, **k: None)
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._watch: List[str] = []
        self._book: Dict[str, tuple] = {}       # slug -> (t, marketData)
        self._spec: Dict[str, tuple] = {}       # slug -> (t, spec | None)
        self._targets: Optional[tuple] = None   # (t, [targets])
        self._progs: Optional[tuple] = None     # (t, {slug: period})
        self._thread: Optional[threading.Thread] = None
        self.stop = False
        self.errors = 0
        self.requests = 0
        self.cycles = 0
        self.last_cycle_s = 0.0
        self.last_error: Optional[str] = None
        self.restarts = 0
        self._disc_at = -1e18

    # ------------------------------------------------ engine side (no I/O)
    def watch(self, slugs) -> None:
        with self._lock:
            self._watch = sorted({str(s) for s in slugs})

    def _age(self, t: Optional[float]) -> float:
        return float("inf") if t is None else self._clock() - t

    def book(self, slug: str) -> tuple:
        """(marketData | None, age seconds)."""
        with self._lock:
            hit = self._book.get(slug)
        return (None, float("inf")) if hit is None else \
            (hit[1], self._age(hit[0]))

    def spec(self, slug: str) -> tuple:
        """(spec | None when the venue says closed | UNKNOWN, age)."""
        with self._lock:
            hit = self._spec.get(slug)
        return (UNKNOWN, float("inf")) if hit is None else \
            (hit[1], self._age(hit[0]))

    def targets(self) -> tuple:
        with self._lock:
            hit = self._targets
        return ([], float("inf")) if hit is None else \
            (list(hit[1]), self._age(hit[0]))

    def programs(self) -> tuple:
        with self._lock:
            hit = self._progs
        return ({}, float("inf")) if hit is None else \
            (dict(hit[1]), self._age(hit[0]))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            ages = {s: round(self._age(v[0]), 1)
                    for s, v in self._book.items() if s in self._watch}
            disc = None if self._targets is None else \
                round(self._age(self._targets[0]), 1)
        return {"alive": bool(self._thread and self._thread.is_alive()),
                "restarts": self.restarts,
                "cycles": self.cycles, "requests": self.requests,
                "errors": self.errors, "last_error": self.last_error,
                "last_cycle_s": round(self.last_cycle_s, 2),
                "book_age_s": ages, "discovery_age_s": disc}

    # ------------------------------------------------ feed thread (I/O)
    def _call(self, fn: Callable, *a: Any) -> Any:
        self.requests += 1
        try:
            return fn(*a)
        finally:
            self._sleep(REQUEST_GAP_S)

    def _fail(self, what: str, exc: BaseException) -> None:
        self.errors += 1
        self.last_error = "%s:%s:%s" % (what, type(exc).__name__,
                                        str(exc)[:80])

    def _discover(self) -> None:
        from src import pmus_sports_rewards as R
        d = self._call(self._get, "/v1/events?active=true&closed=false"
                       "&limit=200&categories=crypto") or {}
        evs = d if isinstance(d, list) else d.get("events") or []
        tg = []
        for ev in evs:
            for m in (ev.get("markets") or []) \
                    if isinstance(ev, Mapping) else []:
                slug = str((m or {}).get("slug") or "")
                if CR.tradable(slug):
                    tg.append({"slug": slug, "end": m.get("endDate")
                               or ev.get("endDate")})
        now = self._clock()
        with self._lock:
            self._targets = (now, tg)
            ask = sorted({t["slug"] for t in tg} | set(self._watch))
        if not ask:
            with self._lock:
                self._progs = (now, {})
            return
        found, diag = R.fetch_programs(
            lambda p: self._call(self._get, p), ask)
        if diag["errors"]:
            raise RuntimeError(str(diag["errors"][:1]))
        out: Dict[str, Any] = {}
        for s, prog in R.build_program_map(found).items():
            per = R.paying_period(prog, time.time(), event_live=True)
            if per and per.get("period") in ("daily_event", "daily"):
                per["M_program"] = max(int(per.get("M_program") or 1),
                                       len(CR.ASSETS))
                out[s] = per
        with self._lock:
            self._progs = (self._clock(), out)

    def poll_once(self, force: bool = False) -> None:
        """One pass over everything that is due. Each read fails on its
        own: the old answer stays and simply grows older."""
        t0 = self._clock()
        if force or t0 - self._disc_at >= CR.DISCOVER_S:
            self._disc_at = t0
            try:
                self._discover()
            except Exception as exc:                      # noqa: BLE001
                self._fail("discover", exc)
        with self._lock:
            watch = list(self._watch)
            tg = [t["slug"] for t in (self._targets or (0, []))[1]]
        for slug in sorted(set(watch) | set(tg)):
            if self.stop:
                return
            if force or self._age((self._spec.get(slug) or (None,))[0]) \
                    >= SPEC_S:
                try:
                    sp = self._call(self._spec_fn, slug)
                    with self._lock:
                        self._spec[slug] = (self._clock(), sp)
                except Exception as exc:                  # noqa: BLE001
                    self._fail("spec", exc)
        for slug in watch:
            if self.stop:
                return
            if force or self._age((self._book.get(slug) or (None,))[0]) \
                    >= CR.BOOK_S:
                try:
                    md = (self._call(self._get,
                                     "/v1/markets/%s/book" % slug) or {}
                          ).get("marketData")
                    if isinstance(md, Mapping):
                        with self._lock:
                            self._book[slug] = (self._clock(), md)
                except Exception as exc:                  # noqa: BLE001
                    self._fail("book", exc)
        with self._lock:
            for d in (self._book, self._spec):
                for s in [s for s in d if s not in watch and s not in tg]:
                    d.pop(s, None)
        self.cycles += 1
        self.last_cycle_s = self._clock() - t0

    def _run(self) -> None:
        while not self.stop:
            try:
                self.poll_once()
            except Exception as exc:                      # noqa: BLE001
                self._fail("cycle", exc)
            self._sleep(1.0)

    def fresh(self, slug: str) -> Optional[str]:
        """Why this market's data cannot support an order, or None: an
        open-market spec and two-sided depth, both recent. No I/O."""
        spec, sage = self.spec(slug)
        if spec is UNKNOWN or sage > CR.SPEC_MAX_AGE_S:
            return "feed_stale:spec"
        if spec is None:
            return "market_closed"
        book, age = self.book(slug)
        if book is None or age > CR.BOOK_MAX_AGE_S or \
                top_of_book(book) is None:
            return "feed_stale:book"
        return None

    def start(self) -> None:
        """Start the reader, or start it AGAIN if its thread has died. The
        cache is kept: entries simply carry their age across the gap."""
        if self.stop or (self._thread is not None
                         and self._thread.is_alive()):
            return
        again = self._thread is not None
        self.restarts += 1 if again else 0
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="crypto-feed")
        self._thread.start()
        self._log("crypto_feed_restarted" if again
                  else "crypto_feed_started", book_s=CR.BOOK_S,
                  discover_s=CR.DISCOVER_S, spec_s=SPEC_S,
                  restarts=self.restarts)


def top_of_book(md: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"bid", "ask"} (as strings the venue served) from depth, or None."""
    from decimal import Decimal as D
    bids, asks = CR.book_levels(md, 0.0001)
    if not bids or not asks:
        return None
    b, a = D(max(bids)) / D(10000), D(min(asks)) / D(10000)
    return {"bid": b, "ask": a} if b < a else None
