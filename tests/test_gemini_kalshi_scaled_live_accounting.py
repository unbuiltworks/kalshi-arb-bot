from decimal import Decimal

import pytest

from src.gemini_kalshi_scaled_live_accounting import scaled_costs, scaled_realized_pnl

D = Decimal


def _record(gqty="5", kqty="5"):
    return {
        "gemini_payload": {"eventOutcome": "NO", "symbol": "GEMI-BTC15M-X-UP"},
        "kalshi_payload": {"side": "bid", "ticker": "KXBTC15M-X"},
        "result": {
            "gemini": {
                "filled_quantity": gqty,
                "average_fill_price": "0.25",
                "fee_paid": "0.05",
            },
            "kalshi": {
                "filled_quantity": kqty,
                "average_fill_price": "0.60",
                "fee_paid": "0.10",
            },
        },
    }


def test_q5_costs_are_not_silently_counted_as_q1():
    costs = scaled_costs(_record(), required_quantity=D("5"))
    assert costs["raw_cost"] == D("4.25")
    assert costs["fees"] == D("0.15")
    assert costs["capital"] == D("4.40")
    assert costs["gemini_amount"] == D("1.30")
    assert costs["kalshi_amount"] == D("3.10")


def test_q5_agreeing_settlement_pays_five_not_one():
    record = _record()
    costs = scaled_costs(record, required_quantity=D("5"))
    settlement = {"gemini_outcome": "yes", "kalshi_outcome": "yes"}
    pnl = scaled_realized_pnl(record, costs, settlement, required_quantity=D("5"))
    assert pnl["payout"] == D("5")
    assert pnl["gross_pnl"] == D("0.75")
    assert pnl["net_pnl"] == D("0.60")


def test_q5_index_disagreement_math_counts_each_actual_winning_leg():
    record = _record()
    costs = scaled_costs(record, required_quantity=D("5"))
    settlement = {"gemini_outcome": "no", "kalshi_outcome": "yes"}
    pnl = scaled_realized_pnl(record, costs, settlement, required_quantity=D("5"))
    assert pnl["payout"] == D("10")
    assert pnl["net_pnl"] == D("5.60")


def test_q5_costs_fail_closed_on_quantity_mismatch():
    with pytest.raises(RuntimeError, match="unexpected scaled live quantities"):
        scaled_costs(_record(gqty="5", kqty="1"), required_quantity=D("5"))
