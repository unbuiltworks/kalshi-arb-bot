from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.kalshi_nested_bounded_hedge import (
    allocate_fragment_loss_budget,
    build_bounded_fragment_hedge,
)
from src.kalshi_nested_live_probe_guard import NestedProbePlan, ProbeLeg

D = Decimal
NY = ZoneInfo("America/New_York")
EVENT = "KXSOLD-26AUG1620"


def _plan():
    return NestedProbePlan(
        series="KXSOLD",
        event_ticker=EVENT,
        event_close=datetime(2026, 8, 16, 20, 0, tzinfo=NY),
        maker=ProbeLeg(EVENT + "-T75.7499", "no", D("0.95")),
        hedge=ProbeLeg(EVENT + "-T75.2499", "yes", D("0.01")),
        fee_type="quadratic",
        fee_multiplier=D("1"),
        target_profit=D("0.03"),
    )


def test_fractional_loss_budget_is_pro_rata_not_repeated_per_fill():
    assert allocate_fragment_loss_budget(
        total_probe_loss_budget=D("0.04"), quantity=D("0.25")
    ) == D("0.0100")
    assert allocate_fragment_loss_budget(
        total_probe_loss_budget=D("0.04"), quantity=D("0.75")
    ) == D("0.0300")


def test_fragment_budgets_sum_to_probe_budget():
    total = D("0.05")
    quantities = [D("0.10"), D("0.25"), D("0.65")]
    allocated = sum(
        allocate_fragment_loss_budget(total_probe_loss_budget=total, quantity=q)
        for q in quantities
    )
    assert allocated == total


def test_bounded_hedge_is_fok_and_within_fragment_loss_cap():
    bounded = build_bounded_fragment_hedge(
        plan=_plan(),
        quantity=D("0.50"),
        total_probe_loss_budget=D("0.04"),
        client_order_id="bounded-1",
    )
    assert bounded.payload["count"] == "0.50"
    assert bounded.payload["time_in_force"] == "fill_or_kill"
    assert bounded.payload["post_only"] is False
    assert bounded.fragment_loss_budget == D("0.0200")
    assert bounded.rescue.pnl_at_rescue_limit >= -bounded.fragment_loss_budget


def test_total_budget_has_hard_first_probe_ceiling():
    with pytest.raises(ValueError, match="0.25"):
        allocate_fragment_loss_budget(
            total_probe_loss_budget=D("0.26"), quantity=D("1.00")
        )


def test_no_invalid_fragment_size():
    with pytest.raises(ValueError, match="quantity"):
        allocate_fragment_loss_budget(
            total_probe_loss_budget=D("0.04"), quantity=D("0.001")
        )
