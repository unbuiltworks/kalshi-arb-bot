"""Regression: loss ceiling and provenance must not authorize trading by accident."""
from decimal import Decimal as D

from src import l5_pmus_live_runner as runner
from src.l5_live_execution import InventoryStore


def test_daily_loss_limit_defaults_to_five(monkeypatch):
    monkeypatch.delenv("L5_DAILY_REALIZED_LOSS_HALT_USD", raising=False)
    assert runner.daily_realized_loss_halt() == D("5")


def test_daily_loss_limit_explicit_fifty(monkeypatch):
    monkeypatch.setenv("L5_DAILY_REALIZED_LOSS_HALT_USD", "50")
    assert runner.daily_realized_loss_halt() == D("50")


def test_daily_loss_limit_invalid_or_unsafe_fails_closed(monkeypatch):
    for raw in ("", "oops", "NaN", "Infinity", "-5", "0", "4.99", "50.01", "100"):
        monkeypatch.setenv("L5_DAILY_REALIZED_LOSS_HALT_USD", raw)
        assert runner.daily_realized_loss_halt() == D("5"), raw


def test_legacy_attributed_is_not_verified_l5_net(tmp_path):
    store = InventoryStore(str(tmp_path / "ledger.json"))
    store.state["realized_attributed"] = "-4.15"
    store.state["realized_unattributed"] = "0"
    store.state["leg_cost_basis_unknown"] = [
        "cpc-btc-updown-15m-2026-10-15-1945z|DOWN"
    ]
    report = store.realized_lines()
    assert report["realized_attributed"] == "-4.15"
    assert D(report["realized_total"]) == D("-4.15")
    assert report["verified_l5_realized_net"] is None
    assert report["verified_l5_status"] == "UNPROVEN_LEGACY_ATTRIBUTION"
    assert report["unknown_basis_keys"] == [
        "cpc-btc-updown-15m-2026-10-15-1945z|DOWN"
    ]

def test_retirement_preserves_unknown_basis_provenance_in_source():
    """Guard against silently dropping the provenance at settlement."""
    import inspect
    from src.l5_live_execution import InventoryStore
    source = inspect.getsource(InventoryStore.retire_settled_market)
    assert 'unknown_basis_at_settlement' in source
    assert '"unverified_cost_basis_keys": unknown_basis_at_settlement' in source
    assert '"l5_owner_attribution_verified": verified_l5' in source
    assert '"pnl_provenance"' in source

def test_provenance_for_l5_fill_and_unowned_fill(tmp_path):
    from src.l5_live_execution import OWN
    market = "cpc-btc-updown-15m-2026-10-15-2345z"
    s = InventoryStore(str(tmp_path / "new.json"))
    s.record_submit("l5-001", {
        "market": market, "side": "UP", "price": "0.25", "qty": "1"
    }, session_id="single-use-owner-approved-session", mode="LIVE")
    s.apply_fill(market, "UP", D("1"), D("0.25"),
                 coid="l5-001", basis=OWN)
    s.apply_fill(market, "DOWN", D("1"), D("0.20"),
                 coid=None, basis=OWN)
    proof = s.state["fill_provenance"][market]
    assert proof["legs"]["UP"]["verified_qty"] == "1"
    assert D(proof["legs"]["UP"]["verified_cost"]) == D("0.25")
    assert proof["entries"][0]["l5_owner_verified"] is True
    assert proof["entries"][1]["l5_owner_verified"] is False
    assert proof["legs"]["DOWN"]["total_qty"] == "1"


def test_unattested_fill_does_not_become_verified_l5_settlement(tmp_path):
    from src.l5_live_execution import OWN
    market = "cpc-btc-updown-15m-2026-10-15-2345z"
    s = InventoryStore(str(tmp_path / "manual.json"))
    s.apply_fill(market, "DOWN", D("2"), D("0.30"),
                 coid=None, basis=OWN)
    out = s.retire_settled_market(market, yes_value=D("1"))
    assert out["retired"] is True
    rec = s.state["retired_markets"][market]
    assert rec["l5_owner_attribution_verified"] is False
    assert rec["pnl_provenance"] == "OWNERSHIP_NOT_ATTESTED"
    assert "verified_l5_settlement_gross" not in s.state

def test_verified_strategy_net_requires_after_fee_attestation(tmp_path):
    s = InventoryStore(str(tmp_path / "net.json"))
    assert s.verified_l5_net_for_gates() is None
    s.state["verified_l5_settlement_gross"] = "-9.00"
    assert s.verified_l5_net_for_gates() is None
    s.state["verified_l5_net_attestation"] = {
        "net_usd": "-7", "source": "AUDITED_L5_FILLS_FEES_SETTLEMENTS",
        "order_ids": ["order-confirmed"], "fee_evidence": ["fee-confirmed"]}
    assert s.verified_l5_net_for_gates() == D("-7")


def test_two_independent_loss_budgets_preserve_account_guard():
    from src.l5_live_validation_gates import ValidationGates
    gate = ValidationGates(max_cumulative_loss_usd=D("10"),
                           max_strategy_loss_usd=D("5"))
    base = dict(order_qty=D("1"), order_price=D("0.3"),
                order_intent="ORDER_INTENT_BUY_LONG",
                cumulative_unrealized_loss=D("0"))
    # Owner-specific evidence absent; unknown never counts as zero.
    assert gate.refuse(**base, cumulative_realized=D("-1"),
                       verified_strategy_net=None) == "STRATEGY_NET_UNVERIFIED"
    # Account-wide losses still stop orders even when strategy is positive.
    assert gate.refuse(**base, cumulative_realized=D("-10"),
                       verified_strategy_net=D("2")).startswith(
                           "CUMULATIVE_LOSS_LIMIT:")
    # Strategy losses stop orders even when account ledger looks positive.
    assert gate.refuse(**base, cumulative_realized=D("2"),
                       verified_strategy_net=D("-5")).startswith(
                           "STRATEGY_LOSS_LIMIT:")
