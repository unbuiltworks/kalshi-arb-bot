"""Pre-resting BOUNDS — P2. The venue publishes fills, never placements.

So the only answerable question is the latest placement consistent with an
observed fill, and whether that falls before L5's first leg. The classification
must never harden into a claim, and it must be able to return all three
answers.
"""
import pytest

import analysis.l5.pair_formation.pre_resting as PR


def _seq(*, gap, mt="maker", comp_price="0.50", first_t=1000.0):
    return {"market_slug": "m", "first_side": "Up", "first_price": "0.45",
            "first_t": first_t, "first_maker_taker": "maker",
            "comp_side": "Down", "comp_price": comp_price,
            "comp_t": first_t + gap, "comp_maker_taker": mt,
            "gap_s": gap, "same_transaction": False}


def test_a_maker_complement_too_fast_to_react_rules_out_a_reaction():
    r = PR.classify(_seq(gap=1.0), latency_s=PR.BLOCK_S)
    assert r["bound"] == "CONSISTENT_WITH_PRE_RESTING"
    assert r["latest_placement_before_first_fill"] is True
    assert "already resting" in r["why"]


def test_a_taker_complement_requires_a_reactive_placement():
    """A taker executes on submission, so it was submitted at the fill — an
    act, not a standing order being hit."""
    r = PR.classify(_seq(gap=1.0, mt="taker"), latency_s=PR.BLOCK_S)
    assert r["bound"] == "REQUIRES_REACTIVE_PLACEMENT"
    assert "executes on submission" in r["why"]


def test_a_slow_maker_complement_is_unresolved_not_assumed():
    r = PR.classify(_seq(gap=120.0), latency_s=PR.BLOCK_S)
    assert r["bound"] == "UNRESOLVED"
    assert "either story" in r["why"]


def test_an_unpublished_maker_flag_is_unresolved():
    r = PR.classify(_seq(gap=1.0, mt="unknown"), latency_s=PR.BLOCK_S)
    assert r["bound"] == "UNRESOLVED"
    assert "did not publish" in r["why"]


def test_every_bound_produced_is_one_of_the_three():
    for mt in ("maker", "taker", "unknown", None):
        for gap in (0.0, 1.0, 120.0):
            b = PR.classify(_seq(gap=gap, mt=mt), latency_s=5.0)["bound"]
            assert b in PR.CLASSES


def test_the_price_bound_is_exact_for_a_maker_and_one_sided_for_a_taker():
    m = PR.classify(_seq(gap=1.0, comp_price="0.52"), latency_s=PR.BLOCK_S)
    assert m["implied_limit_price"] == "0.52"
    assert "executes at its own resting limit" in m["price_bound"]
    t = PR.classify(_seq(gap=1.0, mt="taker"), latency_s=PR.BLOCK_S)
    assert "only bounded on one side" in t["price_bound"]


def test_the_latest_placement_time_is_the_fill_minus_the_latency():
    r = PR.classify(_seq(gap=1.0, first_t=1000.0), latency_s=5.0)
    assert r["latest_possible_placement_t"] == 996.0     # 1001 - 5


def test_a_longer_assumed_latency_can_only_rule_out_more_reactions():
    """Monotone by construction; if it were not, the grid would be unreadable."""
    prev = -1
    for L in (1.0, 5.0, 30.0, 300.0):
        n = sum(1 for g in (0.5, 3.0, 20.0, 100.0)
                if PR.classify(_seq(gap=g), latency_s=L)["bound"]
                == "CONSISTENT_WITH_PRE_RESTING")
        assert n >= prev
        prev = n


def test_the_structural_floor_is_the_block_time_and_is_marked_as_such():
    ws = {"w": [
        {"window_id": "w", "market_slug": "m", "outcome": "Up", "price": "0.45",
         "quantity": "10", "timestamp": "1000", "maker_taker": "maker",
         "transaction_hash": "0x1"},
        {"window_id": "w", "market_slug": "m", "outcome": "Down",
         "price": "0.50", "quantity": "10", "timestamp": "1001",
         "maker_taker": "maker", "transaction_hash": "0x2"}]}
    r = PR.report(ws)
    assert PR.BLOCK_S == 2.1
    assert r["by_assumed_latency"]["latency_2.1s"]["is_structural_floor"] is True
    assert r["by_assumed_latency"]["latency_5.0s"]["is_structural_floor"] is False
    assert r["by_assumed_latency"]["latency_2.1s"][
        "CONSISTENT_WITH_PRE_RESTING"] == 1


def test_our_own_lag_is_measured_separately_from_his_behaviour():
    """Whether L5 pre-rested is about him. Whether a reactive clone could have
    placed the complement at all is about OUR lag, and does not depend on
    resolving his behaviour."""
    ws = {f"w{i}": [
        {"window_id": f"w{i}", "market_slug": "m", "outcome": "Up",
         "price": "0.45", "quantity": "10", "timestamp": "1000",
         "maker_taker": "maker", "transaction_hash": "0x1"},
        {"window_id": f"w{i}", "market_slug": "m", "outcome": "Down",
         "price": "0.50", "quantity": "10", "timestamp": str(1000 + gap),
         "maker_taker": "maker", "transaction_hash": "0x2"}]
        for i, gap in enumerate((3, 10, 60, 300))}
    r = PR.report(ws)
    u = r["unreachable_by_a_reactive_clone"]
    assert u["our_observation_lag_s"] == 15.16
    assert u["n"] == 2 and u["of"] == 4        # the 3s and 10s complements
    assert "our lag, not his behaviour" in u["why"]


def test_a_window_that_never_goes_two_sided_yields_no_sequence():
    ws = {"w": [{"window_id": "w", "market_slug": "m", "outcome": "Up",
                 "price": "0.45", "quantity": "10", "timestamp": "1000",
                 "maker_taker": "maker"}]}
    assert PR.sequences(ws) == []


def test_the_report_says_these_are_bounds_and_not_a_claim():
    r = PR.report({})
    assert "BOUNDS, not a claim" in r["labels"]
    assert "not that pre-resting is proved" in r["labels"]
