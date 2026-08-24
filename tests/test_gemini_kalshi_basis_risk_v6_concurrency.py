from decimal import Decimal

import pytest

from src import gemini_kalshi_basis_risk_dynamic_policy as dynamic
from src import gemini_kalshi_basis_risk_q1_child_v6 as v6

D = Decimal


def _state(**updates):
    state = {
        "inflight_or_unpaired": [],
        "unreviewed_realized_losses": [],
        "remaining_loss_budget_usd": D("4.91"),
        "reservations": 7,
        "open_paired_count": 0,
        "open_paired_reserved_usd": D("0"),
    }
    state.update(updates)
    return state


def test_v6_allows_next_probe_with_two_paired_open():
    allowed, reason = v6.automatic_rollover_allowed(
        _state(open_paired_count=2, open_paired_reserved_usd=D("2"))
    )
    assert allowed is True
    assert reason == "OK"


def test_v6_blocks_fourth_open_paired_position():
    allowed, reason = v6.automatic_rollover_allowed(
        _state(open_paired_count=3, open_paired_reserved_usd=D("3"))
    )
    assert allowed is False
    assert reason == "OPEN_PAIRED_RISK_CAP_REACHED"


def test_v6_blocks_any_inflight_or_unpaired_risk():
    allowed, reason = v6.automatic_rollover_allowed(
        _state(inflight_or_unpaired=[{"trade_id": "x"}])
    )
    assert allowed is False
    assert reason == "INFLIGHT_OR_UNPAIRED_EXECUTION_RISK"


def test_v6_never_reserves_beyond_remaining_loss_budget():
    allowed, reason = v6.automatic_rollover_allowed(
        _state(remaining_loss_budget_usd=D("0.99"))
    )
    assert allowed is False
    assert reason == "REMAINING_LOSS_BUDGET_CANNOT_COVER_NEXT_PAIRED_POSITION"


def test_v6_new_unreviewed_loss_is_hard_stop():
    allowed, reason = v6.automatic_rollover_allowed(
        _state(unreviewed_realized_losses=[{"trade_id": "loss"}])
    )
    assert allowed is False
    assert reason == "NEW_OR_UNREVIEWED_REALIZED_LOSS_HARD_STOP"


def test_v6_turnover_limit_does_not_change_loss_budget():
    assert v6.PROMOTED_PROBE_LIMIT == 200
    assert v6.MAX_OPEN_PAIRED_RISK == 3
    assert D(v6.child.APPROVED_BUDGET) == D("5")
    allowed, reason = v6.automatic_rollover_allowed(
        _state(reservations=v6.PROMOTED_PROBE_LIMIT)
    )
    assert allowed is False
    assert reason == "PROMOTED_PROBE_LIMIT_REACHED"


def test_asset_scope_can_partition_workers(monkeypatch):
    monkeypatch.setenv(dynamic.ASSET_SCOPE_ENV, "SOL")
    assert dynamic.configured_assets() == ("SOL",)
    monkeypatch.setenv(dynamic.ASSET_SCOPE_ENV, "ETH,XRP")
    assert dynamic.configured_assets() == ("ETH", "XRP")


def test_asset_scope_keeps_btc_quarantined(monkeypatch):
    monkeypatch.setenv(dynamic.ASSET_SCOPE_ENV, "BTC")
    with pytest.raises(PermissionError):
        dynamic.configured_assets()
