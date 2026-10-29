"""BTC15m isolated — the cohort where L5 is 96% two-sided and we are 0%.

Two things under test. The per-leg walk must report the pair cost as a PATH
(the running number at each leg) rather than an endpoint, because the whole
mechanism turns out to live in the difference. And the control-vs-emergent
discriminator must be able to return either answer: a median near 0.96 is not
evidence of a 0.96 target, and a test suite that can only confirm one verdict
is not testing anything.
"""
import math
from decimal import Decimal as D

import pytest

import analysis.l5.pair_formation.btc15m as B


def _f(outcome, qty, price, t, *, tte=800, seq=1, slug="m"):
    return {"window_id": "w", "market_slug": slug, "asset": "BTC",
            "duration_minutes": "15", "outcome": outcome, "side": "BUY",
            "quantity": str(qty), "price": str(price), "timestamp": str(t),
            "trade_seq": str(seq), "seconds_to_settlement": str(tte)}


# ------------------------------------------- the pair cost is a path

def test_the_running_pair_cost_is_reported_at_every_leg():
    legs = B.walk([_f("Up", 10, "0.50", 0, seq=1),
                   _f("Down", 10, "0.49", 10, seq=2),
                   _f("Down", 10, "0.35", 20, seq=3)])
    assert legs[0]["effective_pair_cost"] is None      # one-sided
    assert legs[1]["effective_pair_cost"] == "0.990000"
    assert legs[2]["effective_pair_cost"] == "0.920000"


def test_a_one_sided_leg_has_no_pair_cost_rather_than_zero():
    legs = B.walk([_f("Up", 10, "0.50", 0)])
    assert legs[0]["effective_pair_cost"] is None
    assert legs[0]["matched_qty"] == "0"


def test_each_leg_carries_the_inventory_and_residual_at_that_point():
    legs = B.walk([_f("Up", 30, "0.50", 0, seq=1),
                   _f("Down", 10, "0.49", 10, seq=2)])
    assert legs[1]["cum_up"] == "30" and legs[1]["cum_dn"] == "10"
    assert legs[1]["matched_qty"] == "10" and legs[1]["residual"] == "20"


def test_formation_separates_the_pair_cost_at_entry_from_the_final_one():
    """The mechanism lives in this difference: L5 forms at a median 0.99 and
    grinds to 0.9624 over a median 121 further legs."""
    f = B.formation([_f("Up", 10, "0.50", 0, seq=1),
                     _f("Down", 10, "0.49", 10, seq=2),
                     _f("Down", 10, "0.35", 20, seq=3)])
    assert f["pair_cost_at_formation"] == "0.990000"
    assert f["final_pair_cost"] == "0.920000"
    assert f["complement_at_fill_n"] == 2
    assert f["seconds_to_complement"] == 10.0


def test_extends_before_the_complement_are_counted():
    f = B.formation([_f("Up", 10, "0.50", 0, seq=1),
                     _f("Up", 10, "0.51", 5, seq=2),
                     _f("Down", 10, "0.49", 10, seq=3)])
    assert f["extends_before_complement"] == 1
    assert f["complement_at_fill_n"] == 3


def test_a_window_that_never_goes_two_sided_has_no_formation():
    assert B.formation([_f("Up", 10, "0.50", 0), _f("Up", 5, "0.51", 9)]) is None


# --------------------------- the discriminator can return either answer

def _forms(pairs):
    return [{"first_price": str(a), "complement_price": str(b),
             "pair_cost_at_formation": str(round(a + b, 6))}
            for a, b in pairs]


def test_a_steered_pair_reads_as_controlled():
    """Cheap first leg followed by a dearer complement: the sum is pinned."""
    pairs = [(0.40 + i * 0.01, 0.59 - i * 0.01) for i in range(60)]
    r = B.control_or_emergent(_forms(pairs))
    assert r["decided"] is True
    assert r["first_leg_vs_complement_corr"] < -0.9
    assert r["tightening_ratio"] < 0.1
    assert r["reading"].startswith("CONTROLLED")


def test_independent_legs_read_as_emergent():
    """The negative control. A suite that can only confirm 'controlled' is not
    testing anything, and a median near 0.96 would look like a target."""
    import random
    random.seed(7)
    pairs = [(round(random.uniform(0.30, 0.70), 4),
              round(random.uniform(0.30, 0.70), 4)) for _ in range(400)]
    r = B.control_or_emergent(_forms(pairs))
    assert r["reading"].startswith("EMERGENT")
    assert abs(r["first_leg_vs_complement_corr"]) < 0.15
    assert r["tightening_ratio"] > 0.7


def test_too_few_formations_decides_nothing():
    r = B.control_or_emergent(_forms([(0.4, 0.5)] * 10))
    assert r["decided"] is False and "too few" in r["why"]


def test_the_discriminator_refuses_to_read_a_median_as_a_target():
    r = B.control_or_emergent(_forms([(0.40 + i * 0.001, 0.56 - i * 0.0005)
                                      for i in range(100)]))
    assert "NOT evidence of a 0.96 target" in r["labels"]


def test_a_partial_signal_is_called_mixed_and_not_forced():
    assert B._reading(0.10, 0.12, -0.5).startswith("MIXED")
    assert B._reading(0.05, 0.12, -0.05).startswith("MIXED")


# ------------------------------------------ our ceiling never filters this

def test_our_ceiling_is_reported_for_comparison_and_filters_nothing():
    from src.l5_shadow import two_sided as TS
    assert B.ADAPTED_CEILING == TS.ADAPTED_EXECUTABLE_PAIR_CEILING
    legs = B.walk([_f("Up", 10, "0.60", 0, seq=1),
                   _f("Down", 10, "0.55", 10, seq=2)])
    # 1.15 is far outside our ceiling and is measured anyway
    assert legs[1]["effective_pair_cost"] == "1.150000"


def test_the_report_says_it_is_not_our_fill_or_realized_pnl():
    r = B.report(B.SEQ, limit=500)
    assert "Not our fill" in r["labels"] and "realized P&L" in r["labels"]
    assert "never filters" in r["labels"]
