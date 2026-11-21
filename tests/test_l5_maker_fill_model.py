"""MODELLED maker fills must never flatter us.

Every test here exists to stop a specific way of inventing a fill:
unknown depth read as zero, a cancellation read as queue advancement, a
touch read as a fill, or a modelled number leaking into realized P&L.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_maker_fill_model as M

SLUG = "cpc-btc-updown-15m-2026-10-12-1800z"


def frame(bids, asks, slug=SLUG):
    """The venue's own wire shape: px is a nested {'value': ...}."""
    return {"marketSlug": slug,
            "bids": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                     for p, q in bids],
            "offers": [{"px": {"value": p, "currency": "USD"}, "qty": q}
                       for p, q in asks]}


def pr(px, qty, direction=None, t=1.0):
    return {"px": px, "qty": qty, "dir": direction, "t": t}


@pytest.fixture()
def lad():
    L = M.Ladder()
    L.ingest(frame([("0.5300", "300"), ("0.5200", "500"), ("0.5100", "900")],
                   [("0.5400", "400"), ("0.5500", "600")]), 100.0)
    return L


# ------------------------------------------------------- ladder capture

def test_the_full_ladder_is_retained_not_just_the_touch(lad):
    assert lad.depth_at(SLUG, M.BID, D("0.5300")) == D("300")
    assert lad.depth_at(SLUG, M.BID, D("0.5100")) == D("900")
    assert lad.depth_at(SLUG, M.ASK, D("0.5500")) == D("600")


def test_a_rung_beyond_the_served_ladder_is_UNKNOWN_not_zero(lad):
    """The whole reason depth was None before. Unknown must stay unknown."""
    assert lad.depth_at(SLUG, M.BID, D("0.4000")) is None
    assert lad.depth_at(SLUG, M.ASK, D("0.9000")) is None


def test_an_empty_rung_inside_the_served_range_is_zero(lad):
    assert lad.depth_at(SLUG, M.BID, D("0.5250")) == D("0")


def test_queue_ahead_counts_better_prices_then_our_own_level(lad):
    # a bid at 0.52 waits for 0.53 (300) plus its own level (500)
    assert lad.ahead_of_us(SLUG, M.BID, D("0.5200")) == D("800")
    # an ask at 0.55 waits for 0.54 (400) plus its own level (600)
    assert lad.ahead_of_us(SLUG, M.ASK, D("0.5500")) == D("1000")


def test_a_book_level_is_never_mistaken_for_a_trade_print():
    """{'price','quantity'} is NOT the venue's shape; this is the fixture
    error that once made me doubt the recorder."""
    L = M.Ladder()
    L.ingest(frame([("0.5000", "100")], []), 1.0)
    assert L.depth_at(SLUG, M.BID, D("0.5000")) == D("100")


def test_a_malformed_rung_is_dropped_not_guessed():
    L = M.Ladder()
    L.ingest({"marketSlug": SLUG,
              "bids": [{"px": {"value": "0.50"}, "qty": "100"},
                       {"px": {"value": "nope"}, "qty": "5"}],
              "offers": []}, 1.0)
    assert L.rejected == 1
    assert L.depth_at(SLUG, M.BID, D("0.50")) == D("100")


# ------------------------------------------------------- wire convention

