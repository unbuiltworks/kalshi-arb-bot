"""The windowed DESC walk that defeats the /activity offset cap."""

from __future__ import annotations

from src.polymarket_l5 import config
from src.polymarket_l5.cache import RawCache
from src.polymarket_l5.fetch import fetch_activity_windowed


def make_opener(fills):
    """Serve `fills` (list of ts-desc dicts) honoring limit/offset/end params."""
    import urllib.parse

    calls = []

    def opener(url, timeout):
        calls.append(url)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        end = int(q["end"]) if "end" in q else None
        limit, offset = int(q["limit"]), int(q["offset"])
        eligible = [f for f in fills if end is None or f["timestamp"] <= end]
        if offset >= 5000:
            return 400, None, "max historical activity offset of 5000 exceeded"
        return 200, eligible[offset : offset + limit], ""

    opener.calls = calls
    return opener


def fill(ts):
    return {"type": "TRADE", "timestamp": ts, "transactionHash": f"0x{ts}", "side": "BUY",
            "size": 1, "price": 0.5, "conditionId": "0xc", "asset": "a"}


def test_walk_slides_past_the_offset_cap(tmp_path):
    # 12,000 fills, one per second, newest first: needs 3 windows at 4500/window.
    fills = [fill(1_000_000_000 - i) for i in range(12_000)]
    cache = RawCache(tmp_path / "raw")
    report = fetch_activity_windowed(
        "0xabc", cache=cache, opener=make_opener(fills), max_total_pages=40,
    )
    assert report["stopped_because"] == "history_exhausted"
    # Every fill retrieved at least once (boundary seconds may repeat; dedup's job).
    seen = set()
    for batch in cache.payload_batches("activity"):
        for item in batch:
            seen.add(item["timestamp"])
    assert len(seen) == 12_000
    assert report["oldest_ts"] == 1_000_000_000 - 11_999
    assert report["newest_ts"] == 1_000_000_000


def test_walk_respects_total_page_budget(tmp_path):
    fills = [fill(1_000_000_000 - i) for i in range(30_000)]
    cache = RawCache(tmp_path / "raw")
    report = fetch_activity_windowed(
        "0xabc", cache=cache, opener=make_opener(fills), max_total_pages=10,
    )
    assert report["stopped_because"] == "max_total_pages"
    assert report["pages"] == 10


def test_walk_stops_when_cursor_cannot_advance(tmp_path):
    # Pathological: more fills in one second than a whole window can page.
    fills = [dict(fill(999), transactionHash=f"0x{i}") for i in range(6_000)]
    cache = RawCache(tmp_path / "raw")
    report = fetch_activity_windowed(
        "0xabc", cache=cache, opener=make_opener(fills), max_total_pages=40,
    )
    assert report["stopped_because"] == "cursor_stalled"
