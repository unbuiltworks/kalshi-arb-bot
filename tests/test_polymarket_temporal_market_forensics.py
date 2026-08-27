from src.polymarket_temporal_market_forensics import analyze_condition, modeled_taker_fee


def _row(ts, outcome, price, size, condition="0xabc"):
    return {
        "timestamp": ts,
        "conditionId": condition,
        "side": "BUY",
        "outcome": outcome,
        "price": price,
        "size": size,
        "title": "Bitcoin Up or Down - August 27, 7:30AM-7:45AM ET",
        "transactionHash": f"0x{ts}{outcome}",
    }


def test_fifo_pairing_preserves_directional_residual():
    rows = [
        _row(1, "Down", 0.70, 10),
        _row(2, "Up", 0.25, 6),
    ]
    out = analyze_condition(rows, "0xabc")
    assert out["matched_quantity_fifo"] == 6
    assert abs(out["fifo_gross_pair_edge_usd"] - 0.30) < 1e-9
    assert out["residual"]["DOWN"]["quantity"] == 4
    assert out["residual"]["UP"]["quantity"] == 0


def test_fifo_can_contain_profitable_lots_even_when_later_inventory_is_expensive():
    rows = [
        _row(1, "Down", 0.61, 5),
        _row(2, "Up", 0.22, 5),
        _row(3, "Down", 0.80, 5),
        _row(4, "Up", 0.40, 5),
    ]
    out = analyze_condition(rows, "0xabc")
    assert out["matched_quantity_fifo"] == 10
    # First five pairs make 17c each; second five lose 20c each.
    assert abs(out["fifo_gross_pair_edge_usd"] - (-0.15)) < 1e-9
    assert out["fifo_positive_gross_pair_quantity"] == 5
    assert out["fifo_gross_edge_ge_3c_quantity"] == 5


def test_modeled_taker_fees_reduce_pair_edge():
    rows = [_row(1, "Up", 0.30, 1), _row(2, "Down", 0.60, 1)]
    out = analyze_condition(rows, "0xabc")
    expected_fees = modeled_taker_fee(0.30, 1) + modeled_taker_fee(0.60, 1)
    assert abs(out["fifo_gross_pair_edge_usd"] - 0.10) < 1e-9
    assert abs(out["fifo_modeled_taker_fees_usd"] - expected_fees) < 1e-9
    assert out["fifo_modeled_net_pair_edge_usd"] < out["fifo_gross_pair_edge_usd"]


def test_filters_other_conditions_and_nonbuys():
    rows = [
        _row(1, "Up", 0.30, 1),
        {**_row(2, "Down", 0.60, 1), "side": "SELL"},
        _row(3, "Down", 0.60, 1, condition="0xother"),
    ]
    out = analyze_condition(rows, "0xabc")
    assert out["buy_fill_count"] == 1
    assert out["matched_quantity_fifo"] == 0
    assert out["residual"]["UP"]["quantity"] == 1