def test_an_UP_buy_rests_as_a_bid_at_the_wire_price(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.resting_side == M.BID and o.wire_px == D("0.52")
    assert o.queue_ahead == D("800")
    assert o.ticks_behind_touch() == 1


def test_a_DOWN_buy_rests_as_an_ask_at_one_minus_its_cost(lad):
    """own-outcome 0.45 means selling YES at 0.55."""
    o = M.HypoOrder(SLUG, M.DOWN, D("0.45"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.resting_side == M.ASK and o.wire_px == D("0.55")
    assert o.queue_ahead == D("1000")
    assert o.ticks_behind_touch() == 1


def test_a_crossing_quote_is_not_restable(lad):
    crossing = M.HypoOrder(SLUG, M.UP, D("0.60"), D("5"), 1.0, ladder=lad,
                           bid=D("0.53"), ask=D("0.54"))
    assert crossing.restable is False
    passive = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                          bid=D("0.53"), ask=D("0.54"))
    assert passive.restable is True


# ------------------------------------------------------- the four traps

def test_touch_alone_is_not_a_fill(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([])                                  # price reached, no prints
    assert not any(o.filled.values())


def test_a_cancellation_ahead_of_us_is_NOT_queue_advancement(lad):
    """The level empties with no prints at all. That must fill nothing."""
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    lad.ingest(frame([("0.5300", "0"), ("0.5200", "0")],
                     [("0.5400", "400")]), 200.0)
    o.advance([])
    assert o.cleared == D(0)
    assert not any(o.filled.values())
    assert o.queue_ahead == D("800"), "queue was re-read after placement"


def test_unknown_queue_never_fills_conservative_or_base(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.40"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.queue_ahead is None
    o.advance([pr("0.40", "100000", "SELL")])
    assert o.filled[M.CONSERVATIVE] is False
    assert o.filled[M.BASE] is False


def test_a_sweep_can_fill_an_unknown_queue_but_only_a_sweep(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.40"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.40", "10", "SELL")])          # AT our price, not through
    assert o.filled[M.OPTIMISTIC] is False
    o.advance([pr("0.39", "10", "SELL")])          # strictly THROUGH
    assert o.filled[M.OPTIMISTIC] is True


# ------------------------------------------------------- the three scenarios

def test_the_scenarios_separate_in_the_right_order(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.queue_ahead == D("800")
    o.advance([pr("0.52", "800", "SELL")])         # exactly the queue
    assert o.filled[M.BASE] is True
    assert o.filled[M.CONSERVATIVE] is False       # our own 5 has not traded
    o.advance([pr("0.52", "5", "SELL")])
    assert o.filled[M.CONSERVATIVE] is True


def test_a_buy_aggressor_does_not_consume_a_resting_bid(lad):
    """Direction matters: a buy print lifts the offer, it does not fill our
    bid."""
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.52", "5000", "BUY")])
    assert o.cleared == D(0) and not any(o.filled.values())


def test_an_unlabelled_print_is_counted_conservatively(lad):
    """No aggressor label -> count it as ahead of us. That can only ever
    make the queue look longer, never shorter."""
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.52", "10", None)])
    assert o.cleared == D("10")


def test_prints_above_our_bid_never_count(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.53", "9999", "SELL")])
    assert o.cleared == D(0)


# ------------------------------------------------------- hypothetical book

def test_a_modelled_first_leg_creates_modelled_inventory():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.UP, D("5"), D("0.52"))
    assert inv.up(SLUG) == D("5")
    assert inv.residual(SLUG) == D("5")
    assert inv.matched(SLUG) == D("0")
    assert inv.cash_out(SLUG) == D("2.60")


def test_a_modelled_complement_matches_the_pair():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.UP, D("5"), D("0.52"))
    inv.fill(SLUG, M.DOWN, D("5"), D("0.44"))
    assert inv.matched(SLUG) == D("5")
    assert inv.residual(SLUG) == D("0")
    s = inv.settle(SLUG, up_won=True)
    # pair cost 0.96, redeems 1.00 -> +0.04 a share on 5 = +0.20
    assert s["modelled_net"] == "0.20"
    assert inv.settle(SLUG, up_won=False)["modelled_net"] == "0.20"


def test_a_pair_above_par_loses_under_both_outcomes():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.UP, D("5"), D("0.61"))
    inv.fill(SLUG, M.DOWN, D("5"), D("0.59"))
    for won in (True, False):
        assert inv.settle(SLUG, up_won=won)["modelled_net"] == "-1.00"


def test_residual_pays_only_on_the_winning_side():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.UP, D("5"), D("0.50"))        # unmatched long UP
    assert inv.settle(SLUG, up_won=True)["modelled_net"] == "2.50"
    assert inv.settle(SLUG, up_won=False)["modelled_net"] == "-2.50"


def test_a_down_residual_pays_when_up_loses():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.DOWN, D("5"), D("0.50"))
    assert inv.settle(SLUG, up_won=False)["modelled_net"] == "2.50"
    assert inv.settle(SLUG, up_won=True)["modelled_net"] == "-2.50"


