"""What L5's trades can and cannot tell us about where he quotes.

OWNER 2026-09-11: "reconstruct, wherever public data permits, L5's trade price
versus contemporaneous bid/ask... Do not make unsupported claims about L5 queue
position or self-trading."

The hard requirement here is NEGATIVE: there is no contemporaneous book for
these windows, so distance-from-touch and queue position must be refused
rather than estimated. Most of these tests exist to keep that refusal in
place, because an estimated queue number would be the most damaging output
this module could produce.
"""
from __future__ import annotations

import inspect

from src import l5_touch_proximity as tp


def _rows():
    return [
        {"maker_taker": "maker", "matched_pair_unit_cost": "0.95",
         "duration_minutes": "15"},
        {"maker_taker": "maker", "matched_pair_unit_cost": "0.99",
         "duration_minutes": "15"},
        {"maker_taker": "taker", "matched_pair_unit_cost": "1.04",
         "duration_minutes": "5"},
        {"maker_taker": "", "matched_pair_unit_cost": "0.97",
         "duration_minutes": "15"},
    ]


# --------------------------------------- the refusals, which are the point
def test_distance_from_touch_is_refused_not_estimated():
    assert "NOT COMPUTABLE" in tp.NOT_COMPUTABLE
    assert "not estimated" in tp.NOT_COMPUTABLE
    out = tp.pair_cost_distribution(_rows())
    assert out["not_computable"] == tp.NOT_COMPUTABLE, (
        "every pair-cost report must carry the limit with it")


def test_no_queue_position_or_distance_field_is_ever_emitted():
    """If one of these ever appears, it was invented: there is no book."""
    res = {"pair_cost": tp.pair_cost_distribution(_rows()),
           "role": tp.liquidity_role(_rows())}
    flat = str(res)
    for banned in ("queue_ahead", "queue_position", "distance_to_touch",
                   "ticks_from_touch", "levels_better"):
        assert banned not in flat, banned


def test_self_trading_is_not_claimed_in_either_direction():
    assert "no claim either way is made" in tp.NO_SELF_TRADE_CLAIM
    src = inspect.getsource(tp)
    assert "wash" not in src.lower().replace("unwash", "")


# ------------------------------------------------- maker/taker accounting
def test_unlabelled_fills_are_never_folded_into_either_side():
    """The takerOnly walk covered one era. Treating an unlabelled early fill
    as maker would manufacture the headline."""
    got = tp.liquidity_role(_rows())
    assert got["fills"] == 4
    assert got["labelled"] == 3 and got["unlabelled"] == 1
    assert got["maker_share_of_labelled"] == round(2 / 3, 4)


def test_maker_share_is_none_when_nothing_is_labelled():
    got = tp.liquidity_role([{"maker_taker": ""}])
    assert got["maker_share_of_labelled"] is None, "unknown is not zero"


# ------------------------------------------------ the gate comparison
def test_our_gates_partition_l5s_pairs_without_overlap_or_gap():
    vals = [0.90, 0.97, 0.99, 1.05]
    g = tp.gate_split(vals)
    assert g["a <=0.96 spring preference"] == 0.25
    assert g["b 0.96-0.98 observation cap"] == 0.25
    assert g["c 0.98-1.00 hard ceiling"] == 0.25
    assert g["d >1.00 we REFUSE"] == 0.25
    assert round(sum(g[k] for k, _ in [(n, h) for n, h in
                                       [(x[0], x[1]) for x in tp.OUR_GATES]])
                 + g["d >1.00 we REFUSE"], 6) == 1.0


def test_the_two_headline_shares_are_cumulative_not_bucketed():
    vals = [0.90, 0.97, 0.99, 1.05]
    g = tp.gate_split(vals)
    assert g["would_clear_our_0_96_preference"] == 0.25
    assert g["would_clear_our_1_00_ceiling"] == 0.75


def test_a_pair_cost_of_zero_or_below_is_absent_not_free():
    out = tp.pair_cost_distribution(
        [{"matched_pair_unit_cost": "0"}, {"matched_pair_unit_cost": ""},
         {"matched_pair_unit_cost": "0.95"}])
    assert out["n"] == 1, "an unmatched fill has no pair cost, it is not 0.00"


def test_small_groups_are_dropped_rather_than_reported_thin():
    out = tp.pair_cost_distribution(_rows(), by="duration_minutes")
    assert out.get("by_duration_minutes") == {}, (
        "under 200 rows a bucket says nothing and is not shown")
