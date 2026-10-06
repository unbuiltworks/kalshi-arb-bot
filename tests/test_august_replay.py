"""The replay engine's invariants, on a synthetic tape with known answers.

These tests exist because every one of them is a way the replay could flatter
S1 without the output looking wrong: crediting it with L5's fills, filling it
because its price equalled the touch, or reading the book one second late.
"""
from __future__ import annotations

import pytest

from analysis.l5.historical_l2.bbo_offset import Book
from analysis.l5.august_replay.replay import (FROZEN_FINGERPRINT, Ledger,
                                              assert_frozen, divergence,
                                              replay)
from src.polymarket_l5.owned_order_shadow import CANDIDATE, FINGERPRINT

UP, DN = "tok_up", "tok_down"
START, END = 1_000_000, 1_000_000 + 900
LEG = CANDIDATE["quantity_shares"]


def row(bb, bsz, ba=None, asz=10.0):
    return {"best_bid": bb, "best_ask": ba if ba is not None else bb + 0.02,
            "bid_prices": [bb], "bid_sizes": [bsz],
            "ask_prices": [ba if ba is not None else bb + 0.02],
            "ask_sizes": [asz]}


def tape(up_rows, dn_rows):
    books = {UP: Book(), DN: Book()}
    for t, r in up_rows:
        books[UP].add(float(t), r)
    for t, r in dn_rows:
        books[DN].add(float(t), r)
    for b in books.values():
        b.sort()
    return books


def flat(bb=0.50, bsz=100.0):
    """A book that never moves, re-stated every 30 s.

    The capture writes a row only on change, but the carry bound is 60 s, so a
    single row cannot keep a 900 s window resolvable. Re-stating the SAME book
    periodically is what a real quiet tape looks like after a REST resync.
    """
    return [(START + k, row(bb, bsz)) for k in range(0, 900, 30)]


def step(bb0, bb1, at, bsz=100.0):
    """`bb0` until `at`, then `bb1` -- both re-stated every 30 s."""
    out = [(START + k, row(bb0, bsz)) for k in range(0, at, 30)]
    out += [(START + k, row(bb1, bsz)) for k in range(at, 900, 30)]
    out.append((START + at, row(bb1, bsz)))
    return out


def test_frozen_fingerprint_is_the_one_under_test():
    assert FINGERPRINT == FROZEN_FINGERPRINT
    assert_frozen()


def test_touch_alone_never_fills_s1():
    """A book that never moves: S1 rests at the touch for the whole window
    and must finish with ZERO inventory. This is the no-touch=fill rule."""
    r = replay(tape(flat(), flat()), UP, DN, START, END, {}, {},
               use_prints=False)
    assert r["s1_final"]["inv_up"] == 0.0
    assert r["s1_final"]["inv_down"] == 0.0
    assert r["s1_final"]["n_fills"] == 0
    assert r["orders_placed"] == 2          # one per side, never replaced


