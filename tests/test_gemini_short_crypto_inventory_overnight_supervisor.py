from decimal import Decimal as D
import os

from src import gemini_short_crypto_inventory_overnight_supervisor as s


def test_overnight_dual_key_arm(monkeypatch):
    monkeypatch.setenv(s.OVERNIGHT_LIVE_ENV, "YES")
    monkeypatch.setenv(s.OVERNIGHT_ID_ENV, "night-1")
    monkeypatch.setenv(s.OVERNIGHT_ARM_ENV, "night-1")
    assert s.live_armed() is True
    monkeypatch.setenv(s.OVERNIGHT_ARM_ENV, "wrong")
    assert s.live_armed() is False


def test_hard_caps_are_bounded():
    assert s.HARD_MAX_RUNTIME_S == 8 * 60 * 60
    assert s.HARD_MAX_EXECUTED_CAMPAIGNS == 24
    assert s.HARD_MAX_REALIZED_LOSS == D("3.00")


def test_loss_cap_only_hits_at_or_below_negative_three():
    assert s.loss_cap_hit(D("-3.00")) is True
    assert s.loss_cap_hit(D("-3.01")) is True
    assert s.loss_cap_hit(D("-2.99")) is False
    assert s.loss_cap_hit(D("1.00")) is False


def test_child_ids_are_short_and_unique():
    a = s.child_canary_id("overnight-session-long", 1)
    b = s.child_canary_id("overnight-session-long", 2)
    assert a != b
    assert a.startswith("ov3-") and b.startswith("ov3-")
    assert len(a) < 40 and len(b) < 40


def test_only_exact_settlement_pending_error_is_retryable():
    assert s.settlement_pending(RuntimeError("Gemini settlement not posted yet for inventory canary"))
    assert not s.settlement_pending(RuntimeError("client-id/outcome mismatch"))
