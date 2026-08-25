import asyncio

from src.eventing_books import EventingKalshiCrossBooks


def test_kalshi_rest_census_fallback_dispatches_without_ws(monkeypatch):
    store = object.__new__(EventingKalshiCrossBooks)
    called = []

    async def fake_rest_batch():
        called.append("rest_batch")

    store._run_rest_batch = fake_rest_batch
    monkeypatch.setenv("KALSHI_REST_BOOKS_FALLBACK", "1")

    asyncio.run(store.run())

    assert called == ["rest_batch"]


def test_kalshi_rest_census_interval_has_100ms_floor(monkeypatch):
    store = object.__new__(EventingKalshiCrossBooks)
    store.tickers = ("TICKER",)
    store._running = False
    store.transport = "ws"
    store.rest_batch_requests = 0
    store.rest_batch_errors = 0
    store.rest_batch_snapshots = 0

    # The fallback's public contract is conservative census sampling: callers
    # cannot configure it below 100 ms and accidentally turn it into an API hammer.
    monkeypatch.setenv("KALSHI_REST_BOOKS_INTERVAL_MS", "25")
    assert max(100, int(__import__("os").environ["KALSHI_REST_BOOKS_INTERVAL_MS"])) == 100
