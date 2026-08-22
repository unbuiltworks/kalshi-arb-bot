from decimal import Decimal

import pytest

from src import gemini_kalshi_crypto_campaign_child_reliable as child
from src import gemini_kalshi_crypto_postincident_boot as boot
from src.gemini_kalshi_hedge_cushion import best_fee_aware_kalshi_hedge
from src.gemini_kalshi_live_probe_guard import recompute_payload_locked_profit

D = Decimal


G_XRP = {
    "symbol": "GEMI-XRP15M2608201500-UP",
    "side": "BUY",
    "type": "LIMIT",
    "timeInForce": "FOK",
    "price": "0.09",
    "quantity": "1",
    "eventOutcome": "YES",
    "clientOrderId": "g-xrp-replay",
}
K_XRP = {
    "ticker": "KXXRP15M-26AUG201100-00",
    "client_order_id": "k-xrp-replay",
    "side": "ask",
    "count": "1.00",
    "price": "0.15",
    "time_in_force": "fill_or_kill",
}


def test_exact_xrp_incident_has_four_cent_modeled_edge_before_cushion():
    assert recompute_payload_locked_profit(
        G_XRP, K_XRP, kalshi_fee_multiplier=D("1")
    ) == D("0.04")


def test_exact_xrp_incident_can_cushion_one_cent_and_keep_three_cent_floor():
    cushion = best_fee_aware_kalshi_hedge(
        G_XRP,
        K_XRP,
        kalshi_fee_multiplier=D("1"),
        profit_floor=D("0.03"),
    )
    assert cushion.original_yes_price == D("0.15")
    assert cushion.cushioned_yes_price == D("0.14")
    assert cushion.cushion_cents == 1
    assert cushion.original_locked_profit == D("0.04")
    assert cushion.cushioned_locked_profit == D("0.03")
    assert cushion.payload["price"] == "0.14"


def test_postincident_boot_refuses_to_run_without_explicit_cushion_arm(monkeypatch):
    monkeypatch.delenv(child.HEDGE_CUSHION_ENV, raising=False)
    with pytest.raises(PermissionError, match=child.HEDGE_CUSHION_ENV):
        boot.main()


def test_postincident_boot_refuses_uncertified_settlement_equivalence(monkeypatch):
    monkeypatch.setenv(child.HEDGE_CUSHION_ENV, "YES")
    monkeypatch.delenv(boot.SETTLEMENT_EQUIVALENCE_ENV, raising=False)
    with pytest.raises(PermissionError, match=boot.SETTLEMENT_EQUIVALENCE_ENV):
        boot.main()


def test_postincident_boot_installs_accounting_then_calls_safe_boot(monkeypatch):
    calls = []
    monkeypatch.setenv(child.HEDGE_CUSHION_ENV, "YES")
    monkeypatch.setenv(boot.SETTLEMENT_EQUIVALENCE_ENV, "YES")
    monkeypatch.setattr(boot.incident_accounting, "install", lambda: calls.append("accounting"))
    monkeypatch.setattr(boot.safe_boot, "main", lambda: calls.append("safe_boot"))
    boot.main()
    assert calls == ["accounting", "safe_boot"]
