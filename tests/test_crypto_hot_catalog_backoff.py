import asyncio

from src import gemini_kalshi_multi_ws_hot_catalog as catalog_mod


def test_failed_initial_discovery_is_singleflight_and_cooldown_blocks_retry(monkeypatch):
    calls = 0

    async def fail_discovery(_http, _max_window_skew_s):
        nonlocal calls
        calls += 1
        raise RuntimeError("rate limited")

    monkeypatch.setattr(catalog_mod, "_discover_pairs", fail_discovery)

    async def scenario():
        catalog = catalog_mod.BackgroundPairCatalog(refresh_s=30.0)
        assets = ["BTC", "ETH", "SOL", "XRP"]

        first = await asyncio.gather(
            *(catalog.current_pair(object(), asset) for asset in assets)
        )
        assert first == [None, None, None, None]
        assert calls == 1
        assert catalog._errors == 1
        assert catalog._next_refresh_not_before > 0

        # Immediate retries from all workers must hit the shared cooldown without
        # producing another network discovery burst.
        second = await asyncio.gather(
            *(catalog.current_pair(object(), asset) for asset in assets)
        )
        assert second == [None, None, None, None]
        assert calls == 1
        assert catalog._errors == 1

        if catalog._task is not None:
            catalog._task.cancel()
            await asyncio.gather(catalog._task, return_exceptions=True)

    asyncio.run(scenario())


def test_success_resets_failure_backoff(monkeypatch):
    calls = 0

    async def fail_then_succeed(_http, _max_window_skew_s):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("rate limited")
        return []

    monkeypatch.setattr(catalog_mod, "_discover_pairs", fail_then_succeed)

    async def scenario():
        catalog = catalog_mod.BackgroundPairCatalog(refresh_s=30.0)
        try:
            await catalog._refresh(object())
        except RuntimeError:
            pass
        else:
            raise AssertionError("first refresh must fail")

        assert catalog._failure_backoff_s == 1.0
        catalog._next_refresh_not_before = 0.0
        assert await catalog._refresh(object()) is True
        assert calls == 2
        assert catalog._failure_backoff_s == 0.5
        assert catalog._next_refresh_not_before == 0.0

    asyncio.run(scenario())
