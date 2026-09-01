from decimal import Decimal as D

from src import gemini_short_crypto_inventory_hour_supervisor as s


def test_hour_dual_key_arm(monkeypatch):
    monkeypatch.setenv(s.HOUR_LIVE_ENV, "YES")
    monkeypatch.setenv(s.HOUR_ID_ENV, "hour-1")
    monkeypatch.setenv(s.HOUR_ARM_ENV, "hour-1")
    assert s.live_armed() is True
    monkeypatch.setenv(s.HOUR_ARM_ENV, "wrong")
    assert s.live_armed() is False


def test_hour_hard_caps_are_bounded():
    assert s.HARD_MAX_RUNTIME_S == 60 * 60
    assert s.HARD_MAX_EXECUTED_CAMPAIGNS == 4
    assert s.HARD_MAX_REALIZED_LOSS == D("1.00")


def test_hour_loss_cap_only_hits_at_or_below_negative_one():
    assert s.loss_cap_hit(D("-1.00")) is True
    assert s.loss_cap_hit(D("-1.01")) is True
    assert s.loss_cap_hit(D("-0.99")) is False
    assert s.loss_cap_hit(D("1.00")) is False


def test_hour_child_ids_are_short_and_unique():
    a = s.child_canary_id("one-hour-session-long", 1)
    b = s.child_canary_id("one-hour-session-long", 2)
    assert a != b
    assert a.startswith("h1-") and b.startswith("h1-")
    assert len(a) < 40 and len(b) < 40


def test_hour_only_exact_settlement_pending_error_is_retryable():
    assert s.settlement_pending(RuntimeError("Gemini settlement not posted yet for inventory canary"))
    assert not s.settlement_pending(RuntimeError("client-id/outcome mismatch"))
