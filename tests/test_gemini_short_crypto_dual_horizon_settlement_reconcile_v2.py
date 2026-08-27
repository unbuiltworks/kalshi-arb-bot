from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as r
from src import gemini_short_crypto_inventory_local_budget as b5
from src import gemini_short_crypto_inventory15_local_budget as b15


def test_strategy_classifier_supports_both_horizons():
    h, b = r._strategy({
        "strategy": b5.STRATEGY,
        "symbol": "GEMI-BTC05M2608272040-UP",
        "event_ticker": "BTC05M2608272040",
    })
    assert h == "5m" and b is b5
    h, b = r._strategy({
        "strategy": b15.STRATEGY,
        "symbol": "GEMI-BTC15M2608272115-UP",
        "event_ticker": "BTC15M2608272115",
    })
    assert h == "15m" and b is b15


def test_exact_live_matched_pair_is_plus_eleven_cents():
    out = r.settled_economics(
        yes_fill=D("1"),
        no_fill=D("1"),
        yes_price=D("0.44"),
        no_price=D("0.43"),
        resolution_side="yes",
    )
    assert out["raw_cost"] == D("0.87")
    assert out["yes_maker_fee"] == D("0.01")
    assert out["no_maker_fee"] == D("0.01")
    assert out["maker_fees"] == D("0.02")
    assert out["payout"] == D("1")
    assert out["net_profit"] == D("0.11")
    assert out["matched_quantity"] == D("1")
    assert out["residual_quantity"] == D("0")
    assert out["matched_complete_set"] is True


def test_matched_pair_profit_is_resolution_independent():
    yes = r.settled_economics(
        yes_fill=D("1"), no_fill=D("1"),
        yes_price=D("0.44"), no_price=D("0.43"), resolution_side="yes",
    )
    no = r.settled_economics(
        yes_fill=D("1"), no_fill=D("1"),
        yes_price=D("0.44"), no_price=D("0.43"), resolution_side="no",
    )
    assert yes["net_profit"] == no["net_profit"] == D("0.11")


def test_one_sided_loser_includes_maker_fee():
    out = r.settled_economics(
        yes_fill=D("1"), no_fill=D("0"),
        yes_price=D("0.44"), no_price=D("0"), resolution_side="no",
    )
    assert out["raw_cost"] == D("0.44")
    assert out["maker_fees"] == D("0.01")
    assert out["payout"] == D("0")
    assert out["net_profit"] == D("-0.45")
    assert out["matched_complete_set"] is False


def test_fractional_imbalanced_inventory_uses_resolution():
    out = r.settled_economics(
        yes_fill=D("1"), no_fill=D("0.25"),
        yes_price=D("0.44"), no_price=D("0.43"), resolution_side="no",
    )
    assert out["matched_quantity"] == D("0.25")
    assert out["residual_quantity"] == D("0.75")
    assert out["payout"] == D("0.25")
    assert out["maker_fees"] >= D("0.02")


def test_event_resolution_requires_exact_settled_contract():
    event = {
        "status": "settled",
        "resolvedAt": "2026-08-27T20:40:01Z",
        "contracts": [{
            "status": "settled",
            "instrumentSymbol": "GEMI-BTC05M2608272040-UP",
            "resolutionSide": "yes",
            "resolvedAt": "2026-08-27T20:40:01Z",
        }],
    }
    side, resolved_at = r._event_resolution(event, "GEMI-BTC05M2608272040-UP")
    assert side == "yes"
    assert resolved_at is not None


def test_event_resolution_fails_closed_before_settlement():
    event = {
        "status": "closed",
        "contracts": [{
            "status": "closed",
            "instrumentSymbol": "GEMI-BTC05M2608272040-UP",
            "resolutionSide": None,
        }],
    }
    with pytest.raises(RuntimeError, match="settlement not posted yet"):
        r._event_resolution(event, "GEMI-BTC05M2608272040-UP")


def test_terminal_rows_require_exact_distinct_client_ids():
    yid, nid = "b5-a-yes", "b5-a-no"
    y, n = r._terminal_rows([
        {"clientOrderId": yid, "filledQuantity": "1", "outcome": "yes"},
        {"clientOrderId": nid, "filledQuantity": "1", "outcome": "no"},
        {"clientOrderId": "other", "filledQuantity": "1", "outcome": "yes"},
    ], yid, nid)
    assert r._filled(y) == D("1")
    assert r._filled(n) == D("1")
