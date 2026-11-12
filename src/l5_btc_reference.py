"""The live BTC reference and the exact market strike. READ-ONLY.

WHAT THE MARKET SETTLES ON, from the venue's own market record. A PMUS
BTC15m market settles UP when CF Benchmarks' BRTI, AVERAGED OVER THE LAST 60
SECONDS before the window end, is at or above the same 60-second average
taken before the window start. The venue publishes both ends itself, free
and unauthenticated, on the public gateway this engine already reads:

  strike   `GET /v1/market/slug/{slug}` -> `assetPriceTerms.priceToBeat`,
           with `windowStart`, `windowEnd` and `indexSymbol`;
  index    `GET /v1/asset-prices/history?assets=crypto:btc
           &bucket=ASSET_PRICE_BUCKET_1S&from=<epoch>` -> one value a second,
           each ALREADY the 60-second average (`tickCount` 60). The value in
           the bucket ending at the window start IS the strike (verified
           2026-11-10: bucket 18:14:59Z 73118.92 == priceToBeat 73118.92).

So the reference is the settlement series itself, not a proxy for it: no
exchange basis, no second data vendor, no cost.

WHAT THIS MODULE DOES. Polls that series about once a second on a daemon
thread, keeps the last twenty minutes, fetches the strike once per market,
and turns the two into a probability that the market settles UP -- with the
timestamps and latency of everything it used. It places nothing, writes
nothing and holds no credential.

THE PROBABILITY. Let A(t) be the published average and R(t) the raw index
underneath it, so A(t) - A(t-1) = (R(t) - R(t-60)) / 60. The settlement
value is A(T) at T = window end - 1. With tau = T - t seconds to go:

  mean   A(t) + dA * tau' * (119 - tau') / 120,  tau' = min(tau, 60),
         dA = A(t) - A(t-1): the raw index is AHEAD of its own average by
         about half of the last minute's move, and the average is still
         catching up. (For tau >= 60 this is A + 29.5*dA, the best linear
         estimate of R(t) from the one-minute change.)
  sd     sigma * sqrt(tau - 39.5 + 5)                     for tau >= 60
         sigma * sqrt(tau(tau+1)(2tau+1)/21600 + 5*tau/60) for tau < 60
         -- averaging removes about forty seconds of variance, and inside
         the last minute most of the settlement value is already observed.
  sigma  the index's own per-second volatility over the recent history,
         from its one-minute changes, floored, and INFLATED by a stated
         factor so that an uncertain volatility pulls the answer to 0.5.

  P(UP) = Phi((mean - strike) / sd).

FAIL CLOSED, EVERYWHERE. A stale index, a clock that disagrees with the
venue's, a strike for a different window or a different index, too little
history for a volatility, a non-finite number: each returns NO probability
and names the reason. The caller then has no model belief and falls back to
the book -- it never gets a guess.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import os
import threading
import time
import urllib.request
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple

ENABLE_ENV = "L5_BTC_REFERENCE"
VENUE_INDEX = "VENUE_INDEX"
GATEWAY = "https://gateway.polymarket.us"
INDEX_SYMBOL = "BRTI"
WINDOW_S = 900
AVERAGING_S = 60
KEEP_S = 1200
#: the newest bucket must have ENDED no longer ago than this, and must not
#: end in our future by more than CLOCK_SKEW_S. Outside that the two clocks
#: disagree or the feed has stalled; either way there is no reference.
MAX_AGE_S = 3.0
CLOCK_SKEW_S = 2.0
MIN_HISTORY_S = 300
#: per-second relative volatility is never taken below this (about 20%
#: annualised) and the estimate is multiplied by VOL_INFLATION.
SIGMA_REL_FLOOR = 3.5e-5
VOL_INFLATION = 1.25
HTTP_TIMEOUT_S = 2.5


def enabled() -> bool:
    """Off unless the owner sets L5_BTC_REFERENCE to exactly VENUE_INDEX."""
    return os.getenv(ENABLE_ENV, "") == VENUE_INDEX


def _http_json(url: str, timeout: float = HTTP_TIMEOUT_S) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def _epoch(iso: Any) -> Optional[float]:
    try:
        return dt.datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _fin(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# =================================================================== strike
def parse_strike(payload: Any, *, slug: str, window_start: float,
                 window_s: float = WINDOW_S) -> Dict[str, Any]:
    """The venue's own strike for EXACTLY this market and window, or why not.

    Every field that could make the strike belong to something else is
    checked: the slug, the index, the market type, both window bounds."""
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, reason=reason, slug=str(slug))
    m = payload.get("market") if isinstance(payload, Mapping) else None
    if not isinstance(m, Mapping):
        return no("MARKET_RECORD_UNREADABLE")
    if str(m.get("slug")) != str(slug):
        return no("STRIKE_IS_FOR_ANOTHER_MARKET", got=str(m.get("slug")))
    terms = m.get("assetPriceTerms")
    if not isinstance(terms, Mapping):
        return no("NO_ASSET_PRICE_TERMS")
    if str(terms.get("indexSymbol")) != INDEX_SYMBOL:
        return no("UNEXPECTED_INDEX", got=str(terms.get("indexSymbol")))
    if str(terms.get("marketType")) != "ASSET_PRICE_MARKET_TYPE_UP_DOWN":
        return no("NOT_AN_UP_DOWN_MARKET", got=str(terms.get("marketType")))
    sym = (terms.get("asset") or {}).get("symbol") if isinstance(
        terms.get("asset"), Mapping) else None
    if str(sym).lower() != "btc":
        return no("NOT_BTC", got=str(sym))
    ws, we = _epoch(terms.get("windowStart")), _epoch(terms.get("windowEnd"))
    if ws is None or we is None:
        return no("WINDOW_UNREADABLE")
    if abs(ws - float(window_start)) > 0.5:
        return no("STRIKE_WINDOW_MISALIGNED", strike_window_start=ws,
                  wanted_window_start=float(window_start))
    if abs((we - ws) - float(window_s)) > 0.5:
        return no("WINDOW_IS_NOT_FIFTEEN_MINUTES" if float(window_s) == WINDOW_S
                  else "WINDOW_LENGTH_UNEXPECTED", window_s=we - ws,
                  wanted_window_s=float(window_s))
    ptb = terms.get("priceToBeat")
    k = _fin(ptb.get("value")) if isinstance(ptb, Mapping) else None
    if k is None or k <= 0:
        return no("STRIKE_NOT_PUBLISHED_YET")
    if isinstance(ptb, Mapping) and str(ptb.get("currency")) != "USD":
        return no("STRIKE_NOT_IN_USD", got=str(ptb.get("currency")))
    return {"ok": True, "slug": str(slug), "strike": k,
            "window_start": ws, "window_end": we,
            "index": INDEX_SYMBOL, "source": "VENUE_PRICE_TO_BEAT"}


# ============================================================== probability
def sigma_per_second(series: List[Tuple[int, float]], *, now_bucket: int,
                     lookback_s: int = 900) -> Optional[float]:
    """Per-second dollar volatility of the RAW index, from one-minute moves.

    60 * (A(t) - A(t-1)) is exactly R(t) - R(t-60), whose variance is
    60 * sigma^2. None when there is too little history to say."""
    by = dict(series)
    d2: List[float] = []
    for t in range(now_bucket - lookback_s, now_bucket + 1):
        a, b = by.get(t), by.get(t - 1)
        if a is None or b is None:
            continue
        d2.append((AVERAGING_S * (a - b)) ** 2)
    if len(d2) < MIN_HISTORY_S:
        return None
    return math.sqrt(sum(d2) / len(d2) / AVERAGING_S)


def settlement_distribution(*, a_now: float, a_prev: float, sigma: float,
                            seconds_left: float) -> Dict[str, float]:
    """Mean and sd of the settlement average, `seconds_left` before it."""
    tau = float(seconds_left)
    da = a_now - a_prev
    tc = min(tau, float(AVERAGING_S))
    mean = a_now + da * tc * (119.0 - tc) / 120.0
    if tau >= AVERAGING_S:
        var = tau - 39.5 + 5.0
    else:
        var = tau * (tau + 1.0) * (2.0 * tau + 1.0) / 21600.0 \
            + 5.0 * tau / AVERAGING_S
    return {"mean": mean, "sd": sigma * math.sqrt(max(var, 1e-9)),
            "raw_ahead_of_average": mean - a_now}


def probability_up(*, series: List[Tuple[int, float]], strike: float,
                   window_end: float, now: float,
                   received_t: Optional[float] = None,
                   rtt_s: Optional[float] = None) -> Dict[str, Any]:
    """P(settles UP) from the index, the strike and the time left -- or why
    there is none. `series` is [(bucket_start_epoch, value)], oldest first."""
    def no(reason: str, **kw: Any) -> Dict[str, Any]:
        return dict(kw, ok=False, p_up=None, reason=reason,
                    decision_t=float(now))
    if not series:
        return no("NO_INDEX")
    k = _fin(strike)
    if k is None or k <= 0:
        return no("NO_STRIKE")
    bucket, a_now = series[-1]
    a_now = _fin(a_now)
    if a_now is None or a_now <= 0:
        return no("INDEX_UNREADABLE")
    # a bucket stamped t covers [t, t+1): it has ENDED at t+1
    age = float(now) - (float(bucket) + 1.0)
    if age > MAX_AGE_S:
        return no("INDEX_STALE", index_age_s=age, bucket_t=bucket)
    if age < -CLOCK_SKEW_S:
        return no("CLOCK_MISALIGNED", index_age_s=age, bucket_t=bucket)
    settle_bucket = float(window_end) - 1.0
    tau = settle_bucket - float(bucket)
    if tau <= 0:
        return no("WINDOW_OVER", seconds_left=tau)
    if tau > WINDOW_S + AVERAGING_S:
        return no("WINDOW_NOT_STARTED", seconds_left=tau)
    by = dict(series)
    a_prev = by.get(bucket - 1)
    if a_prev is None:
        return no("INDEX_GAP", missing_bucket=bucket - 1)
    sig = sigma_per_second(series, now_bucket=bucket)
    if sig is None:
        return no("TOO_LITTLE_HISTORY_FOR_VOLATILITY")
    sig_used = max(sig, SIGMA_REL_FLOOR * a_now) * VOL_INFLATION
    dist = settlement_distribution(a_now=a_now, a_prev=float(a_prev),
                                   sigma=sig_used, seconds_left=tau)
    z = (dist["mean"] - k) / dist["sd"]
    p = 0.5 * math.erfc(-z / math.sqrt(2.0))
    if not math.isfinite(p):
        return no("PROBABILITY_NOT_FINITE")
    return {
        "ok": True, "p_up": min(1.0, max(0.0, p)), "reason": None,
        "decision_t": float(now), "bucket_t": int(bucket),
        "index_age_s": age, "received_t": received_t,
        "receive_lag_s": (None if received_t is None
                          else float(received_t) - (float(bucket) + 1.0)),
        "rtt_s": rtt_s, "index": a_now, "strike": k,
        "distance": a_now - k, "expected_settlement": dist["mean"],
        "raw_ahead_of_average": dist["raw_ahead_of_average"],
        "sd": dist["sd"], "z": z, "seconds_left": tau,
        "sigma_per_second": sig, "sigma_used": sig_used,
        "vol_inflation": VOL_INFLATION, "source": "VENUE_INDEX_BRTI_60S",
    }


# ===================================================================== feed
class Reference:
    """The polling feed. One daemon thread; every read is a copy."""

    def __init__(self, *, fetch: Callable[[str], Any] = _http_json,
                 clock: Callable[[], float] = time.time,
                 gateway: str = GATEWAY, poll_s: float = 1.0,
                 window_s: float = WINDOW_S) -> None:
        self._fetch, self._clock = fetch, clock
        #: the length of the markets this feed prices: 900 for BTC15m (the
        #: default, unchanged), 300 for the BTC5m runner. A strike for a
        #: window of any other length is refused.
        self.window_s = float(window_s)
        self._gateway, self._poll_s = gateway.rstrip("/"), float(poll_s)
        self._lock = threading.Lock()
        self._series: Deque[Tuple[int, float]] = collections.deque()
        self._received_t: Optional[float] = None
        self._rtt_s: Optional[float] = None
        self._strikes: Dict[str, Dict[str, Any]] = {}
        self._want: Optional[Tuple[str, float]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.stats: Dict[str, int] = collections.Counter()

    # ------------------------------------------------------------- control
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="l5-btc-reference")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def want(self, slug: str, window_start: float) -> None:
        """Name the market whose strike should be kept ready."""
        with self._lock:
            self._want = (str(slug), float(window_start))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except BaseException as exc:                       # noqa: BLE001
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                self.stats["poll_error:%s" % type(exc).__name__] += 1
            self._stop.wait(self._poll_s)

    # --------------------------------------------------------------- polls
    def poll_once(self) -> None:
        self._poll_index()
        self._poll_strike()

    def _poll_index(self) -> None:
        now = self._clock()
        with self._lock:
            last = self._series[-1][0] if self._series else None
        start = int(now) - KEEP_S if last is None else last + 1
        start = max(start, int(now) - KEEP_S)
        t0 = self._clock()
        doc = self._fetch(
            "%s/v1/asset-prices/history?assets=crypto:btc"
            "&bucket=ASSET_PRICE_BUCKET_1S&from=%d&to=%d"
            % (self._gateway, start, int(now) + 2))
        t1 = self._clock()
        rows: List[Tuple[int, float]] = []
        for s in (doc.get("series") or []) if isinstance(doc, Mapping) else []:
            asset = s.get("asset") if isinstance(s, Mapping) else None
            if not isinstance(asset, Mapping) or \
                    str(asset.get("symbol")).lower() != "btc" or \
                    str(s.get("currency")) != "USD":
                self.stats["index_series_rejected"] += 1
                continue
            for c in s.get("candles") or []:
                ts, v = c.get("timestamp"), _fin(c.get("close"))
                # only a bucket built from the full minute is the series
                if not isinstance(ts, int) or v is None or v <= 0 or \
                        c.get("tickCount") != AVERAGING_S:
                    self.stats["index_bucket_rejected"] += 1
                    continue
                rows.append((ts, v))
        rows.sort()
        with self._lock:
            for ts, v in rows:
                if not self._series or ts > self._series[-1][0]:
                    self._series.append((ts, v))
            while self._series and \
                    self._series[0][0] < int(now) - KEEP_S - 5:
                self._series.popleft()
            if rows:
                self._received_t, self._rtt_s = t1, t1 - t0
        self.stats["index_polls"] += 1

    def _poll_strike(self) -> None:
        with self._lock:
            want = self._want
            have = self._strikes.get(want[0]) if want else None
        if want is None or (have is not None and have.get("ok")):
            return
        if have is not None and \
                self._clock() - float(have.get("checked_t", 0)) < 2.0:
            return
        slug, ws = want
        got = parse_strike(self._fetch("%s/v1/market/slug/%s"
                                       % (self._gateway, slug)),
                           slug=slug, window_start=ws,
                           window_s=self.window_s)
        got["checked_t"] = self._clock()
        with self._lock:
            self._strikes[slug] = got
            for old in [k for k in self._strikes if k != slug][:-3]:
                self._strikes.pop(old, None)
        self.stats["strike_polls"] += 1

    # --------------------------------------------------------------- reads
    def series(self) -> List[Tuple[int, float]]:
        with self._lock:
            return list(self._series)

    def strike(self, slug: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            got = self._strikes.get(str(slug))
            return dict(got) if got is not None else None

    def estimate(self, slug: str, window_start: float,
                 now: Optional[float] = None) -> Dict[str, Any]:
        """P(UP) for exactly this market right now, or the reason for none."""
        now = self._clock() if now is None else float(now)
        self.want(slug, window_start)
        k = self.strike(slug)
        if k is None:
            return {"ok": False, "p_up": None, "decision_t": now,
                    "reason": "STRIKE_NOT_FETCHED_YET"}
        if not k.get("ok"):
            return {"ok": False, "p_up": None, "decision_t": now,
                    "reason": str(k.get("reason")), "strike_record": k}
        if abs(float(k["window_start"]) - float(window_start)) > 0.5:
            return {"ok": False, "p_up": None, "decision_t": now,
                    "reason": "STRIKE_WINDOW_MISALIGNED"}
        with self._lock:
            series = list(self._series)
            rcv, rtt = self._received_t, self._rtt_s
        out = probability_up(series=series, strike=k["strike"],
                             window_end=k["window_end"], now=now,
                             received_t=rcv, rtt_s=rtt)
        out["slug"] = str(slug)
        out["strike_source"] = k.get("source")
        return out
