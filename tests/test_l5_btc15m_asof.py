"""As-of BTC15m inputs: causal, fail-closed, and settlement-correct.

No network, no corpus dependency: every series here is built in the test.
"""
from __future__ import annotations

import math

import pytest

from analysis.l5.btc15m_asof import (MINUTE_S, SETTLE_AVERAGE_S, WINDOW_S,
                                     Minute, MinuteSeries, asof_snapshot,
                                     effective_horizon_seconds,
                                     minutes_from_coinbase_rows, settled_value,
                                     strike)
from analysis.l5.btc15m_probability import estimate_up

WS = 1788000000.0          # minute-aligned window start


def _flat(n_before: int = 90, n_after: int = 15, px: float = 80000.0,
          step: float = 0.0, ws: float = WS, wobble: float = 40.0) -> MinuteSeries:
    """A tape of `n_before` minutes before `ws` and `n_after` inside it.

    `wobble` is a deterministic zig-zag so the tape has real residual
    volatility: a perfectly linear ramp has none once drift is removed, which
    collapses every probability to 0 or 1 and makes the fixture degenerate
    rather than the estimator wrong.
    """
    out = {}
    for i in range(-n_before, n_after + 1):
        t = ws + i * MINUTE_S
        p = px + step * i + wobble * (1 if i % 2 else -1)
        out[t] = Minute(start=t, low=p - 1.0, high=p + 1.0, open=p, close=p)
    return MinuteSeries(out)


# ----------------------------------------------------- settlement functional
def test_strike_is_the_minute_ending_at_the_window_open():
    s = _flat(step=10.0)
    assert strike(s, WS) == s.minute_starting(WS - SETTLE_AVERAGE_S).mean


def test_settled_value_is_the_minute_ending_at_the_window_close():
    s = _flat()
    assert settled_value(s, WS) == s.minute_starting(
        WS + WINDOW_S - SETTLE_AVERAGE_S).mean


def test_effective_horizon_is_forty_seconds_short_of_expiry():
    # (window_end - 60 - t) + 20 == (window_end - t) - 40
    for off in (0.0, 120.0, 480.0, 839.0):
        assert effective_horizon_seconds(WS, WS + off) == pytest.approx(
            WINDOW_S - off - 40.0)


def test_horizon_shrinks_monotonically_through_the_window():
    prev = math.inf
    for off in range(0, 840, 30):
        cur = effective_horizon_seconds(WS, WS + off)
        assert cur < prev
        prev = cur


# --------------------------------------------------------------- causality
def test_only_candles_closed_by_the_decision_are_visible():
    s = _flat()
    m = s.closed_at_or_before(WS + 125.0)
    assert m is not None and m.end <= WS + 125.0
    assert m.start == WS + 60.0


def test_a_candle_closing_exactly_at_the_decision_is_visible():
    s = _flat()
    m = s.closed_at_or_before(WS + 120.0)
    assert m.end == WS + 120.0


def test_future_candles_never_reach_the_snapshot():
    s = _flat(step=100.0)
    snap = asof_snapshot(s, WS, WS + 300.0)
    # the as-of spot is the close of the minute ending at +300, never later
    assert snap.spot == s.minute_starting(WS + 240.0).close
    assert snap.spot_timestamp == WS + 300.0


def test_snapshot_is_accepted_by_the_committed_estimator():
    s = _flat(step=5.0)
    p = estimate_up(asof_snapshot(s, WS, WS + 300.0))
    assert 0.0 < p < 1.0


# ------------------------------------------------------------- fail closed
def test_refuses_a_decision_inside_the_terminal_average():
    s = _flat()
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + WINDOW_S - SETTLE_AVERAGE_S)
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + WINDOW_S)


def test_refuses_a_decision_before_the_window_opens():
    s = _flat()
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS - 1.0)


def test_refuses_when_the_strike_minute_is_missing():
    s = _flat()
    del s._m[WS - SETTLE_AVERAGE_S]
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0)


def test_refuses_a_stale_as_of_observation():
    s = _flat()
    for t in (WS + 180.0, WS + 240.0):
        del s._m[t]
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0, max_age_seconds=60.0)


def test_refuses_a_gap_inside_the_volatility_lookback():
    s = _flat()
    del s._m[WS - 600.0]
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0)


def test_refuses_too_short_a_lookback():
    s = _flat(n_before=10)
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0)


def test_refuses_zero_realized_volatility():
    s = _flat(step=0.0, wobble=0.0)           # perfectly flat closes
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0)


def test_refuses_nonfinite_times():
    s = _flat(step=5.0)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            asof_snapshot(s, WS, bad)


def test_refuses_an_unaligned_window_start():
    s = _flat(step=5.0)
    with pytest.raises(ValueError):
        asof_snapshot(s, WS + 1.0, WS + 300.0)


# ------------------------------------------------------- series validation
def test_rejects_an_inconsistent_candle():
    with pytest.raises(ValueError):
        MinuteSeries({WS: Minute(start=WS, low=10.0, high=1.0,
                                 open=5.0, close=5.0)})


def test_rejects_a_nonpositive_price():
    with pytest.raises(ValueError):
        MinuteSeries({WS: Minute(start=WS, low=0.0, high=1.0,
                                 open=0.0, close=1.0)})


def test_rejects_an_unaligned_candle():
    with pytest.raises(ValueError):
        MinuteSeries({WS + 1.0: Minute(start=WS + 1.0, low=1.0, high=2.0,
                                       open=1.5, close=1.5)})


