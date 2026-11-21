"""THE QUEUE-SCENARIO REPLAY, AND THE PROPERTY A BUG OF MINE VIOLATED (#208).

The harness brackets a maker fill from ladder snapshots, because
`src/l5_queue_depletion` establishes there is NO trade-print series for
these recordings. Its three scenarios differ in the fill ARM and in how much
of the displayed size is assumed ahead of us.

THE LOAD-BEARING INVARIANTS, both of which a first version got wrong:

  1. PROVEN IS INDEPENDENT OF QUEUE POSITION. The opposite touch reaching
     our price fills us wherever we sat in the queue. A walk that returned
     at the FIRST condition met let an early POSSIBLE mask a later PROVEN,
     which made the optimistic assumption report FEWER proven fills than the
     pessimistic one -- impossible, and the signal that the walk was wrong.

  2. MORE FAVOURABLE ASSUMPTIONS CANNOT PRODUCE FEWER FILLS. pessimistic <=
     base <= optimistic, in pairs and in net, cell by cell.

Nothing here is a realized result.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "analysis", "l5", "issue208"))

from src import l5_queue_depletion as QD          # noqa: E402
import queue_scenarios as QS                      # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def ladder(bid, ask, bid_sz="100", ask_sz="100", t=0.0):
    """A one-tick book with a single rung a side, for exact arithmetic."""
    return {"t": t, "bids": [[bid, bid_sz]], "asks": [[ask, ask_sz]]}


# =====================================================================
# 1. THE TWO INVARIANTS.
# =====================================================================

def test_proven_does_not_depend_on_queue_position():
    """The opposite touch reaching our price fills us from anywhere."""
    frames = [ladder("0.5000", "0.5100", t=0.0),
              ladder("0.4000", "0.4100", t=5.0)]      # ask fell through 0.50
    seen = set()
    for frac in (D(1), D("0.5"), D(0)):
        out = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                             queue_fraction=frac, own_qty=D(1), ttl_s=None)
        assert out["proven"] is True, (frac, out)
        seen.add(out["queue_ahead"])
    assert len(seen) == 1            # the same queue reading, every time


def test_an_early_possible_cannot_mask_a_later_proven():
    """The exact shape of the bug: size leaves the level first, and the
    opposite touch arrives afterwards."""
    frames = [ladder("0.5000", "0.5100", bid_sz="10", t=0.0),
              ladder("0.5000", "0.5100", bid_sz="1", t=5.0),   # 9 cleared
              ladder("0.4000", "0.4100", t=10.0)]              # then PROVEN
    out = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                         queue_fraction=D(0), own_qty=D(1), ttl_s=None)
    assert out["possible"] is True
    assert out["proven"] is True
    assert out["verdict"] == QD.PROVEN
    assert QS.filled(out, arm=QD.PROVEN) is True


def test_the_front_of_the_queue_still_needs_our_own_size_to_clear():
    """`optimistic` is not "filled for free": something must have traded."""
    quiet = [ladder("0.5000", "0.5100", bid_sz="10", t=0.0),
             ladder("0.5000", "0.5100", bid_sz="10", t=5.0)]
    out = QS.leg_outcome(QS.UP, D("0.5000"), quiet, start=0,
                         queue_fraction=D(0), own_qty=D(1), ttl_s=None)
    assert out["possible"] is False
    assert out["verdict"] == QD.IMPOSSIBLE


def test_a_bigger_queue_ahead_is_never_easier_to_clear():
    frames = [ladder("0.5000", "0.5100", bid_sz="10", t=0.0),
              ladder("0.5000", "0.5100", bid_sz="5", t=5.0)]
    front = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                           queue_fraction=D(0), own_qty=D(1), ttl_s=None)
    behind = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                            queue_fraction=D(1), own_qty=D(1), ttl_s=None)
    assert front["possible"] is True
    assert behind["possible"] is False
    assert D(behind["need"]) > D(front["need"])


# =====================================================================
# 2. TTL IS A CANCELLATION, AND IT COSTS THE FILL.
# =====================================================================

def test_a_ttl_cancels_before_a_later_fill_and_says_so():
    frames = [ladder("0.5000", "0.5100", t=0.0),
              ladder("0.5000", "0.5100", t=30.0),
              ladder("0.4000", "0.4100", t=90.0)]      # PROVEN, too late
    held = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                          queue_fraction=D(1), own_qty=D(1), ttl_s=None)
    assert held["proven"] is True
    cancelled = QS.leg_outcome(QS.UP, D("0.5000"), frames, start=0,
                               queue_fraction=D(1), own_qty=D(1), ttl_s=60.0)
    assert cancelled["proven"] is False
    assert cancelled["verdict"] == QD.IMPOSSIBLE
    assert cancelled["reason"] == "TTL_CANCELLED"


# =====================================================================
# 3. THE ECONOMICS, EACH LINE APART.
# =====================================================================

def test_a_matched_pair_redeems_a_dollar_and_a_single_leg_is_carried():
    pair = QS.window_pnl(pair_cost=D("0.95"), qty=D(1), up_filled=True,
                         dn_filled=True, rebate_per_fill=D(0))
    assert pair["state"] == "PAIR"
    assert D(pair["matched_pnl"]) == D("0.05")
    assert D(pair["residual_pnl"]) == 0

    single = QS.window_pnl(pair_cost=D("0.95"), qty=D(1), up_filled=True,
                           dn_filled=False, rebate_per_fill=D(0))
    assert single["state"] == "RESIDUAL"
    assert D(single["matched_pnl"]) == 0
    assert D(single["residual_pnl"]) == QS.RESIDUAL_PER_CONTRACT

    none = QS.window_pnl(pair_cost=D("0.95"), qty=D(1), up_filled=False,
                         dn_filled=False, rebate_per_fill=D(0))
    assert none["state"] == "NO_FILL"
    assert D(none["rebate"]) == 0


def test_the_rebate_is_its_own_line_and_defaults_to_zero():
    """It stays at zero until credited and source-accounted."""
    no_reb = QS.window_pnl(pair_cost=D("0.95"), qty=D(1), up_filled=True,
                           dn_filled=True, rebate_per_fill=D(0))
    assert D(no_reb["rebate"]) == 0
    with_reb = QS.window_pnl(pair_cost=D("0.95"), qty=D(1), up_filled=True,
                             dn_filled=True,
                             rebate_per_fill=QS.CONFIRMED_REBATE_PER_FILL)
    assert D(with_reb["rebate"]) == QS.CONFIRMED_REBATE_PER_FILL * 2
    assert D(with_reb["matched_pnl"]) == D(no_reb["matched_pnl"])


def test_the_break_even_completion_rate_is_the_measured_identity():
    """q* = -residual / ((1 - pair) - residual), with residual = -0.0236."""
    assert QS.breakeven_completion_rate("0.9600") == D("0.3711")
    assert QS.breakeven_completion_rate("0.9800") == D("0.5413")
    assert QS.breakeven_completion_rate("0.8700") == D("0.1536")
    # a pair that cannot beat carrying at ANY fill rate has no break-even
    assert QS.breakeven_completion_rate("1.0300") is None
    # dearer pairs always demand a higher completion rate
    rates = [QS.breakeven_completion_rate(p)
             for p in ("0.8700", "0.9300", "0.9600", "0.9800")]
    assert rates == sorted(rates)


# =====================================================================
# 4. THE REPLAY ITSELF, ON THE RECORDED LADDERS.
# =====================================================================

@pytest.fixture(scope="module")
def oos():
    rows = []
    for d in QS.DEPTHS:
        rows += QS.replay(QS.OUT_OF_SAMPLE, depth_ticks=d, root=ROOT)
    return rows


def test_the_out_of_sample_recordings_are_present_and_used(oos):
    assert len(oos) > 1000
    assert {r["depth_ticks"] for r in oos} == set(QS.DEPTHS)
    assert {r["scenario"] for r in oos} == {n for n, _, _ in QS.SCENARIOS}


def test_pairs_and_net_rise_with_the_favourability_of_the_assumption(oos):
    """INVARIANT 2, cell by cell. The bug broke exactly this."""
    by = {(s["depth_ticks"], s["scenario"]): s for s in QS.summarise(oos)}
    for d in QS.DEPTHS:
        p = by[(d, "pessimistic")]
        b = by[(d, "base")]
        o = by[(d, "optimistic")]
        assert p["pairs"] <= b["pairs"] <= o["pairs"], d
        assert D(p["net"]) <= D(b["net"]) <= D(o["net"]), d
        assert p["residual"] >= b["residual"] >= o["residual"], d


def test_the_pair_a_depth_buys_is_the_spread_identity(oos):
    """`pair = 1 - spread - 2*d*tick`, so on a one-tick book the touch buys
    0.99 and two ticks behind buys 0.95. The <=0.96 / >0.96 split at these
    depths is therefore arithmetic, NOT a statement about rescue cases."""
    for d, expected in ((0, D("0.99")), (2, D("0.95"))):
        costs = {D(r["pair_cost"]) for r in oos if r["depth_ticks"] == d
                 and r["state"] == "PAIR"}
        assert costs, d
        assert max(costs) <= expected, (d, max(costs))


def test_the_touch_fails_the_conservative_arm_and_depth_survives_it(oos):
    """THE DECISION. At the touch the pair is 0.99, needing a 70.2%
    completion rate; two ticks behind it is 0.95, needing 32.1%."""
    by = {(s["depth_ticks"], s["scenario"]): s for s in QS.summarise(oos)}
    assert D(by[(0, "pessimistic")]["net_per_entry"]) < 0
    assert D(by[(2, "pessimistic")]["net_per_entry"]) > 0
    assert QS.breakeven_completion_rate("0.99") > D("0.70")
    assert QS.breakeven_completion_rate("0.95") < D("0.33")
