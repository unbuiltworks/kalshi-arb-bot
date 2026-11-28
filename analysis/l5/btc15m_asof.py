#!/usr/bin/env python3
"""Synchronized as-of inputs for the research-only BTC15m probability model.

Research only. No network, no orders, no credentials, and no I/O of any kind:
the caller passes an already-loaded reference series. Nothing here is a
settlement replay and nothing here is permission to trade.

WHAT THE MARKET ACTUALLY SETTLES ON, source first and then measured.

Polymarket's own market description for every `btc-updown-15m-*` window names
Chainlink BTC/USD as the resolution source and says the window resolves "Up"
if the end-of-range price is GREATER THAN OR EQUAL TO the price at the
beginning of the range -- so a tie resolves UP, and the strike is the
reference's own value at the window open, not a listed number. Windows from
2026-08-28 onward name a different stream, `btc-usd-twap-60s-streams`, and
their description says the comparison uses "the time-weighted average price
(TWAP) of Bitcoin ... of the time range specified in the title".

Read literally that would make the payoff an average over the whole 900 s.
Measured against 315 resolved windows it is not. Taking the 1-minute Coinbase
reference tape as a proxy and asking which functional's SIGN agrees with the
recorded resolution:

    trailing-60s mean at each endpoint      307/315   97.5%     <-- this one
    close of final minute vs window open    295/315   93.7%
    mean of the 15 in-window closes         268/315   85.1%

The window-wide average is the WORST of the three. The stream is a 60-second
rolling TWAP, so the market compares that stream's value AT the two endpoints:
a 60 s average ending at the window close against a 60 s average ending at the
window open. `mean of the 15 in-window closes` is what the description's words
say and it is 12.4 points worse, so the description is loose and the endpoint
reading is the one the data supports.

CONSEQUENCES FOR THE MODEL, both of which this module applies:

  * The strike is OBSERVED, not quoted: the trailing-60s mean ending at the
    window open. It is known by decision time, so it adds no uncertainty.
  * The settled value is an average over the final 60 s, not the price at one
    instant. For a driftless lognormal the variance of a continuous average
    over [T1, T1+D] is sigma^2 * (T1 + D/3), so the effective horizon from a
    decision at t is (window_end - 60 - t) + 20 -- forty seconds SHORTER than
    the naive time-to-expiry. Ignoring that overstates the horizon's variance
    and therefore pulls every probability toward 0.5.

THE 2.5% THAT DOES NOT AGREE IS NOT NOISE TO BE IGNORED. Coinbase is a PROXY;
the settlement source is Chainlink's own aggregate, and the 8 windows of 315
where the proxy's sign disagrees are the floor on how well ANY signal built
from this tape can do. The disagreement is concentrated exactly where the
money is: agreement is 62.5% when the move is under 1 bp and 97.3% above
10 bp. A near-the-money directional opinion is therefore the case the proxy
is least able to support, and that is the case this market mostly offers.

RESOLUTION. The reference tape is 1-MINUTE candles, so an as-of observation
can be up to 60 s old and a tick-tape staleness bound of 2 s would refuse
every call. `max_age_seconds` defaults to 60.0 for that reason. This is a
coarser instrument being reused, not a safety gate being relaxed: it is
reported in every snapshot as `reference_resolution_seconds`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

from analysis.l5.btc15m_probability import Snapshot
from analysis.l5.btc15m_volatility import Tick, realized_sigma

#: Length of the rolling TWAP the settlement stream publishes, in seconds.
SETTLE_AVERAGE_S = 60.0
#: Window length for the btc-updown-15m series, in seconds.
WINDOW_S = 900.0
#: Candle length of the reference tape, in seconds.
MINUTE_S = 60.0
#: Resolution sources seen in the corpus, and whether the 60 s endpoint
#: average applies. Both are endpoint comparisons; only the TWAP stream
#: averages, and a 60 s average of a 60 s candle is that candle.
TWAP_SOURCE = "btc-usd-twap-60s-streams"
SPOT_SOURCE = "btc-usd"
#: THE LIVE MARKET NO LONGER USES EITHER OF THE ABOVE. Read from the venue on
#: 2026-11-06 for cpc-btc-updown-15m-2026-11-06-0130z and -0145z, via
#: gateway.polymarket.us/v1/markets, the settlement text is:
#:
#:   "Outcome sourced from CF Benchmarks' Bitcoin Real-Time Index (BRTI) ...
#:    in the last minute before each of those two times, 60 BRTI prices are
#:    collected. The official value for that time is the simple average of
#:    these prices, rounded to the nearest 2 decimal places. Please note that
#:    this market is about the price according to CF Benchmarks' BRTI, not
#:    according to other sources or spot markets."
#:
#: So the FUNCTIONAL FORM carried over -- a trailing-60s average at both
#: endpoints, ties to Up -- but the REFERENCE INDEX did not: Chainlink is
#: gone and CF Benchmarks BRTI is in its place. Every fidelity number in
#: analysis/l5/btc15m_signal/VERDICT.md was measured against CHAINLINK
#: windows from 2026-03 to 2026-08 and does NOT transfer to BRTI.
#:
#: This constant exists so `averaging()` refuses it by name rather than by
#: falling through to "unrecognised". It is NOT wired into a strike or a
#: horizon, because no BRTI history has been collected and the proxy
#: agreement against BRTI is unmeasured.
BRTI_SOURCE = "brti"
#: Named for the same reason: the venue field carrying the window start
#: changed too. The historical corpus used `eventStartTime` with
#: `endDate` == start + 900 s. The live gateway rows use `gameStartTime` for
#: the window start, and their `endDate` is start + 45 MINUTES -- a market
#: housekeeping time, not the settlement time. Reading `endDate` as the
#: settlement instant would put it three windows late.
LIVE_WINDOW_START_FIELD = "gameStartTime"


@dataclass(frozen=True)
class Minute:
    """One reference candle. `start` is the candle's opening second."""
    start: float
    low: float
    high: float
    open: float
    close: float

    @property
    def end(self) -> float:
        return self.start + MINUTE_S

    @property
    def mean(self) -> float:
        """OHLC4, the cheapest unbiased stand-in for the minute's TWAP."""
        return (self.low + self.high + self.open + self.close) / 4.0


