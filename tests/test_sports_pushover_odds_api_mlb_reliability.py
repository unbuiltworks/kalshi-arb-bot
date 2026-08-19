import asyncio

import httpx

import src.sports_pushover_odds_api_mlb as alerts
import src.universal_three_venue_sports as discovery


class _FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def get(self, url, **kwargs):
        del url, kwargs
        self.calls += 1
        return self.responses.pop(0)


def _response(status: int, payload=None, headers=None):
    request = httpx.Request("GET", "https://example.test/events")
    return httpx.Response(status, json=payload or {}, headers=headers or {}, request=request)


def test_kalshi_429_retries_then_uses_fresh_response(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(discovery.asyncio, "sleep", fake_sleep)
    http = _FakeHTTP([
        _response(429, headers={"Retry-After": "0"}),
        _response(200, {"events": [{"event_ticker": "KX-OK"}]}),
    ])

    result = asyncio.run(discovery._kalshi_json(http, "https://example.test/events"))

    assert result["events"][0]["event_ticker"] == "KX-OK"
    assert http.calls == 2
    assert slept == [discovery.KALSHI_429_BASE_DELAY_S]


def test_sportsbook_scan_continues_when_kalshi_discovery_fails(monkeypatch):
    async def fetch_sportsbooks(http, api_key):
        del http, api_key
        return [], {"remaining": 999}

    async def empty_source(http):
        del http
        return []

    async def failed_kalshi(http):
        del http
        raise RuntimeError("simulated Kalshi 429 exhaustion")

    async def price_counterparts(http, gemini, poly, kalshi):
        del http
        assert gemini == []
        assert poly == []
        assert kalshi == []
        return [], [], 0

    monkeypatch.setattr(alerts.base, "fetch_sportsbooks", fetch_sportsbooks)
    monkeypatch.setattr(alerts.base, "fetch_gemini_mlb", empty_source)
    monkeypatch.setattr(alerts.base, "fetch_polymarket_mlb", empty_source)
    monkeypatch.setattr(alerts.base, "fetch_kalshi_mlb", failed_kalshi)
    monkeypatch.setattr(alerts.base, "price_counterparts", price_counterparts)
    monkeypatch.setattr(alerts.base, "full_game_only_events", lambda rows: rows)
    monkeypatch.setattr(alerts.base, "sportsbook_quotes", lambda events, received_ms: [])
    monkeypatch.setattr(alerts.base, "find_cross_venue_locks", lambda *args, **kwargs: [])
    monkeypatch.setattr(alerts.base, "_dedupe", lambda rows: rows)
    monkeypatch.setattr(alerts.base, "_update_tally", lambda rows: {"scans": 1})

    result = asyncio.run(alerts.run_once("test-key"))

    assert result["prediction_sources_available"] == {
        "gemini": True,
        "polymarket": True,
        "kalshi": False,
    }
    assert "kalshi" in result["prediction_source_errors"]
    assert result["retail_vs_prediction_positive_locks"] == 0
    assert result["read_only"] is True
    assert result["order_entry"] is False
