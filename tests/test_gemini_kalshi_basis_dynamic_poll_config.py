from decimal import Decimal

from src import gemini_kalshi_basis_risk_dynamic_policy as policy


def test_basis_poll_defaults_to_historical_pacing(monkeypatch):
    monkeypatch.delenv(policy.KALSHI_REST_POLL_INTERVAL_ENV, raising=False)
    assert policy.configured_kalshi_rest_poll_interval_s() == Decimal("0.18")


def test_basis_poll_honors_service_scoped_value(monkeypatch):
    monkeypatch.setenv(policy.KALSHI_REST_POLL_INTERVAL_ENV, "0.12")
    assert policy.configured_kalshi_rest_poll_interval_s() == Decimal("0.12")


def test_basis_poll_fails_safe_to_bounds(monkeypatch):
    monkeypatch.setenv(policy.KALSHI_REST_POLL_INTERVAL_ENV, "0.01")
    assert policy.configured_kalshi_rest_poll_interval_s() == Decimal("0.05")
    monkeypatch.setenv(policy.KALSHI_REST_POLL_INTERVAL_ENV, "0.50")
    assert policy.configured_kalshi_rest_poll_interval_s() == Decimal("0.25")
    monkeypatch.setenv(policy.KALSHI_REST_POLL_INTERVAL_ENV, "not-a-number")
    assert policy.configured_kalshi_rest_poll_interval_s() == Decimal("0.18")