class MinuteSeries:
    """Causal lookups over a minute-aligned reference tape."""

    def __init__(self, minutes: Mapping[float, Minute]) -> None:
        self._m: Dict[float, Minute] = {float(k): v for k, v in minutes.items()}
        for start, m in self._m.items():
            if start % MINUTE_S:
                raise ValueError("candle start not minute-aligned")
            if not all(math.isfinite(v) for v in
                       (m.low, m.high, m.open, m.close, m.start)):
                raise ValueError("nonfinite candle")
            if m.low <= 0 or not (m.low <= m.open <= m.high
                                  and m.low <= m.close <= m.high):
                raise ValueError("candle is not internally consistent")
            if m.start != start:
                raise ValueError("candle keyed under the wrong start")

    def __len__(self) -> int:
        return len(self._m)

    def minute_starting(self, start: float) -> Optional[Minute]:
        return self._m.get(float(start))

    def closed_at_or_before(self, t: float) -> Optional[Minute]:
        """The most recent candle whose CLOSE is at or before `t`.

        Causal by construction: a candle closing after `t` is never returned,
        so no part of its interval lies in the future of the decision.
        """
        if not math.isfinite(t):
            raise ValueError("nonfinite time")
        start = math.floor(t / MINUTE_S) * MINUTE_S - MINUTE_S
        return self._m.get(float(start))


def averaging(resolution_source: str = TWAP_SOURCE) -> bool:
    """Does this stream average, per analysis/l5/btc15m_settlement_id.py?

    Measured separately on each stream, with the same candidate set, the
    winner inverts: `btc-usd` settles on the terminal value against the
    window open (96.2% of 400 windows) and the TWAP stream on a trailing-60s
    mean at each endpoint (95.5% of 421). An unrecognised source raises
    rather than defaulting, because guessing the functional silently mis-prices
    every window.
    """
    tail = str(resolution_source).rstrip("/").rsplit("/", 1)[-1]
    if tail == TWAP_SOURCE:
        return True
    if tail == SPOT_SOURCE:
        return False
    if BRTI_SOURCE in str(resolution_source).lower():
        # The live reference as of 2026-11-06. Refused BY NAME, not as an
        # unknown string, so the message says what is actually wrong: the
        # functional form matches the TWAP branch but no BRTI history has
        # been collected, so neither the proxy fidelity nor the calibration
        # transfers. Treating it as the TWAP source would silently reuse a
        # Chainlink-derived calibration on a different index.
        raise ValueError(
            "CF Benchmarks BRTI is the live reference and is NOT calibrated "
            "here: no BRTI history collected, proxy agreement unmeasured")
    raise ValueError("unrecognised resolution source: %r" % (resolution_source,))


def strike(series: MinuteSeries, window_start: float,
           resolution_source: str = TWAP_SOURCE) -> float:
    """The observed strike. Never a quoted number: the reference's own value.

    TWAP stream: the trailing-60s mean ending at the window open.
    Spot stream: the reference price AT the window open, proxied by the open
    of the minute that starts there.
    """
    if averaging(resolution_source):
        m = series.minute_starting(window_start - SETTLE_AVERAGE_S)
        if m is None:
            raise ValueError("strike minute absent from reference tape")
        return m.mean
    m = series.minute_starting(window_start)
    if m is None:
        raise ValueError("strike minute absent from reference tape")
    return m.open


