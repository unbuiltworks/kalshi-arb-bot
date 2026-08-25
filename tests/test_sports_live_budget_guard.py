from decimal import Decimal

import pytest

from src.sports_live_budget_guard import (
    CUMULATIVE_BUDGET,
    budget_used_from_trades,
    duplicate_settlement_exposure,
)


SETTLEMENT_KEY = (
    "baseball|mlb|mil,sea|player_prop|full_contest|player|"
    "taylorward|home_runs|GE|1"
)


def _row(*, trade_id: str, status: str, capital: str, reserved: str, market: str = SETTLEMENT_KEY):
    return {
        "trade_id": trade_id,
        "strategy": "sports_gk_autonomous_rule_risk",
        "mode": "LIVE_PROBE",
        "market": market,
        "status": status,
        "capital_filled": capital,
        "metadata": {"reserved_usd": reserved},
    }


def test_cumulative_live_risk_ceiling_is_five_dollars():
    assert CUMULATIVE_BUDGET == Decimal("5")


def test_settled_filled_trade_permanently_blocks_duplicate_settlement_key():
    prior = _row(
        trade_id="sports-gk:prior",
        status="SETTLED_COMPATIBLE",
        capital="0.95",
        reserved="0.97",
    )

    blocked_by = duplicate_settlement_exposure([prior], SETTLEMENT_KEY)

    assert blocked_by is prior


def test_active_reservation_blocks_duplicate_before_any_fill():
    prior = _row(
        trade_id="sports-gk:reserved",
        status="CAPITAL_RESERVED",
        capital="0",
        reserved="0.97",
    )

    assert duplicate_settlement_exposure([prior], SETTLEMENT_KEY) is prior


def test_released_zero_fill_reservation_allows_later_retry():
    released = _row(
        trade_id="sports-gk:released",
        status="CAPITAL_RELEASED",
        capital="0",
        reserved="0.97",
    )

    assert duplicate_settlement_exposure([released], SETTLEMENT_KEY) is None
    assert budget_used_from_trades([released]) == Decimal("0")


def test_release_does_not_erase_actual_fill_exposure():
    filled_then_released = _row(
        trade_id="sports-gk:filled",
        status="CAPITAL_RELEASED",
        capital="0.95",
        reserved="0.97",
    )

    assert duplicate_settlement_exposure([filled_then_released], SETTLEMENT_KEY) is filled_then_released
    assert budget_used_from_trades([filled_then_released]) == Decimal("0.95")


def test_other_market_does_not_block_exact_settlement_key():
    other = _row(
        trade_id="sports-gk:other",
        status="SETTLED_COMPATIBLE",
        capital="0.95",
        reserved="0.97",
        market=SETTLEMENT_KEY.replace("GE|1", "GE|2"),
    )

    assert duplicate_settlement_exposure([other], SETTLEMENT_KEY) is None


def test_blank_settlement_key_fails_closed():
    with pytest.raises(ValueError, match="settlement_key is required"):
        duplicate_settlement_exposure([], "")
