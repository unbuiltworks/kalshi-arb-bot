from decimal import Decimal

import pytest

from src import gemini_kalshi_xrp_maker_live_canary_fill_wait as wait

D = Decimal


def test_fillable_choose_quote_rejects_quote_behind_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.48")})
    assert wait.fillable_choose_quote(maker_best_bid=D("0.63")) is None


def test_fillable_choose_quote_accepts_quote_at_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.63")})
    result = wait.fillable_choose_quote(maker_best_bid=D("0.63"))
    assert result is not None
    assert result["maker_quote"] == D("0.63")


def test_fillable_choose_quote_accepts_quote_above_bid(monkeypatch):
    monkeypatch.setattr(wait, "_ORIGINAL_CHOOSE_QUOTE", lambda **kwargs: {"maker_quote": D("0.64")})
    result = wait.fillable_choose_quote(maker_best_bid=D("0.63"))
    assert result is not None


@pytest.mark.asyncio
async def test_pair_discovery_is_cached(monkeypatch):
    calls = 0

    async def fake_discover(http, seconds):
        nonlocal calls
        calls += 1
        return ["xrp-pair"]

    monkeypatch.setattr(wait, "_ORIGINAL_DISCOVER_PAIRS", fake_discover)
    monkeypatch.setattr(wait, "_PAIR_CACHE", None)
    first = await wait.cached_discover_pairs(object(), 30.0)
    second = await wait.cached_discover_pairs(object(), 30.0)
    assert first == second == ["xrp-pair"]
    assert calls == 1


@pytest.mark.asyncio
async def test_fee_metadata_is_cached(monkeypatch):
    calls = 0

    async def fake_fee(http, series):
        nonlocal calls
        calls += 1
        return {"series": series}

    monkeypatch.setattr(wait, "_ORIGINAL_FEE_METADATA", fake_fee)
    monkeypatch.setattr(wait, "_FEE_CACHE", {})
    first = await wait.cached_fee_metadata(object(), "KXXRP15M")
    second = await wait.cached_fee_metadata(object(), "KXXRP15M")
    assert first == second == {"series": "KXXRP15M"}
    assert calls == 1
