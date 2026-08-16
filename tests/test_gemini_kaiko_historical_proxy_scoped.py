import pytest

from src import gemini_kaiko_historical_proxy_scoped as scoped


def test_kalshi_scope_is_exactly_four_15m_crypto_series():
    assert scoped.SERIES == ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M")


@pytest.mark.asyncio
async def test_trade_history_pages_with_since_tid_until_close_is_covered(monkeypatch):
    first = [
        {"tid": i, "timestampms": 1_000 + i, "price": "100"}
        for i in range(1, 501)
    ]
    second = [
        {"tid": 501, "timestampms": 80_000, "price": "101"},
    ]
    calls = []

    async def fake_page(http, *, asset, timestamp_ms=None, since_tid=None):
        calls.append((asset, timestamp_ms, since_tid))
        return first if len(calls) == 1 else second

    monkeypatch.setattr(scoped, "_request_trade_page", fake_page)
    rows, quarantined = await scoped._spot_trades_coverage_verified(
        object(), asset="BTC", start_ms=0
    )

    assert quarantined is False
    assert len(rows) == 501
    assert calls == [("BTC", 0, None), ("BTC", None, 500)]
