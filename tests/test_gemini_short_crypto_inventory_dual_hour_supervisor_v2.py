from decimal import Decimal as D

from src import gemini_short_crypto_inventory_dual_hour_supervisor_v2 as s


def test_v2_dual_key_arm(monkeypatch):
    monkeypatch.setenv(s.LIVE_ENV, "YES")
    monkeypatch.setenv(s.SESSION_ID_ENV, "dual-v2")
    monkeypatch.setenv(s.SESSION_ARM_ENV, "dual-v2")
    assert s.live_armed() is True
    monkeypatch.setenv(s.SESSION_ARM_ENV, "wrong")
    assert s.live_armed() is False


def test_v2_hard_caps_unchanged():
    assert s.HARD_MAX_RUNTIME_S == 60 * 60
    assert s.HARD_MAX_EXECUTED_TOTAL == 4
    assert s.HARD_MAX_EXECUTED_PER_HORIZON == 2
    assert s.HARD_MAX_REALIZED_LOSS == D("1.00")


def test_v2_loss_cap():
    assert s.loss_cap_hit(D("-1.00")) is True
    assert s.loss_cap_hit(D("-1.01")) is True
    assert s.loss_cap_hit(D("-0.99")) is False
    assert s.loss_cap_hit(D("0.11")) is False


def test_v2_child_ids_encode_horizon():
    a = s.child_id("dual-session", "5m", 1)
    b = s.child_id("dual-session", "15m", 2)
    assert a != b
    assert "-5m-" in a
    assert "-15m-" in b
    assert len(a) < 40 and len(b) < 40


def test_v2_choose_balances_horizons():
    ready = [{"horizon": "5m"}, {"horizon": "15m"}]
    assert s._choose(ready, {"5m": 1, "15m": 0}, None)["horizon"] == "15m"
    assert s._choose(ready, {"5m": 0, "15m": 0}, "15m")["horizon"] == "5m"


def test_settlement_and_exact_history_lag_are_retryable():
    assert s.settlement_pending(RuntimeError("Gemini settlement not posted yet for inventory canary"))
    assert s.settlement_pending(RuntimeError("missing exact canary order history expected=2 found=1"))
    assert s.settlement_pending(RuntimeError("open reservation has zero authoritative fills; manual review required"))
    assert not s.settlement_pending(RuntimeError("client-id/outcome mismatch"))


def test_fill_signal_pending_is_a_valid_open_status():
    assert "FILL_SIGNAL_RECONCILIATION_PENDING" in s.VALID_OPEN_STATUSES
