"""Regression: optional stale-distance recovery must not strand confirmed inventory."""
from collections import Counter
from types import SimpleNamespace

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TSP


@pytest.mark.parametrize("residual,side,should_recover", [
    ("-1", "UP", False),  # held DOWN; UP is reducing quote
    ("1", "DOWN", False),  # held UP; DOWN is reducing quote
    ("-1", "DOWN", True),  # held DOWN; DOWN is opening quote
    ("1", "UP", True),  # held UP; UP is opening quote
    ("0", "UP", True),  # flat; both are opening quotes
])
def test_optional_distance_recovery_preserves_reducing_quote(
        monkeypatch, residual, side, should_recover):
    monkeypatch.setattr(R.AUTH, "class_budget_report",
                        lambda *args, **kwargs: {"OPEN": {"remaining": "2"}})
    monkeypatch.setattr(R.AUTH, "ledger_path", lambda: "/tmp/unused")
    monkeypatch.setattr(R.AUTH, "experiment_id", lambda: "test")
    called = []

    def fake_recover(**kwargs):
        called.append(kwargs)
        return {"recover": True, "reason": "STALE_BY_DISTANCE"}

    monkeypatch.setattr(TSP, "recover_stale_leg", fake_recover)
    runner = SimpleNamespace(
        transport=SimpleNamespace(tick_size="0.01"),
        book=SimpleNamespace(snapshot=lambda slug: {
            "bid": "0.50", "ask": "0.51", "stream_age_s": 0.1}),
        store=SimpleNamespace(residual=lambda slug: residual),
        stats=Counter(), acc_recoveries={})
    live = {"order-1": {
        "market": "market-1", "side": side, "price": "0.49",
        "qty": "1", "remaining": "1", "state": "OPEN",
        "t": "2026-11-07T01:00:13+00:00"}}
    result = R.acc_stale_by_distance(runner, 1794013240.0,
                                     "market-1", live)
    assert bool(result) is should_recover
    assert bool(called) is should_recover
    if not should_recover:
        assert runner.stats["acc_recover:REDUCING_LEG_DEFER_TO_COMPLETION"] == 1


def test_unknown_inventory_cannot_authorize_optional_cancel(monkeypatch):
    runner = SimpleNamespace(
        transport=SimpleNamespace(tick_size="0.01"),
        book=SimpleNamespace(snapshot=lambda slug: {
            "bid": "0.50", "ask": "0.51", "stream_age_s": 0.1}),
        store=SimpleNamespace(residual=lambda slug: (_ for _ in ()).throw(ValueError())),
        stats=Counter(), acc_recoveries={})
    monkeypatch.setattr(R.AUTH, "class_budget_report",
                        lambda *args, **kwargs: {"OPEN": {"remaining": "2"}})
    monkeypatch.setattr(R.AUTH, "ledger_path", lambda: "/tmp/unused")
    monkeypatch.setattr(R.AUTH, "experiment_id", lambda: "test")
    result = R.acc_stale_by_distance(runner, 1794013240.0, "market-1", {
        "order-1": {"market": "market-1", "side": "UP", "price": "0.49",
                    "qty": "1", "remaining": "1", "state": "OPEN",
                    "t": "2026-11-07T01:00:13+00:00"}})
    assert result == []
    assert runner.stats["acc_recover:RESIDUAL_UNREADABLE"] == 1


def test_mandatory_age_deadline_still_cancels_reducing_quote(monkeypatch):
    """Optional distance guard never overrides mandatory age-based cancellation."""
    market = "cpc-btc-updown-15m-2026-11-07-0100z"
    coid = "up-complement"
    row = {"market": market, "side": "UP", "price": "0.46",
           "qty": "1", "remaining": "1", "state": "OPEN",
           "t": "2026-11-07T01:00:13+00:00"}
    cancellations = []
    monkeypatch.setattr(R.AB, "slug_start", lambda slug: 1794013200.0)
    monkeypatch.setattr(TSP, "active", lambda slug: True)
    monkeypatch.setattr(TSP, "order_due", lambda *args: True)
    monkeypatch.setattr(R, "acc_stale_by_distance", lambda *args, **kwargs: [])
    monkeypatch.setattr(R, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(R.LX, "deadman", lambda store, transport, **kw:
                        cancellations.append(kw) or {"cancelled": [coid]})
    runner = SimpleNamespace(
        transport=SimpleNamespace(market_slug=market, armed=True),
        store=SimpleNamespace(live_orders=lambda: {coid: row}),
        poll_orders=lambda: [], win=Counter(), acc_recoveries={})
    result = R.Runner.maybe_acc_deadline(runner, 1794013330.0)
    assert cancellations == [{"reason": "acc_order_deadline", "only": [coid]}]
    assert result["overdue"] == [coid]
