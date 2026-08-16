from decimal import Decimal

from src.gemini_kaiko_proxy_calibration import SpotState


def test_spot_state_piecewise_constant_60s_twap():
    state = SpotState("BTC")
    state.update(bid=Decimal("99"), ask=Decimal("101"), receive_ms=0, exchange_event_ns=None)
    state.update(bid=Decimal("109"), ask=Decimal("111"), receive_ms=30_000, exchange_event_ns=None)

    twap, span_ms = state.twap(now_ms=60_000, window_ms=60_000)

    assert span_ms == 60_000
    assert twap == Decimal("105")


def test_spot_state_reports_short_observed_span_when_started_inside_window():
    state = SpotState("ETH")
    state.update(bid=Decimal("1999"), ask=Decimal("2001"), receive_ms=30_000, exchange_event_ns=None)

    twap, span_ms = state.twap(now_ms=60_000, window_ms=60_000)

    assert span_ms == 30_000
    assert twap == Decimal("2000")