def test_settlement_has_no_mark_to_market():
    inv = M.HypoInventory()
    inv.fill(SLUG, M.UP, D("5"), D("0.50"))
    s = inv.settle(SLUG, up_won=False)
    assert s["residual_redeemed"] == "0"           # zero, not a model price


# ------------------------------------------------------- adverse selection

def test_adverse_selection_is_flagged_when_the_market_left(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.51", "5000", "SELL")])        # swept downward
    a = M.adverse_selection(o, M.OPTIMISTIC, D("0.10"))
    assert a["adverse"] is True
    assert a["moved_against_us"] == "0.43"


def test_a_favourable_move_is_not_flagged_adverse(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.51", "5000", "SELL")])
    a = M.adverse_selection(o, M.OPTIMISTIC, D("0.90"))
    assert a["adverse"] is False


def test_an_unfilled_order_has_no_adverse_selection(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert M.adverse_selection(o, M.BASE, D("0.10")) is None


# ------------------------------------------------------- labelling

def test_every_record_says_it_is_modelled(lad):
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=lad,
                    bid=D("0.53"), ask=D("0.54"))
    assert "MODELLED" in o.record()["labels"]
    assert "MODELLED" in M.HypoInventory().settle(SLUG, True)["labels"]


def test_the_model_cannot_submit_anything():
    """Checked on the AST, not the prose: the docstrings legitimately
    discuss the transport, and a substring search on source text flagged
    that as a violation."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(M))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for net in ("requests", "httpx", "urllib", "socket", "aiohttp",
                "websockets", "http"):
        assert net not in imported, "fill model imports %r" % net
    # it must not reach into the execution stack either
    for mod in ("l5_pmus_transport", "l5_live_execution", "pmus_q1_executor"):
        assert mod not in imported, "fill model imports %r" % mod

    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    # not "get"/"post": dict.get is legitimate and the import check above
    # already rules out any network client.
    for bad in ("submit", "arm", "cancel", "place"):
        assert bad not in called, "fill model calls .%s()" % bad


# ============================================================ ModelSession
# A modelled first-leg fill must change what the NEXT intent means.

def _sess():
    s = M.ModelSession(SLUG)
    # the ask ladder runs out to 0.56 so a DOWN complement at own-outcome
    # 0.44 (which rests as an ASK at wire 0.56) has a SERVED rung. Without
    # it queue_ahead is correctly None and correctly never fills.
    s.ingest_frame(frame([("0.5300", "300"), ("0.5200", "100")],
                         [("0.5400", "400"), ("0.5500", "100"),
                          ("0.5600", "200")]), 100.0)
    return s


def test_the_first_intent_on_a_flat_book_is_an_OPEN():
    s = _sess()
    rec = s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    assert rec["modelled_intent_kind"][M.BASE] == M.OPEN
    assert rec["displayed_depth_at_price"] == "100"
    assert rec["queue_ahead_at_placement"] == "400"


def test_a_modelled_fill_makes_the_opposite_side_a_COMPLETE():
    """The sequencing this instrumentation exists to measure."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    ev = s.advance([pr("0.52", "405", "SELL", t=2.0)])      # clears 400 + 5
    assert any(e["scenario"] == M.BASE and e["kind"] == M.OPEN for e in ev)
    assert s.inv[M.BASE].residual(SLUG) == D("5")
    rec = s.place(M.DOWN, D("0.44"), D("5"), 3.0, D("0.53"), D("0.54"), "c2")
    assert rec["modelled_intent_kind"][M.BASE] == M.COMPLETE


def test_a_same_side_intent_after_a_fill_is_an_EXTEND():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "405", "SELL", t=2.0)])
    rec = s.place(M.UP, D("0.51"), D("5"), 3.0, D("0.53"), D("0.54"), "c2")
    assert rec["modelled_intent_kind"][M.BASE] == M.EXTEND


