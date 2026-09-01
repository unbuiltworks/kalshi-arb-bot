import asyncio
from decimal import Decimal as D

from src import gemini_l5_pair_q1 as m


def test_choose_pair_joins_top_when_edge_and_reserve_are_already_safe(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0"))
    monkeypatch.setattr(m.completion, "gemini_predictions_maker_fee", lambda p, q: D("0"))
    out = m.choose_pair(
        D("0.47"), D("0.51"), min_edge=D("0.02"),
        yes_queue=D("500"), no_queue=D("500"), tie_seed="even",
    )
    assert out is not None
    assert out["yes"] == D("0.47")
    assert out["no"] == D("0.49")
    assert out["total_backoff"] == D("0")
    assert out["edge"] == D("0.04")
    assert out["completion_reserve_ticks"] == 1


def test_choose_pair_rejects_old_one_cent_edge_shape_without_rescue_room(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    monkeypatch.setattr(m.completion, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    out = m.choose_pair(
        D("0.48"), D("0.50"), min_edge=D("0.01"),
        yes_queue=D("100"), no_queue=D("500"), tie_seed="x",
    )
    assert out is not None
    # The old controller chose 47/50 (1c edge) and had nothing left to spend
    # after a first fill.  The new controller reserves another cent by choosing
    # the balanced 47/49 pair (2c initial edge).
    assert out["yes"] == D("0.47")
    assert out["no"] == D("0.49")
    assert out["yes_backoff"] == D("0.01")
    assert out["no_backoff"] == D("0.01")
    assert out["total_backoff"] == D("0.02")
    assert out["edge"] == D("0.02")
    assert out["completion_reserve_ticks"] == 1


def test_balanced_backoff_beats_two_cents_on_one_side_when_total_is_equal(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    monkeypatch.setattr(m.completion, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    out = m.choose_pair(
        D("0.48"), D("0.50"), min_edge=D("0.01"),
        yes_queue=D("900"), no_queue=D("100"), tie_seed="x",
    )
    assert out is not None
    assert out["yes_backoff"] == D("0.01")
    assert out["no_backoff"] == D("0.01")
    assert max(D(str(out["yes_backoff"])), D(str(out["no_backoff"]))) == D("0.01")


def test_equal_queue_tie_break_is_deterministic(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    monkeypatch.setattr(m.completion, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    first = m.choose_pair(
        D("0.48"), D("0.50"), min_edge=D("0.01"),
        yes_queue=D("500"), no_queue=D("500"), tie_seed="cycle-a",
    )
    second = m.choose_pair(
        D("0.48"), D("0.50"), min_edge=D("0.01"),
        yes_queue=D("500"), no_queue=D("500"), tie_seed="cycle-a",
    )
    assert first == second
    assert D(str(first["total_backoff"])) == D("0.02")


def test_choose_pair_fails_closed_when_two_cents_per_side_is_not_enough(monkeypatch):
    monkeypatch.setattr(m, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    monkeypatch.setattr(m.completion, "gemini_predictions_maker_fee", lambda p, q: D("0.01"))
    assert m.choose_pair(
        D("0.55"), D("0.44"), min_edge=D("0.03"),
        yes_queue=D("1"), no_queue=D("1"), tie_seed="x",
    ) is None


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
