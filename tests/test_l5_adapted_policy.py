"""The ADAPTED arms are frozen; these pin the policy so it cannot drift."""
from decimal import Decimal as D

import pytest

from src.l5_shadow import adapted_policy as P


def _fill(s, side, n=1):
    for _ in range(n):
        s = P.on_evidenced_fill(s, side=side, qty=D("10"), cost=D("5"))
    return s


def test_the_frozen_arm_set_is_exactly_three():
    assert P.ARMS == ("cap5", "cap8", "cap13")
    assert P.CAPS == (5, 8, 13)
    with pytest.raises(ValueError):
        P.arm_cap("cap2")               # not in the preregistered family


def test_both_sides_quote_until_the_cap_is_reached():
    s = _fill(P.new_state(), "Up", 4)
    q = P.plan(s, arm="cap5", tte_s=500, best_bid_up=D("0.5"),
               best_bid_dn=D("0.5"))
    assert {x["side"] for x in q["quotes"]} == {"Up", "Down"}
    assert q["withheld_side"] is None


def test_the_run_side_is_withheld_at_the_cap():
    s = _fill(P.new_state(), "Up", 5)
    q = P.plan(s, arm="cap5", tte_s=500, best_bid_up=D("0.5"),
               best_bid_dn=D("0.5"))
    assert q["withheld_side"] == "Up"
    assert [x["side"] for x in q["quotes"]] == ["Down"]


def test_a_fill_on_the_other_side_resets_the_run():
    s = _fill(P.new_state(), "Up", 6)
    assert P.withheld(s, arm="cap5") == "Up"
    s = _fill(s, "Down", 1)
    assert P.withheld(s, arm="cap5") is None


def test_a_looser_cap_still_quotes_where_a_tighter_one_withholds():
    s = _fill(P.new_state(), "Up", 6)
    assert P.withheld(s, arm="cap5") == "Up"
    assert P.withheld(s, arm="cap8") is None
    assert P.withheld(s, arm="cap13") is None


def test_nothing_is_quoted_outside_the_band():
    s = P.new_state()
    for tte in (901.0, 59.0):
        assert P.plan(s, arm="cap8", tte_s=tte, best_bid_up=D("0.5"),
                      best_bid_dn=D("0.5"))["quotes"] == []


def test_every_quote_is_a_non_crossing_maker_bid_and_shadow_only():
    s = P.new_state()
    q = P.plan(s, arm="cap8", tte_s=500, best_bid_up=D("0.5"),
               best_bid_dn=D("0.5"))
    assert q["shadow_only"] is True and q["would_submit"] is False
    for x in q["quotes"]:
        assert x["type"] == "maker_bid" and x["crosses"] is False


def test_the_reference_ceiling_never_blocks_a_quote():
    """0.96 is a reference here. A pair already far above it must not stop
    either side from quoting -- gating entry on it is what could not bootstrap."""
    s = P.new_state()
    s = P.on_evidenced_fill(s, side="Up", qty=D("10"), cost=D("9"))    # 0.90
    s = P.on_evidenced_fill(s, side="Down", qty=D("10"), cost=D("9"))  # 0.90
    e = P.economics(s)
    assert e["pair_cost"] == "1.8"
    assert e["pair_cost_vs_reference"] == "over_reference"
    q = P.plan(s, arm="cap8", tte_s=500, best_bid_up=D("0.5"),
               best_bid_dn=D("0.5"))
    assert len(q["quotes"]) == 2


def test_economics_reports_matched_and_residual_without_calling_it_realized():
    s = _fill(P.new_state(), "Up", 3)
    s = _fill(s, "Down", 1)
    e = P.economics(s)
    assert e["matched_qty"] == "10"
    assert e["residual_qty"] == "20" and e["residual_side"] == "Up"
    assert "Never realized profit" in e["labels"]