def test_scenarios_keep_separate_inventory():
    """A fill that only happens optimistically must not create matched
    inventory in the conservative book."""
    s = _sess()
    s.place(M.UP, D("0.40"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.39", "10", "SELL", t=2.0)])            # sweep only
    assert s.inv[M.OPTIMISTIC].residual(SLUG) == D("5")
    assert s.inv[M.CONSERVATIVE].residual(SLUG) == D("0")
    assert s.inv[M.BASE].residual(SLUG) == D("0")


def test_a_fill_is_booked_once_however_many_times_prints_arrive():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "405", "SELL", t=2.0)])
    s.advance([pr("0.52", "405", "SELL", t=3.0)])
    assert s.inv[M.BASE].residual(SLUG) == D("5"), "double-booked a fill"


def test_the_print_cursor_does_not_replay_old_prints():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    tape = [pr("0.52", "100", "SELL", t=2.0)]
    s.advance(tape)
    s.advance(tape)                                 # same list, no new rows
    o = s.orders[0]
    assert o.cleared == D("100"), "a print was counted twice"


def test_an_unfilled_order_closes_as_rested_to_close():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.close(up_won=True)
    assert s.orders[0].end_reason == "RESTED_TO_MARKET_CLOSE_UNFILLED"


def test_a_crossing_intent_is_recorded_but_never_rests():
    s = _sess()
    s.place(M.UP, D("0.60"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    assert s.not_restable == 1
    assert s.orders[0].end_reason == "WOULD_HAVE_CROSSED_NOT_RESTED"
    s.advance([pr("0.52", "99999", "SELL", t=2.0)])
    assert not any(s.orders[0].filled.values())


def test_the_window_report_separates_the_three_scenarios():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "405", "SELL", t=2.0)])
    rep = s.close(up_won=True, wire_at_close=D("0.10"))
    assert set(rep["scenarios"]) == set(M.SCENARIOS)
    assert rep["scenarios"][M.BASE]["modelled_fills"] == 1
    assert rep["scenarios"][M.BASE]["opens"] == 1
    assert rep["scenarios"][M.CONSERVATIVE]["modelled_fills"] == 1
    assert rep["modelled_intents"] == 1
    assert "MODELLED" in rep["labels"]


def test_an_unknown_window_outcome_books_no_settlement():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "405", "SELL", t=2.0)])
    rep = s.close(up_won=None)
    assert rep["scenarios"][M.BASE]["settlement"]["modelled_net"] is None


def test_a_full_open_then_complete_cycle_nets_the_pair_edge():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "405", "SELL", t=2.0)])
    s.place(M.DOWN, D("0.44"), D("5"), 3.0, D("0.53"), D("0.54"), "c2")
    # ask 0.56 = DOWN 0.44. queue ahead = 0.54(400)+0.55(100)+own level(200)
    s.advance([pr("0.56", "705", "BUY", t=4.0)])
    rep = s.close(up_won=False, wire_at_close=D("0.00"))
    b = rep["scenarios"][M.BASE]
    assert b["opens"] == 1 and b["completes"] == 1
    # pair 0.52 + 0.44 = 0.96 -> +0.04 x 5 = +0.20, either outcome
    assert b["settlement"]["modelled_net"] == "0.20"
    assert b["settlement"]["residual"] == "0"


def test_the_cursor_survives_the_recorders_rolling_500_cap():
    """An index cursor freezes once `trades` hits its 500-row cap,
    dropping every later print. 1800z saw trades_seen 500.

    Asserted on the SESSION's prints_consumed rather than an order's
    `cleared`: once an order fills in every scenario it is retired from
    open_orders and correctly stops accumulating volume, so `cleared` is no
    longer a measure of the cursor.
    """
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    tape = [pr("0.52", "1", "SELL", t=float(i)) for i in range(2, 502)]
    s.advance(tape)
    assert s.prints_consumed == 500
    # the list is now capped: it stays length 500 while OLD rows fall off
    tape = tape[1:] + [pr("0.52", "1", "SELL", t=502.0)]
    s.advance(tape)
    assert s.prints_consumed == 501, "a capped tape lost a print"


def test_an_already_seen_print_is_not_recounted_across_batches():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    tape = [pr("0.52", "100", "SELL", t=2.0)]
    s.advance(tape)
    s.advance(tape)
    s.advance(list(tape))
    assert s.orders[0].cleared == D("100")


