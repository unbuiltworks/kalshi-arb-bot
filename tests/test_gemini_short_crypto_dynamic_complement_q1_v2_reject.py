from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_dynamic_complement_q1_v2 as v2


@pytest.mark.asyncio
async def test_rejected_replacement_id_is_not_added_to_settlement_chain(monkeypatch):
    cancel_calls = []

    async def fake_wait(_maker, *, client_id, deadline):
        return D("0")

    async def fake_top(_symbol):
        return D("0.59"), D("10"), D("0.67"), D("10")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def reject_post(_maker, *, symbol, outcome, price, client_id):
        return {"status": 400}, None, 7.0

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "_post_maker", reject_post)
    monkeypatch.setattr(v2.v1, "emit", lambda *args, **kwargs: None)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    yes_ids = ["yes0"]
    no_ids = ["no0"]
    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-reject",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=yes_ids, no_ids=no_ids,
    )

    assert out["status"] == "DYNAMIC_COMPLEMENT_REPOST_REJECTED"
    assert out["hard_halt"] is True
    assert cancel_calls == ["yes0"]
    # The rejected replacement has no authoritative terminal order and therefore
    # must not contaminate the settlement identity chain.
    assert yes_ids == ["yes0"]
    assert no_ids == ["no0"]
