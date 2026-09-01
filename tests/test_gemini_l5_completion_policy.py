from decimal import Decimal as D

from src import gemini_l5_completion_policy as p


def test_reserve_requires_one_cent_room_for_either_first_fill(monkeypatch):
    monkeypatch.setattr(p, "gemini_predictions_maker_fee", lambda price, qty: D("0.01"))
    # 47 + 49 + two 1c fees = 98c cost => 2c initial edge.  Spending one
    # cent on either complement leaves exactly the required 1c final edge.
    assert p.reserve_ok_for_either_first_fill(
        yes_price=D("0.47"), no_price=D("0.49"), min_final_edge=D("0.01")
    )
    # 48/49 has only 1c initial edge after fees and therefore no completion reserve.
    assert not p.reserve_ok_for_either_first_fill(
        yes_price=D("0.48"), no_price=D("0.49"), min_final_edge=D("0.01")
    )


def test_edge_ceiling_accounts_for_both_maker_fees(monkeypatch):
    monkeypatch.setattr(p, "gemini_predictions_maker_fee", lambda price, qty: D("0.01"))
    assert p.max_edge_safe_complement_price(
        filled_price=D("0.47"), min_final_edge=D("0.01")
    ) == D("0.50")
    assert p.completed_edge(filled_price=D("0.47"), complement_price=D("0.50")) == D("0.01")


def test_yes_maker_passive_ceiling_is_one_tick_below_yes_ask():
    assert p.maker_passive_ceiling(
        missing_outcome="YES", yes_bid=D("0.47"), yes_ask=D("0.50")
    ) == D("0.49")


def test_no_maker_passive_ceiling_is_one_tick_below_implied_no_taker():
    assert p.maker_passive_ceiling(
        missing_outcome="NO", yes_bid=D("0.47"), yes_ask=D("0.50")
    ) == D("0.52")


def test_completion_plan_spends_only_edge_safe_maker_room(monkeypatch):
    monkeypatch.setattr(p, "gemini_predictions_maker_fee", lambda price, qty: D("0.01"))
    out = p.complement_plan(
        filled_outcome="YES",
        filled_price=D("0.47"),
        current_complement_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_ask=D("0.51"),
        min_final_edge=D("0.01"),
    )
    assert out["action"] == "REPRICE"
    assert out["target_price"] == D("0.50")
    assert out["final_edge"] == D("0.01")


def test_completion_plan_never_crosses_maker_ceiling(monkeypatch):
    monkeypatch.setattr(p, "gemini_predictions_maker_fee", lambda price, qty: D("0"))
    out = p.complement_plan(
        filled_outcome="NO",
        filled_price=D("0.45"),
        current_complement_price=D("0.47"),
        yes_bid=D("0.47"),
        yes_ask=D("0.49"),
        min_final_edge=D("0.01"),
    )
    assert out["action"] == "REPRICE"
    assert out["target_price"] == D("0.48")
    assert out["target_price"] == out["passive_ceiling"]


def test_no_safe_improvement_holds_current_price(monkeypatch):
    monkeypatch.setattr(p, "gemini_predictions_maker_fee", lambda price, qty: D("0.01"))
    out = p.complement_plan(
        filled_outcome="YES",
        filled_price=D("0.48"),
        current_complement_price=D("0.49"),
        yes_bid=D("0.48"),
        yes_ask=D("0.50"),
        min_final_edge=D("0.01"),
    )
    assert out["action"] == "HOLD"