def test_an_untimed_print_is_still_consumed_once():
    """Identity anchoring does not need a usable timestamp, so an untimed
    print is folded rather than discarded -- but only once."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    row = {"px": "0.52", "qty": "100", "dir": "SELL"}
    s.advance([row])
    s.advance([row])
    assert s.orders[0].cleared == D("100")


# ===================================================== trade-cursor safety
# The recorder stamps "t": now -- the FRAME arrival time -- so every print
# carried in one WS frame SHARES a timestamp. A timestamp cursor keeps one
# print per frame and discards the rest, which undercounts exactly the busy
# moments that fill orders. These tests pin that down.

def test_prints_sharing_a_timestamp_are_all_consumed_once():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    frame_rows = [pr("0.52", "10", "SELL", t=5.0),
                  pr("0.52", "20", "SELL", t=5.0),
                  pr("0.52", "30", "SELL", t=5.0)]
    s.advance(frame_rows)
    assert s.orders[0].cleared == D("60"), "same-timestamp prints were lost"
    s.advance(list(frame_rows))
    assert s.orders[0].cleared == D("60"), "same-timestamp prints recounted"


def test_same_timestamp_prints_split_across_batches_are_not_dropped():
    """The exact systematic bug: a frame's prints arriving in two reads."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    a = pr("0.52", "10", "SELL", t=5.0)
    b = pr("0.52", "20", "SELL", t=5.0)
    s.advance([a])
    s.advance([a, b])            # the list grew; b shares a's timestamp
    assert s.orders[0].cleared == D("30")
    assert s.duplicate_rows_skipped == 0


def test_identical_rows_in_one_frame_are_distinguished_by_venue_time():
    """Two genuinely identical prints are not collapsed when the venue
    serves its own per-trade stamp."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    a = dict(pr("0.52", "10", "SELL", t=5.0), trade_time=1)
    b = dict(pr("0.52", "10", "SELL", t=5.0), trade_time=2)
    s.advance([a, b])
    assert s.orders[0].cleared == D("20")


def test_truly_indistinguishable_rows_collapse_and_that_is_reported():
    """Without a venue stamp two identical prints in one frame are
    indistinguishable. We UNDERCOUNT, which is conservative, and the skip
    is counted rather than hidden."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    a = pr("0.52", "10", "SELL", t=5.0)
    s.advance([a, dict(a)])
    assert s.orders[0].cleared == D("10")
    assert s.duplicate_rows_skipped == 1


# ----------------------------------------------------- coverage GAPS

