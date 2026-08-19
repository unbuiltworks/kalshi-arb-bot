import pytest

from src import sports_kgp_account_preflight as preflight


def test_polymarket_available_cash_parses_usd_and_usdc_rows():
    body = {
        "balances": [
            {"currency": "USD", "available": "1.25"},
            {"currency": "USDC", "availableBalance": "4.50"},
            {"currency": "BTC", "balance": "100"},
        ]
    }
    assert preflight._poly_available(body) == preflight.D("4.50")


@pytest.mark.asyncio
async def test_ready_only_when_all_three_read_checks_pass(monkeypatch):
    async def gemini():
        return {"reachable": True, "terms_accepted": True, "minimum_cash_ready": True}

    async def kalshi():
        return {"reachable": True, "minimum_cash_ready": True}

    async def poly():
        return {"reachable": True, "minimum_cash_ready": True}

    monkeypatch.setattr(preflight, "_gemini", gemini)
    monkeypatch.setattr(preflight, "_kalshi", kalshi)
    monkeypatch.setattr(preflight, "_polymarket", poly)
    out = await preflight.run_preflight()
    assert out["ready"] is True
    assert out["read_only"] is True
    assert out["order_entry"] is False
    assert out["arm_token_created"] is False


@pytest.mark.asyncio
async def test_unaccepted_gemini_terms_fail_readiness(monkeypatch):
    async def gemini():
        return {"reachable": True, "terms_accepted": False, "minimum_cash_ready": True}

    async def ready():
        return {"reachable": True, "minimum_cash_ready": True}

    monkeypatch.setattr(preflight, "_gemini", gemini)
    monkeypatch.setattr(preflight, "_kalshi", ready)
    monkeypatch.setattr(preflight, "_polymarket", ready)
    out = await preflight.run_preflight()
    assert out["ready"] is False


@pytest.mark.asyncio
async def test_one_venue_failure_does_not_mutate_and_fails_ready(monkeypatch):
    async def gemini():
        return {"reachable": True, "terms_accepted": True, "minimum_cash_ready": True}

    async def failed():
        raise RuntimeError("read failed")

    async def ready():
        return {"reachable": True, "minimum_cash_ready": True}

    monkeypatch.setattr(preflight, "_gemini", gemini)
    monkeypatch.setattr(preflight, "_kalshi", failed)
    monkeypatch.setattr(preflight, "_polymarket", ready)
    out = await preflight.run_preflight()
    assert out["ready"] is False
    assert out["venues"]["kalshi"]["reachable"] is False
    assert out["order_entry"] is False
