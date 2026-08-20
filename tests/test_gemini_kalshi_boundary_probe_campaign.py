import os

import pytest

from src import gemini_kalshi_boundary_probe_5to10bp as probe
from src import gemini_kalshi_boundary_probe_campaign as campaign
from src import gemini_kalshi_crypto_live_campaign as base
from src import gemini_kalshi_crypto_live_campaign_reliable as reliable
from src import gemini_kalshi_sol_live_canary_v2 as v2


def _valid_env(monkeypatch):
    monkeypatch.setenv(probe.ARM_ENV, "YES")
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    monkeypatch.setenv(campaign.CAMPAIGN_ID_ENV, "bp-260820")
    monkeypatch.setenv(campaign.MAX_TRADES_ENV, "5")
    monkeypatch.setenv(campaign.MAX_EXECUTION_ATTEMPTS_ENV, "25")
    monkeypatch.setenv(campaign.HARD_BUDGET_ENV, "5")
    monkeypatch.setenv(campaign.DEADLINE_ENV, "2099-08-21T03:59:00Z")


def test_campaign_requires_separate_experimental_arm(monkeypatch):
    _valid_env(monkeypatch)
    monkeypatch.setenv(probe.ARM_ENV, "NO")
    with pytest.raises(PermissionError, match=probe.ARM_ENV):
        campaign._configure_campaign_env()


def test_campaign_requires_existing_live_probe_gate(monkeypatch):
    _valid_env(monkeypatch)
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "NO")
    with pytest.raises(PermissionError, match="live probe environment gate"):
        campaign._configure_campaign_env()


def test_campaign_configures_unique_bounded_reliable_namespace(monkeypatch):
    _valid_env(monkeypatch)
    cfg = campaign._configure_campaign_env()
    assert cfg == {
        "campaign_id": "bp-260820",
        "max_trades": 5,
        "max_execution_attempts": 25,
        "hard_budget_usd": "5",
    }
    assert os.environ[base.CAMPAIGN_ID_ENV] == "bp-260820"
    assert os.environ[base.MAX_TRADES_ENV] == "5"
    assert os.environ[base.HARD_BUDGET_ENV] == "5"
    assert os.environ[reliable.MAX_EXECUTION_ATTEMPTS_ENV] == "25"
    assert os.environ[v2.ARM_ENV] == "YES"


@pytest.mark.parametrize("max_trades", ["0", "6"])
def test_campaign_rejects_trade_count_outside_one_to_five(monkeypatch, max_trades):
    _valid_env(monkeypatch)
    monkeypatch.setenv(campaign.MAX_TRADES_ENV, max_trades)
    with pytest.raises(ValueError, match="max trades"):
        campaign._configure_campaign_env()


@pytest.mark.parametrize("budget", ["0", "5.01", "not-a-number"])
def test_campaign_rejects_budget_above_five_or_invalid(monkeypatch, budget):
    _valid_env(monkeypatch)
    monkeypatch.setenv(campaign.HARD_BUDGET_ENV, budget)
    with pytest.raises(ValueError, match="hard budget"):
        campaign._configure_campaign_env()


@pytest.mark.parametrize("attempts", ["4", "26"])
def test_campaign_rejects_attempt_count_outside_bounded_range(monkeypatch, attempts):
    _valid_env(monkeypatch)
    monkeypatch.setenv(campaign.MAX_EXECUTION_ATTEMPTS_ENV, attempts)
    with pytest.raises(ValueError, match="max execution attempts"):
        campaign._configure_campaign_env()


def test_campaign_rejects_expired_arm_deadline(monkeypatch):
    _valid_env(monkeypatch)
    monkeypatch.setenv(campaign.DEADLINE_ENV, "2020-01-01T00:00:00Z")
    with pytest.raises(RuntimeError, match="already expired"):
        campaign._configure_campaign_env()


@pytest.mark.asyncio
async def test_boundary_ledger_wrapper_preserves_event_but_sets_distinct_cohort(monkeypatch):
    posted = []

    async def fake_post(_http, event):
        posted.append(event)

    monkeypatch.setattr(campaign, "_ORIGINAL_POST_EVENT", fake_post)
    original = {
        "event_id": "x",
        "strategy": "gemini_kalshi_15m_crypto",
        "mode": "LIVE_PROBE",
        "status": "FILLED",
        "metadata": {"run_id": "bp-260820-01"},
    }
    await campaign._post_boundary_event(object(), original)

    assert original["strategy"] == "gemini_kalshi_15m_crypto"
    assert original["mode"] == "LIVE_PROBE"
    assert posted[0]["strategy"] == campaign.BOUNDARY_STRATEGY
    assert posted[0]["mode"] == campaign.BOUNDARY_MODE
    assert posted[0]["status"] == "FILLED"
    assert posted[0]["metadata"]["run_id"] == "bp-260820-01"
    assert posted[0]["metadata"]["boundary_probe_band"] == "5_to_lt_10_bp"
    assert posted[0]["metadata"]["q5_graduation_evidence"] is False
