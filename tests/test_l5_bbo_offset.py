"""BBO offsets from historical L2 — the measurement that sizes the D grid.

Three things would each quietly ruin it: reading the book AT the fill (the
offset collapses to zero by construction), treating an unchanged book as stale
(throws away every quiet moment and biases toward volatile ones), and calling
anonymous depth L5's order.
"""
from decimal import Decimal as D

import pytest

import analysis.l5.historical_l2.bbo_offset as B


def _row(ts, best_bid, *, sizes=None, prices=None, best_ask=None):
    prices = prices or [best_bid, round(best_bid - 0.01, 2),
                        round(best_bid - 0.02, 2)]
    return ts, {"best_bid": best_bid,
                "best_ask": best_ask if best_ask is not None else best_bid + 0.03,
                "bid_prices": prices,
                "bid_sizes": sizes or [10.0] * len(prices),
                "seconds_to_close": 800.0}


def _book(rows):
    b = B.Book()
    for ts, r in rows:
        b.add(ts, r)
    b.sort()
    return b


# ------------------------------------------- an old row is not a stale row

def test_an_unchanged_book_is_carried_forward_not_discarded():
    """The capture writes a row only when the book CHANGES, so a 40-second-old
    row is the current state. Discarding it would keep only volatile moments."""
    bk = _book([_row(1000.0, 0.45)])
    s = bk.at(1040.0)
    assert s is not None and s["best_bid"] == 0.45
    assert s["carried_s"] == 40.0


def test_a_row_carried_past_the_resync_interval_is_refused():
    """Beyond the collector's own 60s REST resync, undetectable delta drift is
    outside the dataset's stated accuracy."""
    bk = _book([_row(1000.0, 0.45)])
    assert bk.at(1000.0 + B.MAX_CARRY_S + 1) is None
    assert B.MAX_CARRY_S == 60.0


def test_the_latest_row_at_or_before_the_time_is_used():
    bk = _book([_row(1000.0, 0.45), _row(1010.0, 0.47)])
    assert bk.at(1009.0)["best_bid"] == 0.45
    assert bk.at(1010.0)["best_bid"] == 0.47


def test_a_time_before_any_snapshot_has_no_book():
    assert _book([_row(1000.0, 0.45)]).at(999.0) is None


# ------------------------------------------------ the offset arithmetic

def test_joining_the_touch_is_zero_ticks():
    assert B.offset_ticks(0.45, 0.45) == 0
    assert B.bucket(0) == "join"


def test_resting_below_the_touch_is_positive_ticks():
    assert B.offset_ticks(0.45, 0.42) == 3
    assert B.bucket(3) == "-3"


def test_a_fill_above_the_prevailing_bid_is_flagged_not_folded_in():
    """It means the book moved, or the level was created by the fill. Calling
    it '-1 tick' would invent depth that was never rested at."""
    assert B.offset_ticks(0.45, 0.47) == -2
    assert B.bucket(-2) == "ABOVE_BBO"


def test_an_absent_bid_gives_no_offset():
    assert B.offset_ticks(None, 0.45) is None
    assert B.offset_ticks(0, 0.45) is None
    assert B.bucket(None) == "UNKNOWN"


# --------------------------------- size at a price is not ownership

def test_size_at_the_fill_price_is_read_from_the_ladder():
    _, r = _row(1000.0, 0.45, prices=[0.45, 0.44, 0.43],
                sizes=[10.0, 25.0, 40.0])
    assert B._size_at(r, D("0.44")) == D("25")
    assert B._size_at(r, D("0.40")) == D("0")


def test_how_long_a_level_existed_is_about_the_level_not_the_owner():
    bk = _book([_row(t, 0.45, prices=[0.45, 0.44], sizes=[10.0, 5.0])
                for t in (1000.0, 1010.0, 1020.0)])
    assert bk.level_present_s(D("0.44"), until=1020.0) == 20.0
    import inspect
    assert "never ownership" in inspect.getdoc(B.Book.level_present_s).lower() \
        or "NEVER OWNERSHIP" in inspect.getdoc(B.Book.level_present_s)


def test_a_level_that_was_absent_breaks_the_run():
    bk = _book([_row(1000.0, 0.45, prices=[0.45], sizes=[10.0]),
                _row(1010.0, 0.45, prices=[0.45, 0.44], sizes=[10.0, 5.0]),
                _row(1020.0, 0.45, prices=[0.45, 0.44], sizes=[10.0, 5.0])])
    assert bk.level_present_s(D("0.44"), until=1020.0) == 10.0


# --------------------------------- the book is read BEFORE the fill

def test_offsets_are_taken_at_lead_times_not_at_the_fill():
    assert B.OFFSET_AT_S == (30, 5, 1)
    assert 0 in B.SNAPSHOT_GRID_S and 120 in B.SNAPSHOT_GRID_S
    bk = _book([_row(970.0, 0.50), _row(999.0, 0.45)])
    rows = B.build([{"token_id": "t", "t": 1000.0, "price": "0.45",
                     "size": "10", "l5_role": "maker", "tx": "0x1",
                     "population": "FIRST_FILL"}], {"t": bk})
    assert rows[0]["offset_T-30"] == 5      # touch was 0.50 thirty seconds out
    assert rows[0]["offset_T-1"] == 0       # and 0.45 one second out


