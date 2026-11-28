"""The queue-depletion estimator: bounds, never invented probabilities.

0100z rested 1 contract at own 0.4600 with 2923.02 contracts of displayed
depth ahead of it, and nothing in the engine could say whether that was a
quote or a wish. These tests pin what the ladder can and cannot establish.

THE DATA LIMIT, asserted rather than assumed: there is no trade-print
series. The durable ledger's 17,600 records are pre-submit shadow records
(`usable_for_calibration: 0`, `events_appended: 0`), so the per-order
observation loop that records `prints_at_our_price` never ran. Everything
here is derived from ladder snapshots alone.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_queue_depletion as QD


def F(bids, asks, t=0.0):
    return {"t": t, "bids": bids, "asks": asks}


# =============================================== 1. PRICE AND SIDE ALGEBRA

def test_up_rests_on_the_bid_at_its_own_price():
    assert QD.wire_of("UP", D("0.46")) == D("0.46")
    assert QD.rests_on("UP") == "BID"


def test_down_is_wired_as_a_sell_of_yes_on_the_ask():
    assert QD.wire_of("DOWN", D("0.46")) == D("0.54")
    assert QD.rests_on("DOWN") == "ASK"


# ================================================= 2. QUEUE AHEAD, PRIORITY

def test_queue_ahead_counts_better_prices_and_our_own_level():
    """Strict price priority, then FIFO at our level. The 0100z shape:
    resting at own 0.46 with the bid at 0.51."""
    bids = [["0.51", "100"], ["0.50", "200"], ["0.46", "50"],
            ["0.45", "900"]]
    q = QD.queue_ahead("UP", D("0.46"), bids=bids, asks=[["0.53", "1"]])
    assert q == D("350")                       # 100 + 200 + 50, not 0.45


def test_queue_ahead_for_down_counts_lower_asks():
    """DOWN at own 0.46 rests at wire 0.54; lower asks are ahead of it."""
    asks = [["0.53", "10"], ["0.54", "20"], ["0.55", "900"]]
    q = QD.queue_ahead("DOWN", D("0.46"), bids=[["0.40", "1"]], asks=asks)
    assert q == D("30")


def test_an_empty_or_unreadable_ladder_is_never_read_as_zero_queue():
    assert QD.queue_ahead("UP", D("0.46"), bids=[], asks=[]) is None
    assert QD.queue_ahead("UP", D("0.46"), bids=None, asks=None) is None
    assert QD.queue_ahead("UP", "x", bids=[["0.5", "1"]]) is None
    # garbage rows are dropped, not guessed at
    assert QD.queue_ahead("UP", D("0.40"),
                          bids=[["x", "y"], ["0.50", "7"]]) == D("7")


# ============================================ 3. THE THREE VERDICTS

def test_proven_when_the_opposite_touch_reaches_our_price():
    """A resting buy cannot sit above the best ask: it executed."""
    frames = [F([["0.50", "500"]], [["0.51", "10"]]),
              F([["0.45", "500"]], [["0.46", "10"]])]
    v = QD.fill_verdict("UP", D("0.46"), frames, start=0)
    assert v["verdict"] == QD.PROVEN
    assert v["proven_at_frame"] == 1


def test_proven_for_a_down_leg_when_the_bid_rises_to_our_wire():
    """DOWN at own 0.46 rests at wire 0.54; a bid at 0.54 executes it."""
    frames = [F([["0.50", "5"]], [["0.51", "500"]]),
              F([["0.54", "5"]], [["0.55", "500"]])]
    v = QD.fill_verdict("DOWN", D("0.46"), frames, start=0)
    assert v["verdict"] == QD.PROVEN


def test_impossible_when_not_enough_size_ever_left_the_queue():
    """A hard negative: it holds whatever the trade/cancel mix was."""
    frames = [F([["0.50", "1000"]], [["0.51", "10"]]),
              F([["0.50", "999"]], [["0.51", "10"]]),
              F([["0.50", "998"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.IMPOSSIBLE
    assert v["queue_ahead_at_join"] == "1000"
    assert v["displayed_cleared"] == "2"
    assert v["still_needed"] == "998"


def test_possible_once_the_cumulative_decrease_covers_the_queue():
    """NECESSARY ONLY. The decrease may have been cancellations, which is
    exactly why this bounds the fill rate from above and not below."""
    frames = [F([["0.50", "100"]], [["0.51", "10"]]),
              F([["0.50", "40"]], [["0.51", "10"]]),
              F([["0.50", "0"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.POSSIBLE
    assert v["possible_at_frame"] == 2
    assert v["displayed_cleared"] == "100"


def test_a_partial_clearance_reduces_the_requirement_proportionally():
    frames = [F([["0.50", "100"]], [["0.51", "10"]]),
              F([["0.50", "70"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.IMPOSSIBLE
    assert v["displayed_cleared"] == "30"
    assert v["still_needed"] == "70"


# ====================== 4. CANCELLATIONS, ADDITIONS AND THE BOUND DIRECTION

def test_additions_are_never_allowed_to_reduce_the_requirement():
    """Size added at our level joins BEHIND us; size added at a better
    price joins ahead. The ladder cannot tell which, so an increase must
    neither help nor hurt -- which is what keeps POSSIBLE an upper bound.
    """
    grow = [F([["0.50", "100"]], [["0.51", "10"]]),
            F([["0.50", "400"]], [["0.51", "10"]]),
            F([["0.50", "300"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), grow, start=0)
    # only the 400->300 decrease counts; the 100->400 addition does not
    assert v["displayed_cleared"] == "100"
    assert v["verdict"] == QD.POSSIBLE


def test_a_cancellation_and_a_trade_are_indistinguishable_by_design():
    """Identical ladders must give an identical verdict, because the ladder
    carries no information that separates them. This is the measurement
    limit, asserted so it cannot be quietly forgotten."""
    frames = [F([["0.50", "10"]], [["0.51", "10"]]),
              F([["0.50", "0"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.POSSIBLE          # never PROVEN
    lab = v["labels"].lower()
    assert "necessary only" in lab
    assert "from above" in lab                  # i.e. an upper bound
    assert "cancellation" in lab


def test_proven_outranks_possible_when_both_conditions_hold():
    frames = [F([["0.50", "10"]], [["0.51", "10"]]),
              F([["0.49", "0"]], [["0.50", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.PROVEN


# ============================================== 5. OUR OWN ORDER AND INPUTS

def test_our_own_quantity_never_reduces_the_queue_ahead_of_it():
    a = QD.fill_verdict("UP", D("0.50"),
                        [F([["0.50", "100"]], [["0.51", "1"]]),
                         F([["0.50", "100"]], [["0.51", "1"]])], start=0)
    b = QD.fill_verdict("UP", D("0.50"),
                        [F([["0.50", "100"]], [["0.51", "1"]]),
                         F([["0.50", "100"]], [["0.51", "1"]])],
                        start=0, own_qty=D("50"))
    assert a["queue_ahead_at_join"] == b["queue_ahead_at_join"] == "100"
    assert b["own_qty"] == "50"


@pytest.mark.parametrize("kw,why", [
    (dict(side="SIDEWAYS", own_px=D("0.5")), "unknown side"),
    (dict(side="UP", own_px=None), "no price"),
])
def test_unreadable_side_or_price_yields_no_verdict(kw, why):
    v = QD.fill_verdict(frames=[F([["0.5", "1"]], [["0.6", "1"]])], **kw)
    assert v["verdict"] is None


def test_no_frames_yields_no_verdict():
    assert QD.fill_verdict("UP", D("0.5"), [])["verdict"] is None
    assert QD.fill_verdict("UP", D("0.5"), [F([["0.5", "1"]], [["0.6", "1"]])],
                           start=9)["verdict"] is None


def test_an_unreadable_frame_mid_series_decides_nothing():
    frames = [F([["0.50", "100"]], [["0.51", "10"]]),
              F([], []),
              F([["0.50", "0"]], [["0.51", "10"]])]
    v = QD.fill_verdict("UP", D("0.50"), frames, start=0)
    assert v["verdict"] == QD.POSSIBLE


# ====================================================== 6. PAIR ECONOMICS

def test_the_touch_pair_is_exactly_one_minus_the_spread():
    """own_UP = best_bid and own_DOWN = 1 - best_ask, so buying both sides
    at the market cannot beat $1.00 by more than the spread."""
    assert QD.touch_pair([["0.50", "1"]], [["0.51", "1"]]) == D("0.99")
    assert QD.touch_pair([["0.50", "1"]], [["0.52", "1"]]) == D("0.98")


def test_a_crossed_or_one_sided_book_has_no_touch_pair():
    assert QD.touch_pair([["0.52", "1"]], [["0.50", "1"]]) is None
    assert QD.touch_pair([], [["0.51", "1"]]) is None


@pytest.mark.parametrize("d,want", [
    (0, D("0.99")), (1, D("0.97")), (2, D("0.95")), (3, D("0.93")),
])
def test_depth_sets_the_pair_by_identity_not_by_taste(d, want):
    """1 - spread - 2*d*tick. The deployed depth of 2 gives 0.95, which is
    exactly the pair 0100z had resting."""
    assert QD.pair_at_depth(D("0.01"), d, D("0.01")) == want


def test_a_matched_pair_redeems_exactly_one_dollar():
    assert QD.maker_pair_edge(D("0.95")) == D("0.05")
    assert QD.maker_pair_edge(D("1.00")) == D("0.00")
    assert QD.maker_pair_edge(D("0.95"), D("4")) == D("0.20")


def test_the_maker_rebate_defaults_to_zero_and_must_be_passed_explicitly():
    """A zero commission is NOT a rebate. Nothing may be credited until it
    is actually paid and source-accounted."""
    assert QD.maker_pair_edge(D("1.00")) == D("0")
    assert QD.maker_pair_edge(
        D("1.00"), maker_rebate_per_contract=D("0.003")) == D("0.003")


def test_unreadable_economics_return_none_rather_than_a_default():
    assert QD.pair_at_depth(None, 2, D("0.01")) is None
    assert QD.pair_at_depth(D("0.01"), 2, D("0")) is None
    assert QD.pair_at_depth(D("0.01"), -1, D("0.01")) is None
    assert QD.maker_pair_edge(None) is None


# ================================= 7. THE 0100z STATE, AND THE DATA LIMIT

def test_the_0100z_queue_ahead_reproduces_the_venue_recorded_figure():
    """`queue_ahead_at_resting_measured: 2923.0200` at own 0.4600, from the
    L5_TS_REQUOTE record. Rebuilt from a ladder of the same shape."""
    bids = [["0.5100", "561.0200"], ["0.5000", "900.0000"],
            ["0.4900", "800.0000"], ["0.4800", "400.0000"],
            ["0.4700", "212.0000"], ["0.4600", "50.0000"],
            ["0.4500", "9999.0000"]]
    q = QD.queue_ahead("UP", D("0.4600"), bids=bids, asks=[["0.5300", "1"]])
    assert q == D("2923.0200")
    # and the level the requote wanted to chase was far shallower
    assert QD.queue_ahead("UP", D("0.5100"), bids=bids,
                          asks=[["0.5300", "1"]]) == D("561.0200")


def test_no_module_function_returns_anything_called_a_probability():
    """The ladder carries no trade series, so a probability is not in this
    data. The module must expose bounds and counts only."""
    import inspect
    src = inspect.getsource(QD)
    code = "\n".join(l for l in src.splitlines()
                     if l.strip() and not l.strip().lstrip("#").startswith(
                         ("PROVEN", "POSSIBLE", "IMPOSSIBLE"))
                     and not l.strip().startswith("#"))
    for banned in ("def probability", "def p_fill", "fill_probability",
                   "def expected_fill", "def likelihood"):
        assert banned not in code, banned
    assert "PROVEN" in src and "POSSIBLE" in src and "IMPOSSIBLE" in src
