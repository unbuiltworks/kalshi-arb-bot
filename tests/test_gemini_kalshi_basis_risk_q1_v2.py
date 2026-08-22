import os

import pytest

from src import gemini_kalshi_basis_risk_q1_campaign as campaign
from src import gemini_kalshi_basis_risk_q1_campaign_v2 as campaign_v2
from src import gemini_kalshi_basis_risk_q1_child as child
from src import gemini_kalshi_basis_risk_q1_child_v2 as child_v2
from src import gemini_kalshi_crypto_postincident_boot as postincident


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


def test_guaranteed_arb_hard_stop_remains_independent(monkeypatch):
    monkeypatch.delenv(postincident.SETTLEMENT_EQUIVALENCE_ENV, raising=False)
    with pytest.raises(PermissionError):
        postincident._require_settlement_equivalence()
