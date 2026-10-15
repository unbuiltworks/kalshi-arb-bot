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