def test_a_fill_with_no_book_is_marked_not_dropped():
    rows = B.build([{"token_id": "nope", "t": 1.0, "price": "0.45",
                     "size": "10", "l5_role": "maker"}], {})
    assert rows[0]["status"] == "NO_BOOK_FOR_TOKEN"


# ------------------------------------------------- the decision table

C = "consistency_T-5"
OK = "RESTING_BID_CONSISTENT"


def test_the_table_splits_the_three_populations():
    rows = [{"role": "maker", "population": "FIRST_FILL", "offset_T-5": 0, C: OK},
            {"role": "maker", "population": "FIRST_OPPOSITE_FILL",
             "offset_T-5": 2, C: OK},
            {"role": "maker", "population": None, "offset_T-5": 4, C: OK}]
    t = B.table(rows, lead=5)
    assert set(t) == set(B.POPULATIONS)
    assert t["FIRST_FILL"]["n_maker_fills"] == 1
    assert t["ALL_BTC15M_MAKER_FILLS"]["n_maker_fills"] == 3
    assert t["ALL_BTC15M_MAKER_FILLS"]["by_offset"]["join"]["fills"] == 1


def test_taker_fills_never_enter_a_maker_offset_table():
    rows = [{"role": "taker", "population": "FIRST_FILL", "offset_T-5": 0}]
    assert B.table(rows, lead=5)["FIRST_FILL"]["n_maker_fills"] == 0


def test_the_table_answers_the_grid_question_directly():
    """If the mass is join..-2 the -4 rung is wasted; if it is beyond -4 the
    grid is too shallow."""
    rows = [{"role": "maker", "population": None, "offset_T-5": o, C: OK}
            for o in ([0] * 5 + [1] * 3 + [2] * 2 + [7])]
    t = B.table(rows, lead=5)["ALL_BTC15M_MAKER_FILLS"]
    assert t["cumulative_join_through_minus2"] == pytest.approx(10 / 11, 1e-3)
    assert t["mass_beyond_minus4"] == pytest.approx(1 / 11, 1e-3)


def test_offsets_are_ordered_shallow_to_deep_in_the_table():
    rows = [{"role": "maker", "population": None, "offset_T-5": o, C: OK}
            for o in (3, 0, 1, -1, None)]
    keys = list(B.table(rows, lead=5)["ALL_BTC15M_MAKER_FILLS"]["by_offset"])
    assert keys == ["ABOVE_BBO", "join", "-1", "-3", "UNKNOWN"]


# ------------------------------------- two mechanisms, not one noisy one

def test_a_maker_buy_above_the_ask_is_not_counted_as_depth():
    """26% of L5's maker buys priced at or above the prevailing ask. Calling
    those "0 ticks" would drag the grid answer toward the touch."""
    snap = {"best_bid": 0.53, "best_ask": 0.54}
    assert B.placement_consistency(snap, "0.56") == "AT_OR_ABOVE_ASK_NOT_MEASURABLE"
    assert B.placement_consistency(snap, "0.53") == "RESTING_BID_CONSISTENT"
    assert B.placement_consistency(snap, "0.50") == "RESTING_BID_CONSISTENT"
    assert B.placement_consistency(snap, "0.535") == "INSIDE_SPREAD_IMPROVED_THE_TOUCH"


def test_a_row_with_no_book_is_not_silently_consistent():
    assert B.placement_consistency(None, "0.5") == "NO_BOOK"
    assert B.placement_consistency({"best_bid": None}, "0.5") == "NO_BOOK"


def test_unmeasurable_rows_are_reported_not_dropped():
    """An excluded row must still be COUNTED, or the exclusion is invisible."""
    rows = [{"role": "maker", "population": None, "offset_T-5": 0, C: OK},
            {"role": "maker", "population": None, "offset_T-5": -3,
             C: "AT_OR_ABOVE_ASK_NOT_MEASURABLE"}]
    t = B.table(rows, lead=5)["ALL_BTC15M_MAKER_FILLS"]
    assert t["n_maker_fills"] == 2
    assert t["n_measurable_resting_bids"] == 1
    assert t["placement_consistency"]["AT_OR_ABOVE_ASK_NOT_MEASURABLE"] == 1
    assert t["by_offset"]["join"]["fills"] == 1


def test_a_fill_with_no_block_timestamp_is_never_placed_against_a_book():
    """Interpolating a fill time is what put a quarter of his buys above the
    ask in the first draft. An unknown time must stay unknown."""
    rows = B.build([{"token_id": "T", "t": None, "price": "0.5", "size": "1"}],
                   {"T": B.Book()})
    assert rows[0]["status"] == "NO_BLOCK_TIMESTAMP"
    assert "offset_T-1" not in rows[0]
