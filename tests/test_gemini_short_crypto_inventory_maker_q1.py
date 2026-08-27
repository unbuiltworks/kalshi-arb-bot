from decimal import Decimal

from src import gemini_short_crypto_btc5m_inventory_maker_canary_q1 as inv

D = Decimal


def test_inventory_status_zero_releases():
    assert inv.inventory_status(D("0"), D("0")) == "NO_FILL_RELEASED"


def test_inventory_status_matched_waits_for_settlement():
    assert inv.inventory_status(D("1"), D("1")) == "MATCHED_INVENTORY_NOT_SETTLED"
    assert inv.inventory_status(D("0.37"), D("0.37")) == "MATCHED_INVENTORY_NOT_SETTLED"


def test_inventory_status_one_sided_is_valid_strategy_state():
    assert inv.inventory_status(D("1"), D("0")) == "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED"
    assert inv.inventory_status(D("0.37"), D("0")) == "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED"
    assert inv.inventory_status(D("1"), D("0.25")) == "ONE_SIDED_OR_IMBALANCED_INVENTORY_NOT_SETTLED"


def test_inventory_bounds_allow_fractional_but_never_above_q1():
    assert inv.safe_inventory(D("0"), D("0"))
    assert inv.safe_inventory(D("1"), D("1"))
    assert inv.safe_inventory(D("0.37"), D("0.82"))
    assert not inv.safe_inventory(D("1.01"), D("0"))
    assert not inv.safe_inventory(D("0"), D("1.01"))
    assert not inv.safe_inventory(D("-0.01"), D("0"))


def test_inventory_arm_is_separate_from_old_canary(monkeypatch):
    monkeypatch.setenv("GEMINI_SHORT_CRYPTO_LIVE_Q1", "YES")
    monkeypatch.setenv("GEMINI_SHORT_CRYPTO_CANARY_ID", "old")
    monkeypatch.setenv("GEMINI_SHORT_CRYPTO_CANARY_ARM", "old")
    monkeypatch.delenv(inv.LIVE_ENV, raising=False)
    monkeypatch.delenv(inv.CANARY_ID_ENV, raising=False)
    monkeypatch.delenv(inv.CANARY_ARM_ENV, raising=False)
    assert not inv.live_armed()

    monkeypatch.setenv(inv.LIVE_ENV, "YES")
    monkeypatch.setenv(inv.CANARY_ID_ENV, "inventory-1")
    monkeypatch.setenv(inv.CANARY_ARM_ENV, "inventory-1")
    assert inv.live_armed()
