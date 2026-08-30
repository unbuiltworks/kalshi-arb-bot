from __future__ import annotations

import pytest

from src.l5 import config
from src.l5.fetch import FetchStats, _page_sweep
from src.l5.http import FetchError


class Cache:
    def __init__(self):
        self.pages = []

    def append(self, *args):
        self.pages.append(args)


class DeepOffsetRejectingHttp:
    def get(self, endpoint, params):
        offset = int(params["offset"])
        if offset >= config.PAGE_LIMIT:
            raise FetchError(f"GET {endpoint}?offset={offset} -> HTTP 400")
        # Exactly one full page: enough to require a second request.
        return [
            {"timestamp": 10_000 - i, "id": i}
            for i in range(config.PAGE_LIMIT)
        ]


class FirstPageRejectingHttp:
    def get(self, endpoint, params):
        raise FetchError(f"GET {endpoint} -> HTTP 400")


def test_deep_offset_400_is_treated_as_effective_cap_not_fatal():
    stats = FetchStats(name="raw.jsonl", endpoint="https://example.test/activity")
    cache = Cache()
    exhausted, oldest = _page_sweep(
        DeepOffsetRejectingHttp(), cache, stats, {"user": "wallet"}, 10_000
    )
    assert exhausted is False
    assert oldest == 10_000 - (config.PAGE_LIMIT - 1)
    assert stats.pages == 1
    assert stats.records == config.PAGE_LIMIT
    assert stats.errors and "effective offset cap" in stats.errors[-1]


def test_first_page_400_still_fails_loudly():
    stats = FetchStats(name="raw.jsonl", endpoint="https://example.test/activity")
    with pytest.raises(FetchError):
        _page_sweep(FirstPageRejectingHttp(), Cache(), stats, {"user": "wallet"}, 10_000)
