import asyncio
from decimal import Decimal as D

from src import gemini_l5_pair_q1 as m


def test_choose_pair_joins_top_when_edge_is_already_safe(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0"))
    out = m.choose_pair(D("0.47"), D("0.51"), min_edge=D("0.02"))
    assert out is not None
    assert out["yes"] == D("0.47")
    assert out["no"] == D("0.49")
    assert out["backoff"] == D("0")
    assert out["edge"] == D("0.04")


def test_choose_pair_backs_off_only_as_much_as_needed(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    out = m.choose_pair(D("0.48"), D("0.50"), min_edge=D("0.01"))
    assert out is not None
    assert out["yes"] == D("0.47")
    assert out["no"] == D("0.49")
    assert out["backoff"] == D("0.01")
    assert out["edge"] == D("0.02")


def test_choose_pair_fails_closed_when_two_cent_backoff_is_not_enough(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    assert m.choose_pair(D("0.55"), D("0.44"), min_edge=D("0.03")) is None


def test_pair_shape_hard_caps_same_side_inventory_to_q1():
    specs = [
        {"side": "YES", "price": D("0.47"), "id": "y"},
        {"side": "NO", "price": D("0.49"), "id": "n"},
    ]
    fills = {"y": D("1"), "n": D("0")}
    out = m.summarize(specs, fills)
    assert D(out["yes_qty"]) <= D("1")
    assert D(out["no_qty"]) <= D("1")
    assert D(out["residual_qty"]) == D("1")


def test_disarmed_makes_no_venue_call(monkeypatch):
    monkeypatch.setattr(m, "armed", lambda: False)
    async def boom():
        raise AssertionError("venue call while disarmed")
    monkeypatch.setattr(m, "_gemini_terms_ok", boom)
    asyncio.run(m.run())
