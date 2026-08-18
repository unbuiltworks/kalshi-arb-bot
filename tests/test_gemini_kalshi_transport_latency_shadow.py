import asyncio
from collections import deque

import pytest

from src import gemini_kalshi_transport_latency_shadow as latency


def test_summary_percentiles_and_max():
    values = deque([1.0, 2.0, 3.0, 4.0, 10.0])
    out = latency._summary(values)
    assert out["n"] == 5
    assert out["p50_ms"] == 2.0
    assert out["p95_ms"] == 4.0
    assert out["avg_ms"] == 4.0
    assert out["max_ms"] == 10.0


def test_summary_empty():
    assert latency._summary(deque()) == {
        "n": 0,
        "p50_ms": None,
        "p95_ms": None,
        "avg_ms": None,
        "max_ms": None,
    }


def test_order_mutations_are_fail_closed():
    with pytest.raises(RuntimeError, match="order mutation disabled"):
        asyncio.run(latency._mutation_forbidden())
    assert latency.GeminiTradingSession.place_fok is latency._mutation_forbidden
    assert latency.GeminiTradingSession.emergency_flatten_market is latency._mutation_forbidden
    assert latency.KalshiV2Executor.place is latency._mutation_forbidden


def test_gemini_ping_rtt_accepts_pong_waiter(monkeypatch):
    class FakeWS:
        def ping(self):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(None)
            return future

    class FakeSession:
        ws = FakeWS()

    value = asyncio.run(latency.gemini_ping_rtt_ms(FakeSession()))
    assert value >= 0
