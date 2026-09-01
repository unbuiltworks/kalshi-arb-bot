from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_dynamic_complement_q1_v2 as v2


def test_safe_to_rest_current_requires_edge_and_non_crossing_maker():
    assert v2._safe_to_rest_current(
        missing="YES",
        filled_price=D("0.33"),
        current_price=D("0.59"),
        yes_bid=D("0.59"),
        yes_ask=D("0.67"),
        increment=D("0.01"),
    ) is True
    # 66c would be maker-passive but breaks the 2c completed-pair floor.
    assert v2._safe_to_rest_current(
        missing="YES",
        filled_price=D("0.33"),
        current_price=D("0.66"),
        yes_bid=D("0.59"),
        yes_ask=D("0.67"),
        increment=D("0.01"),
    ) is False


@pytest.mark.asyncio
async def test_v2_does_not_cancel_when_no_better_safe_target(monkeypatch):
    monkeypatch.setattr(v2, "RESCUE_SECONDS", 0.02)
    monkeypatch.setattr(v2, "REPRICE_INTERVAL_SECONDS", 1.0)
    cancel_calls = []

    async def fake_wait(_maker, *, client_id, deadline):
        return D("0")

    async def fake_top(_symbol):
        # Current YES is already 60c. With NO filled at 35c and a 2c floor,
        # there is only one cent of room, but a 61c ask caps maker at 60c.
        return D("0.58"), D("10"), D("0.61"), D("10")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "emit", lambda *args, **kwargs: None)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.35"),
        complement_initial_id="yes0", complement_initial_price=D("0.60"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=["yes0"], no_ids=["no0"],
    )
    assert out["paired"] is False
    # Only the final timeout cancellation is allowed. V2 must not cancel merely
    # because it checked the book and found no strictly better safe target.
    assert cancel_calls == ["yes0"]


@pytest.mark.asyncio
async def test_v2_cancel_reposts_only_after_better_target_and_can_pair(monkeypatch):
    cancel_calls = []
    posts = []

    async def fake_wait(_maker, *, client_id, deadline):
        if posts and client_id == posts[-1][2]:
            return D("1")
        return D("0")

    async def fake_top(_symbol):
        # NO filled at 33c; YES can improve from 59c to 63c and still keep 2c.
        return D("0.59"), D("10"), D("0.67"), D("10")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def fake_post(_maker, *, symbol, outcome, price, client_id):
        posts.append((outcome, price, client_id))
        return {"status": 200}, {"clientOrderId": client_id, "filledQuantity": "0"}, 7.0

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "_post_maker", fake_post)
    monkeypatch.setattr(v2.v1, "emit", lambda *args, **kwargs: None)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    yes_ids = ["yes0"]
    no_ids = ["no0"]
    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=yes_ids, no_ids=no_ids,
    )
    assert out["paired"] is True
    assert cancel_calls == ["yes0"]
    assert posts[0][0] == "YES"
    assert posts[0][1] == D("0.63")
    assert posts[0][2] in yes_ids
    assert no_ids == ["no0"]


@pytest.mark.asyncio
async def test_v2_keeps_resting_complement_when_pre_cancel_depth_is_temporarily_missing(monkeypatch):
    monkeypatch.setattr(v2, "REPRICE_INTERVAL_SECONDS", 0.0)
    cancel_calls = []
    posts = []
    emitted = []
    top_calls = {"n": 0}

    async def fake_wait(_maker, *, client_id, deadline):
        if posts and client_id == posts[-1][2]:
            return D("1")
        return D("0")

    async def fake_top(_symbol):
        top_calls["n"] += 1
        if top_calls["n"] == 1:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return D("0.59"), D("10"), D("0.67"), D("10")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def fake_post(_maker, *, symbol, outcome, price, client_id):
        posts.append((outcome, price, client_id))
        return {"status": 200}, {"clientOrderId": client_id, "filledQuantity": "0"}, 7.0

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "_post_maker", fake_post)
    monkeypatch.setattr(v2.v1, "emit", lambda name, **kwargs: emitted.append((name, kwargs)))

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    yes_ids = ["yes0"]
    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=yes_ids, no_ids=["no0"],
    )

    assert out["paired"] is True
    # The first missing-depth check must not cancel the live complement. Cancellation
    # occurs only later, once a fresh book proves a strictly better safe target.
    assert cancel_calls == ["yes0"]
    assert posts[0][1] == D("0.63")
    assert any(
        name == "DYNAMIC_RESCUE_DEPTH_UNAVAILABLE"
        and data["action"] == "KEEP_EXISTING_COMPLEMENT_AND_RETRY"
        and data["complement_resting"] is True
        for name, data in emitted
    )


