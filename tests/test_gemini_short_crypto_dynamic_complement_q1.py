from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_dynamic_complement_q1 as dyn


def test_previous_strike_distance_entry_is_now_rejected():
    # 60c + 35c + two 1c maker fees leaves only 3c. The dynamic build requires
    # 5c at entry so it does not spend its completion budget before the first fill.
    model = {
        "yes_quote": D("0.60"),
        "no_quote": D("0.35"),
        "modeled_edge": D("0.03"),
    }
    assert dyn.entry_eligible(model) is False


def test_raw_prior_book_preserves_completion_budget_and_is_eligible():
    model = dyn.raw_pair_model(D("0.59"), D("0.67"))
    assert model["yes_quote"] == D("0.59")
    assert model["no_quote"] == D("0.33")
    assert model["modeled_edge"] == D("0.06")
    assert dyn.entry_eligible(model) is True


def test_entry_requires_five_cents_and_incentive_band():
    assert dyn.entry_eligible({
        "yes_quote": D("0.60"), "no_quote": D("0.33"), "modeled_edge": D("0.05")
    }) is True
    assert dyn.entry_eligible({
        "yes_quote": D("0.60"), "no_quote": D("0.33"), "modeled_edge": D("0.04")
    }) is False
    assert dyn.entry_eligible({
        "yes_quote": D("0.81"), "no_quote": D("0.10"), "modeled_edge": D("0.07")
    }) is False


def test_replacement_client_ids_are_deterministic_distinct_and_bounded():
    a = dyn.replacement_client_id("trade-abc", "YES", 1)
    b = dyn.replacement_client_id("trade-abc", "YES", 2)
    c = dyn.replacement_client_id("trade-abc", "NO", 1)
    assert a == dyn.replacement_client_id("trade-abc", "YES", 1)
    assert len({a, b, c}) == 3
    assert all(len(x) <= 36 for x in (a, b, c))
    with pytest.raises(ValueError):
        dyn.replacement_client_id("trade-abc", "YES", dyn.MAX_REPRICES + 1)


def test_fill_price_uses_execution_event_and_safe_fallback():
    assert dyn.fill_price_from_event({"L": "0.34"}, D("0.33")) == D("0.34")
    assert dyn.fill_price_from_event({}, D("0.33")) == D("0.33")
    assert dyn.fill_price_from_event({"L": "1.4"}, D("0.33")) == D("0.33")


def test_safe_inventory_never_allows_more_than_q1_per_outcome():
    assert dyn.safe_inventory(D("1"), D("1")) is True
    assert dyn.safe_inventory(D("1"), D("0")) is True
    assert dyn.safe_inventory(D("1.01"), D("0")) is False
    assert dyn.safe_inventory(D("0"), D("-0.01")) is False


@pytest.mark.asyncio
async def test_aggregate_ids_fails_visibility_separately_from_quantity(monkeypatch):
    rows = {
        "a": {"filledQuantity": "0"},
        "b": {"filledQuantity": "1"},
    }

    async def fake_order(cid, _symbol):
        return rows.get(cid)

    monkeypatch.setattr(dyn.base, "_order_by_client", fake_order)
    qty, missing = await dyn._aggregate_ids(["a", "b", "c"], "SYM")
    assert qty == D("1")
    assert missing == ["c"]


@pytest.mark.asyncio
async def test_dynamic_rescue_reposts_only_missing_side_at_safe_price(monkeypatch):
    cancel_calls = []
    posted = []

    async def fake_cancel(_maker, *, client_id, symbol, row=None):
        cancel_calls.append(client_id)
        return {"ok": True, "method": "TEST"}, 5.0, row, D("0")

    async def fake_top(_symbol):
        # NO filled at 33c. Missing YES can improve from 59c to 63c and retain 2c.
        return D("0.59"), D("10"), D("0.67"), D("10")

    async def fake_post(_maker, *, symbol, outcome, price, client_id):
        posted.append((outcome, price, client_id))
        return {"status": 200}, {"clientOrderId": client_id, "filledQuantity": "0"}, 8.0

    async def fake_wait(_maker, *, client_id, deadline):
        return D("1") if posted and client_id == posted[-1][2] else D("0")

    monkeypatch.setattr(dyn, "_cancel", fake_cancel)
    monkeypatch.setattr(dyn.base, "fresh_top", fake_top)
    monkeypatch.setattr(dyn, "_post_maker", fake_post)
    monkeypatch.setattr(dyn, "_wait_for_specific_fill", fake_wait)

    class Event:
        symbol = "GEMI-ETH05M-X-UP"
        price_increment = D("0.01")
        seconds_to_expiry = 120.0

    yes_ids = ["yes0"]
    no_ids = ["no0"]
    out = await dyn._dynamic_rescue(
        object(), event=Event(), trade_id="trade-x",
        filled_outcome="NO", filled_price=D("0.33"),
        complement_initial_id="yes0", complement_initial_price=D("0.59"),
        complement_initial_row={"clientOrderId": "yes0"},
        yes_ids=yes_ids, no_ids=no_ids,
    )
    assert out["paired"] is True
    assert cancel_calls == ["yes0"]
    assert posted[0][0] == "YES"
    assert posted[0][1] == D("0.63")
    assert posted[0][2] in yes_ids
    assert no_ids == ["no0"]