def test_coinbase_row_order_is_low_high_open_close():
    s = minutes_from_coinbase_rows([[WS, 76695.99, 76746.03, 76696.0,
                                     76736.25, 2.3]])
    m = s.minute_starting(WS)
    assert (m.low, m.high, m.open, m.close) == (76695.99, 76746.03,
                                                76696.0, 76736.25)
    assert m.mean == pytest.approx((76695.99 + 76746.03 + 76696.0
                                    + 76736.25) / 4.0)


# ------------------------------------------------------------- directional
def test_spot_above_strike_gives_probability_above_a_half():
    s = _flat(step=20.0)                      # rising tape
    assert estimate_up(asof_snapshot(s, WS, WS + 420.0)) > 0.5


def test_spot_below_strike_gives_probability_below_a_half():
    s = _flat(step=-20.0)                     # falling tape
    assert estimate_up(asof_snapshot(s, WS, WS + 420.0)) < 0.5


def test_probability_moves_toward_the_certain_side_as_time_runs_out():
    s = _flat(step=20.0)
    early = estimate_up(asof_snapshot(s, WS, WS + 120.0))
    late = estimate_up(asof_snapshot(s, WS, WS + 780.0))
    assert late > early


# ------------------------------------------- the two reference regimes
# analysis/l5/btc15m_settlement_id.py measures each stream separately with
# the same candidate set and the winner inverts: btc-usd settles on the
# terminal value against the window open, the TWAP stream on a trailing-60s
# mean at each endpoint. These pin that the module follows the stream.
from analysis.l5.btc15m_asof import SPOT_SOURCE, TWAP_SOURCE, averaging


def test_the_twap_stream_averages_and_the_spot_stream_does_not():
    assert averaging(TWAP_SOURCE) is True
    assert averaging(SPOT_SOURCE) is False
    assert averaging("https://data.chain.link/streams/" + TWAP_SOURCE) is True
    assert averaging("https://data.chain.link/streams/" + SPOT_SOURCE) is False


def test_an_unrecognised_stream_raises_instead_of_guessing():
    for bad in ("", "btc-usd-twap-300s", "eth-usd", "https://example.com/x"):
        with pytest.raises(ValueError):
            averaging(bad)


def test_the_spot_strike_is_the_window_open_not_the_prior_minute():
    s = _flat(step=20.0)
    assert strike(s, WS, SPOT_SOURCE) == s.minute_starting(WS).open
    assert strike(s, WS, TWAP_SOURCE) == s.minute_starting(
        WS - SETTLE_AVERAGE_S).mean
    assert strike(s, WS, SPOT_SOURCE) != strike(s, WS, TWAP_SOURCE)


def test_the_spot_settled_value_is_a_close_not_a_mean():
    s = _flat(step=20.0)
    last = s.minute_starting(WS + WINDOW_S - SETTLE_AVERAGE_S)
    assert settled_value(s, WS, SPOT_SOURCE) == last.close
    assert settled_value(s, WS, TWAP_SOURCE) == last.mean


def test_the_spot_horizon_is_time_to_expiry_unadjusted():
    for off in (0.0, 300.0, 839.0):
        assert effective_horizon_seconds(WS, WS + off, SPOT_SOURCE) == \
            pytest.approx(WINDOW_S - off)
        assert effective_horizon_seconds(WS, WS + off, TWAP_SOURCE) == \
            pytest.approx(WINDOW_S - off - 40.0)


def test_the_stream_reaches_the_snapshot():
    s = _flat(step=20.0)
    a = asof_snapshot(s, WS, WS + 300.0, resolution_source=SPOT_SOURCE)
    b = asof_snapshot(s, WS, WS + 300.0, resolution_source=TWAP_SOURCE)
    assert a.strike != b.strike
    assert a.seconds_left == pytest.approx(b.seconds_left + 40.0)
    assert a.spot == b.spot                      # the as-of spot is the same


def test_an_unrecognised_stream_refuses_a_snapshot():
    s = _flat(step=20.0)
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0, resolution_source="eth-usd")


# ------------------------------------- the live reference moved off Chainlink
# Read from gateway.polymarket.us on 2026-11-06: the live BTC15m markets now
# settle on CF Benchmarks BRTI, not Chainlink. The functional form carried
# over but the index did not, so nothing calibrated against Chainlink may be
# applied to it. These pin the refusal rather than a silent fallback.
from analysis.l5.btc15m_asof import BRTI_SOURCE, LIVE_WINDOW_START_FIELD


def test_the_live_brti_reference_is_refused_by_name():
    for src in ("brti", "BRTI",
                "https://www.cfbenchmarks.com/data/indices/BRTI"):
        with pytest.raises(ValueError, match="BRTI"):
            averaging(src)


def test_brti_is_not_silently_treated_as_the_twap_source():
    """The shapes match; the calibration does not. It must still refuse."""
    with pytest.raises(ValueError):
        averaging(BRTI_SOURCE)
    s = _flat(step=20.0)
    with pytest.raises(ValueError):
        asof_snapshot(s, WS, WS + 300.0, resolution_source=BRTI_SOURCE)


def test_the_live_window_start_field_is_recorded_as_game_start_time():
    # the historical corpus used eventStartTime with endDate == start + 900s;
    # live rows use gameStartTime and their endDate is start + 45 minutes
    assert LIVE_WINDOW_START_FIELD == "gameStartTime"
