from decimal import Decimal as D

import pytest

from src import gemini_short_crypto_dual_horizon_settlement_reconcile_v2 as r
from src import gemini_short_crypto_inventory_local_budget as b5
from src import gemini_short_crypto_inventory15_local_budget as b15


def test_strategy_classifier_supports_btc5_eth5_and_btc15():
    h, b = r._strategy({
        "strategy": b5.STRATEGY,
        "symbol": "GEMI-BTC05M2608272040-UP",
        "event_ticker": "BTC05M2608272040",
    })
    assert h == "5m" and b is b5
    h, b = r._strategy({
        "strategy": b5.STRATEGY,
        "symbol": "GEMI-ETH05M2608281700-UP",
        "event_ticker": "ETH05M2608281700",
    })
    assert h == "5m" and b is b5
    h, b = r._strategy({
        "strategy": b15.STRATEGY,
        "symbol": "GEMI-BTC15M2608272115-UP",
        "event_ticker": "BTC15M2608272115",
    })
    assert h == "15m" and b is b15


def test_strategy_classifier_rejects_eth15():
    with pytest.raises(RuntimeError, match="unexpected dual-horizon reservation identity"):
        r._strategy({
            "strategy": b15.STRATEGY,
            "symbol": "GEMI-ETH15M2608281715-UP",
            "event_ticker": "ETH15M2608281715",
        })


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


def test_dynamic_declared_ids_fall_back_to_static_pair():
    yes, no = r._declared_client_ids({}, "trade-a")
    assert len(yes) == len(no) == 1
    assert yes[0] != no[0]


def test_dynamic_declared_ids_use_exact_durable_chain():
    yes, no = r._declared_client_ids({
        "yes_client_order_ids": ["y0", "y1"],
        "no_client_order_ids": ["n0", "n1", "n2"],
    }, "trade-a")
    assert yes == ["y0", "y1"]
    assert no == ["n0", "n1", "n2"]


def test_dynamic_declared_ids_reject_overlap_and_duplicates():
    with pytest.raises(RuntimeError, match="distinct"):
        r._declared_client_ids({"yes_client_order_ids": ["y0", "y0"], "no_client_order_ids": ["n0"]}, "t")
    with pytest.raises(RuntimeError, match="overlap"):
        r._declared_client_ids({"yes_client_order_ids": ["same"], "no_client_order_ids": ["same"]}, "t")


def test_terminal_rows_many_requires_every_replacement_id():
    rows = [
        {"clientOrderId": "y0", "filledQuantity": "1", "outcome": "yes"},
        {"clientOrderId": "n0", "filledQuantity": "0", "outcome": "no"},
        {"clientOrderId": "n1", "filledQuantity": "1", "outcome": "no"},
    ]
    terminal = r._terminal_rows_many(rows, ["y0", "n0", "n1"])
    assert set(terminal) == {"y0", "n0", "n1"}
    with pytest.raises(RuntimeError, match="missing exact canary order history"):
        r._terminal_rows_many(rows, ["y0", "n0", "n1", "n2"])


def test_aggregate_side_sums_replacement_fills_and_fees_rowwise():
    terminal = {
        "n0": {"clientOrderId": "n0", "filledQuantity": "0", "outcome": "no", "price": "0.30"},
        "n1": {"clientOrderId": "n1", "filledQuantity": "1", "outcome": "no", "avgExecutionPrice": "0.34"},
    }
    out = r._aggregate_side(terminal, ["n0", "n1"], "no")
    assert out["fill"] == D("1")
    assert out["cost"] == D("0.34")
    assert out["fees"] == D("0.01")
    assert out["avg_price"] == D("0.34")


def test_aggregate_side_fails_closed_above_q1():
    terminal = {
        "n0": {"clientOrderId": "n0", "filledQuantity": "0.6", "outcome": "no", "avgExecutionPrice": "0.30"},
        "n1": {"clientOrderId": "n1", "filledQuantity": "0.5", "outcome": "no", "avgExecutionPrice": "0.31"},
    }
    with pytest.raises(RuntimeError, match="exceeds q=1"):
        r._aggregate_side(terminal, ["n0", "n1"], "no")
