"""Per-fill outcome rows, and the two adverse-selection denominators.

VALIDATED AGAINST A REAL SETTLEMENT. Campaign E's three venue-confirmed fills
(UP 0.57, DOWN 0.42, UP 0.30) in a window that resolved DOWN must reproduce
the venue's own realized_net_usd of -0.2900 from the per-fill gross figures:
-0.57 + 0.58 - 0.30. If that identity breaks, the join is wrong.
"""
from decimal import Decimal as D

import pytest

from src.l5_fill_outcome import fill_outcomes

CAMP_E = [{"coid": "l5f-5134e3b3", "side": "UP", "qty": "1", "own_px": "0.57"},
          {"coid": "l5f-aab4d9a8", "side": "DOWN", "qty": "1", "own_px": "0.42"},
          {"coid": "l5f-d6d5a2ce", "side": "UP", "qty": "1", "own_px": "0.30"}]


def campE(**kw):
    base = dict(slug="cpc-btc-updown-15m-2026-11-05-0245z", fills=CAMP_E,
                settled_side="DOWN", actual_fees_usd="0",
                resolution_source="VENUE_SETTLEMENT_PX")
    base.update(kw)
    return fill_outcomes(**base)


# ------------------------------------------------- the venue identity

def test_per_fill_gross_reproduces_the_venue_realized_net():
    r = campE()
    tot = sum(D(f["gross_settle_usd"]) for f in r["fills"])
    assert tot == D("-0.29"), tot


def test_inventory_lines_match_the_venue():
    r = campE()
    assert r["matched_qty"] == "1"
    assert r["residual_qty"] == "1"
    assert r["residual_side"] == "UP"
    assert r["qty_up"] == "2" and r["qty_down"] == "1"


def test_the_window_contributes_a_losing_residual():
    """The market resolved DOWN and we were left long UP."""
    assert campE()["residual_side_lost"] is True


def test_complement_allocation_is_in_fill_order_and_never_over_credits():
    r = campE()
    by = {f["coid"]: f for f in r["fills"]}
    assert by["l5f-5134e3b3"]["complemented"] is True
    assert by["l5f-aab4d9a8"]["complemented"] is True
    assert by["l5f-d6d5a2ce"]["complemented"] is False
    assert sum(D(f["complement_qty"]) for f in r["fills"]) == D(2)


def test_on_winner_is_per_side():
    by = {f["coid"]: f for f in campE()["fills"]}
    assert by["l5f-aab4d9a8"]["on_winner"] is True
    assert by["l5f-5134e3b3"]["on_winner"] is False
    assert by["l5f-d6d5a2ce"]["on_winner"] is False


def test_uncomplemented_denominator():
    r = campE()
    assert r["uncomplemented_fills"] == 1
    assert r["uncomplemented_lost"] == 1


# ------------------------------------- unknown settlement is never a guess

@pytest.mark.parametrize("bad", [None, "", "  ", "MAYBE", "PENDING", 0])
def test_unresolved_settlement_contributes_to_no_denominator(bad):
    r = campE(settled_side=bad)
    assert r["settled_side"] is None
    assert r["residual_side_lost"] is None, "an unresolved window is not 50/50"
    assert r["uncomplemented_fills"] == 0
    assert all(f["on_winner"] is None for f in r["fills"])
    assert all(f["gross_settle_usd"] is None for f in r["fills"])


def test_a_flat_window_has_no_residual_observation():
    r = fill_outcomes(slug="m", settled_side="UP",
                      fills=[{"coid": "a", "side": "UP", "qty": "1",
                              "own_px": "0.50"},
                             {"coid": "b", "side": "DOWN", "qty": "1",
                              "own_px": "0.46"}])
    assert r["residual_qty"] == "0"
    assert r["residual_side"] is None
    assert r["residual_side_lost"] is None
    assert r["uncomplemented_fills"] == 0


def test_no_fills_yields_no_rows_rather_than_zeros():
    r = fill_outcomes(slug="m", fills=[], settled_side="UP")
    assert r["fills"] == []
    assert r["n_fills"] == 0
    assert r["residual_side_lost"] is None


# ------------------------------------------------- unreadable inputs

@pytest.mark.parametrize("f", [
    {"coid": "x", "side": "SIDEWAYS", "qty": "1", "own_px": "0.5"},
    {"coid": "x", "side": "UP", "qty": "nope", "own_px": "0.5"},
    {"coid": "x", "side": "UP", "qty": "0", "own_px": "0.5"},
    {"coid": "x", "side": "UP", "qty": "-1", "own_px": "0.5"},
    {"coid": "x", "side": None, "qty": "1", "own_px": "0.5"},
])
def test_an_unreadable_fill_is_named_not_dropped(f):
    r = fill_outcomes(slug="m", fills=[f], settled_side="UP")
    assert len(r["fills"]) == 1
    assert r["fills"][0]["unreadable"] is True
    assert r["n_fills"] == 0


def test_nan_and_inf_prices_do_not_produce_a_settle_figure():
    for px in ("nan", "inf", "-inf"):
        r = fill_outcomes(slug="m", settled_side="UP",
                          fills=[{"coid": "x", "side": "UP", "qty": "1",
                                  "own_px": px}])
        assert r["fills"][0]["own_px"] is None
        assert r["fills"][0]["gross_settle_usd"] is None


def test_yes_no_are_read_as_up_down():
    r = fill_outcomes(slug="m", settled_side="YES",
                      fills=[{"coid": "a", "side": "YES", "qty": "1",
                              "own_px": "0.5"}])
    assert r["fills"][0]["side"] == "UP"
    assert r["settled_side"] == "UP"
    assert r["fills"][0]["on_winner"] is True


def test_the_two_denominators_are_documented_as_not_interchangeable():
    lab = campE()["labels"]
    assert "not" in lab.lower() and "interchangeable" in lab.lower()


def test_rows_carry_the_evidence_fields_the_owner_asked_for():
    r = fill_outcomes(slug="m", settled_side="UP",
                      fills=[{"coid": "a", "side": "UP", "qty": "1",
                              "own_px": "0.5", "queue_ahead": "120",
                              "level_depth": "900", "latency_s": "0.42",
                              "order_resting": True}])
    f = r["fills"][0]
    for k in ("queue_ahead", "level_depth", "latency_s", "order_resting",
              "own_px", "qty", "complemented", "on_winner",
              "gross_settle_usd", "resolution_source"):
        assert k in f, k
    assert f["latency_s"] == "0.42"
