from decimal import Decimal as D
from types import SimpleNamespace

from src import gemini_kalshi_basis_execution_revalidation as reval
from src.gemini_kalshi_execution_shadow import TopOfBook


def _plan(direction):
    return reval.ExactLimitPlan(
        asset="BTC",
        direction=direction,
        quantity=D("5"),
        opened_ms=1000,
        gemini_symbol="g",
        kalshi_ticker="k",
        gemini_limit=D("0.40"),
        kalshi_limit=D("0.50"),
        entry_locked_profit=D("0.10"),
        entry_capital=D("4.90"),
        entry_basis_2bp=True,
        entry_basis_5bp=False,
    )


def test_same_limits_gemini_yes_kalshi_no_requires_price_and_depth():
    plan = _plan("GEMINI_YES_KALSHI_NO")
    g = TopOfBook("gemini", "g", 1, D("0.39"), D("9"), D("0.40"), D("5"))
    # k.no_ask = 1 - yes_bid = .50, no depth = yes bid size = 5
    k = TopOfBook("kalshi", "k", 1, D("0.50"), D("5"), D("0.51"), D("9"))
    result = reval.same_limits_marketable(plan, g, k)
    assert result["both_marketable"] is True

    k_too_thin = TopOfBook("kalshi", "k", 1, D("0.50"), D("4"), D("0.51"), D("9"))
    assert reval.same_limits_marketable(plan, g, k_too_thin)["both_marketable"] is False


def test_same_limits_kalshi_yes_gemini_no_uses_transformed_no_book():
    plan = _plan("KALSHI_YES_GEMINI_NO")
    # Gemini NO ask = 1 - yes_bid = .40 with size 5.
    g = TopOfBook("gemini", "g", 1, D("0.60"), D("5"), D("0.61"), D("9"))
    k = TopOfBook("kalshi", "k", 1, D("0.49"), D("9"), D("0.50"), D("5"))
    result = reval.same_limits_marketable(plan, g, k)
    assert result["both_marketable"] is True

    # Worse Gemini NO ask (.41) violates the original .40 limit.
    worse_g = TopOfBook("gemini", "g", 1, D("0.59"), D("5"), D("0.61"), D("9"))
    assert reval.same_limits_marketable(plan, worse_g, k)["both_marketable"] is False


def test_plan_captures_individual_limits_not_only_total_cost():
    g = TopOfBook("gemini", "g", 1000, D("0.60"), D("5"), D("0.61"), D("5"))
    k = TopOfBook("kalshi", "k", 1000, D("0.48"), D("5"), D("0.50"), D("5"))
    candidate = SimpleNamespace(locked_profit=D("0.10"), net_cost=D("4.90"))
    snapshot = {
        "research_proxy_gate_2bp_fresh_1000ms": True,
        "research_proxy_gate_5bp_fresh_1000ms": True,
    }
    plan = reval._plan_from_books(
        asset="BTC",
        pair=SimpleNamespace(gemini_symbol="g", kalshi_ticker="k"),
        direction="GEMINI_YES_KALSHI_NO",
        quantity=D("5"),
        candidate=candidate,
        g=g,
        k=k,
        opened_ms=1000,
        snapshot=snapshot,
    )
    assert plan.gemini_limit == D("0.61")
    assert plan.kalshi_limit == D("0.52")  # transformed NO ask = 1 - YES bid
    assert plan.entry_basis_5bp is True
