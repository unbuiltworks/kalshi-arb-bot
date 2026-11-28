"""One request budget for every SIGNED venue call the sports bot makes.

2026-11-21 rate-limit audit: one venue API key is shared by several services;
the sports bot got ~3 HTTP 429/min on its account read while its own
research threads (combo study, combo maker RFQ polls) spent the same budget.
This governor sits in front of the signed transport and orders the bot's
calls by what they protect:

  critical  cancels and exact order-status reads (POST .../cancel,
            GET /v1/order/{id}). Never delayed, never refused, even in a
            429 cool-down: cancellation and fill truth come first.
  normal    order placement, account / open-orders / positions reads
            (reconciliation). Wait for budget (bounded), and through a 429
            cool-down; refused with BudgetDeferred if still none.
  safety    account audit, inventory reconciliation and the reward read,
            marked by the caller with `budget.safety()`. LIVE 2026-11-23:
            both hourly audits of a campaign were refused because quote
            traffic had spent the bucket, so the bot ran 1 h 45 min with no
            account audit. A safety read waits longer (SAFETY_WAIT_S) and,
            while one is waiting, order placement and other normal reads
            stand aside: the next token goes to the safety read. Cancels
            and order-status reads are still never delayed.
  research  everything else (RFQs, incentives, activity, history). Only
            from spare budget -- never below RESERVE of the bucket, never in
            a cool-down -- and refused at once rather than queued.

A 429 from any call starts a cool-down. Counters are published in the
heartbeat (`venue_requests`), like l5-pmus-live's governor. Pure apart from
the clock/sleep it is given; it places and cancels nothing itself.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Dict, Optional

_ORDER_STATUS = re.compile(r"^/v1/order/[^/]+$")
_CANCEL = re.compile(r"^/v1/order/[^/]+/cancel$|^/v1/orders/open/cancel$")
_NORMAL_PREFIXES = ("/v1/orders", "/v1/order/preview", "/v1/portfolio",
                    "/v1/account", "/v1/market/")


class BudgetDeferred(Exception):
    """Refused by the shared budget (not sent to the venue)."""


def classify(method: str, path: str) -> str:
    p = str(path).split("?", 1)[0]
    m = str(method).upper()
    if m == "POST" and _CANCEL.match(p):
        return "critical"
    if m == "GET" and _ORDER_STATUS.match(p):
        return "critical"
    if p.startswith(_NORMAL_PREFIXES):
        return "normal"
    return "research"


_ROUTE_RULES = (
    (re.compile(r"^/v1/order/[^/]+/cancel$"), "/v1/order/:id/cancel"),
    (re.compile(r"^/v1/order/preview$"), "/v1/order/preview"),
    (re.compile(r"^/v1/order/[^/]+$"), "/v1/order/:id"),
    (re.compile(r"^/v1/markets/[^/]+/(bbo|book)$"), r"/v1/markets/:slug/\1"),
    (re.compile(r"^/v1/market/slug/[^/]+$"), "/v1/market/slug/:slug"),
    (re.compile(r"^/v1/rfqs/[^/]+/quotes/[^/]+/confirm$"),
     "/v1/rfqs/:id/quotes/:id/confirm"),
    (re.compile(r"^/v1/rfqs/[^/]+/quotes/[^/]+$"), "/v1/rfqs/:id/quotes/:id"),
)
ROUTE_FAMILIES_MAX = 64


def route_family(method: str, path: str) -> str:
    """"GET /v1/order/:id": the request's route with ids, slugs and the
    query removed, so a counter per route stays small. Reporting only."""
    p = str(path).split("?", 1)[0].rstrip("/") or "/"
    for rx, rep in _ROUTE_RULES:
        if rx.match(p):
            p = rx.sub(rep, p)
            break
    return "%s %s" % (str(method).upper(), p)


def count_route(table: Dict[str, int], key: str) -> None:
    """Bounded counter: an unexpected flood of distinct routes lands in
    "other" instead of growing the heartbeat."""
    if key not in table and len(table) >= ROUTE_FAMILIES_MAX:
        key = "other"
    table[key] = table.get(key, 0) + 1


def _is_429(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status", None)
    return code == 429 or "429" in str(exc)


class RequestBudget:
    def __init__(self, rps: float = 1.0, burst: float = 4.0,
                 cooldown_s: float = 15.0, reserve: float = 0.5,
                 normal_wait_s: float = 3.0, safety_wait_s: float = 20.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.rps, self.burst = float(rps), float(burst)
        self.cooldown_s, self.reserve = float(cooldown_s), float(reserve)
        self.normal_wait_s = float(normal_wait_s)
        self.safety_wait_s = float(safety_wait_s)
        self._safety_waiting = 0
        self._tl = threading.local()
        self.clock, self.sleep = clock, sleep
        self._tokens = self.burst
        self._last = clock()
        self._cool_until = 0.0
        self._lock = threading.Lock()
        self.stats = {"sent": {"critical": 0, "safety": 0, "normal": 0,
                               "research": 0},
                      "deferred": {"safety": 0, "normal": 0, "research": 0},
                      "http_429": 0, "cooldowns": 0, "critical_in_cooldown": 0,
                      # REPORTING ONLY (2026-11-28 request audit): what was
                      # handed to the venue, deferred, and answered 429, per
                      # route family. Nothing reads these to decide anything.
                      "by_route": {}, "deferred_by_route": {},
                      "http_429_by_route": {}}
        self._t0 = clock()

    def _refill(self) -> None:
        now = self.clock()
        self._tokens = min(self.burst,
                           self._tokens + (now - self._last) * self.rps)
        self._last = now

    def cooling(self) -> bool:
        return self.clock() < self._cool_until

    def safety(self, max_wait_s: Optional[float] = None) -> "_Safety":
        """Context: signed requests made by THIS thread inside it are
        safety reads (see the module text). Re-entrant. `max_wait_s` caps
        how long a request inside it may wait (default safety_wait_s): a
        caller on the trading loop uses it so a cool-down cannot hold the
        loop (and the cancels it issues) longer than a normal request."""
        return _Safety(self, max_wait_s)

    def in_safety(self) -> bool:
        return getattr(self._tl, "depth", 0) > 0

    def _acquire_safety(self) -> None:
        cap = getattr(self._tl, "max_wait", None)
        deadline = self.clock() + (self.safety_wait_s if cap is None
                                   else min(self.safety_wait_s, cap))
        with self._lock:
            self._safety_waiting += 1
        try:
            while True:
                with self._lock:
                    self._refill()
                    if not self.cooling() and self._tokens >= 1:
                        self._tokens -= 1
                        self.stats["sent"]["safety"] += 1
                        return
                    wait = max(self._cool_until - self.clock(),
                               (1 - self._tokens) / self.rps
                               if self.rps > 0 else self.safety_wait_s)
                if self.clock() + min(wait, 0.5) > deadline:
                    with self._lock:
                        self.stats["deferred"]["safety"] += 1
                    raise BudgetDeferred("safety request deferred "
                                         "(shared budget / 429 cool-down)")
                self.sleep(min(max(wait, 0.05), 0.5))
        finally:
            with self._lock:
                self._safety_waiting -= 1

    def acquire(self, cls: str) -> None:
        if cls == "safety":
            return self._acquire_safety()
        deadline = self.clock() + self.normal_wait_s
        while True:
            with self._lock:
                self._refill()
                if cls == "critical":
                    if self.cooling():
                        self.stats["critical_in_cooldown"] += 1
                    # always sent; may run the bucket into deficit so the
                    # other classes yield to it
                    self._tokens = max(-self.burst, self._tokens - 1)
                    self.stats["sent"]["critical"] += 1
                    return
                if cls == "research":
                    if not self.cooling() and self._safety_waiting == 0 \
                            and self._tokens - 1 >= self.reserve * self.burst:
                        self._tokens -= 1
                        self.stats["sent"]["research"] += 1
                        return
                    self.stats["deferred"]["research"] += 1
                    raise BudgetDeferred("research request deferred "
                                         "(shared budget)")
                if not self.cooling() and self._tokens >= 1 and \
                        self._safety_waiting == 0:
                    self._tokens -= 1
                    self.stats["sent"]["normal"] += 1
                    return
                wait = max(self._cool_until - self.clock(),
                           (1 - self._tokens) / self.rps if self.rps > 0
                           else self.normal_wait_s)
            if self.clock() + min(wait, 0.5) > deadline:
                with self._lock:
                    self.stats["deferred"]["normal"] += 1
                raise BudgetDeferred("normal request deferred "
                                     "(shared budget / 429 cool-down)")
            self.sleep(min(max(wait, 0.05), 0.5))

    def note_route(self, table: str, method: str, path: str) -> None:
        """Count one request in a per-route table. Never raises."""
        try:
            key = route_family(method, path)
            with self._lock:
                count_route(self.stats[table], key)
        except Exception:                                 # noqa: BLE001
            pass

    def note_429(self) -> None:
        with self._lock:
            self.stats["http_429"] += 1
            if not self.cooling():
                self.stats["cooldowns"] += 1
            self._cool_until = self.clock() + self.cooldown_s

    def report(self) -> Dict[str, Any]:
        with self._lock:
            up = max(1e-9, self.clock() - self._t0)
            sent = sum(self.stats["sent"].values())
            return {"rps_budget": self.rps, "sent_rps": round(sent / up, 3),
                    "cooling": self.cooling(),
                    **{k: (dict(v) if isinstance(v, dict) else v)
                       for k, v in self.stats.items()},
                    "label": "shared signed-request budget: cancels and "
                             "order-status first, research only from spare"}


class _Safety:
    def __init__(self, budget: "RequestBudget",
                 max_wait_s: Optional[float] = None):
        self._b = budget
        self._cap = max_wait_s
        self._prev = None

    def __enter__(self) -> "_Safety":
        self._b._tl.depth = getattr(self._b._tl, "depth", 0) + 1
        self._prev = getattr(self._b._tl, "max_wait", None)
        if self._cap is not None:
            self._b._tl.max_wait = self._cap if self._prev is None \
                else min(self._prev, self._cap)
        return self

    def __exit__(self, *exc: Any) -> None:
        self._b._tl.depth = max(0, getattr(self._b._tl, "depth", 1) - 1)
        self._b._tl.max_wait = self._prev


class SafetyTransport:
    """A transport whose own requests are safety reads. Only the request
    itself is marked: whatever a caller runs between requests (the audit's
    order-management pass) keeps its ordinary class."""

    def __init__(self, t: Any):
        self._t = t

    def request(self, *a: Any, **k: Any) -> Any:
        b = getattr(self._t, "budget", None)
        if b is None:
            return self._t.request(*a, **k)
        with b.safety():
            return self._t.request(*a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._t, name)


class GovernedTransport:
    """Wraps the signed transport; every request goes through the budget."""

    def __init__(self, inner: Any, budget: RequestBudget):
        self._inner, self.budget = inner, budget

    def request(self, method: str, path: str, *a: Any, **kw: Any) -> Any:
        base = kw.get("base", a[1] if len(a) > 1 else None)
        if base is not None and "gateway" in str(base):
            # public, unsigned gateway reads are not on the key's budget
            return self._inner.request(method, path, *a, **kw)
        # a caller may raise ONE read to "critical" (never to anything
        # lower): the open-order list that replaces per-order status polls
        want = kw.pop("budget_class", None)
        cls = "critical" if want == "critical" else classify(method, path)
        if cls != "critical" and self.budget.in_safety():
            cls = "safety"
        note = getattr(self.budget, "note_route", None)
        try:
            self.budget.acquire(cls)
        except BudgetDeferred:
            if note is not None:
                note("deferred_by_route", method, path)
            raise
        if note is not None:
            note("by_route", method, path)
        try:
            return self._inner.request(method, path, *a, **kw)
        except Exception as exc:                          # noqa: BLE001
            if _is_429(exc):
                self.budget.note_429()
                if note is not None:
                    note("http_429_by_route", method, path)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def from_env() -> RequestBudget:
    def f(name: str, default: str) -> float:
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return float(default)
    return RequestBudget(rps=f("PMUS_TWO_SIDED_SIGNED_RPS", "1.0"),
                         burst=f("PMUS_TWO_SIDED_SIGNED_BURST", "4"),
                         cooldown_s=f("PMUS_TWO_SIDED_429_COOLDOWN_S", "15"),
                         reserve=f("PMUS_TWO_SIDED_RESEARCH_RESERVE", "0.5"),
                         safety_wait_s=f("PMUS_TWO_SIDED_SAFETY_WAIT_S",
                                         "20"))
