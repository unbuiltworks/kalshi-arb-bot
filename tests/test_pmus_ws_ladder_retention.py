"""The ONE gap that made a PMUS-native maker-fill measurement impossible.

`apply_events` folded the venue's 35-40 rung ladder down to rung 0, so
`l5_maker_fill_model.Ladder` -- which exists to answer "what was ahead of us
at the level we joined?" -- had nothing to read. These pin the fix AND the
bounds, because the reason the ladder was not retained is a real 8.27 GB RSS
incident, not an oversight.
"""

import os

import pytest

from src import pmus_ws_evidence_recorder as R


def _book(slug="m1", bids=(("0.55", "100"), ("0.54", "200"), ("0.53", "300")),
          asks=(("0.56", "150"), ("0.57", "250"))):
    return {"markets": [{"slug": slug, "kind": "book",
                         "bids": [{"px": p, "qty": q} for p, q in bids],
                         "asks": [{"px": p, "qty": q} for p, q in asks],
                         "best_bid": bids[0][0] if bids else None,
                         "best_ask": asks[0][0] if asks else None}]}


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for e in (R.LADDER_MARKETS_ENV, R.LADDER_RUNGS_ENV, R.LADDER_HISTORY_ENV):
        monkeypatch.delenv(e, raising=False)


# -- the default must not change the memory profile ------------------------

def test_no_ladder_is_retained_by_default():
    st = {}
    R.apply_events(st, _book(), 1.0)
    assert "ladder_history" not in st["m1"]
    assert R.ladder_markets() == frozenset()


def test_an_unlisted_market_retains_no_ladder(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "other")
    st = {}
    R.apply_events(st, _book(), 1.0)
    assert "ladder_history" not in st["m1"]


def test_the_allowlist_is_exact_slugs_not_patterns(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m*")
    st = {}
    R.apply_events(st, _book(), 1.0)
    assert "ladder_history" not in st["m1"], "a pattern must not match"


# -- what it retains when switched on --------------------------------------

def test_an_allowlisted_market_retains_both_ladders(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m1")
    st = {}
    R.apply_events(st, _book(), 1.0)
    row = st["m1"]["ladder_history"][-1]
    assert row["bids"] == [["0.55", "100"], ["0.54", "200"], ["0.53", "300"]]
    assert row["asks"] == [["0.56", "150"], ["0.57", "250"]]
    assert row["t"] == 1.0


def test_the_bbo_history_is_untouched_by_the_addition(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m1")
    st = {}
    R.apply_events(st, _book(), 1.0)
    bbo = st["m1"]["bbo_history"][-1]
    assert set(bbo) == {"t", "bid", "ask", "bid_qty", "ask_qty", "ask_book_missing"}


def test_trade_prints_still_record_the_book_at_the_trade(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m1")
    st = {}
    R.apply_events(st, _book(), 1.0)
    R.apply_events(st, {"markets": [{"slug": "m1", "kind": "trade",
                                     "price": "0.56", "qty": "10"}]}, 2.0)
    tr = st["m1"]["trades"][-1]
    assert tr["px"] == "0.56"
    assert tr["bid_at_trade"] == "0.55" and tr["ask_at_trade"] == "0.56"


# -- the bounds, which are the reason this was not done before -------------

def test_rungs_are_capped_so_a_huge_ladder_cannot_be_retained(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m1")
    monkeypatch.setenv(R.LADDER_RUNGS_ENV, "99999")
    assert R.ladder_rungs() == R.LADDER_RUNGS_CAP
    st = {}
    R.apply_events(st, _book(bids=tuple((f"0.{i:02d}", "1") for i in range(1, 99))), 1.0)
    assert len(st["m1"]["ladder_history"][-1]["bids"]) <= R.LADDER_RUNGS_CAP


def test_history_is_capped_and_keeps_the_newest(monkeypatch):
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "m1")
    monkeypatch.setenv(R.LADDER_HISTORY_ENV, "3")
    st = {}
    for i in range(10):
        R.apply_events(st, _book(), float(i))
    hist = st["m1"]["ladder_history"]
    assert len(hist) == 3
    assert [r["t"] for r in hist] == [7.0, 8.0, 9.0]


def test_an_unreadable_bound_reads_as_the_default_never_unbounded(monkeypatch):
    for bad in ("", "abc", "0", "-5", "None"):
        monkeypatch.setenv(R.LADDER_RUNGS_ENV, bad)
        monkeypatch.setenv(R.LADDER_HISTORY_ENV, bad)
        assert R.ladder_rungs() == R.LADDER_RUNGS_DEFAULT, bad
        assert R.ladder_history() == R.LADDER_HISTORY_DEFAULT, bad


# -- an unknown queue must stay unknown ------------------------------------

def test_an_unparseable_rung_is_dropped_not_carried_as_zero():
    rows = [{"px": "0.55", "qty": "100"}, {"px": None, "qty": "200"},
            {"px": "0.53", "qty": None}, {"px": "0.52", "qty": "400"}]
    out = R._levels(rows, 16)
    assert out == [["0.55", "100"], ["0.52", "400"]]
    assert all(q != "0" for _, q in out)


def test_a_non_list_ladder_is_empty_not_an_error():
    for bad in (None, {}, "0.55", 7):
        assert R._levels(bad, 16) == []


def test_rung_order_is_preserved_because_queue_position_depends_on_it():
    rows = [{"px": "0.55", "qty": "1"}, {"px": "0.54", "qty": "2"},
            {"px": "0.53", "qty": "3"}]
    assert R._levels(rows, 2) == [["0.55", "1"], ["0.54", "2"]]
