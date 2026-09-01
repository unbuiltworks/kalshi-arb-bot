from decimal import Decimal as D

from src import gemini_short_crypto_inventory_dual_hour_supervisor as s
from src import gemini_short_crypto_inventory_local_budget as b5
from src import gemini_short_crypto_inventory15_local_budget as b15
from src import gemini_short_crypto_dual_horizon_settlement_reconcile as settle


def test_dual_hour_arm_namespace(monkeypatch):
    monkeypatch.setenv(s.LIVE_ENV, "YES")
    monkeypatch.setenv(s.SESSION_ID_ENV, "dual-1")
    monkeypatch.setenv(s.SESSION_ARM_ENV, "dual-1")
    assert s.live_armed() is True
    monkeypatch.setenv(s.SESSION_ARM_ENV, "wrong")
    assert s.live_armed() is False


def test_dual_hour_hard_caps():
    assert s.HARD_MAX_RUNTIME_S == 60 * 60
    assert s.HARD_MAX_EXECUTED_TOTAL == 4
    assert s.HARD_MAX_EXECUTED_PER_HORIZON == 2
    assert s.HARD_MAX_REALIZED_LOSS == D("1.00")


def test_loss_cap():
    assert s.loss_cap_hit(D("-1.00")) is True
    assert s.loss_cap_hit(D("-1.01")) is True
    assert s.loss_cap_hit(D("-0.99")) is False


def test_child_ids_encode_horizon_and_differ():
    a = s.child_id("dual-session", "5m", 1)
    b = s.child_id("dual-session", "15m", 2)
    assert a != b
    assert "-5m-" in a
    assert "-15m-" in b
    assert len(a) < 40 and len(b) < 40


def test_choose_prefers_underrepresented_horizon_then_alternates():
    ready = [{"horizon": "5m"}, {"horizon": "15m"}]
    assert s._choose(ready, {"5m": 1, "15m": 0}, None)["horizon"] == "15m"
    assert s._choose(ready, {"5m": 0, "15m": 0}, "15m")["horizon"] == "5m"


def test_dual_settlement_strategy_classifier():
    h, b = settle._strategy({"strategy": b5.STRATEGY, "symbol": "GEMI-BTC05M2608272100-UP", "event_ticker": "BTC05M2608272100"})
    assert h == "5m" and b is b5
    h, b = settle._strategy({"strategy": b15.STRATEGY, "symbol": "GEMI-BTC15M2608272115-UP", "event_ticker": "BTC15M2608272115"})
    assert h == "15m" and b is b15