@pytest.mark.asyncio
async def test_v2_waits_for_fresh_depth_if_post_cancel_revalidation_temporarily_fails(monkeypatch):
    monkeypatch.setattr(v2, "REPRICE_INTERVAL_SECONDS", 0.0)
    cancel_calls = []
    posts = []
    emitted = []
    top_calls = {"n": 0}

    async def fake_wait(_maker, *, client_id, deadline):
        if posts and client_id == posts[-1][2]:
            return D("1")
        return D("0")

    async def fake_top(_symbol):
        top_calls["n"] += 1
        if top_calls["n"] == 2:
            raise RuntimeError("no fresh Gemini BTC5m depth")
        return D("0.59"), D("10"), D("0.67"), D("10")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def fake_post(_maker, *, symbol, outcome, price, client_id):
        posts.append((outcome, price, client_id))
        return {"status": 200}, {"clientOrderId": client_id, "filledQuantity": "0"}, 7.0

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "_post_maker", fake_post)
    monkeypatch.setattr(v2.v1, "emit", lambda name, **kwargs: emitted.append((name, kwargs)))
    monkeypatch.setattr(v2.asyncio, "sleep", no_sleep)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    yes_ids = ["yes0"]
    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=yes_ids, no_ids=["no0"],
    )

    assert out["paired"] is True
    assert cancel_calls == ["yes0"]
    assert top_calls["n"] >= 3
    assert len(posts) == 1
    assert posts[0][1] == D("0.63")
    assert any(
        name == "DYNAMIC_RESCUE_DEPTH_UNAVAILABLE"
        and data["action"] == "WAIT_FOR_FRESH_DEPTH_WITHOUT_BLIND_REPOST"
        and data["complement_resting"] is False
        for name, data in emitted
    )


@pytest.mark.asyncio
async def test_v2_persistent_depth_loss_times_out_and_cancels_original_complement(monkeypatch):
    monkeypatch.setattr(v2, "RESCUE_SECONDS", 0.01)
    monkeypatch.setattr(v2, "REPRICE_INTERVAL_SECONDS", 0.0)
    cancel_calls = []
    posts = []

    async def fake_wait(_maker, *, client_id, deadline):
        return D("0")

    async def fake_top(_symbol):
        raise RuntimeError("no fresh Gemini BTC5m depth")

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def fake_post(*args, **kwargs):
        posts.append((args, kwargs))
        raise AssertionError("must not blindly post without fresh depth")

    monkeypatch.setattr(v2.v1, "_wait_for_specific_fill", fake_wait)
    monkeypatch.setattr(v2.v1.base, "fresh_top", fake_top)
    monkeypatch.setattr(v2.v1, "_cancel", fake_cancel)
    monkeypatch.setattr(v2.v1, "_post_maker", fake_post)
    monkeypatch.setattr(v2.v1, "emit", lambda *args, **kwargs: None)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    out = await v2._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=["yes0"], no_ids=["no0"],
    )

    assert out == {
        "status": "DYNAMIC_RESCUE_EXPIRED_ONE_SIDED",
        "paired": False,
        "hard_halt": False,
        "reprices": 0,
    }
    assert cancel_calls == ["yes0"]
    assert posts == []


@pytest.mark.asyncio
async def test_v2_does_not_mask_unexpected_runtime_errors(monkeypatch):
    async def broken_top(_symbol):
        raise RuntimeError("authentication contract changed")

    monkeypatch.setattr(v2.v1.base, "fresh_top", broken_top)
    monkeypatch.setattr(v2.v1, "emit", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="authentication contract changed"):
        await v2._fresh_top_or_none(
            "GEMI-ETH05M-X-UP",
            missing="NO",
            current_id="no0",
            current_price=D("0.43"),
            phase="TEST",
        )