def settled_value(series: MinuteSeries, window_start: float,
                  resolution_source: str = TWAP_SOURCE) -> float:
    """Proxy settled value at the window close.

    This is an OUTCOME. It is never an input to a probability and callers must
    not pass it, or anything derived from it, into `asof_snapshot`.
    """
    m = series.minute_starting(window_start + WINDOW_S - SETTLE_AVERAGE_S)
    if m is None:
        raise ValueError("settlement minute absent from reference tape")
    return m.mean if averaging(resolution_source) else m.close


def effective_horizon_seconds(window_start: float, decision_ts: float,
                              resolution_source: str = TWAP_SOURCE) -> float:
    """Variance-equivalent horizon to the settled value.

    On the TWAP stream the settled value is an average over the final 60 s.
    Var(ln of a continuous average over [T1, T1+D]) ~= sigma^2 (T1 + D/3),
    so the horizon is (time to the start of that interval) + D/3 -- forty
    seconds SHORTER than time-to-expiry. On the spot stream the settled value
    is a single instant and the horizon is time-to-expiry unadjusted.
    """
    if averaging(resolution_source):
        tau = ((window_start + WINDOW_S - SETTLE_AVERAGE_S) - decision_ts
               + SETTLE_AVERAGE_S / 3.0)
    else:
        tau = (window_start + WINDOW_S) - decision_ts
    if not math.isfinite(tau):
        raise ValueError("nonfinite horizon")
    return tau


def asof_snapshot(series: MinuteSeries, window_start: float,
                  decision_ts: float, *,
                  resolution_source: str = TWAP_SOURCE,
                  lookback_seconds: float = 3600.0,
                  min_returns: int = 30,
                  max_gap_seconds: float = 90.0,
                  max_age_seconds: float = 60.0) -> Snapshot:
    """Build an as-of Snapshot, or raise. Never returns a fallback.

    Uses only candles CLOSED at or before `decision_ts`. Refuses a decision
    at or after the start of the terminal averaging interval, because from
    that point the settled value is partly already determined and the
    lognormal horizon below no longer describes it.
    """
    for name, v in (("window_start", window_start),
                    ("decision_ts", decision_ts),
                    ("lookback_seconds", lookback_seconds),
                    ("max_gap_seconds", max_gap_seconds),
                    ("max_age_seconds", max_age_seconds)):
        if not math.isfinite(v):
            raise ValueError("nonfinite %s" % name)
    if window_start % MINUTE_S:
        raise ValueError("window start not minute-aligned")
    if max_age_seconds < 0:
        raise ValueError("invalid max_age_seconds")
    if decision_ts < window_start:
        raise ValueError("decision precedes the window open")
    t1 = window_start + WINDOW_S - SETTLE_AVERAGE_S
    if decision_ts >= t1:
        # On the TWAP stream the settled value is already partly determined
        # here. On the spot stream it is not, but the last minute's close is
        # the settled value itself, so an as-of observation inside it would
        # be reading the outcome. Both are refused.
        raise ValueError("decision inside the terminal averaging interval")

    k = strike(series, window_start, resolution_source)
    obs = series.closed_at_or_before(decision_ts)
    if obs is None:
        raise ValueError("no reference candle closed by the decision time")
    age = decision_ts - obs.end
    if age < 0:
        raise ValueError("reference candle closes after the decision")
    if age > max_age_seconds:
        raise ValueError("stale reference observation")

    ticks = []
    t = obs.start
    first = decision_ts - lookback_seconds
    while True:
        m = series.minute_starting(t)
        if m is None or m.end < first:
            break
        ticks.append(Tick(timestamp=m.end, price=m.close))
        t -= MINUTE_S
    ticks.reverse()
    sigma = realized_sigma(ticks, obs.end,
                           lookback_seconds=lookback_seconds,
                           max_latest_age_seconds=0.0,
                           min_returns=min_returns,
                           max_gap_seconds=max_gap_seconds)

    return Snapshot(spot=obs.close, strike=k,
                    seconds_left=effective_horizon_seconds(
                        window_start, decision_ts, resolution_source),
                    sigma_per_sqrt_second=sigma,
                    spot_timestamp=obs.end,
                    decision_timestamp=decision_ts,
                    max_staleness_seconds=max_age_seconds)


def minutes_from_coinbase_rows(rows: Sequence[Sequence[float]]) -> MinuteSeries:
    """Adapt the existing collector's candle rows: [t, low, high, open, close, volume]."""
    out: Dict[float, Minute] = {}
    for r in rows:
        if len(r) < 5:
            raise ValueError("short candle row")
        t = float(r[0])
        out[t] = Minute(start=t, low=float(r[1]), high=float(r[2]),
                        open=float(r[3]), close=float(r[4]))
    return MinuteSeries(out)
