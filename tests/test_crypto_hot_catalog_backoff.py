import asyncio

from src import gemini_kalshi_multi_ws_hot_catalog as catalog_mod


def _patch_discovery_used_by_catalog(monkeypatch, replacement):
    monkeypatch.setitem(
        catalog_mod.BackgroundPairCatalog._refresh.__globals__,
        "_discover_pairs",
        replacement,
    )


def test_failed_initial_discovery_is_singleflight_and_cooldown_blocks_retry(monkeypatch):
    calls = 0

    async def fail_discovery(_http, _max_window_skew_s):
        nonlocal calls
        calls += 1
        raise RuntimeError("rate limited")

    _patch_discovery_used_by_catalog(monkeypatch, fail_discovery)

    async def scenario():
        catalog = catalog_mod.BackgroundPairCatalog(refresh_s=30.0)
        first = await asyncio.gather(
            *(catalog._refresh(object(), min_spacing_s=0.5) for _ in range(4)),
            return_exceptions=True,
        )
        assert isinstance(first[0], RuntimeError)
        assert first[1:] == [False, False, False]
        assert calls == 1
        assert catalog._errors == 1
        assert catalog._next_refresh_not_before > 0
        second = await asyncio.gather(
            *(catalog._refresh(object(), min_spacing_s=0.5) for _ in range(4))
        )
        assert second == [False, False, False, False]
        assert calls == 1
        assert catalog._errors == 1

    asyncio.run(scenario())


def test_success_resets_failure_backoff(monkeypatch):
    calls = 0

    async def fail_then_succeed(_http, _max_window_skew_s):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("rate limited")
        return []

    _patch_discovery_used_by_catalog(monkeypatch, fail_then_succeed)

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
