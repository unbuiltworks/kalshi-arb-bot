import json
from decimal import Decimal as D

from src import pmus_aws_supervisor as s


def _status():
    return {
        "t": "2099-01-01T00:00:00+00:00",
        "conn": "connected",
        "auth": "ok",
        "top_markets": [
            {
                "slug": "m1", "bid": "0.55", "ask": "0.555",
                "bid_qty": "100", "ask_qty": "100",
                "both_sides_served": True, "trades_1h": 500,
                "slot_counts_theoretical": {"MATCHED": 3},
                "gate_status": "Q1_GATE_CLEARED",
            },
            {
                "slug": "m2", "bid": "0.45", "ask": "0.48",
                "bid_qty": "10", "ask_qty": "10",
                "both_sides_served": True, "trades_1h": 100,
                "slot_counts_theoretical": {"MATCHED": 2},
                "gate_status": "Q1_GATE_CLEARED",
            },
        ],
    }


class FakeAccount:
    def __init__(self, position="0", orders=None):
        self.position = position
        self.orders = [] if orders is None else orders

    def request(self, method, path, body=None, base=None):
        if path == "/v1/orders/open":
            return {"orders": self.orders}
        if path == "/v1/portfolio/positions":
            return {"positions": {"existing": {"netPosition": self.position}}}
        raise AssertionError((method, path, body, base))


def test_candidate_ranking_prefers_tighter_stronger_clear():
    rows = s.eligible_candidates(_status())
    assert [r["slug"] for r in rows] == ["m1", "m2"]


def test_nonzero_position_blocks_new_market():
    assert s.account_block_reason(FakeAccount(position="1")).startswith(
        "nonzero_positions:existing=1")


def test_open_order_blocks_new_market():
    assert s.account_block_reason(FakeAccount(orders=[{"id": "x"}])) == "open_orders:1"


def test_disarmed_supervisor_never_invokes_campaign(monkeypatch):
    st = _status()
    monkeypatch.setattr(s, "latest_recorder_status", lambda: st)
    monkeypatch.setattr(s, "status_age_seconds", lambda status: 1.0)
    monkeypatch.setattr(s, "account_block_reason", lambda t: None)
    monkeypatch.setattr(s, "armed", lambda: False)
    called = {"n": 0}

    def no_campaign(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("must not trade")

    monkeypatch.setattr(s.q1, "run_campaign", no_campaign)
    result = s.run_once(object())
    assert result["action"] == "WOULD_TRADE"
    assert result["candidate"] == "m1"
    assert called["n"] == 0


def test_residual_blocks_even_with_live_gate(monkeypatch):
    st = _status()
    monkeypatch.setattr(s, "latest_recorder_status", lambda: st)
    monkeypatch.setattr(s, "status_age_seconds", lambda status: 1.0)
    monkeypatch.setattr(s, "armed", lambda: True)
    result = s.run_once(FakeAccount(position="1"))
    assert result["action"] == "BLOCK"
    assert "nonzero_positions" in result["reason"]


def test_stale_status_waits_without_account_call(monkeypatch):
    monkeypatch.setattr(s, "latest_recorder_status", lambda: _status())
    monkeypatch.setattr(s, "status_age_seconds", lambda status: 999.0)
    result = s.run_once(object())
    assert result["action"] == "WAIT"
    assert result["reason"] == "stale_recorder_status"
