from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src import sports_kp_live_combo_supervisor_v5 as v5


def _market() -> dict:
    return {
        "ticker": "KXTEST-MKT",
        "status": "open",
        "close_time": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
        "yes_bid_dollars": "0.70",
        "yes_bid_size_fp": "3.00",
        "yes_ask_dollars": "0.72",
        "yes_ask_size_fp": "4.00",
        "no_bid_dollars": "0.28",
        "no_ask_dollars": "0.30",
    }


def test_generated_yes_leg_uses_yes_bid_depth_for_no_hedge():
    horizon = datetime.now(timezone.utc) + timedelta(hours=3)
    leg = v5._fixed_leg_from_market(_market(), "KXTEST-EVENT", "yes", horizon)
    assert leg is not None
    assert leg.hedge_side == "no"
    assert leg.hedge_price == Decimal("0.30")
    assert leg.hedge_depth == Decimal("3.00")


def test_existing_mve_scan_uses_yes_bid_depth_for_no_hedge():
    hedge = v5._fixed_hedge_quote(_market(), "yes")
    assert hedge["ok"] is True
    assert hedge["hedge_side"] == "no"
    assert hedge["price"] == Decimal("0.30")
    assert hedge["depth"] == Decimal("3.00")


def test_live_q1_revalidation_uses_yes_bid_depth_for_no_hedge():
    plan = v5._fixed_make_hedge(_market(), "yes")
    assert plan is not None
    assert plan.hedge_side == "no"
    assert plan.price == Decimal("0.30")
    assert plan.depth == Decimal("3.00")
    assert plan.leg.native_outcome == "NO"
    assert plan.leg.yes_book_limit == Decimal("0.70")
