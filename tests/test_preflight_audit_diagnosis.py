"""A blocked preflight must say WHY, and must not report a broken read as 93
settlement facts.

LIVE 2026-09-11T22:45:07Z. The boot gate passed, the advance gate fired, and
continuation_preflight blocked on `venue_settled` with all 93 markets at
status AUDIT_UNAVAILABLE and every other field null -- settled_by,
realized_usd, venue_qty_total, ledger_qty_total. settle_held() puts the
exception in `error`; the preflight's per-market dict did not copy it, so the
row said "not settled" 93 times and could not say that it had never managed to
ask. That cost a deployment to discover.
"""
from decimal import Decimal as D

import pytest

from src import pmus_l5_continuous_session as cont


@pytest.fixture
def _stub(monkeypatch):
    monkeypatch.setattr(cont, "sweep_orphan_orders", lambda t: {
        "seen": 0, "cancelled": [], "foreign": [], "errors": []})
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "unsettled_slugs", lambda: [])


def _unavailable(t, slug):
    return {"status": "AUDIT_UNAVAILABLE",
            "error": "HTTPError:403 Forbidden"}


def _settled(t, slug):
    return {"status": "REALIZED", "settled": True,
            "settled_by": "POSITION_RESOLUTION", "realized_confirmed": True,
            "realized_usd": "0.10", "venue_qty_total": "1",
            "ledger_qty_total": "1"}


def test_the_error_reaches_the_preflight_row(_stub, monkeypatch):
    """Without this the blocker is undiagnosable from the log."""
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a", "b"}, {"a", "b"})
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is False
    for slug in ("a", "b"):
        assert vs["markets"][slug]["error"] == "HTTPError:403 Forbidden"


def test_a_uniform_failure_is_named_as_one_fact_not_many(_stub, monkeypatch):
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a", "b", "c"}, set())
    vs = pre["checks"]["venue_settled"]
    assert vs["uniform_failure"] is True
    assert vs["audit_unavailable"] == 3
    assert vs["audit_error_kinds"] == ["HTTPError:403 Forbidden"]
    assert "venue/transport fault" in vs["diagnosis"]
    assert "not a settlement fact" in vs["diagnosis"]


def test_a_genuine_unsettled_market_is_not_called_a_transport_fault(
        _stub, monkeypatch):
    """The diagnosis must not fire when the venue answered and the answer was
    simply 'not settled' -- that IS a settlement fact and must still block."""
    def mixed(t, slug):
        return _settled(t, slug) if slug == "a" else {
            "status": "PENDING", "settled": False}
    monkeypatch.setattr(cont, "settle_held", mixed)
    pre = cont.continuation_preflight(None, {"a", "b"}, set())
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is False, "still blocks -- b is not settled"
    assert vs["uniform_failure"] is False
    assert vs["diagnosis"] is None
    assert vs["audit_unavailable"] == 0


def test_a_clean_audit_still_passes_and_reports_no_errors(_stub, monkeypatch):
    monkeypatch.setattr(cont, "settle_held", _settled)
    pre = cont.continuation_preflight(None, {"a"}, {"a"})
    vs = pre["checks"]["venue_settled"]
    assert vs["ok"] is True
    assert vs["audit_unavailable"] == 0 and vs["audit_error_kinds"] == []
    assert vs["uniform_failure"] is False
    assert vs["markets"]["a"]["error"] is None


def test_the_check_still_blocks_on_an_unavailable_audit(_stub, monkeypatch):
    """Diagnosis is not permission. "We could not ask" must never pass."""
    monkeypatch.setattr(cont, "settle_held", _unavailable)
    pre = cont.continuation_preflight(None, {"a"}, {"a"})
    assert pre["ok"] is False
    assert "venue_settled" in pre["failed"]
