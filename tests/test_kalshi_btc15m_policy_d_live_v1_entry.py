import asyncio

import httpx
import pytest

from src import kalshi_btc15m_policy_d_live_v1 as engine
from src import kalshi_btc15m_policy_d_live_v1_entry as entry


def test_install_replaces_only_engine_client_constructor():
    original = engine.KalshiClient
    try:
        entry.install()
        assert engine.KalshiClient is entry.FailClosedKalshiClient
    finally:
        engine.KalshiClient = original


def test_definite_create_reject_is_raised_once(monkeypatch):
    calls = {"n": 0}

    async def fake_post(self, path, **kwargs):
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad_order", "message": "definite reject"})

    monkeypatch.setattr(entry.BaseKalshiClient, "post", fake_post)
    client = entry.FailClosedKalshiClient(base_url="https://example.com")
    with pytest.raises(entry.DefiniteOrderCreateRejected) as exc:
        asyncio.run(client.post("/portfolio/events/orders", json={}))
    assert exc.value.status_code == 400
    assert exc.value.safe_body == {"error": "bad_order", "message": "definite reject"}
    assert calls["n"] == 1


def test_non_order_post_passthrough(monkeypatch):
    async def fake_post(self, path, **kwargs):
        return httpx.Response(400, json={"error": "other"})

    monkeypatch.setattr(entry.BaseKalshiClient, "post", fake_post)
    client = entry.FailClosedKalshiClient(base_url="https://example.com")
    response = asyncio.run(client.post("/some/other/path", json={}))
    assert response.status_code == 400
