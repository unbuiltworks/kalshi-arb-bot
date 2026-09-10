"""The risk-recycling ratio: the metric that says whether breadth helps.

OWNER: "we need a metric that tells us whether the engine is recycling risk
faster than it manufactures residuals. If that ratio is poor, more breadth
simply gets us to -$15 faster."
"""
from __future__ import annotations

from decimal import Decimal as D

from src import pmus_risk_recycling as R


def test_a_positive_floor_charges_the_budget_nothing():
    """A completed pair below par: min(up,dn) - spent >= 0."""
    assert R.floor_of("19.87", "19.87", "18.8307") > 0
    assert R.budget_contribution(R.floor_of("19.87", "19.87", "18.8307")) == 0


def test_a_one_sided_position_charges_its_whole_spend():
    assert R.floor_of("0", "11.24", "3.6028") == D("-3.6028")
    assert R.budget_contribution(D("-3.6028")) == D("-3.6028")


def test_completing_returns_risk():
    """The cricket-shaped market completing: -12.819 -> 0."""
    d = R.delta(R.floor_of("0", "15.3", "12.819"),
                R.floor_of("15.3", "15.3", "12.819"))
    assert d["risk_returned_usd"] == "12.819"
    assert d["risk_created_usd"] == "0"


def test_opening_creates_risk():
    d = R.delta(0, R.floor_of("0", "11.24", "3.6028"))
    assert d["risk_created_usd"] == "3.6028"
    assert d["risk_returned_usd"] == "0"


def test_no_action_needs_labelling_the_budget_says_which_happened():
    """Neither delta() nor recycling() takes an event type."""
    import inspect
    for fn in (R.delta, R.recycling):
        params = inspect.signature(fn).parameters
        assert not any(p in params for p in ("event", "relation", "action"))


def test_the_ratio_reads_above_one_when_recycling_outpaces_creation():
    out = R.recycling([
        {"slug": "done", "floor_before": "-10", "floor_after": "0"},
        {"slug": "new", "floor_before": "0", "floor_after": "-4"},
    ])
    assert out["risk_recycling_ratio"] == "2.5000"
    assert "recycling faster" in out["verdict"]


def test_the_ratio_reads_below_one_when_manufacturing_outpaces_recycling():
    out = R.recycling([
        {"slug": "done", "floor_before": "-2", "floor_after": "0"},
        {"slug": "a", "floor_before": "0", "floor_after": "-5"},
        {"slug": "b", "floor_before": "0", "floor_after": "-5"},
    ])
    assert D(out["risk_recycling_ratio"]) < 1
    assert "loss stop sooner" in out["verdict"]


def test_a_period_with_no_creation_is_UNDEFINED_not_infinite():
    """Dividing by zero would print a spectacular number for a period in
    which the engine did nothing."""
    out = R.recycling([{"slug": "done", "floor_before": "-5",
                        "floor_after": "0"}])
    assert out["risk_recycling_ratio"] is None
    assert "undefined" in out["verdict"]


def test_an_idle_market_contributes_to_neither_side():
    out = R.recycling([{"slug": "cricket", "floor_before": "-0.8015",
                        "floor_after": "-0.8015"}])
    assert out["risk_returned_usd"] == "0" and out["risk_created_usd"] == "0"


def test_a_favourable_market_cannot_subsidise_another_markets_loss():
    """min(0, floor) -- the same rule unsettled_floor_total enforces."""
    out = R.recycling([
        {"slug": "winner", "floor_before": "5", "floor_after": "9"},
        {"slug": "loser", "floor_before": "0", "floor_after": "-3"},
    ])
    assert out["risk_returned_usd"] == "0", "a positive floor returns nothing"
    assert out["risk_created_usd"] == "3"


# ------------------------------------------------------------- the funnel
def test_the_funnel_does_not_count_orders_or_attempts():
    """OWNER: 'Don't judge the new system primarily on number of orders.'"""
    for stage in R.FUNNEL_STAGES:
        assert "order" not in stage and "attempt" not in stage, stage


def test_the_funnel_carries_every_stage_the_owner_named():
    for want in ("first_leg_fills", "completions_le_1", "residuals_opened",
                 "completed_pair_headroom_returned_usd", "quarantine_entries",
                 "additional_acquisitions_admitted", "portfolio_gate_refusals",
                 "settlements", "trading_pnl_usd", "credited_incentives_usd"):
        assert want in R.FUNNEL_STAGES, want


def test_an_unmeasured_stage_is_None_and_is_named_not_silently_zero():
    out = R.funnel_report("hour", {"first_leg_fills": 0})
    assert out["funnel"]["first_leg_fills"] == 0
    assert out["funnel"]["settlements"] is None
    assert "settlements" in out["unmeasured_stages"]
    assert "first_leg_fills" not in out["unmeasured_stages"], \
        "a measured zero is not an unmeasured stage"


def test_the_ratio_is_prominent_in_the_report():
    out = R.funnel_report("hour", {}, [
        {"slug": "d", "floor_before": "-6", "floor_after": "0"},
        {"slug": "n", "floor_before": "0", "floor_after": "-2"}])
    assert out["risk_recycling_ratio"] == "3.0000"
    assert list(out)[2] == "risk_recycling_ratio", "ratio sits near the top"
