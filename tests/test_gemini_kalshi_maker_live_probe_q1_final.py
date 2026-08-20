from decimal import Decimal

import pytest

from src import gemini_kalshi_maker_live_probe_q1 as base
from src import gemini_kalshi_maker_live_probe_q1_safe as safe
from src import gemini_kalshi_maker_live_probe_q1_safe_v2 as hardened
from src import gemini_kalshi_maker_live_probe_q1_final as final

D = Decimal


def test_preflight_defaults_on(monkeypatch):
    monkeypatch.delenv(final.PREFLIGHT_ENV, raising=False)
    assert final.preflight_requested() is True


def test_preflight_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv(final.PREFLIGHT_ENV, "NO")
    assert final.preflight_requested() is False


def test_probe_target_can_be_lowered_to_one_cent(monkeypatch):
    old_base, old_safe, old_hardened = base.TARGET_PROFIT, safe.TARGET_PROFIT, hardened.TARGET_PROFIT
    try:
        monkeypatch.setenv(final.TARGET_ENV, "0.01")
        target = final.configure_target_profit()
        assert target == D("0.01")
        assert base.TARGET_PROFIT == target
        assert safe.TARGET_PROFIT == target
        assert hardened.TARGET_PROFIT == target
    finally:
        base.TARGET_PROFIT = old_base
        safe.TARGET_PROFIT = old_safe
        hardened.TARGET_PROFIT = old_hardened


def test_probe_target_refuses_below_one_cent(monkeypatch):
    monkeypatch.setenv(final.TARGET_ENV, "0.009")
    with pytest.raises(ValueError):
        final.configure_target_profit()


def test_probe_target_refuses_above_three_cents(monkeypatch):
    monkeypatch.setenv(final.TARGET_ENV, "0.031")
    with pytest.raises(ValueError):
        final.configure_target_profit()


def test_probe_policy_can_use_join(monkeypatch):
    old = base.POLICY
    try:
        monkeypatch.setenv(final.POLICY_ENV, "JOIN")
        assert final.configure_policy() == "JOIN"
        assert base.POLICY == "JOIN"
    finally:
        base.POLICY = old


def test_probe_policy_refuses_unknown(monkeypatch):
    monkeypatch.setenv(final.POLICY_ENV, "CROSS_THE_SPREAD")
    with pytest.raises(ValueError):
        final.configure_policy()
