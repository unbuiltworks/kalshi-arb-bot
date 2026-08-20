from decimal import Decimal

from src.gemini_kalshi_scaled_live_accounting import scaled_costs, scaled_realized_pnl

D = Decimal


def _record():
    return {
        "gemini_payload": {
            "symbol": "GEMI-BTC15M-Q10-UP",
            "eventOutcome": "NO",
            "quantity": "10",
        },
        "kalshi_payload": {
            "ticker": "KXBTC15M-Q10",
            "side": "bid",
            "count": "10.00",
        },
        "result": {
            "outcome": "BOTH_FILLED",
            "gemini": {
                "filled_quantity": "10",
                "average_fill_price": "0.25",
                "fee_paid": "0.14",
            },
            "kalshi": {
                "filled_quantity": "10",
                "average_fill_price": "0.60",
                "fee_paid": "0.17",
            },
        },
    }


def test_q10_costs_use_all_ten_filled_contracts():
    costs = scaled_costs(_record(), required_quantity=D("10"))
    assert costs["quantity"] == D("10")
    assert costs["raw_cost"] == D("8.50")
    assert costs["fees"] == D("0.31")
    assert costs["capital"] == D("8.81")


def test_q10_normal_agreeing_settlement_pays_ten_not_one():
    record = _record()
    costs = scaled_costs(record, required_quantity=D("10"))
    settlement = {
        "gemini_outcome": "no",
        "kalshi_outcome": "no",
        "venue_outcomes_agree": True,
    }
    pnl = scaled_realized_pnl(record, costs, settlement, required_quantity=D("10"))
    assert pnl["payout"] == D("10")
    assert pnl["net_pnl"] == D("1.19")


def test_q10_index_disagreement_can_pay_twenty_or_zero_and_is_not_hidden():
    record = _record()
    costs = scaled_costs(record, required_quantity=D("10"))
    both_purchased_legs_win = {
        "gemini_outcome": "no",
        "kalshi_outcome": "yes",
        "venue_outcomes_agree": False,
    }
    both_purchased_legs_lose = {
        "gemini_outcome": "yes",
        "kalshi_outcome": "no",
        "venue_outcomes_agree": False,
    }
    win = scaled_realized_pnl(record, costs, both_purchased_legs_win, required_quantity=D("10"))
    lose = scaled_realized_pnl(record, costs, both_purchased_legs_lose, required_quantity=D("10"))
    assert win["payout"] == D("20")
    assert win["net_pnl"] == D("11.19")
    assert lose["payout"] == D("0")
    assert lose["net_pnl"] == D("-8.81")
