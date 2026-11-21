"""The preregistration itself, asserted as a test.

A preregistered diagnostic is only worth something if the marks cannot quietly
move after data starts arriving. These tests are the lock: they pin the marks,
the strict criterion, the pending sentinel, and -- most importantly -- the two
venue economic mappings, which are NOT the same object and must never be
collapsed into one "pair".
"""
from __future__ import annotations

import pytest

from src import l5_residual_episode_clock as C


def test_the_marks_are_frozen():
    """Chosen 2026-09-17 to straddle the historical 5-30s / 120s+ boundary so
    prospective data can CONTRADICT it. Changing one invalidates the
    preregistration."""
    assert C.MARKS_S == (5, 15, 30, 60, 120, 300)


def test_a_complement_counts_only_at_the_strict_criterion():
    assert C.STRICT == "VOLUME_AHEAD_CLEARED"
    assert C.RUNGS[-1] == C.STRICT
    assert set(C.RUNGS[:-1]) == {"quote_restable", "book_reached_quote",
                                 "trade_flow_through_quote"}


def test_settlement_is_a_sentinel_not_a_zero():
    ep = C.Episode("k", venue="PMUS", symbol="s", t=0.0, side="Up", px=0.5,
                   qty=1.0, tte_s=100.0, run_position=1)
    assert ep.settled == C.PENDING == "SETTLEMENT_PENDING"
    row = ep.record(5, {})
    assert row["settled_cashflow"] == "SETTLEMENT_PENDING"
    assert row["settled_cashflow"] != 0


def test_marks_come_due_in_order_and_only_once():
    ep = C.Episode("k", venue="PMUS", symbol="s", t=1000.0, side="Up", px=0.5,
                   qty=1.0, tte_s=100.0, run_position=1)
    assert ep.due(1002.0) == []
    assert ep.due(1005.0) == [5]
    ep.record(5, {})
    assert 5 not in ep.due(1005.0)
    assert ep.due(1120.0) == [15, 30, 60, 120]


def test_every_rung_is_recorded_separately_at_every_mark():
    ep = C.Episode("k", venue="PMUS", symbol="s", t=0.0, side="Up", px=0.5,
                   qty=1.0, tte_s=100.0, run_position=1)
    row = ep.record(30, {"quote_restable": True, "book_reached_quote": True,
                         "trade_flow_through_quote": True,
                         C.STRICT: False, "evidenced_complement": False})
    assert row["rungs"] == {"quote_restable": True, "book_reached_quote": True,
                            "trade_flow_through_quote": True,
                            C.STRICT: False}
    assert row["evidenced_complement"] is False, \
        "a weaker rung must never promote itself into a complement"


# --------------------------------------------------------------------------
# the two economic mappings are different objects

def test_gemini_keeps_the_four_quantities_apart():
    e = C.pair_economics("GEMINI", buy_px=0.79, sell_px=0.99,
                         buy_fee="f1", sell_fee="f2")
    assert e["buy_cost"] == 0.79
    assert e["sell_proceeds"] == 0.99
    assert e["synthetic_complement_cost"] == pytest.approx(0.01)
    assert e["effective_pair_cost"] == pytest.approx(0.80)
    assert e["buy_fee"] == "f1" and e["sell_fee"] == "f2"
    assert e["fees_netted"] is False


def test_a_gemini_pair_is_never_realized_here():
    for kw in ({"buy_px": 0.79, "sell_px": 0.99},
               {"buy_px": 0.79, "sell_px": None},
               {"buy_px": None, "sell_px": 0.99}):
        e = C.pair_economics("GEMINI", **kw)
        assert e["realized"] is False
        assert "BOTH orders actually fill" in e["labels"]


def test_one_leg_filled_is_an_open_position_not_half_a_pair():
    e = C.pair_economics("GEMINI", buy_px=0.79, sell_px=None)
    assert e["synthetic_complement_cost"] is None
    assert e["effective_pair_cost"] is None
    assert e["edge_per_unit"] is None


def test_pmus_owns_two_tokens_and_says_so():
    e = C.pair_economics("PMUS", buy_px=0.49, complement_buy_px=0.49)
    assert "two tokens owned" in e["mapping"]
    assert e["effective_pair_cost"] == pytest.approx(0.98)
    assert "sell_proceeds" not in e, \
        "there is no sale on PMUS; both legs are bought"
    assert e["realized"] is False


def test_the_two_mappings_are_not_interchangeable():
    """Same numbers, different venues, different meaning. 0.79 and 0.99 is a
    0.80 pair on Gemini and a 1.78 pair on PMUS -- collapsing the mappings
    would turn a good trade into an impossible one or vice versa."""
    g = C.pair_economics("GEMINI", buy_px=0.79, sell_px=0.99)
    p = C.pair_economics("PMUS", buy_px=0.79, complement_buy_px=0.99)
    assert g["effective_pair_cost"] == pytest.approx(0.80)
    assert p["effective_pair_cost"] == pytest.approx(1.78)
    assert g["mapping"] != p["mapping"]


def test_the_clock_tracks_and_retires_episodes():
    ck = C.Clock("GEMINI")
    ck.open_episode("a", symbol="s", t=0.0, side="BUY", px=0.5, qty=1.0,
                    tte_s=60.0, run_position=1)
    assert ck.n_opened == 1 and "a" in ck.open
    assert [m for _ep, m in ck.due_marks(31.0)] == [5, 15, 30]
    ck.retire("a")
    assert ck.open == {}
