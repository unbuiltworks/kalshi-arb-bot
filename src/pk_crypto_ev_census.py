"""Read-only Polymarket US / Kalshi BTC 15-minute EV census.

This module deliberately does *not* treat Polymarket and Kalshi crypto contracts
as settlement-equivalent.  Each venue is scored independently against a
spot-derived proxy fair value.  The cross-venue join is used only to compare
aligned windows and, after settlement, measure empirical outcome divergence.

What is measured:
- executable BBO and displayed touch depth on each venue,
- market midpoint versus a labelled spot-proxy fair value,
- taker EV after the venue's current fee formula at several order sizes,
- Polymarket public trade prints and subsequent BBO/depth refill,
- Kalshi REST book round-trip latency,
- aligned-window settlement disagreement, including near-strike proxy margins.

What is *not* claimed:
- the proxy fair model is an oracle,
- a Polymarket/Kalshi pair is a locked arbitrage,
- displayed depth is repeatable capacity,
- public-print refill equals our own future market impact.

No order-entry code is imported or called.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import json
import logging
import math
import os
from pathlib import Path
import statistics
import time
from typing import Any, Iterable

import httpx
from polymarket_us import PolymarketUS

from src.gemini_kalshi_kalshi_fees import fetch_series_fee_metadata
from src.polymarket_kalshi_shadow_v2 import PolyBookStore
from src.universal_three_venue_crypto import _poly_open_markets, poly_candidates

D = Decimal
logger = logging.getLogger("kalshi_arb.pk_crypto_ev_census")

KALSHI_REST = "https://external-api.kalshi.com/trade-api/v2"
POLY_GATEWAY = "https://gateway.polymarket.us"
KALSHI_SERIES = "KXBTC15M"
WINDOW_SECONDS = 15 * 60
POLY_TAKER_THETA = D("0.05")
SIZE_BUCKETS = (1, 10, 50, 100, 250, 500)
REFILL_DELAYS_MS = (100, 250, 500, 1000, 5000)


@dataclass(frozen=True)
class SpotSample:
    ts_ms: int
    price: float


class SpotTape:
    def __init__(self, maxlen: int = 10000) -> None:
        self.samples: deque[SpotSample] = deque(maxlen=maxlen)

    def add(self, ts_ms: int, price: float) -> None:
        if not math.isfinite(price) or price <= 0:
            return
        if self.samples and ts_ms <= self.samples[-1].ts_ms:
            return
        self.samples.append(SpotSample(ts_ms, price))

    @property
    def latest(self) -> SpotSample | None:
        return self.samples[-1] if self.samples else None

    def price_near(self, ts_ms: int, *, max_gap_ms: int = 2500) -> float | None:
        best: SpotSample | None = None
        best_gap = 10**18
        for row in reversed(self.samples):
            gap = abs(row.ts_ms - ts_ms)
            if gap < best_gap:
                best, best_gap = row, gap
            if row.ts_ms < ts_ms and gap > best_gap:
                break
        return None if best is None or best_gap > max_gap_ms else best.price

    def covered(self, start_ms: int, end_ms: int, *, edge_slack_ms: int = 2500) -> bool:
        if not self.samples or end_ms <= start_ms:
            return False
        return (
            self.samples[0].ts_ms <= start_ms + edge_slack_ms
            and self.samples[-1].ts_ms >= end_ms - edge_slack_ms
        )

    def integral(self, start_ms: int, end_ms: int) -> float | None:
        """Trapezoidal price*time integral in USD-seconds."""
        if end_ms <= start_ms or not self.covered(start_ms, end_ms):
            return None
        pts: list[tuple[int, float]] = []
        start_px = self.price_near(start_ms)
        end_px = self.price_near(end_ms)
        if start_px is None or end_px is None:
            return None
        pts.append((start_ms, start_px))
        pts.extend(
            (row.ts_ms, row.price)
            for row in self.samples
            if start_ms < row.ts_ms < end_ms
        )
        pts.append((end_ms, end_px))
        total = 0.0
        for (ta, pa), (tb, pb) in zip(pts, pts[1:]):
            total += ((pa + pb) / 2.0) * ((tb - ta) / 1000.0)
        return total

    def sigma_log_per_sqrt_second(self, *, lookback_s: float = 300.0) -> float | None:
        latest = self.latest
        if latest is None:
            return None
        cutoff = latest.ts_ms - int(lookback_s * 1000)
        rows = [x for x in self.samples if x.ts_ms >= cutoff]
        if len(rows) < 20:
            return None
        variance_rates: list[float] = []
        for a, b in zip(rows, rows[1:]):
            dt = (b.ts_ms - a.ts_ms) / 1000.0
            if dt <= 0:
                continue
            r = math.log(b.price / a.price)
            variance_rates.append((r * r) / dt)
        if len(variance_rates) < 10:
            return None
        # Mean squared high-frequency return per second. This intentionally
        # preserves jumps instead of de-meaning away short-window movement.
        return math.sqrt(sum(variance_rates) / len(variance_rates))


@dataclass(frozen=True)
class WindowPair:
    kalshi_ticker: str
    kalshi_close: datetime
    kalshi_strike: float | None
    poly_slug: str
    poly_close: datetime
    close_skew_s: float

    @property
    def poly_start(self) -> datetime:
        return self.poly_close - timedelta(seconds=WINDOW_SECONDS)


@dataclass(frozen=True)
class BBO:
    yes_bid: Decimal
    yes_bid_depth: Decimal
    yes_ask: Decimal
    yes_ask_depth: Decimal
    received_ms: int
    state: str = ""

    @property
    def no_ask(self) -> Decimal:
        return D("1") - self.yes_bid

    @property
    def no_ask_depth(self) -> Decimal:
        return self.yes_bid_depth

    @property
    def mid(self) -> Decimal:
        return (self.yes_bid + self.yes_ask) / D("2")


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _normal_cdf(z: float) -> float:
    if z >= 10:
        return 1.0
    if z <= -10:
        return 0.0
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _clamp_probability(value: float) -> float:
    return min(1.0, max(0.0, value))


def polymarket_proxy_fair(tape: SpotTape, start: datetime, close: datetime, now_ms: int) -> float | None:
    """Proxy P(full-window spot TWAP >= spot at window start).

    The venue's official Chainlink series is not reconstructed here.  We use a
    Coinbase/Kraken composite as a deliberately labelled proxy.  Under an
    arithmetic-Brownian approximation, the future price integral over r seconds
    has variance sigma_abs^2 * r^3 / 3.
    """
    latest = tape.latest
    sigma_log = tape.sigma_log_per_sqrt_second()
    if latest is None or sigma_log is None:
        return None
    start_ms = int(start.timestamp() * 1000)
    close_ms = int(close.timestamp() * 1000)
    if now_ms < start_ms:
        return None
    start_px = tape.price_near(start_ms)
    if start_px is None:
        return None
    end_obs_ms = min(now_ms, close_ms)
    accrued = tape.integral(start_ms, end_obs_ms)
    if accrued is None:
        return None
    elapsed_s = max(0.0, (end_obs_ms - start_ms) / 1000.0)
    remaining_s = max(0.0, WINDOW_SECONDS - elapsed_s)
    target_integral = start_px * WINDOW_SECONDS
    if remaining_s <= 0:
        return 1.0 if accrued >= target_integral else 0.0
    spot = latest.price
    mean_total = accrued + spot * remaining_s
    sigma_abs = max(1e-9, spot * sigma_log)
    sd_integral = sigma_abs * (remaining_s ** 1.5) / math.sqrt(3.0)
    if sd_integral <= 1e-12:
        return 1.0 if mean_total >= target_integral else 0.0
    return _clamp_probability(_normal_cdf((mean_total - target_integral) / sd_integral))


def kalshi_proxy_fair(tape: SpotTape, close: datetime, strike: float | None, now_ms: int) -> float | None:
    """Proxy P(final 60-second average >= Kalshi's published start average).

    Kalshi's official BRTI values are not reconstructed. Before the final minute,
    the final 60-second average has conditional variance sigma_abs^2*(h+60/3),
    where h is time until that final minute starts. During the final minute we
    integrate observed composite spot and model only the remaining integral.
    """
    if strike is None or strike <= 0:
        return None
    latest = tape.latest
    sigma_log = tape.sigma_log_per_sqrt_second()
    if latest is None or sigma_log is None:
        return None
    close_ms = int(close.timestamp() * 1000)
    tau = (close_ms - now_ms) / 1000.0
    spot = latest.price
    sigma_abs = max(1e-9, spot * sigma_log)
    if tau > 60.0:
        h = tau - 60.0
        sd_avg = sigma_abs * math.sqrt(h + 20.0)
        if sd_avg <= 1e-12:
            return 1.0 if spot >= strike else 0.0
        return _clamp_probability(_normal_cdf((spot - strike) / sd_avg))

    final_start_ms = close_ms - 60_000
    end_obs_ms = min(max(now_ms, final_start_ms), close_ms)
    accrued = tape.integral(final_start_ms, end_obs_ms)
    if accrued is None:
        return None
    remaining_s = max(0.0, (close_ms - end_obs_ms) / 1000.0)
    target_integral = strike * 60.0
    if remaining_s <= 0:
        return 1.0 if accrued >= target_integral else 0.0
    mean_total = accrued + spot * remaining_s
    sd_integral = sigma_abs * (remaining_s ** 1.5) / math.sqrt(3.0)
    if sd_integral <= 1e-12:
        return 1.0 if mean_total >= target_integral else 0.0
    return _clamp_probability(_normal_cdf((mean_total - target_integral) / sd_integral))


def polymarket_taker_fee(price: Decimal, quantity: Decimal) -> Decimal:
    raw = POLY_TAKER_THETA * quantity * price * (D("1") - price)
    return raw.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


def _size_score(
    *,
    venue: str,
    ticker: str,
    fair: float,
    bbo: BBO,
    kalshi_fee_fn: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    fair_d = D(str(fair))
    for q_int in SIZE_BUCKETS:
        q = D(q_int)
        if venue == "polymarket":
            yes_fee = polymarket_taker_fee(bbo.yes_ask, q)
            no_fee = polymarket_taker_fee(bbo.no_ask, q)
        else:
            yes_fee = kalshi_fee_fn(bbo.yes_ask, q)
            no_fee = kalshi_fee_fn(bbo.no_ask, q)
        yes_depth_ok = bbo.yes_ask_depth >= q
        no_depth_ok = bbo.no_ask_depth >= q
        yes_total_ev = (fair_d - bbo.yes_ask) * q - yes_fee if yes_depth_ok else None
        no_total_ev = ((D("1") - fair_d) - bbo.no_ask) * q - no_fee if no_depth_ok else None
        candidates = [x for x in (yes_total_ev, no_total_ev) if x is not None]
        best = max(candidates) if candidates else None
        direction = None
        if best is not None:
            direction = "YES" if yes_total_ev is not None and best == yes_total_ev else "NO"
        out.append({
            "q": q_int,
            "yes_depth_ok": yes_depth_ok,
            "no_depth_ok": no_depth_ok,
            "yes_fee_total": None if not yes_depth_ok else str(yes_fee),
            "no_fee_total": None if not no_depth_ok else str(no_fee),
            "yes_total_ev": None if yes_total_ev is None else str(yes_total_ev),
            "no_total_ev": None if no_total_ev is None else str(no_total_ev),
            "best_direction": direction,
            "best_total_ev": None if best is None else str(best),
            "best_ev_per_contract": None if best is None else str(best / q),
        })
    return out


def _best_score(rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    available = [r for r in rows if r.get("best_total_ev") is not None]
    if not available:
        return None
    return max(available, key=lambda r: D(str(r["best_total_ev"])))


async def _spot_once(http: httpx.AsyncClient) -> tuple[float | None, dict[str, Any]]:
    async def coinbase() -> float:
        r = await http.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
        r.raise_for_status()
        return float(r.json()["price"])

    async def kraken() -> float:
        r = await http.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XBTUSD"})
        r.raise_for_status()
        payload = r.json().get("result") or {}
        row = next(iter(payload.values()))
        return float(row["c"][0])

    results = await asyncio.gather(coinbase(), kraken(), return_exceptions=True)
    values = [float(x) for x in results if not isinstance(x, Exception) and math.isfinite(float(x))]
    detail = {
        "coinbase_ok": not isinstance(results[0], Exception),
        "kraken_ok": not isinstance(results[1], Exception),
    }
    if not values:
        return None, detail
    return statistics.median(values), detail


async def spot_loop(tape: SpotTape, *, poll_ms: int) -> None:
    last_diag = 0.0
    async with httpx.AsyncClient(timeout=2.0) as http:
        while True:
            started = time.perf_counter_ns()
            price, detail = await _spot_once(http)
            now_ms = int(time.time() * 1000)
            if price is not None:
                tape.add(now_ms, price)
            now = time.monotonic()
            if now - last_diag >= 10:
                sigma = tape.sigma_log_per_sqrt_second()
                logger.info("PK_EV_SPOT %s", json.dumps({
                    "ts_ms": now_ms,
                    "price": price,
                    "source": "MEDIAN_COINBASE_KRAKEN_PROXY",
                    "sources": detail,
                    "samples": len(tape.samples),
                    "sigma_log_per_sqrt_second": sigma,
                    "annualized_sigma_proxy": None if sigma is None else sigma * math.sqrt(365.0 * 24 * 3600),
                    "request_ms": round((time.perf_counter_ns() - started) / 1e6, 3),
                }, separators=(",", ":")))
                last_diag = now
            await asyncio.sleep(max(0.0, poll_ms / 1000.0 - (time.perf_counter_ns() - started) / 1e9))


async def _kalshi_markets(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await http.get(
        f"{KALSHI_REST}/markets",
        params={"series_ticker": KALSHI_SERIES, "status": "open", "limit": 100, "mve_filter": "exclude"},
    )
    r.raise_for_status()
    return list(r.json().get("markets") or [])


def _select_pair(kalshi_rows: list[dict[str, Any]], poly_rows: list[dict[str, Any]], now: datetime) -> WindowPair | None:
    ks: list[tuple[dict[str, Any], datetime]] = []
    for row in kalshi_rows:
        close = _dt(row.get("close_time"))
        if close is not None and close > now - timedelta(seconds=5):
            ks.append((row, close))
    polys = [p for p in poly_candidates(poly_rows) if p.asset == "BTC" and p.close > now - timedelta(seconds=5)]
    best: tuple[float, dict[str, Any], datetime, Any] | None = None
    for krow, kclose in ks:
        for p in polys:
            skew = abs((kclose - p.close).total_seconds())
            candidate = (skew, krow, kclose, p)
            if best is None or candidate[0] < best[0]:
                best = candidate
    if best is None or best[0] > 120:
        return None
    skew, krow, kclose, p = best
    strike_raw = krow.get("floor_strike")
    strike = None
    try:
        strike = float(strike_raw) if strike_raw is not None else None
    except Exception:
        strike = None
    return WindowPair(
        kalshi_ticker=str(krow.get("ticker") or ""),
        kalshi_close=kclose,
        kalshi_strike=strike,
        poly_slug=p.slug,
        poly_close=p.close,
        close_skew_s=float(skew),
    )


async def discover_pair(http: httpx.AsyncClient) -> WindowPair | None:
    kalshi_rows, poly_rows = await asyncio.gather(_kalshi_markets(http), _poly_open_markets(http))
    return _select_pair(kalshi_rows, poly_rows, datetime.now(timezone.utc))


async def kalshi_bbo(http: httpx.AsyncClient, ticker: str) -> tuple[BBO | None, float]:
    started = time.perf_counter_ns()
    r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
    elapsed_ms = (time.perf_counter_ns() - started) / 1e6
    r.raise_for_status()
    row = r.json().get("market") or {}
    try:
        yes_bid = D(str(row.get("yes_bid_dollars")))
        yes_ask = D(str(row.get("yes_ask_dollars")))
        bid_depth = D(str(row.get("yes_bid_size_fp") or "0"))
        ask_depth = D(str(row.get("yes_ask_size_fp") or "0"))
    except Exception:
        return None, elapsed_ms
    if not (D("0") <= yes_bid <= yes_ask <= D("1")) or min(bid_depth, ask_depth) <= 0:
        return None, elapsed_ms
    return BBO(
        yes_bid=yes_bid,
        yes_bid_depth=bid_depth,
        yes_ask=yes_ask,
        yes_ask_depth=ask_depth,
        received_ms=int(time.time() * 1000),
        state=str(row.get("status") or ""),
    ), elapsed_ms


class PolyStream:
    def __init__(self, slug: str, trade_queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.slug = slug
        self.trade_queue = trade_queue
        self.store = PolyBookStore()
        self.client: PolymarketUS | None = None
        self.ws: Any = None

    def _trade(self, data: Any) -> None:
        row = data if isinstance(data, dict) else {"raw": str(data)}
        try:
            self.trade_queue.put_nowait(row)
        except asyncio.QueueFull:
            pass

    async def run(self) -> None:
        key_id = os.environ["POLYMARKET_KEY_ID"]
        secret = os.environ["POLYMARKET_SECRET_KEY"]
        self.client = PolymarketUS(key_id=key_id, secret_key=secret, timeout=5.0)
        self.ws = self.client.ws.markets()
        self.ws.on("market_data", lambda data: self.store.ingest(data, source="ws"))
        self.ws.on("trade", self._trade)
        self.ws.on("error", lambda err: logger.warning("PK_EV_POLY_WS_ERROR %s", err))
        await self.ws.connect()
        await self.ws.subscribe("pk-ev-book", "SUBSCRIPTION_TYPE_MARKET_DATA", [self.slug])
        await self.ws.subscribe("pk-ev-trades", "SUBSCRIPTION_TYPE_TRADE", [self.slug])
        logger.info("PK_EV_POLY_WS_SUBSCRIBED %s", json.dumps({"slug": self.slug}, separators=(",", ":")))
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            try:
                await self.ws.close()
            except Exception:
                pass
            if self.client is not None:
                try:
                    self.client.close()
                except Exception:
                    pass

    def bbo(self) -> BBO | None:
        q = self.store.quote(self.slug, max_age_ms=2000)
        if q is None:
            return None
        return BBO(
            yes_bid=D("1") - q.no_ask,
            yes_bid_depth=q.no_depth,
            yes_ask=q.yes_ask,
            yes_ask_depth=q.yes_depth,
            received_ms=q.received_ms,
            state=q.state,
        )


def _trade_fields(payload: dict[str, Any]) -> dict[str, Any]:
    row = payload
    for key in ("trade", "tradeData", "trade_data", "data"):
        if isinstance(row.get(key), dict):
            row = row[key]
            break
    def amount(*keys: str) -> str | None:
        for key in keys:
            value = row.get(key)
            if isinstance(value, dict):
                value = value.get("value")
            if value is not None:
                return str(value)
        return None
    return {
        "market_slug": str(row.get("marketSlug") or row.get("market_slug") or ""),
        "qty": amount("qty", "quantity", "size", "contracts"),
        "price": amount("px", "price"),
        "side": str(row.get("side") or row.get("takerSide") or row.get("taker_side") or row.get("intent") or ""),
        "trade_id": str(row.get("id") or row.get("tradeId") or row.get("trade_id") or ""),
    }


async def refill_probe(poly: PolyStream, trade: dict[str, Any]) -> None:
    fields = _trade_fields(trade)
    baseline = poly.bbo()
    if baseline is None:
        return
    snapshots: list[dict[str, Any]] = []
    start = time.perf_counter_ns()
    previous = 0
    for delay in REFILL_DELAYS_MS:
        await asyncio.sleep((delay - previous) / 1000.0)
        previous = delay
        q = poly.bbo()
        snapshots.append({
            "delay_ms": delay,
            "yes_bid": None if q is None else str(q.yes_bid),
            "yes_bid_depth": None if q is None else str(q.yes_bid_depth),
            "yes_ask": None if q is None else str(q.yes_ask),
            "yes_ask_depth": None if q is None else str(q.yes_ask_depth),
            "book_age_ms": None if q is None else int(time.time() * 1000) - q.received_ms,
        })
    logger.info("PK_EV_POLY_REFILL %s", json.dumps({
        "ts_ms": int(time.time() * 1000),
        "trade": fields,
        "baseline_at_trade_callback": {
            "yes_bid": str(baseline.yes_bid),
            "yes_bid_depth": str(baseline.yes_bid_depth),
            "yes_ask": str(baseline.yes_ask),
            "yes_ask_depth": str(baseline.yes_ask_depth),
        },
        "snapshots": snapshots,
        "elapsed_ms": round((time.perf_counter_ns() - start) / 1e6, 3),
        "interpretation": "PUBLIC_PRINT_REFILL_PROXY_NOT_OWN_IMPACT",
    }, separators=(",", ":")))


async def trade_consumer(poly: PolyStream, queue: asyncio.Queue[dict[str, Any]]) -> None:
    while True:
        trade = await queue.get()
        asyncio.create_task(refill_probe(poly, trade))


async def _poly_settlement(http: httpx.AsyncClient, slug: str) -> str | None:
    try:
        r = await http.get(f"{POLY_GATEWAY}/v1/markets/{slug}/settlement")
        if r.status_code >= 400:
            return None
        payload = r.json()
        text = json.dumps(payload).lower()
        # Prefer explicit binary numeric settlement values when present.
        values: list[Any] = []
        def walk(x: Any) -> None:
            if isinstance(x, dict):
                for k, v in x.items():
                    if str(k).lower() in {"settlementprice", "settlement_price", "price", "value", "result", "outcome"}:
                        values.append(v)
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(payload)
        for value in values:
            raw = value.get("value") if isinstance(value, dict) else value
            s = str(raw).strip().lower()
            if s in {"1", "1.0", "1.00", "yes", "true", "up"}:
                return "yes"
            if s in {"0", "0.0", "0.00", "no", "false", "down"}:
                return "no"
        if '"yes"' in text and '"no"' not in text:
            return "yes"
        if '"no"' in text and '"yes"' not in text:
            return "no"
    except Exception:
        return None
    return None


async def _kalshi_settlement(http: httpx.AsyncClient, ticker: str) -> str | None:
    try:
        r = await http.get(f"{KALSHI_REST}/markets/{ticker}")
        if r.status_code >= 400:
            return None
        result = str((r.json().get("market") or {}).get("result") or "").lower()
        return result if result in {"yes", "no"} else None
    except Exception:
        return None


async def settlement_watch(pair: WindowPair, tape: SpotTape) -> None:
    # Give both exchanges time to publish a result; continue boundedly for 30m.
    await asyncio.sleep(max(0.0, pair.kalshi_close.timestamp() - time.time() + 1.0))
    deadline = time.monotonic() + 1800
    async with httpx.AsyncClient(timeout=5.0) as http:
        while time.monotonic() < deadline:
            k, p = await asyncio.gather(
                _kalshi_settlement(http, pair.kalshi_ticker),
                _poly_settlement(http, pair.poly_slug),
            )
            if k is not None and p is not None:
                k_close_ms = int(pair.kalshi_close.timestamp() * 1000)
                final60 = tape.integral(k_close_ms - 60_000, k_close_ms)
                k_margin_bp = None
                if final60 is not None and pair.kalshi_strike:
                    k_avg = final60 / 60.0
                    k_margin_bp = (k_avg / pair.kalshi_strike - 1.0) * 10000.0
                p_start_ms = int(pair.poly_start.timestamp() * 1000)
                p_close_ms = int(pair.poly_close.timestamp() * 1000)
                p_integral = tape.integral(p_start_ms, p_close_ms)
                p_start = tape.price_near(p_start_ms)
                p_margin_bp = None
                if p_integral is not None and p_start:
                    p_avg = p_integral / WINDOW_SECONDS
                    p_margin_bp = (p_avg / p_start - 1.0) * 10000.0
                near_bp = None
                margins = [abs(x) for x in (k_margin_bp, p_margin_bp) if x is not None]
                if margins:
                    near_bp = min(margins)
                logger.info("PK_EV_SETTLEMENT_PAIR %s", json.dumps({
                    "kalshi_ticker": pair.kalshi_ticker,
                    "polymarket_slug": pair.poly_slug,
                    "close_skew_s": pair.close_skew_s,
                    "kalshi_outcome": k,
                    "polymarket_outcome": p,
                    "outcome_disagree": k != p,
                    "kalshi_common_spot_final60_margin_bp": k_margin_bp,
                    "polymarket_common_spot_twap_margin_bp": p_margin_bp,
                    "nearest_common_spot_margin_bp": near_bp,
                    "near_5bp": None if near_bp is None else near_bp <= 5,
                    "near_10bp": None if near_bp is None else near_bp <= 10,
                    "near_20bp": None if near_bp is None else near_bp <= 20,
                    "classification": "EMPIRICAL_SETTLEMENT_DIVERGENCE_INCLUDES_INDEX_WINDOW_ROUNDING_RULE_DIFFERENCES",
                }, separators=(",", ":")))
                return
            await asyncio.sleep(5)
    logger.warning("PK_EV_SETTLEMENT_TIMEOUT %s", json.dumps({
        "kalshi_ticker": pair.kalshi_ticker,
        "polymarket_slug": pair.poly_slug,
    }, separators=(",", ":")))


async def run(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tape = SpotTape()
    spot_task = asyncio.create_task(spot_loop(tape, poll_ms=args.spot_poll_ms))
    active: WindowPair | None = None
    poly: PolyStream | None = None
    poly_task: asyncio.Task[Any] | None = None
    trade_task: asyncio.Task[Any] | None = None
    trade_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
    last_discovery = 0.0
    last_summary = 0.0
    sample_count = 0
    positive_by_venue = {"polymarket": 0, "kalshi": 0}
    settlement_tasks: set[asyncio.Task[Any]] = set()

    async with httpx.AsyncClient(timeout=3.0) as http:
        fee_meta = await fetch_series_fee_metadata(http, KALSHI_SERIES)
        kalshi_fee_fn = fee_meta.taker_fee_fn()
        logger.info("PK_EV_START %s", json.dumps({
            "classification": "VENUE_VS_SPOT_EV_NOT_CROSS_VENUE_LOCK",
            "kalshi_series": KALSHI_SERIES,
            "kalshi_fee": fee_meta.as_dict(),
            "polymarket_taker_theta": str(POLY_TAKER_THETA),
            "size_buckets": SIZE_BUCKETS,
            "spot_proxy": "MEDIAN_COINBASE_KRAKEN",
            "read_only": True,
            "order_entry": False,
        }, separators=(",", ":")))

        try:
            while True:
                now_mono = time.monotonic()
                if active is None or now_mono - last_discovery >= args.discovery_s or datetime.now(timezone.utc) > active.kalshi_close + timedelta(seconds=2):
                    last_discovery = now_mono
                    candidate = await discover_pair(http)
                    if candidate is not None and (active is None or candidate != active):
                        previous = active
                        if poly_task is not None:
                            poly_task.cancel()
                        if trade_task is not None:
                            trade_task.cancel()
                        if poly_task is not None or trade_task is not None:
                            await asyncio.gather(*(t for t in (poly_task, trade_task) if t is not None), return_exceptions=True)
                        active = candidate
                        poly = PolyStream(active.poly_slug, trade_queue)
                        poly_task = asyncio.create_task(poly.run())
                        trade_task = asyncio.create_task(trade_consumer(poly, trade_queue))
                        logger.info("PK_EV_WINDOW %s", json.dumps({
                            "kalshi_ticker": active.kalshi_ticker,
                            "kalshi_close": active.kalshi_close.isoformat(),
                            "kalshi_strike": active.kalshi_strike,
                            "polymarket_slug": active.poly_slug,
                            "polymarket_start": active.poly_start.isoformat(),
                            "polymarket_close": active.poly_close.isoformat(),
                            "close_skew_s": active.close_skew_s,
                            "settlement_equivalence": "NOT_ASSUMED",
                        }, separators=(",", ":")))
                        if previous is not None:
                            task = asyncio.create_task(settlement_watch(previous, tape))
                            settlement_tasks.add(task)
                            task.add_done_callback(settlement_tasks.discard)
                    elif candidate is None and active is None:
                        if now_mono - last_summary >= 10:
                            logger.info("PK_EV_NO_ALIGNED_WINDOW read_only=true")
                            last_summary = now_mono
                        await asyncio.sleep(1)
                        continue

                if active is None or poly is None:
                    await asyncio.sleep(0.25)
                    continue
                k_bbo, k_rest_ms = await kalshi_bbo(http, active.kalshi_ticker)
                p_bbo = poly.bbo()
                now_ms = int(time.time() * 1000)
                if k_bbo is None or p_bbo is None:
                    await asyncio.sleep(args.sample_ms / 1000.0)
                    continue

                k_fair = kalshi_proxy_fair(tape, active.kalshi_close, active.kalshi_strike, now_ms)
                p_fair = polymarket_proxy_fair(tape, active.poly_start, active.poly_close, now_ms)
                row: dict[str, Any] = {
                    "ts_ms": now_ms,
                    "window": {
                        "kalshi_ticker": active.kalshi_ticker,
                        "polymarket_slug": active.poly_slug,
                        "close_skew_s": active.close_skew_s,
                        "kalshi_strike": active.kalshi_strike,
                    },
                    "spot": None if tape.latest is None else tape.latest.price,
                    "sigma_log_per_sqrt_second": tape.sigma_log_per_sqrt_second(),
                    "kalshi": {
                        "yes_bid": str(k_bbo.yes_bid),
                        "yes_bid_depth": str(k_bbo.yes_bid_depth),
                        "yes_ask": str(k_bbo.yes_ask),
                        "yes_ask_depth": str(k_bbo.yes_ask_depth),
                        "no_ask": str(k_bbo.no_ask),
                        "no_ask_depth": str(k_bbo.no_ask_depth),
                        "mid": str(k_bbo.mid),
                        "proxy_fair_yes": k_fair,
                        "mid_minus_proxy_fair": None if k_fair is None else float(k_bbo.mid) - k_fair,
                        "rest_roundtrip_ms": round(k_rest_ms, 3),
                        "book_age_ms": now_ms - k_bbo.received_ms,
                        "scores": None,
                    },
                    "polymarket": {
                        "yes_bid": str(p_bbo.yes_bid),
                        "yes_bid_depth": str(p_bbo.yes_bid_depth),
                        "yes_ask": str(p_bbo.yes_ask),
                        "yes_ask_depth": str(p_bbo.yes_ask_depth),
                        "no_ask": str(p_bbo.no_ask),
                        "no_ask_depth": str(p_bbo.no_ask_depth),
                        "mid": str(p_bbo.mid),
                        "proxy_fair_yes": p_fair,
                        "mid_minus_proxy_fair": None if p_fair is None else float(p_bbo.mid) - p_fair,
                        "book_age_ms": now_ms - p_bbo.received_ms,
                        "scores": None,
                    },
                    "classification": "VENUE_VS_SPOT_EV_NOT_CROSS_VENUE_LOCK",
                }
                if k_fair is not None:
                    ks = _size_score(venue="kalshi", ticker=active.kalshi_ticker, fair=k_fair, bbo=k_bbo, kalshi_fee_fn=kalshi_fee_fn)
                    row["kalshi"]["scores"] = ks
                    best = _best_score(ks)
                    if best is not None and D(str(best["best_total_ev"])) > 0:
                        positive_by_venue["kalshi"] += 1
                if p_fair is not None:
                    ps = _size_score(venue="polymarket", ticker=active.poly_slug, fair=p_fair, bbo=p_bbo, kalshi_fee_fn=kalshi_fee_fn)
                    row["polymarket"]["scores"] = ps
                    best = _best_score(ps)
                    if best is not None and D(str(best["best_total_ev"])) > 0:
                        positive_by_venue["polymarket"] += 1

                with output.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, separators=(",", ":")) + "\n")
                sample_count += 1
                if now_mono - last_summary >= 10:
                    logger.info("PK_EV_SUMMARY %s", json.dumps({
                        "samples": sample_count,
                        "positive_samples": positive_by_venue,
                        "kalshi_rest_roundtrip_ms": round(k_rest_ms, 3),
                        "polymarket_book_age_ms": now_ms - p_bbo.received_ms,
                        "kalshi_proxy_fair_ready": k_fair is not None,
                        "polymarket_proxy_fair_ready": p_fair is not None,
                        "spot_samples": len(tape.samples),
                        "settlement_watches": len(settlement_tasks),
                        "read_only": True,
                    }, separators=(",", ":")))
                    last_summary = now_mono
                await asyncio.sleep(args.sample_ms / 1000.0)
        finally:
            spot_task.cancel()
            if poly_task is not None:
                poly_task.cancel()
            if trade_task is not None:
                trade_task.cancel()
            for task in settlement_tasks:
                task.cancel()
            await asyncio.gather(spot_task, *(t for t in (poly_task, trade_task) if t is not None), *settlement_tasks, return_exceptions=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only P/K BTC 15m venue-vs-spot EV census")
    parser.add_argument("--sample-ms", type=int, default=100)
    parser.add_argument("--spot-poll-ms", type=int, default=250)
    parser.add_argument("--discovery-s", type=float, default=10.0)
    parser.add_argument("--output", default="/data/pk_crypto_ev_census.jsonl")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)-8s %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
