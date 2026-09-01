import asyncio
from decimal import Decimal as D

from src import gemini_short_crypto_btc5m_maker_canary_q1_v3 as canary


class _Result:
    def __init__(self, quantity: str):
        self.quantity = quantity

    def as_dict(self):
        return {
            "filled_quantity": self.quantity,
            "status": "FILLED",
        }


class _Session:
    last_params = None

    async def connect(self):
        return None

    async def close(self):
        return None

    async def place_fok(self, params, *, terminal_timeout=3.0):
        type(self).last_params = dict(params)
        return _Result(str(params["quantity"]))


def test_maker_client_ids_remain_distinct_under_gemini_cap():
    trade_id = "crypto-gem-btc5m-aws-local-btc5m-q1-20260827T172716Z-1253"
    yes_id, no_id = canary.maker_client_ids(trade_id)
    assert yes_id != no_id
    assert yes_id.endswith("-yes")
    assert no_id.endswith("-no")
    assert len(yes_id) <= 36
    assert len(no_id) <= 36
    assert canary.maker_client_ids(trade_id) == (yes_id, no_id)


def test_fractional_flatten_uses_exact_observed_quantity(monkeypatch):
    monkeypatch.setattr(canary.base, "GeminiTradingSession", _Session)
    original = {
        "symbol": "GEMI-BTC05M2608271700-UP",
        "eventOutcome": "YES",
    }
    out = asyncio.run(canary.flatten_exact(original, D("0.37")))
    assert out["complete"] is True
    assert out["requested_quantity"] == "0.37"
    assert _Session.last_params["quantity"] == "0.37"
    assert _Session.last_params["side"] == "SELL"
    assert _Session.last_params["type"] == "MARKET"
    assert _Session.last_params["timeInForce"] == "IOC"
    assert _Session.last_params["eventOutcome"] == "YES"


def test_flatten_observed_closes_both_fractional_sides(monkeypatch):
    calls = []

    async def fake_flatten(params, quantity):
        calls.append((params["eventOutcome"], D(str(quantity))))
        return {
            "requested_quantity": str(quantity),
            "filled_quantity": str(quantity),
            "status": "FILLED",
            "complete": True,
        }

    monkeypatch.setattr(canary, "flatten_exact", fake_flatten)
    out = asyncio.run(canary.flatten_observed(
        yes_params={"eventOutcome": "YES"},
        no_params={"eventOutcome": "NO"},
        yes_fill=D("0.23"),
        no_fill=D("0.41"),
    ))
    assert out["complete"] is True
    assert calls == [("YES", D("0.23")), ("NO", D("0.41"))]