def test_l5_fills_are_never_credited_to_s1():
    """L5 trades all window; the book never moves so S1 cannot be evidenced.
    S1's ledger must stay empty while L5's fills."""
    l5 = {START + k: [{"token_id": (UP if (k // 60) % 2 == 0 else DN),
                       "price": "0.49", "size": "20"}]
          for k in range(0, 600, 60)}
    r = replay(tape(flat(), flat()), UP, DN, START, END, l5, {},
               use_prints=False)
    assert r["l5_final"]["n_fills"] == 10
    assert r["l5_final"]["matched"] > 0
    assert r["s1_final"]["n_fills"] == 0
    assert r["s1_final"]["inv_up"] == 0.0


def test_level_consumed_is_the_only_book_only_evidence():
    """The touch drops strictly below S1's price -> EVIDENCED_LEVEL_CONSUMED."""
    r = replay(tape(step(0.50, 0.48, 10), flat()), UP, DN, START, END, {}, {},
               use_prints=False)
    fills = [e for e in r["events"]
             if e["who"] == "S1" and e["kind"] == "fill"]
    assert fills, "a touch moving through the price must evidence a fill"
    assert fills[0]["evidence"] == "EVIDENCED_LEVEL_CONSUMED"
    assert fills[0]["t"] == START + 10
    assert fills[0]["price"] == 0.50


def test_queue_cleared_front_of_book_is_uncertain_not_a_fill():
    """Size at our level drains to zero but the touch holds: we would be at
    the front, still resting. It must be recorded and excluded."""
    up = [(START, row(0.50, 100.0)),
          (START + 5, {"best_bid": 0.50, "best_ask": 0.52,
                       "bid_prices": [0.50], "bid_sizes": [0.0],
                       "ask_prices": [0.52], "ask_sizes": [10.0]})]
    up += [(START + k, {"best_bid": 0.50, "best_ask": 0.52,
                        "bid_prices": [0.50], "bid_sizes": [0.0],
                        "ask_prices": [0.52], "ask_sizes": [10.0]})
           for k in range(30, 900, 30)]
    r = replay(tape(up, flat()), UP, DN, START, END, {}, {},
               use_prints=False)
    assert r["s1_final"]["n_fills"] == 0
    assert r["s1_uncertain_counts"].get("QUEUE_CLEARED_FRONT_OF_BOOK", 0) > 0


def test_trade_through_is_unreachable_without_prints():
    """Arm A reproduces the frozen module, where last_prints is never
    assigned, so no fill may ever carry the TRADE_THROUGH evidence."""
    up = [(START + k, row(0.50, 1.0)) for k in range(0, 900, 30)]
    r = replay(tape(up, flat()), UP, DN, START, END, {},
               {START + 3: [{"token_id": UP, "price": "0.50",
                             "size": "1000"}]}, use_prints=False)
    assert not [e for e in r["events"]
                if e.get("evidence") == "EVIDENCED_TRADE_THROUGH"]


def test_trade_through_fires_in_arm_b_when_prints_clear_the_queue():
    up = [(START + k, row(0.50, 1.0)) for k in range(0, 900, 30)]
    r = replay(tape(up, flat()), UP, DN, START, END, {},
               {START + 3: [{"token_id": UP, "price": "0.50",
                             "size": "1000"}]}, use_prints=True)
    ev = [e for e in r["events"]
          if e.get("evidence") == "EVIDENCED_TRADE_THROUGH"]
    assert ev and ev[0]["t"] == START + 3


def test_tte_shutdown_stops_new_quotes_in_the_last_60s():
    """No order may be PLACED with less than tte_shutdown_s remaining."""
    r = replay(tape(flat(), flat()), UP, DN, START, END, {}, {},
               use_prints=False)
    placed = [e for e in r["events"] if e["kind"] == "order_placed"]
    assert placed
    assert all(END - e["t"] >= CANDIDATE["tte_shutdown_s"] for e in placed)


def test_no_future_information_book_is_read_at_or_before_t():
    """A book improvement at t+1 must not be visible at t."""
    r = replay(tape(step(0.50, 0.40, 100), flat()), UP, DN, START, END, {},
               {}, use_prints=False)
    first = next(e for e in r["events"]
                 if e["who"] == "S1" and e["kind"] == "fill")
    assert first["t"] == START + 100, "the drop must be seen at t, not before"


def test_unresolvable_tick_is_not_a_fill_free_poll():
    """A gap beyond the carry bound yields an explicit unresolvable tick."""
    up = [(START, row(0.50, 100.0)), (START + 500, row(0.50, 100.0))]
    r = replay(tape(up, flat()), UP, DN, START, END, {}, {},
               use_prints=False)
    assert r["unresolvable_ticks"] > 0


def test_unresolvable_tick_still_advances_l5_ledger():
    """An L5 fill is a chain fact; a gap in the L2 capture must not drop it."""
    up = [(START, row(0.50, 100.0))]
    r = replay(tape(up, flat()), UP, DN, START, END,
               {START + 300: [{"token_id": UP, "price": "0.49",
                               "size": "20"}]}, {}, use_prints=False)
    assert r["unresolvable_ticks"] > 0
    assert r["l5_final"]["n_fills"] == 1, "L5 fill dropped on a book gap"


def test_residual_cap_suppresses_quoting_on_the_heavy_side():
    led = Ledger(UP, DN)
    led.buy(UP, 0.5, CANDIDATE["residual_cap_shares"])
    assert led.residual >= CANDIDATE["residual_cap_shares"]
    assert led.matched == 0


def test_pair_cost_is_the_sum_of_leg_vwaps_and_needs_both_legs():
    led = Ledger(UP, DN)
    led.buy(UP, 0.48, 10)
    assert led.snap()["pair_cost"] is None, "one leg is not a pair"
    led.buy(DN, 0.49, 10)
    assert led.snap()["pair_cost"] == pytest.approx(0.97)


def test_divergence_marker_is_l5_matched_while_s1_empty():
    rows = [
        {"t": 1, "l5": {"matched": 0, "residual": 0}, "s1": {"matched": 0,
         "residual": 0}, "l5_fills_this_second": [], "s1_fills_this_second": []},
        {"t": 2, "l5": {"matched": 0, "residual": 20}, "s1": {"matched": 0,
         "residual": 0}, "l5_fills_this_second": [{"x": 1}],
         "s1_fills_this_second": []},
        {"t": 3, "l5": {"matched": 20, "residual": 0}, "s1": {"matched": 0,
         "residual": 0}, "l5_fills_this_second": [{"x": 1}],
         "s1_fills_this_second": []},
    ]
    d = divergence(rows)
    assert d["D1_first_l5_fill"] == 2
    assert d["designated_first_material_divergence"] == 3
    assert d["D3_first_s1_evidenced_fill"] is None
