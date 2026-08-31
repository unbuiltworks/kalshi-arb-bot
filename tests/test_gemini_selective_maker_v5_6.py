from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from src import gemini_adaptive_maker_executor_v5_3 as v53
from src.gemini_prediction_depth_cache_v5_3 import DepthSnapshot
from src import gemini_selective_maker_v5_6 as v56
from src import gemini_selective_maker_v5_6_deadman as deadman


def event():
    return v56.Event(
        ticker="BTC15M2608310030",
        symbol="GEMI-BTC15M2608310030-UP",
        expiry_ms=2_000_000_000_000,
        quantity_minimum=D("0.01"),
        quantity_increment=D("0.01"),
        price_increment=D("0.01"),
    )


def snap(*, bid="0.48", ask="0.50", bid_size="100", ask_size="100"):
    return DepthSnapshot(
        bids=((D(bid), D(bid_size)), (D("0.47"), D("100"))),
        asks=((D(ask), D(ask_size)), (D("0.51"), D("100"))),
        age_ms=10.0,
        received_ns=1,
        sequence=1,
    )


def tape(yes: int, no: int):
    t = v56.GeminiTradeTape30("GEMI-BTC15M2608310030-UP")
    for _ in range(yes):
        t.add("YES", D("1"))
    for _ in range(no):
        t.add("NO", D("1"))
    return t


def test_trade_aggressor_maps_to_maker_outcome():
    assert v56.maker_outcome_from_trade({"m": True}) == "YES"
    assert v56.maker_outcome_from_trade({"m": False}) == "NO"
    assert v56.maker_outcome_from_trade({"m": "true"}) == "YES"
    assert v56.maker_outcome_from_trade({}) is None


def test_policy_d_requires_full_30_trade_tape_and_contra_threshold():
    short = tape(20, 9)
    assert not short.ready
    assert short.contra_outcome() is None
    balanced = tape(16, 14)
    assert balanced.ready
    assert balanced.contra_outcome() is None
    heavy_yes = tape(20, 10)
    assert heavy_yes.contra_outcome() == "NO"
    heavy_no = tape(10, 20)
    assert heavy_no.contra_outcome() == "YES"


def test_selective_plan_uses_contra_side_and_hard_campaign_bound(monkeypatch):
    monkeypatch.setattr(v53.v5, "max_campaign_loss", lambda: D("0.60"))
    monkeypatch.setattr(v53.v5, "max_rescue_loss", lambda: D("0.30"))
    plan = v56.select_safe_plan(event(), snap=snap(), tape=tape(20, 10))
    assert plan is not None
    assert plan.outcome == "NO"
    assert plan.quote == D("0.50")
    assert plan.quantity == D("1")
    assert plan.catastrophic_loss <= D("0.60")
    assert plan.rescue_route.safe


def test_price_floor_blocks_toxic_sub_30c_selected_quote(monkeypatch):
    monkeypatch.setattr(v53.v5, "max_campaign_loss", lambda: D("1"))
    monkeypatch.setattr(v53.v5, "max_rescue_loss", lambda: D("0.30"))
    # Tape is heavy NO, so policy wants YES; YES top bid is only 29c.
    assert v56.select_safe_plan(event(), snap=snap(bid="0.29", ask="0.40"), tape=tape(10, 20)) is None


def test_rescue_depth_is_mandatory_even_when_signal_passes(monkeypatch):
    monkeypatch.setattr(v53.v5, "max_campaign_loss", lambda: D("1"))
    monkeypatch.setattr(v53.v5, "max_rescue_loss", lambda: D("0.30"))
    # Selected NO rescue crosses YES ask; top ask has less than q1.
    assert v56.select_safe_plan(event(), snap=snap(ask_size="0.25"), tape=tape(20, 10)) is None


def test_selective_planner_never_calls_legacy_pair_edge_planner(monkeypatch):
    monkeypatch.setattr(v53.v5, "max_campaign_loss", lambda: D("0.60"))
    monkeypatch.setattr(v53.v5, "max_rescue_loss", lambda: D("0.30"))
    monkeypatch.setattr(v53.policy, "select_plan", lambda **_: (_ for _ in ()).throw(AssertionError("pair planner called")))
    plan = v56.select_safe_plan(event(), snap=snap(), tape=tape(20, 10))
    assert plan is not None


def test_quote_ids_preserve_window_and_quote_identity():
    approval = "very-long-live-approval-id-that-used-to-collide-after-truncation"
    ids = {v56.quote_trade_id(approval, window, quote) for window in (1, 2, 4) for quote in (1, 2, 20)}
    assert len(ids) == 9
    assert all(len(value) <= 24 for value in ids)


def test_watchdog_heartbeat_age():
    now = datetime(2026, 8, 31, 0, 30, tzinfo=timezone.utc)
    state = {"heartbeat_at_utc": (now - timedelta(seconds=12.5)).isoformat()}
    assert deadman.heartbeat_age_seconds(state, now=now) == pytest.approx(12.5)
