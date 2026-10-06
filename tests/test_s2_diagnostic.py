"""Diagnostic S2's invariants. Each test is a way S2 could flatter itself.

The dangerous failures here are specific: repricing counted as a fill, a mint
opportunity moving inventory, S2 credited off L5's activity, or a reprice rule
that fires on a quiet book and so manufactures activity the evidence does not
support.
"""
from __future__ import annotations

import pytest

from analysis.l5.historical_l2.bbo_offset import Book
from analysis.l5.august_replay.s2 import (S2_CANDIDATE, S2_FINGERPRINT,
                                          assert_s1_frozen,
                                          displacement_ticks,
                                          mint_opportunity, replay_s2)
from src.polymarket_l5.owned_order_shadow import CANDIDATE as S1_CANDIDATE

UP, DN = "tok_up", "tok_down"
START, END = 1_000_000, 1_000_000 + 900
LEG = S2_CANDIDATE["quantity_shares"]


def row(bb, bsz, ba=None, asz=50.0, extra_bids=()):
    bp = [bb] + [p for p, _ in extra_bids]
    bs = [bsz] + [s for _, s in extra_bids]
    a = ba if ba is not None else round(bb + 0.02, 4)
    return {"best_bid": bb, "best_ask": a, "bid_prices": bp, "bid_sizes": bs,
            "ask_prices": [a], "ask_sizes": [asz]}


def tape(up_rows, dn_rows):
    bk = {UP: Book(), DN: Book()}
    for t, r in up_rows:
        bk[UP].add(float(t), r)
    for t, r in dn_rows:
        bk[DN].add(float(t), r)
    for b in bk.values():
        b.sort()
    return bk


def flat(bb=0.50, bsz=100.0):
    return [(START + k, row(bb, bsz)) for k in range(0, 900, 30)]


def ramp(bb0, bb1, at, bsz=100.0):
    """`bb0` until `at`, then `bb1`, each re-stated every 30 s."""
    out = [(START + k, row(bb0, bsz)) for k in range(0, at, 30)]
    out.append((START + at, row(bb1, bsz)))
    out += [(START + k, row(bb1, bsz)) for k in range(at + 30, 900, 30)]
    return out


# ---------------------------------------------------------------- frozen S1

def test_s1_is_frozen_and_s2_matches_it_where_it_claims_to():
    assert_s1_frozen()
    for k in ("levels", "offset_from_bid_ticks", "quantity_shares",
              "residual_cap_shares", "residual_control", "tte_shutdown_s",
              "order_lifetime_s", "no_5c_extend_rule"):
        assert S2_CANDIDATE[k] == S1_CANDIDATE[k]


def test_residual_cap_was_not_raised():
    """The brief forbids simply raising it; this locks that in."""
    assert S2_CANDIDATE["residual_cap_shares"] == 80
    assert S2_CANDIDATE["residual_control"] == "QUOTE_SUPPRESSION_ONLY"
    assert S2_CANDIDATE["no_aggressive_opposite_side"] is True


def test_s2_fingerprint_is_stable():
    assert S2_FINGERPRINT == (
        "5650ca16943cd3e2da00254578d2e0bc1fbd146bfa76c9e59fc1ba7e396f7865")


# ------------------------------------------------------------ quote upkeep

def test_quiet_book_never_reprices_however_old_the_order():
    """There is no fixed lifetime. An order at the touch must not be
    replaced, or the rule would manufacture activity on a still book."""
    r = replay_s2(tape(flat(), flat()), UP, DN, START, END, {}, {},
                  use_prints=False)
    assert r["reprices_total"] == 0
    assert r["orders_placed"] == 2
    assert r["s2_final"]["n_fills"] == 0


def test_reprice_fires_when_the_touch_walks_away_and_is_not_a_fill():
    """0.50 -> 0.60 is 10 ticks, past the 6-tick threshold."""
    r = replay_s2(tape(ramp(0.50, 0.60, 120), flat()), UP, DN, START, END,
                  {}, {}, use_prints=False)
    rp = [e for e in r["events"] if e["kind"] == "reprice"]
    assert rp, "the touch moved 10 ticks away and nothing repriced"
    assert rp[0]["why"] == "REPRICE_ON_DISPLACEMENT"
    assert rp[0]["is_a_fill"] is False
    assert all(e["is_a_fill"] is False for e in rp)
    # a reprice must never add inventory
    assert r["s2_final"]["inv_up"] == 0.0


def test_reprice_re_establishes_the_quote_at_the_new_touch():
    r = replay_s2(tape(ramp(0.50, 0.60, 120), flat()), UP, DN, START, END,
                  {}, {}, use_prints=False)
    placed = [e for e in r["events"]
              if e["kind"] == "order_placed" and e["outcome"] == "up"]
    assert len(placed) >= 2
    assert placed[-1]["price"] == pytest.approx(0.60)


def test_displacement_threshold_is_strict():
    """Exactly 6 ticks must NOT reprice; 7 must."""
    six = replay_s2(tape(ramp(0.50, 0.56, 120), flat()), UP, DN, START, END,
                    {}, {}, use_prints=False)
    assert not [e for e in six["events"]
                if e["kind"] == "reprice"
                and e["why"] == "REPRICE_ON_DISPLACEMENT"]
    seven = replay_s2(tape(ramp(0.50, 0.57, 120), flat()), UP, DN, START, END,
                      {}, {}, use_prints=False)
    assert [e for e in seven["events"]
            if e["kind"] == "reprice"
            and e["why"] == "REPRICE_ON_DISPLACEMENT"]


