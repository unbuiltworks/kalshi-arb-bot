from __future__ import annotations

from decimal import Decimal

import pytest

from src.sports_kp_execution import ExecutionLeg
from src.sports_kp_venue_hardening import HardenedKalshiExecutor, HardenedPolymarketExecutor

D = Decimal


def _leg(venue: str = "polymarket") -> ExecutionLeg:
    return ExecutionLeg(
        venue=venue,
        market_id="market-1",
        normalized_outcome="YES",
        native_outcome="YES",
        raw_outcome_price=D("0.40"),
        yes_book_limit=D("0.40"),
        source_side="YES",
    )


class _PolyMarkets:
    def retrieve_by_slug(self, slug):
        return {
            "slug": slug,
            "active": True,
            "closed": False,
            "archived": False,
            "orderBookEnabled": True,
            "minimumTradeQty": "0.01",
            "orderPriceMinTickSize": "0.01",
        }


class _PolyPortfolio:
    def __init__(self):
        self.position = D("0")

    def positions(self, params):
        slug = params["market"]
        return {"positions": {slug: {"netPosition": str(self.position)}}}


class _PolyOrders:
    def __init__(self, portfolio):
        self.portfolio = portfolio
        self.close_payload = None

    def close_position(self, payload):
        self.close_payload = payload
        self.portfolio.position = D("0")
        return {"id": "close-1", "executions": []}


class _PolyClient:
    def __init__(self):
        self.markets = _PolyMarkets()
        self.portfolio = _PolyPortfolio()
        self.orders = _PolyOrders(self.portfolio)

    def close(self):
        pass


@pytest.mark.asyncio
async def test_polymarket_recovery_uses_documented_automatic_close_payload():
    client = _PolyClient()
    client.portfolio.position = D("1")
    executor = HardenedPolymarketExecutor(client=client)
    result = await executor.emergency_flatten(_leg())
    assert result.status == "FLATTENED"
    assert client.orders.close_payload == {
        "marketSlug": "market-1",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
        "synchronousExecution": True,
        "maxBlockTime": "2",
        "slippageTolerance": {"ticks": 20},
    }


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self._body


class _KalshiClient:
    def __init__(self, *, trading=True, market_status="open", position="0"):
        self.trading = trading
        self.market_status = market_status
        self.position = position

    async def get(self, path, params=None):
        if path == "/exchange/status":
            return _Response({"exchange_active": True, "trading_active": self.trading})
        if path == "/markets/market-1":
            return _Response({"market": {"ticker": "market-1", "status": self.market_status}})
        if path == "/portfolio/positions":
            rows = [] if self.position == "0" else [{"ticker": "market-1", "position_fp": self.position}]
            return _Response({"market_positions": rows})
        raise AssertionError(path)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_kalshi_preflight_rejects_exchange_pause():
    executor = HardenedKalshiExecutor(client=_KalshiClient(trading=False))
    with pytest.raises(PermissionError, match="not currently trading"):
        await executor.preflight(_leg("kalshi"))


@pytest.mark.asyncio
async def test_kalshi_preflight_rejects_closed_market():
    executor = HardenedKalshiExecutor(client=_KalshiClient(market_status="closed"))
    with pytest.raises(PermissionError, match="not open"):
        await executor.preflight(_leg("kalshi"))


@pytest.mark.asyncio
async def test_kalshi_preflight_rejects_preexisting_position():
    executor = HardenedKalshiExecutor(client=_KalshiClient(position="1.00"))
    with pytest.raises(PermissionError, match="pre-existing"):
        await executor.preflight(_leg("kalshi"))


@pytest.mark.asyncio
async def test_kalshi_preflight_accepts_open_zero_position():
    executor = HardenedKalshiExecutor(client=_KalshiClient())
    await executor.preflight(_leg("kalshi"))