def test_a_rolled_buffer_is_detected_as_a_coverage_gap():
    """Our anchor falls off the back of the 500-row buffer: prints existed
    that we never saw, and that must be reported, not assumed away."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    first = [pr("0.52", "1", "SELL", t=float(i)) for i in range(500)]
    s.advance(first)
    assert s.coverage_gaps == 0
    # the window stays busy and the buffer rolls entirely past our anchor
    later = [pr("0.52", "1", "SELL", t=float(i)) for i in range(900, 1400)]
    s.advance(later)
    assert s.coverage_gaps == 1


def test_complete_coverage_is_reported_as_complete():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    tape = []
    for i in range(10):
        tape = tape + [pr("0.52", "1", "SELL", t=float(i))]
        s.advance(tape)
    rep = s.close(up_won=True)
    assert rep["trade_coverage"]["complete"] is True
    assert rep["trade_coverage"]["coverage_gaps"] == 0
    assert rep["prints_consumed"] == 10


def test_a_gap_is_reported_as_incomplete_coverage_and_an_undercount():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    s.advance([pr("0.52", "1", "SELL", t=1.0)])
    s.advance([pr("0.52", "1", "SELL", t=float(i)) for i in range(500, 700)])
    rep = s.close(up_won=True)
    cov = rep["trade_coverage"]
    assert cov["complete"] is False
    assert cov["coverage_gaps"] == 1
    assert "UNDERCOUNT" in cov["labels"]


def test_a_gap_does_not_double_count_rows_we_already_folded():
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    a = pr("0.52", "7", "SELL", t=1.0)
    s.advance([a])
    # anchor gone, but `a` is still present: identity must still filter it
    s.advance([a, pr("0.52", "3", "SELL", t=2.0)])
    assert s.orders[0].cleared == D("10")


# ============================================ what the model does NOT claim

def test_the_report_states_it_is_classification_not_generation():
    """It must be impossible to read a modelled COMPLETE as evidence that
    the engine would pursue pair completion."""
    s = _sess()
    s.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    f = s.close(up_won=True)["fidelity"]
    assert f["mode"] == "CLASSIFICATION_OF_REAL_INTENTS"
    assert f["inventory_driven_generation"] is False
    assert "NOT faithful inventory-driven" in f["not_claimed"]
    assert "AVAILABLE" in f["what_completes_mean"]


def test_modelled_inventory_cannot_create_or_alter_an_intent():
    """The engine's intents are L5's actions. A modelled fill must change
    only the LABEL of the next intent, never whether it exists."""
    a = _sess()
    b = _sess()
    # a gets a modelled fill; b does not
    a.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    a.advance([pr("0.52", "405", "SELL", t=2.0)])
    b.place(M.UP, D("0.52"), D("5"), 1.0, D("0.53"), D("0.54"), "c1")
    ra = a.place(M.DOWN, D("0.44"), D("5"), 3.0, D("0.53"), D("0.54"), "c2")
    rb = b.place(M.DOWN, D("0.44"), D("5"), 3.0, D("0.53"), D("0.54"), "c2")
    # identical order in every respect...
    for k in ("side", "own_px", "qty", "wire_px", "rests_as", "placed_t"):
        assert ra[k] == rb[k]
    # ...and only the CLASSIFICATION differs
    assert ra["modelled_intent_kind"][M.BASE] == M.COMPLETE
    assert rb["modelled_intent_kind"][M.BASE] == M.OPEN


# ============================================ the real WS envelope
# Deployment 1093d4d1 reported ladder {frames: 0, slugs: 0} with every
# queue_ahead null, because ingest read marketSlug/bids/offers off the TOP
# LEVEL. The live feed nests the book below the slug. These tests use the
# nested shapes and would all have failed against that first version.

def _nested_marketdata(slug=SLUG):
    """The documented live shape: a marketData sub-document."""
    return {"marketData": {
        "marketSlug": slug,
        "bids": [{"px": {"value": "0.5300"}, "qty": "300"},
                 {"px": {"value": "0.5200"}, "qty": "500"}],
        "offers": [{"px": {"value": "0.5400"}, "qty": "400"}]}}


def _slug_outside_book_inside(slug=SLUG):
    """The slug at an outer level and the book below it -- the case the
    walker's slug_hint exists for."""
    return {"marketSlug": slug,
            "payload": [{"bids": [{"px": {"value": "0.5300"}, "qty": "300"}],
                         "offers": [{"px": {"value": "0.5400"},
                                     "qty": "400"}]}]}


def test_a_nested_marketdata_frame_populates_the_ladder():
    L = M.Ladder()
    assert L.ingest(_nested_marketdata(), 1.0) == SLUG
    assert L.frames == 1
    assert L.depth_at(SLUG, M.BID, D("0.5300")) == D("300")
    assert L.depth_at(SLUG, M.ASK, D("0.5400")) == D("400")
    assert L.ahead_of_us(SLUG, M.BID, D("0.5200")) == D("800")


def test_a_slug_carried_outside_the_book_still_populates():
    L = M.Ladder()
    assert L.ingest(_slug_outside_book_inside(), 1.0) == SLUG
    assert L.depth_at(SLUG, M.BID, D("0.5300")) == D("300")


def test_a_frame_list_is_walked():
    L = M.Ladder()
    L.ingest([_nested_marketdata(), _nested_marketdata("other-slug")], 1.0)
    assert L.status()["slugs"] == 2


