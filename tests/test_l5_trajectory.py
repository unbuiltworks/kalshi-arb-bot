"""L5's own inventory trajectory — the thing the clone is failing to reproduce.

Measured at the source rather than assumed, because every episode our clone
produces is only interpretable against it. Two numbers out of this decide
arguments elsewhere: how fast L5 goes two-sided (whether a reactive clone has
time at all) and what pair cost he actually achieves (whether our own spring
discipline refuses the trade he makes).
"""
from decimal import Decimal as D

import pytest

import analysis.l5.pair_formation.l5_trajectory as TJ


def _f(outcome, qty, price, t, *, wid="w", slug="m", asset="BTC", dur="15"):
    return {"window_id": wid, "market_slug": slug, "asset": asset,
            "duration_minutes": dur, "outcome": outcome, "side": "BUY",
            "quantity": str(qty), "price": str(price), "timestamp": str(t)}


def test_a_one_sided_window_is_not_two_sided():
    t = TJ.trajectory([_f("Up", 10, "0.45", 100), _f("Up", 10, "0.46", 110)])
    assert t["two_sided"] is False
    assert t["pair_cost"] is None and t["t_to_two_sided_s"] is None
    assert t["residual_qty"] == "20"


def test_the_moment_both_sides_are_held_is_when_it_becomes_two_sided():
    t = TJ.trajectory([_f("Up", 10, "0.45", 100), _f("Up", 5, "0.46", 110),
                       _f("Down", 10, "0.50", 154)])
    assert t["two_sided"] is True
    assert t["t_to_two_sided_s"] == 54.0
    assert t["fills_before_two_sided"] == 2


def test_the_pair_cost_is_the_two_average_prices():
    t = TJ.trajectory([_f("Up", 10, "0.45", 100), _f("Down", 10, "0.50", 110)])
    assert t["pair_cost"] == "0.950000"
    assert t["pair_cost_within_our_target"] is True
    assert t["matched_qty"] == "10"


def test_a_pair_above_our_target_is_flagged_against_our_policy_not_his():
    """L5 makes it; our discipline refuses it. The flag records the conflict
    rather than deciding it."""
    t = TJ.trajectory([_f("Up", 10, "0.50", 100), _f("Down", 10, "0.49", 110)])
    assert t["pair_cost"] == "0.990000"
    assert t["pair_cost_within_our_target"] is False


def test_a_pair_costing_over_a_dollar_is_reported_not_discarded():
    """He does it -- ETH's median is above 1.00 -- and a guaranteed loss on the
    matched leg is a finding, not a parsing error."""
    t = TJ.trajectory([_f("Up", 10, "0.60", 100), _f("Down", 10, "0.55", 110)])
    assert D(t["pair_cost"]) > D("1")


def test_only_buys_count_toward_inventory():
    rows = [_f("Up", 10, "0.45", 100), dict(_f("Down", 10, "0.5", 110),
                                            side="SELL")]
    # a SELL is an exit; feeding it in as inventory would fake a pair
    t = TJ.trajectory([r for r in rows if r["side"] == "BUY"])
    assert t["two_sided"] is False


def test_the_residual_is_the_unmatched_remainder():
    t = TJ.trajectory([_f("Up", 30, "0.45", 100), _f("Down", 10, "0.50", 110)])
    assert t["matched_qty"] == "10" and t["residual_qty"] == "20"


def test_the_target_compared_against_is_our_own_spring_discipline():
    assert TJ.PAIR_TARGET == D("0.96")
    from src.l5_shadow import two_sided as TS
    assert TJ.PAIR_TARGET == TS.PAIR_TARGET


def test_the_report_says_a_low_target_rate_is_about_our_policy():
    import inspect
    src = inspect.getsource(TJ.report)
    assert "a fact about our policy, not about him" in src


def test_the_report_labels_none_of_it_as_our_fill_or_realized():
    """Checked on the emitted labels, not on source text: the label is what a
    reader of the report actually sees."""
    r = TJ.report(TJ.SEQ, limit=200)
    lab = r["labels"]
    assert "Not a fill of ours" in lab
    assert "not a shadow result" in lab and "not realized P&L" in lab
    assert r["our_pair_target"] == "0.96"