def test_staleness_trigger_fires_below_the_touch_inside_the_tick_threshold():
    """Up 3 ticks only -- under the displacement threshold -- so the order
    sits behind the touch and the 69 s staleness rule is what must catch it."""
    r = replay_s2(tape(ramp(0.50, 0.53, 60), flat()), UP, DN, START, END,
                  {}, {}, use_prints=False)
    why = [e["why"] for e in r["events"] if e["kind"] == "reprice"]
    assert "REPRICE_ON_STALENESS" in why
    first = next(e for e in r["events"]
                 if e["kind"] == "reprice"
                 and e["why"] == "REPRICE_ON_STALENESS")
    assert first["stale_for_s"] > S2_CANDIDATE["reprice_on_staleness_s"]


def test_displacement_ticks_never_negative():
    assert displacement_ticks(0.50, 0.50) == 0
    assert displacement_ticks(0.48, 0.50) == 0      # we are above the touch
    assert displacement_ticks(0.56, 0.50) == 6
    assert displacement_ticks(None, 0.50) is None


# ------------------------------------------------------------------- mint

def test_no_mint_when_the_complement_cannot_reach_par():
    m = mint_opportunity(0.40, [(0.40, 10.0)], [(0.55, 100.0)], LEG)
    assert m is None, "0.40 + 0.55 = 0.95 is below par"


def test_mint_opportunity_is_bounded_by_the_queue_ahead_of_us():
    # need 0.60; complement has 100 at 0.62. Our side has 80 at/above us.
    m = mint_opportunity(0.40, [(0.40, 80.0)], [(0.62, 100.0)], LEG)
    assert m is not None
    assert m["complement_capacity_at_or_above_need"] == 100.0
    assert m["our_side_size_at_or_above_our_price"] == 80.0
    assert m["capacity_reachable_after_queue_ahead"] == 20.0
    assert m["opportunity_shares_bounded"] == 20.0     # min(23.61, 20.0)


def test_mint_records_nothing_when_the_queue_ahead_absorbs_all_capacity():
    """If everything ahead of us at our own price could absorb the whole
    complement capacity, no mint volume can reach us and nothing is logged."""
    up = [(START + k, row(0.45, 100.0)) for k in range(0, 900, 30)]
    dn = [(START + k, row(0.60, 100.0)) for k in range(0, 900, 30)]
    r = replay_s2(tape(up, dn), UP, DN, START, END, {}, {}, use_prints=False)
    assert r["mint_opportunity_ticks"] == 0
    assert r["mint_opportunity_shares_bounded"] == 0


def test_mint_opportunity_is_never_a_fill():
    m = mint_opportunity(0.40, [(0.40, 1.0)], [(0.62, 1000.0)], LEG)
    assert m["is_a_fill"] is False
    assert m["label"] == "MINT_OPPORTUNITY"


def test_mint_opportunities_do_not_move_inventory():
    """A book where a mint is feasible every second and the touch never
    moves: S2 must record opportunities and acquire NOTHING.

    The complement must carry MORE capacity than the size queued ahead of us
    at our own price, or the conservative bound correctly returns zero
    reachable shares -- which is what an earlier version of this fixture
    accidentally tested.
    """
    up = [(START + k, row(0.45, 10.0)) for k in range(0, 900, 30)]
    dn = [(START + k, row(0.60, 500.0)) for k in range(0, 900, 30)]
    r = replay_s2(tape(up, dn), UP, DN, START, END, {}, {}, use_prints=False)
    assert r["mint_opportunity_ticks"] > 0
    assert r["mint_opportunity_shares_bounded"] > 0
    assert r["s2_final"]["inv_up"] == 0.0
    assert r["s2_final"]["inv_down"] == 0.0
    assert r["s2_final"]["n_fills"] == 0


# ------------------------------------------------------- no credit from L5

def test_l5_fills_are_never_credited_to_s2():
    l5 = {START + k: [{"token_id": (UP if (k // 60) % 2 == 0 else DN),
                       "price": "0.49", "size": "20"}]
          for k in range(0, 600, 60)}
    r = replay_s2(tape(flat(), flat()), UP, DN, START, END, l5, {},
                  use_prints=False)
    assert r["l5_final"]["n_fills"] == 10
    assert r["l5_final"]["matched"] > 0
    assert r["s2_final"]["n_fills"] == 0
    assert r["s2_final"]["inv_up"] == 0.0
    assert r["s2_final"]["inv_down"] == 0.0


def test_touch_alone_still_never_fills_s2():
    r = replay_s2(tape(flat(), flat()), UP, DN, START, END, {}, {},
                  use_prints=False)
    assert r["s2_final"]["n_fills"] == 0


def test_the_stranded_order_scenario_s1_hit_is_repaired():
    """The August failure in miniature: UP walks 0.38 -> 0.99 and never comes
    back. Frozen S1 would sit at 0.38 for the rest of the window; S2 must
    reprice and must still not be handed a fill for doing so."""
    up = ramp(0.38, 0.99, 60)
    r = replay_s2(tape(up, flat()), UP, DN, START, END, {}, {},
                  use_prints=False)
    rp = [e for e in r["events"]
          if e["kind"] == "reprice" and e["outcome"] == "up"]
    assert rp, "S2 must not strand a quote 61 ticks behind the market"
    last = [e for e in r["events"]
            if e["kind"] == "order_placed" and e["outcome"] == "up"][-1]
    assert last["price"] == pytest.approx(0.99)
    assert r["s2_final"]["inv_up"] == 0.0, "repricing is not a fill"


def test_tte_shutdown_still_blocks_late_placement():
    r = replay_s2(tape(flat(), flat()), UP, DN, START, END, {}, {},
                  use_prints=False)
    placed = [e for e in r["events"] if e["kind"] == "order_placed"]
    assert all(END - e["t"] >= S2_CANDIDATE["tte_shutdown_s"]
               for e in placed)
