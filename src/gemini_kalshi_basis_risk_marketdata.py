"""Basis-risk-only Kalshi exact REST market-data transport.

Kalshi currently rejects additional authenticated WebSocket handshakes with HTTP 429
while the account's Advanced REST tier remains healthy. The bounded settlement-basis
canary therefore avoids a second independent Kalshi WebSocket and uses exact depth=1
REST order books only inside this dedicated child process.

Safety properties:
* the optional market_lifecycle_v2 socket stays disabled; exact strikes continue via
  the existing authoritative GET /markets/{ticker} fallback;
* current tickers use the configured exact-book cadence, clamped to no more than 20Hz
  per ticker; when current+next windows overlap (>3 tickers), the cycle automatically
  slows to at least 180ms so exact-book traffic does not starve authoritative target
  discovery at rollover;
* every Kalshi quote is timestamped at HTTP request *start*, not response arrival, so
  network RTT makes it older and can only hurt the downstream freshness/skew gates;
* only exact GET /markets/{ticker}/orderbook?depth=1 responses enter the feed;
* one-sided binary books preserve the quoted side while assigning zero depth to the
  missing side, exactly like the proven WebSocket book logic; no liquidity is invented;
* HTTP 429s back off and emit no quote; stale/missing data therefore fails closed.

All downstream <=100ms quote age, <=50ms cross-venue skew, 50ms persistence, q=1 exact
depth, >=3c after-fee economics, protective threshold-dominance rules, dual-FOK
transport, durable Postgres reservation and settlement controls remain unchanged.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal
import json
import logging
import os
import time
from typing import Any

import httpx

from src import gemini_kalshi_rollover_optimizer as rollover
from src import gemini_kalshi_target_lifecycle_cache as targets
from src.gemini_kalshi_execution_shadow import TopOfBook

D = Decimal
ONE = D("1")
logger = logging.getLogger("kalshi_arb.gemini_kalshi_basis_risk_marketdata")
DEFAULT_POLL_INTERVAL_S = 0.05
MIN_POLL_INTERVAL_S = 0.05
MAX_POLL_INTERVAL_S = 0.25
ROLLOVER_OVERLAP_TICKER_THRESHOLD = 3
ROLLOVER_OVERLAP_MIN_POLL_INTERVAL_S = 0.18
MAX_TICKERS = 8
_INSTALLED = False


def _poll_interval_s() -> float:
    raw = os.getenv("CRYPTO_BASIS_KALSHI_REST_POLL_INTERVAL_S", str(DEFAULT_POLL_INTERVAL_S))
    try:
        value = float(raw)
    except ValueError:
        value = DEFAULT_POLL_INTERVAL_S
    return max(MIN_POLL_INTERVAL_S, min(value, MAX_POLL_INTERVAL_S))


def _cycle_poll_interval_s(ticker_count: int) -> float:
    """Return the safe cycle cadence for the current registered ticker set.

    Steady state is three eligible current-window tickers (ETH/SOL/XRP). During the
    rollover prewarm there can be current+next tickers simultaneously. Slowing only
    that overlap preserves faster steady-state sampling while leaving REST capacity for
    authoritative GET /markets/{ticker} target discovery. This never makes polling
    faster than the configured base interval.
    """
    base = _poll_interval_s()
    if int(ticker_count) > ROLLOVER_OVERLAP_TICKER_THRESHOLD:
        return max(base, ROLLOVER_OVERLAP_MIN_POLL_INTERVAL_S)
    return base


async def _rest_only_lifecycle_placeholder() -> None:
    logger.warning(
        "CRYPTO_BASIS_RISK_TARGET_TRANSPORT lifecycle_ws=false rest_fallback=true risk_gates_changed=false"
    )
    await asyncio.Event().wait()


def _d(value: Any) -> Decimal | None:
    try:
        out = D(str(value))
    except Exception:
        return None
    return out if out.is_finite() else None


def _best_bid(rows: Any) -> tuple[Decimal, Decimal] | None:
    if not isinstance(rows, list):
        return None
    best: tuple[Decimal, Decimal] | None = None
    for row in rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        price = _d(row[0])
        qty = _d(row[1])
        if price is None or qty is None or qty <= 0:
            continue
        if best is None or price > best[0]:
            best = (price, qty)
    return best


def exact_rest_top(payload: dict[str, Any], ticker: str, quote_ts_ms: int) -> TopOfBook | None:
    """Parse exact depth=1 REST book with proven one-sided binary-book semantics."""
    book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    yes = _best_bid(book.get("yes_dollars") or [])
    no = _best_bid(book.get("no_dollars") or [])
    if yes is None and no is None:
        return None
    if yes is None:
        yes_bid, yes_bid_size = D("0"), D("0")
    else:
        yes_bid, yes_bid_size = yes
    if no is None:
        yes_ask, yes_ask_size = D("1"), D("0")
    else:
        no_bid, no_bid_size = no
        yes_ask, yes_ask_size = ONE - no_bid, no_bid_size
    if not (D("0") <= yes_bid <= ONE and D("0") <= yes_ask <= ONE):
        return None
    if yes_bid > yes_ask:
        return None
    return TopOfBook(
        venue="kalshi",
        market_id=str(ticker),
        ts_ms=int(quote_ts_ms),
        yes_bid=yes_bid,
        yes_bid_size=yes_bid_size,
        yes_ask=yes_ask,
        yes_ask_size=yes_ask_size,
    )


class BasisKalshiExactRestHub:
    def __init__(self) -> None:
        self._targets: dict[str, list[tuple[asyncio.Queue, Any]]] = defaultdict(list)
        self._registry_lock = asyncio.Lock()
        self._task: asyncio.Task[Any] | None = None
        self._http: httpx.AsyncClient | None = None
        self._private_key: Any | None = None
        self.requests = 0
        self.rate_limits = 0
        self.parse_misses = 0
        self.emitted = 0
        self.cycles = 0
        self._backoff_until = 0.0

    def health(self) -> dict[str, Any]:
        ticker_count = len(self._targets)
        return {
            "connected": self._task is not None and not self._task.done(),
            "transport": "KALSHI_EXACT_REST_DEPTH1",
            "desired_markets": sorted(self._targets),
            "poll_interval_ms": int(_poll_interval_s() * 1000),
            "effective_poll_interval_ms": int(_cycle_poll_interval_s(ticker_count) * 1000),
            "rollover_overlap": ticker_count > ROLLOVER_OVERLAP_TICKER_THRESHOLD,
            "requests": self.requests,
            "rate_limits": self.rate_limits,
            "parse_misses": self.parse_misses,
            "emitted": self.emitted,
            "cycles": self.cycles,
        }

    async def register(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        async with self._registry_lock:
            target = (queue, stats)
            if target not in self._targets[ticker]:
                self._targets[ticker].append(target)
            if len(self._targets) > MAX_TICKERS:
                self._targets[ticker].remove(target)
                if not self._targets[ticker]:
                    self._targets.pop(ticker, None)
                raise RuntimeError(f"basis REST hub refuses >{MAX_TICKERS} simultaneous tickers")
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run(), name="kalshi-basis-exact-rest-hub")

    async def unregister(self, ticker: str, queue: asyncio.Queue, stats: Any) -> None:
        ticker = str(ticker)
        async with self._registry_lock:
            target = (queue, stats)
            rows = self._targets.get(ticker, [])
            if target in rows:
                rows.remove(target)
            if not rows:
                self._targets.pop(ticker, None)

    async def _fetch_one(self, ticker: str, targets_for_ticker: list[tuple[asyncio.Queue, Any]]) -> None:
        assert self._http is not None and self._private_key is not None
        sign_path = f"/trade-api/v2/markets/{ticker}/orderbook"
        headers = rollover.hot.auth_headers(self._private_key, "GET", sign_path, rollover.hot.API_KEY_ID)
        request_start_ms = int(time.time() * 1000)
        try:
            response = await self._http.get(
                f"{rollover.readiness.KALSHI_REST}/markets/{ticker}/orderbook",
                params={"depth": 1},
                headers=headers,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("CRYPTO_BASIS_KALSHI_REST_ERROR ticker=%s type=%s", ticker, type(exc).__name__)
            return
        self.requests += 1
        if response.status_code == 429:
            self.rate_limits += 1
            raw = response.headers.get("retry-after", "").strip()
            try:
                retry_after = float(raw) if raw else 0.25
            except ValueError:
                retry_after = 0.25
            self._backoff_until = max(self._backoff_until, time.monotonic() + max(0.25, retry_after))
            return
        if response.status_code != 200:
            return
        try:
            payload = response.json()
        except Exception:
            self.parse_misses += 1
            return
        top = exact_rest_top(payload, ticker, request_start_ms)
        if top is None:
            self.parse_misses += 1
            return
        response_receive_ms = int(time.time() * 1000)
        mono_ns = time.perf_counter_ns()
        for queue, stats in list(targets_for_ticker):
            stats.events += 1
            stats.kalshi_events += 1
            rollover.hot._enqueue(queue, ("kalshi", response_receive_ms, None, top, mono_ns), stats)
            self.emitted += 1

    async def _run(self) -> None:
        self._private_key = rollover.hot.load_private_key()
        limits = httpx.Limits(max_connections=MAX_TICKERS, max_keepalive_connections=MAX_TICKERS)
        self._http = httpx.AsyncClient(timeout=2.0, limits=limits)
        logger.warning(
            "CRYPTO_BASIS_KALSHI_BOOK_TRANSPORT %s",
            json.dumps({
                "transport": "EXACT_REST_DEPTH1",
                "poll_interval_ms": int(_poll_interval_s() * 1000),
                "rollover_overlap_min_poll_interval_ms": int(ROLLOVER_OVERLAP_MIN_POLL_INTERVAL_S * 1000),
                "rollover_overlap_ticker_threshold": ROLLOVER_OVERLAP_TICKER_THRESHOLD,
                "max_tickers": MAX_TICKERS,
                "max_reads_per_second": int(MAX_TICKERS / _poll_interval_s()),
                "quote_timestamp": "HTTP_REQUEST_START",
                "one_sided_book_semantics": "MISSING_SIDE_ZERO_DEPTH",
                "stale_or_429_behavior": "NO_QUOTE_FAIL_CLOSED",
                "read_only": True,
                "order_entry": False,
                "risk_gates_changed": False,
            }, separators=(",", ":")),
        )
        try:
            while True:
                if time.monotonic() < self._backoff_until:
                    await asyncio.sleep(min(0.05, self._backoff_until - time.monotonic()))
                    continue
                cycle_start = time.monotonic()
                async with self._registry_lock:
                    snapshot = {ticker: list(rows) for ticker, rows in self._targets.items() if rows}
                effective_interval_s = _cycle_poll_interval_s(len(snapshot))
                if snapshot:
                    await asyncio.gather(*(self._fetch_one(ticker, rows) for ticker, rows in snapshot.items()))
                    self.cycles += 1
                elapsed = time.monotonic() - cycle_start
                await asyncio.sleep(max(0.0, effective_interval_s - elapsed))
        finally:
            if self._http is not None:
                await self._http.aclose()
                self._http = None


_HUB: BasisKalshiExactRestHub | None = None


def install() -> None:
    global _INSTALLED, _HUB
    if _INSTALLED:
        return
    targets._lifecycle_stream = _rest_only_lifecycle_placeholder
    _HUB = BasisKalshiExactRestHub()
    rollover._HUB = _HUB
    _INSTALLED = True
    logger.warning(
        "CRYPTO_BASIS_RISK_MARKETDATA_GUARD_INSTALLED lifecycle_ws=false shared_orderbook_ws=false "
        "kalshi_exact_rest_depth1=true poll_interval_ms=%s rollover_overlap_min_poll_interval_ms=%s "
        "risk_gates_changed=false",
        int(_poll_interval_s() * 1000),
        int(ROLLOVER_OVERLAP_MIN_POLL_INTERVAL_S * 1000),
    )
