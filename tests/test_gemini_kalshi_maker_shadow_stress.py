from decimal import Decimal

from src.gemini_kalshi_execution_shadow import TopOfBook
from src.gemini_kalshi_maker_shadow_stress import PendingStress, stress_economics

D = Decimal


def test_stress_economics_marks_target_survival():
    pending = PendingStress(
        asset="SOL",
        key="x",
        direction="KALSHI_YES_GEMINI_NO",
        policy="JOIN",
        target_profit=D("0.03"),
        maker_price=D("0.50"),
        maker_fee=D("0"),
        fill_qty=D("1"),
        fill_receive_ms=1000,
        remaining_delays={25},
    )
    gemini = TopOfBook(
        venue="gemini", market_id="SOL", ts_ms=1025,
        yes_bid=D("0.40"), yes_bid_size=D("5"),
        yes_ask=D("0.42"), yes_ask_size=D("5"),
    )
    result = stress_economics(pending, gemini)
    assert result["hedge_depth_ok"] is True
    # KALSHI YES means Gemini NO hedge at 1 - Gemini YES bid = 0.60.
    # At 0.50 + 0.60 this must fail even before fee.
    assert result["survives_positive"] is False
    assert result["survives_target"] is False


def test_stress_economics_requires_q1_depth():
    pending = PendingStress(
        asset="BTC",
        key="x",
        direction="GEMINI_YES_KALSHI_NO",
        policy="MAX_SAFE",
        target_profit=D("0.02"),
        maker_price=D("0.40"),
        maker_fee=D("0"),
        fill_qty=D("1"),
        fill_receive_ms=1000,
        remaining_delays={50},
    )
    gemini = TopOfBook(
        venue="gemini", market_id="BTC", ts_ms=1050,
        yes_bid=D("0.45"), yes_bid_size=D("5"),
        yes_ask=D("0.50"), yes_ask_size=D("0.5"),
    )
    result = stress_economics(pending, gemini)
    assert result["hedge_depth_ok"] is False
    assert result["survives_positive"] is False
