from decimal import Decimal

import pytest

from src.gemini_kalshi_btc_ws_capital import (
    capital_economics,
    settlement_delay_scenarios_from_summary,
)


def _row(ts: int, *, positive: bool = True, quantity: str = "10"):
    candidates = []
    if positive:
        candidates = [{
            "direction": "KALSHI_YES_GEMINI_NO",
            "quantity": quantity,
            "net_cost": str(Decimal("0.95") * Decimal(quantity)),
            "fees": "0.10",
            "locked_profit": str(Decimal("0.05") * Decimal(quantity)),
            "roi": str(Decimal("0.05") / Decimal("0.95")),
            "quote_skew_ms": 5,
        }]
    return {
        "asset": "BTC",
        "window_end_utc": "1970-01-01T01:00:00+00:00",
        "gemini_symbol": "GEMI-BTC15M-W1-UP",
        "kalshi_ticker": "KXBTC15M-W1",
        "receive_ts_ms": ts,
        "gemini_receive_ts_ms": ts - 3,
        "kalshi_receive_ts_ms": ts - 8,
        "kalshi_exchange_to_receive_ms": 7,
        "gemini_yes_bid": "0.45",
        "gemini_yes_bid_size": quantity,
        "gemini_yes_ask": "0.46",
        "gemini_yes_ask_size": quantity,
        "kalshi_yes_bid": "0.39",
        "kalshi_yes_bid_size": quantity,
        "kalshi_yes_ask": "0.40",
        "kalshi_yes_ask_size": quantity,
        "both_taker_fee_candidates": candidates,
        "read_only": True,
        "order_entry": False,
    }


def test_capital_hours_use_fresh_consumable_capital_to_window_end():
    rows = [_row(0), _row(100), _row(200, positive=False)]
    result = capital_economics(rows)
    entry = result["entry"]
    assert Decimal(entry["consumable_shadow_pnl_dollars"]) == Decimal("0.50")
    assert Decimal(entry["consumable_capital_deployed_dollars"]) == Decimal("9.50")
    assert Decimal(entry["consumable_capital_hours"]) == Decimal("9.50")
    assert Decimal(entry["consumable_profit_per_capital_hour"]) == Decimal("0.50") / Decimal("9.50")
    assert entry["post_close_settlement_s_assumed"] == "0"
    assert result["projection_warning"] == "NAIVE_SHORT_SAMPLE_NOT_A_FORECAST"
    assert result["order_entry"] is False


def test_latency_scenario_uses_execution_event_time_for_lock_duration():
    rows = [_row(0), _row(100), _row(200, positive=False)]
    result = capital_economics(rows)
    scenario = result["survival"]["50"]
    expected_hours = Decimal("9.50") * (Decimal("3600000") - Decimal("100")) / Decimal("3600000")
    assert Decimal(scenario["consumable_capital_hours"]) == expected_hours
    assert scenario["consumable_contracts"] == "10"


def test_repeated_resting_depth_does_not_multiply_capital_hours():
    rows = [_row(0), _row(100), _row(200, positive=False), _row(300), _row(400), _row(500, positive=False)]
    result = capital_economics(rows)
    assert result["entry"]["consumable_contracts"] == "10"
    assert Decimal(result["entry"]["consumable_capital_hours"]) == Decimal("9.50")


def test_empirical_settlement_delay_increases_capital_hours_without_changing_pnl():
    rows = [_row(0), _row(100), _row(200, positive=False)]
    result = capital_economics(rows, {"p50": Decimal("300"), "p95": Decimal("600")})

    base = result["entry"]
    p50 = result["settlement_delay_adjusted"]["p50"]["entry"]
    p95 = result["settlement_delay_adjusted"]["p95"]["entry"]

    assert p50["consumable_shadow_pnl_dollars"] == base["consumable_shadow_pnl_dollars"]
    assert p95["consumable_shadow_pnl_dollars"] == base["consumable_shadow_pnl_dollars"]
    assert Decimal(p50["consumable_capital_hours"]) == Decimal("9.50") * (Decimal("3900") / Decimal("3600"))
    assert Decimal(p95["consumable_capital_hours"]) == Decimal("9.50") * (Decimal("4200") / Decimal("3600"))
    assert Decimal(p95["consumable_profit_per_capital_hour"]) < Decimal(p50["consumable_profit_per_capital_hour"])
    assert p50["post_close_settlement_s_assumed"] == "300"
    assert result["window_end_only_warning"] == "LOWER_BOUND_UNLESS_SETTLEMENT_DELAY_ADJUSTED_IS_SUPPLIED"


def test_extracts_btc_p50_p95_from_settlement_latency_summary():
    payload = {
        "by_asset": {
            "BTC": {
                "close_to_settlement": {
                    "n": 100,
                    "median_s": 305.5,
                    "p95_s": 412.25,
                }
            }
        }
    }
    assert settlement_delay_scenarios_from_summary(payload) == {
        "p50": Decimal("305.5"),
        "p95": Decimal("412.25"),
    }


def test_settlement_latency_summary_fails_closed_when_missing_btc():
    with pytest.raises(ValueError, match="missing BTC"):
        settlement_delay_scenarios_from_summary({"by_asset": {}})
