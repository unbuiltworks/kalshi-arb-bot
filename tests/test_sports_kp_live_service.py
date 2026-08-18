from decimal import Decimal

from src.sports_kp_live_service import _fingerprint
from src.universal_prediction_quant import LockOpportunity

D = Decimal


def _lock(*, skew: int = 10, profit: str = "0.04") -> LockOpportunity:
    return LockOpportunity(
        settlement_key="baseball|mlb|a,b|game_total|full_contest|game||runs|GT|8.5",
        category="sports_mlb_game",
        yes_venue="polymarket",
        yes_market_id="poly#YES",
        no_venue="kalshi",
        no_market_id="kalshi#YES",
        unit_cost=D("1") - D(profit),
        unit_profit=D(profit),
        max_quantity=D("1"),
        max_capital=D("0.96"),
        max_locked_profit=D(profit),
        quote_skew_ms=skew,
        settlement_confidence="NORMAL_EVENT_EQUIVALENT_NOT_EXECUTABLE",
        expected_settlement_minutes=None,
        capital_velocity_score=D("0.04"),
    )


def test_persistence_fingerprint_ignores_receive_skew_noise():
    assert _fingerprint(_lock(skew=4)) == _fingerprint(_lock(skew=47))


def test_persistence_fingerprint_resets_when_executable_economics_change():
    assert _fingerprint(_lock(profit="0.04")) != _fingerprint(_lock(profit="0.05"))
