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


# -- rolling exact-slug selection ------------------------------------------
# A 15m product retires its slug every quarter hour, so a static exact list
# goes stale inside one window. The selection follows the clock and stays
# EXACT; these pin that it never becomes a pattern and never grows unbounded.


def test_rolling_is_off_by_default(monkeypatch):
    monkeypatch.delenv(R.LADDER_ROLLING_ENV, raising=False)
    assert R.ladder_rolling() is False
    assert R.ladder_markets(1000.0) == frozenset()


def test_rolling_only_turns_on_for_an_explicit_yes(monkeypatch):
    for v in ("", "no", "1", "true", "Y", "yes "):
        monkeypatch.setenv(R.LADDER_ROLLING_ENV, v)
        assert R.ladder_rolling() is (v.strip().upper() == "YES"), v


def test_rolling_adds_the_live_window_and_the_next(monkeypatch):
    monkeypatch.setenv(R.LADDER_ROLLING_ENV, "YES")
    monkeypatch.setenv(R.ROLLOVER_SLUG_ENV, "cpc-btc-updown-15m-")
    monkeypatch.setenv(R.ROLLOVER_ENV, "900")
    got = R.ladder_markets(1792166665.0)
    assert len(got) == 2, got
    assert all(s.startswith("cpc-btc-updown-15m-") for s in got)
    assert all(s.endswith("z") for s in got)


def test_rolling_keeps_the_static_allowlist_too(monkeypatch):
    monkeypatch.setenv(R.LADDER_ROLLING_ENV, "YES")
    monkeypatch.setenv(R.LADDER_MARKETS_ENV, "pinned-one")
    monkeypatch.setenv(R.ROLLOVER_SLUG_ENV, "cpc-btc-updown-15m-")
    monkeypatch.setenv(R.ROLLOVER_ENV, "900")
    got = R.ladder_markets(1792166665.0)
    assert "pinned-one" in got
    assert len(got) == 3


def test_rolling_ahead_is_capped_so_the_set_stays_small(monkeypatch):
    monkeypatch.setenv(R.LADDER_ROLLING_ENV, "YES")
    monkeypatch.setenv(R.LADDER_ROLLING_AHEAD_ENV, "9999")
    monkeypatch.setenv(R.ROLLOVER_SLUG_ENV, "cpc-btc-updown-15m-")
    monkeypatch.setenv(R.ROLLOVER_ENV, "900")
    got = R.ladder_markets(1792166665.0)
    assert len(got) == R.LADDER_ROLLING_AHEAD_CAP + 1


def test_a_broken_prefix_does_not_widen_the_selection(monkeypatch):
    monkeypatch.setenv(R.LADDER_ROLLING_ENV, "YES")
    monkeypatch.delenv(R.ROLLOVER_SLUG_ENV, raising=False)
    assert R.ladder_markets(1792166665.0) == frozenset()


# -- eviction, which is what keeps a rotating product bounded --------------

def test_a_retired_window_releases_its_rungs():
    st = {"live": {"ladder_history": [{"t": 1.0}], "trades": [{"t": 1.0}]},
          "retired": {"ladder_history": [{"t": 1.0}], "trades": [{"t": 1.0}]}}
    dropped = R.drop_stale_ladders(st, frozenset({"live"}))
    assert dropped == 1
    assert "ladder_history" in st["live"]
    assert "ladder_history" not in st["retired"]


def test_eviction_touches_nothing_but_the_ladder():
    st = {"retired": {"ladder_history": [{"t": 1.0}], "trades": [{"t": 2.0}],
                      "bbo_history": [{"t": 3.0}], "bid": "0.31"}}
    R.drop_stale_ladders(st, frozenset())
    assert st["retired"]["trades"] == [{"t": 2.0}]
    assert st["retired"]["bbo_history"] == [{"t": 3.0}]
    assert st["retired"]["bid"] == "0.31"


def test_eviction_is_idempotent_and_counts_only_real_drops():
    st = {"m": {"ladder_history": [{"t": 1.0}]}}
    assert R.drop_stale_ladders(st, frozenset()) == 1
    assert R.drop_stale_ladders(st, frozenset()) == 0


def test_eviction_survives_a_non_dict_entry():
    st = {"m": None, "n": {"ladder_history": [{"t": 1.0}]}}
    assert R.drop_stale_ladders(st, frozenset()) == 1
