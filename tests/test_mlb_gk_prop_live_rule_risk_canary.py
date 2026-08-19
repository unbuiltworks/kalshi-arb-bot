from decimal import Decimal
from types import SimpleNamespace

from src.gemini_kalshi_execution_shadow import TopOfBook
from src import mlb_gk_prop_live_rule_risk_canary as canary

D = Decimal


def _book(*, yes_bid="0.40", yes_ask="0.42", bid_size="3", ask_size="4", ts=1000):
    return TopOfBook(
        venue="x",
        market_id="m",
        ts_ms=ts,
        yes_bid=D(yes_bid),
        yes_bid_size=D(bid_size),
        yes_ask=D(yes_ask),
        yes_ask_size=D(ask_size),
    )


def test_software_gate_requires_both_live_envs(monkeypatch):
    monkeypatch.setenv("ENABLE_GEMINI_KALSHI_LIVE_PROBES", "YES")
    monkeypatch.setenv(canary.ARM_ENV, "NO")
    assert canary._software_authorized(True) is False
    monkeypatch.setenv(canary.ARM_ENV, "YES")
    assert canary._software_authorized(True) is True
    assert canary._software_authorized(False) is False


def test_original_gemini_yes_kalshi_no_limits_must_survive():
    entry_g = _book(yes_ask="0.42", ask_size="2")
    entry_k = _book(yes_bid="0.47", bid_size="2")  # canonical NO ask = .53
    better_g = _book(yes_ask="0.41", ask_size="1")
    better_k = _book(yes_bid="0.48", bid_size="1")  # canonical NO ask = .52
    assert canary._same_original_limits(
        "GEMINI_YES_KALSHI_NO", entry_g, entry_k, better_g, better_k
    ) is True
    worse_g = _book(yes_ask="0.43", ask_size="2")
    assert canary._same_original_limits(
        "GEMINI_YES_KALSHI_NO", entry_g, entry_k, worse_g, better_k
    ) is False


def test_original_kalshi_yes_gemini_no_limits_must_survive():
    entry_g = _book(yes_bid="0.45", bid_size="2")  # canonical NO ask = .55
    entry_k = _book(yes_ask="0.40", ask_size="2")
    better_g = _book(yes_bid="0.46", bid_size="1")  # NO ask .54
    better_k = _book(yes_ask="0.39", ask_size="1")
    assert canary._same_original_limits(
        "KALSHI_YES_GEMINI_NO", entry_g, entry_k, better_g, better_k
    ) is True


def test_payloads_are_one_contract_and_deterministic(monkeypatch):
    monkeypatch.setenv(canary.RUN_ID_ENV, "sports1")
    row = SimpleNamespace(
        gemini_side=SimpleNamespace(side="YES", venue_market_id="GEMI-X"),
        kalshi_side=SimpleNamespace(side="YES", venue_market_id="KX-X"),
    )
    g = _book(yes_bid="0.40", yes_ask="0.41")
    k = _book(yes_bid="0.55", yes_ask="0.56")
    gp, kp = canary._payloads(row, "GEMINI_YES_KALSHI_NO", g, k)
    assert gp["quantity"] == "1"
    assert kp["count"] == "1.00"
    assert gp["clientOrderId"] == "mlbgk-sports1-g"
    assert kp["client_order_id"] == "mlbgk-sports1-k"
    assert gp["eventOutcome"] == "YES"
    assert kp["side"] == "ask"


def test_payloads_fail_closed_on_complement_oriented_contract(monkeypatch):
    monkeypatch.setenv(canary.RUN_ID_ENV, "sports1")
    row = SimpleNamespace(
        gemini_side=SimpleNamespace(side="NO", venue_market_id="GEMI-X"),
        kalshi_side=SimpleNamespace(side="YES", venue_market_id="KX-X"),
    )
    try:
        canary._payloads(row, "GEMINI_YES_KALSHI_NO", _book(), _book())
    except ValueError as exc:
        assert "source-YES" in str(exc)
    else:
        raise AssertionError("expected complement orientation to fail closed")