def test_a_quote_at_the_touch_gets_a_KNOWN_queue_from_a_nested_frame():
    """The runtime symptom: ticks_behind_touch 0 and queue_ahead null. A
    quote resting AT the touch must have a known queue."""
    L = M.Ladder()
    L.ingest(_nested_marketdata(), 1.0)
    o = M.HypoOrder(SLUG, M.UP, D("0.53"), D("5"), 1.0, ladder=L,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.ticks_behind_touch() == 0
    assert o.queue_ahead == D("300"), "queue still unknown at the touch"


def test_a_garbage_frame_is_ignored_without_raising():
    L = M.Ladder()
    for junk in (None, 7, "x", {}, [], {"nothing": "here"}):
        assert L.ingest(junk, 1.0) is None
    assert L.frames == 0


def test_the_ladder_reports_zero_frames_when_nothing_matched():
    """The diagnostic that located this bug must keep working."""
    L = M.Ladder()
    L.ingest({"unrelated": True}, 1.0)
    st = L.status()
    assert st["frames"] == 0 and st["slugs"] == 0


# ============================================ the deployed residual cap
# 1845z modelled OPEN+EXTEND+EXTEND+EXTEND to a residual of -5.55. Live,
# RESIDUAL_ABOVE_CAP would have refused the fourth leg, so the economics
# reported for that sequence were for something that cannot happen.

def _capped(cap="5"):
    s = M.ModelSession(SLUG, max_abs_residual=D(cap))
    s.ingest_frame(_nested_marketdata(), 100.0)
    return s


def _fill_leg(s, side, own_px, qty, t):
    """Place and sweep-fill one leg.

    The book passed must be CONSISTENT with the leg's wire price or
    `restable` correctly rejects it: a DOWN leg at own-cost 0.92 rests as
    an ask at wire 0.08, which would cross a 0.53 bid. 0.06/0.07 is the
    real 1845z book these prices came from.
    """
    s.place(side, D(own_px), D(qty), t, D("0.06"), D("0.07"))
    wire = D(own_px) if side == M.UP else D(1) - D(own_px)
    through = wire - D("0.01") if side == M.UP else wire + D("0.01")
    direction = "SELL" if side == M.UP else "BUY"
    s.advance([pr(str(through), "99999", direction, t=t + 1)])


def test_the_modelled_book_cannot_exceed_the_deployed_residual_cap():
    s = _capped("5")
    for i, q in enumerate(("1.8", "1.8", "1.8", "1.8")):
        _fill_leg(s, M.DOWN, "0.92", q, 10.0 + i * 10)
    assert abs(s.inv[M.OPTIMISTIC].residual(SLUG)) <= D("5")
    assert s.suppressed_by_residual_cap[M.OPTIMISTIC] >= 1


def test_the_cap_suppresses_rather_than_invents_and_is_reported():
    s = _capped("5")
    for i in range(4):
        _fill_leg(s, M.DOWN, "0.92", "1.8", 10.0 + i * 10)
    rep = s.close(up_won=False, wire_at_close=D("0.03"))
    o = rep["scenarios"][M.OPTIMISTIC]
    assert o["suppressed_by_residual_cap"] >= 1
    assert rep["max_abs_residual"] == "5"


def test_a_leg_that_reduces_imbalance_is_never_capped():
    """The deployed rule exactly: a leg shrinking |residual| is never
    refused, at any depth."""
    s = _capped("1")
    _fill_leg(s, M.DOWN, "0.92", "1.0", 10.0)
    assert s.inv[M.OPTIMISTIC].residual(SLUG) == D("-1.0")
    _fill_leg(s, M.UP, "0.05", "1.0", 30.0)        # closes the imbalance
    assert s.inv[M.OPTIMISTIC].residual(SLUG) == D("0")
    assert s.suppressed_by_residual_cap[M.OPTIMISTIC] == 0


def test_no_cap_configured_means_no_suppression():
    s = M.ModelSession(SLUG)
    s.ingest_frame(_nested_marketdata(), 100.0)
    for i in range(4):
        _fill_leg(s, M.DOWN, "0.92", "1.8", 10.0 + i * 10)
    assert s.suppressed_by_residual_cap[M.OPTIMISTIC] == 0
    assert s.close(up_won=False)["max_abs_residual"] is None


def test_the_cap_cannot_create_a_fill():
    """It is suppression only: never more fills with a cap than without."""
    a, b = _capped("5"), M.ModelSession(SLUG)
    b.ingest_frame(_nested_marketdata(), 100.0)
    for i in range(4):
        _fill_leg(a, M.DOWN, "0.92", "1.8", 10.0 + i * 10)
        _fill_leg(b, M.DOWN, "0.92", "1.8", 10.0 + i * 10)
    ra = a.close(up_won=False)["scenarios"][M.OPTIMISTIC]
    rb = b.close(up_won=False)["scenarios"][M.OPTIMISTIC]
    assert ra["modelled_fills"] <= rb["modelled_fills"]


# ===================================================== the sweep shortcut
# LIVE DATA killed the old shortcut: a print 12.02 shares deep went through
# a level with 9,586.35 shares ahead of us and scored a fill in all three
# scenarios. Twelve shares cannot consume nine thousand; in a continuous
# book that means the levels above thinned by CANCELLATION, so crediting it
# credits a cancellation ahead of us as queue advancement.

def test_a_sweep_does_NOT_clear_a_KNOWN_queue():
    """The exact production shape: queue 9586.35, volume 12.02, swept."""
    L = M.Ladder()
    L.ingest(_nested_marketdata(), 1.0)
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=L,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.queue_ahead == D("800")
    o.advance([pr("0.51", "12.02", "SELL", t=2.0)])     # through, tiny
    assert o.swept is True
    assert o.filled[M.CONSERVATIVE] is False
    assert o.filled[M.BASE] is False
    assert o.filled[M.OPTIMISTIC] is False, (
        "a 12-share sweep cleared an 800-share queue")


def test_a_known_queue_is_cleared_only_by_VOLUME():
    L = M.Ladder()
    L.ingest(_nested_marketdata(), 1.0)
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=L,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.52", "800", "SELL", t=2.0)])
    assert o.filled[M.BASE] is True
    assert o.filled[M.CONSERVATIVE] is False
    o.advance([pr("0.52", "5", "SELL", t=3.0)])
    assert o.filled[M.CONSERVATIVE] is True


def test_the_sweep_allowance_survives_only_for_an_UNKNOWN_queue():
    """Where it was needed: an unserved rung volume can never be shown to
    have cleared."""
    L = M.Ladder()
    L.ingest(_nested_marketdata(), 1.0)
    o = M.HypoOrder(SLUG, M.UP, D("0.40"), D("5"), 1.0, ladder=L,
                    bid=D("0.53"), ask=D("0.54"))
    assert o.queue_ahead is None
    o.advance([pr("0.39", "10", "SELL", t=2.0)])
    assert o.filled[M.OPTIMISTIC] is True
    assert o.filled[M.BASE] is False
    assert o.filled[M.CONSERVATIVE] is False


@pytest.mark.parametrize("vol,cons,base,opt", [
    ("100", False, False, False),
    ("800", False, True, True),
    ("805", True, True, True),
])
def test_the_three_scenarios_are_MONOTONE(vol, cons, base, opt):
    """conservative implies base implies optimistic. The old version broke
    this: OPTIMISTIC fired only on sweeps and so could come out BELOW
    conservative once queues were known."""
    L = M.Ladder()
    L.ingest(_nested_marketdata(), 1.0)
    o = M.HypoOrder(SLUG, M.UP, D("0.52"), D("5"), 1.0, ladder=L,
                    bid=D("0.53"), ask=D("0.54"))
    o.advance([pr("0.52", vol, "SELL", t=2.0)])
    assert (o.filled[M.CONSERVATIVE], o.filled[M.BASE],
            o.filled[M.OPTIMISTIC]) == (cons, base, opt)
    # the implication itself, independent of the table
    assert not (o.filled[M.CONSERVATIVE] and not o.filled[M.BASE])
    assert not (o.filled[M.BASE] and not o.filled[M.OPTIMISTIC])


def test_a_window_cannot_report_more_conservative_fills_than_optimistic():
    s = _sess()
    for i, px in enumerate(("0.52", "0.52", "0.52")):
        s.place(M.UP, D(px), D("1"), 1.0 + i, D("0.53"), D("0.54"))
    s.advance([pr("0.52", "5000", "SELL", t=9.0)])
    rep = s.close(up_won=True, wire_at_close=D("0.50"))
    c = rep["scenarios"][M.CONSERVATIVE]["modelled_fills"]
    b = rep["scenarios"][M.BASE]["modelled_fills"]
    o = rep["scenarios"][M.OPTIMISTIC]["modelled_fills"]
    assert c <= b <= o, "scenario ordering inverted: %s/%s/%s" % (c, b, o)
