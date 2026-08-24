import os

import pytest

from src import gemini_kalshi_basis_risk_q1_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_campaign_v2 as campaign_v2
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_crypto_postincident_boot as postincident
from src import gemini_kalshi_live_probe_guard as shared_guard
from src import gemini_kalshi_live_service as live_service
from src import gemini_kalshi_probe_transport as transport


def test_v2_uses_schema_valid_live_probe_mode():
    assert child_v2.LEDGER_MODE == "LIVE_PROBE"


def test_initial_basis_probe_is_hard_limited_to_one():
    assert child_v2.INITIAL_PROBE_LIMIT == 1
    assert campaign.MAX_TRADES == 1
    assert child.INITIAL_TRANCHE_USD <= 1
    assert child.APPROVED_BUDGET == 5


def test_campaign_v2_installs_hardened_child_and_valid_mode(monkeypatch):
    original = campaign._obtain_basis_execution
    original_mode = campaign.MODE
    original_child_mode = child.MODE
    try:
        campaign_v2.install()
        assert campaign.MODE == "LIVE_PROBE"
        assert child.MODE == "LIVE_PROBE"
        assert campaign._obtain_basis_execution is campaign_v2._obtain_hardened_execution
    finally:
        campaign._obtain_basis_execution = original
        campaign.MODE = original_mode
        child.MODE = original_child_mode


def test_child_v2_refuses_to_enter_child_when_reconciliation_fails(monkeypatch):
    called = {"child": False}

    def blocked():
        raise PermissionError("unresolved")

    def should_not_run():
        called["child"] = True

    monkeypatch.setattr(child_v2, "assert_prior_ledger_reconciled", blocked)
    monkeypatch.setattr(child, "main", should_not_run)
    with pytest.raises(PermissionError, match="unresolved"):
        child_v2.main()
    assert called["child"] is False


def test_child_v2_sets_reconciled_gate_only_after_db_check(monkeypatch):
    sequence = []

    def reconciled():
        sequence.append("db")

    def child_main():
        sequence.append("child")
        assert os.environ[child.BASIS_RECONCILED_ENV] == "YES"

    monkeypatch.setattr(child_v2, "assert_prior_ledger_reconciled", reconciled)
    monkeypatch.setattr(child_v2, "install", lambda: sequence.append("install"))
    monkeypatch.setattr(child, "main", child_main)
    monkeypatch.delenv(child.BASIS_RECONCILED_ENV, raising=False)
    child_v2.main()
    assert sequence == ["db", "install", "child"]


def _authorized_basis_env(monkeypatch):
    monkeypatch.setattr(child_v2, "_LEDGER_RECONCILED", True)
    monkeypatch.setenv(child.BASIS_ARM_ENV, "YES")
    monkeypatch.setenv(child.BASIS_RECONCILED_ENV, "YES")
    monkeypatch.setenv(child.BASIS_BUDGET_ENV, "5")
    monkeypatch.setenv(child.BASIS_MIN_GAP_ENV, "10")
    monkeypatch.setenv(child.BASIS_DEADLINE_ENV, "2099-01-01T00:00:00Z")
    monkeypatch.setenv(child.v2.ARM_ENV, "YES")
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")


def test_basis_transport_authorization_requires_every_basis_specific_gate(monkeypatch):
    _authorized_basis_env(monkeypatch)
    assert child_v2.basis_transport_authorized(True) is True
    assert child_v2.basis_transport_authorized(False) is False

    monkeypatch.setenv(child.BASIS_BUDGET_ENV, "4.99")
    assert child_v2.basis_transport_authorized(True) is False
    monkeypatch.setenv(child.BASIS_BUDGET_ENV, "5")

    monkeypatch.setenv(child.BASIS_MIN_GAP_ENV, "9.99")
    assert child_v2.basis_transport_authorized(True) is False
    monkeypatch.setenv(child.BASIS_MIN_GAP_ENV, "10")

    monkeypatch.setenv(child.BASIS_ARM_ENV, "NO")
    assert child_v2.basis_transport_authorized(True) is False


def test_basis_install_is_process_local_and_shared_arb_guard_stays_locked(monkeypatch):
    _authorized_basis_env(monkeypatch)
    original_executor_auth = child.v2.executor.live_probe_authorized
    original_transport_auth = transport.live_probe_authorized
    original_service_auth = live_service.live_probe_authorized
    original_child_mode = child.MODE
    original_reservation = child.acquire_atomic_capital_reservation
    try:
        child_v2.install()
        assert child.v2.executor.live_probe_authorized is child_v2.basis_transport_authorized
        assert transport.live_probe_authorized is child_v2.basis_transport_authorized
        assert live_service.live_probe_authorized is child_v2.basis_transport_authorized
        assert shared_guard.SETTLEMENT_EQUIVALENCE_PROVEN is False
        assert shared_guard.live_probe_authorized(True) is False
    finally:
        child.v2.executor.live_probe_authorized = original_executor_auth
        transport.live_probe_authorized = original_transport_auth
        live_service.live_probe_authorized = original_service_auth
        child.MODE = original_child_mode
        child.acquire_atomic_capital_reservation = original_reservation


def test_guaranteed_arb_hard_stop_remains_independent(monkeypatch):
    monkeypatch.delenv(postincident.SETTLEMENT_EQUIVALENCE_ENV, raising=False)
    with pytest.raises(PermissionError):
        postincident._require_settlement_equivalence()
