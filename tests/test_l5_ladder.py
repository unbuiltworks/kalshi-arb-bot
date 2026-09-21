"""Regressions for the L5 ladder identification, the frozen band, and the
causality invariants of the PMUS ladder replay.

These lock down the three things that would silently invalidate Phase 1-3:
a band rule that drifts off its TRAIN-frozen value, a replay that credits a fill
before the previous one, and the algebraic identity that makes the flow-imbalance
floor a proof rather than an observation.
"""
import sys
import pytest

sys.path.insert(0, "/home/user/kalshi-arb-bot")
sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/l5")
sys.path.insert(0, "/home/user/kalshi-arb-bot/analysis/pmus")

import l5_ladder_heldout as H
import l5_ladder_identification as I


def test_frozen_band_is_the_train_optimum():
    """R=4 must stay at -2..+1, the band identified on TRAIN. If this changes,
    Phase 3 is no longer running the frozen model."""
    assert H.band(4) == (2, 1)
    assert H.band(1) == (0, 0)


def test_one_rung_covers_only_the_touch():
    assert H.covered(0.50, 0.50, 1)
    assert not H.covered(0.49, 0.50, 1)
    assert not H.covered(0.51, 0.50, 1)


def test_four_rungs_straddle_the_touch():
    for px in (0.48, 0.49, 0.50, 0.51):
        assert H.covered(px, 0.50, 4), px
    assert not H.covered(0.47, 0.50, 4)
    assert not H.covered(0.52, 0.50, 4)


def test_replay_never_uses_the_price_it_is_scored_on():
    """The touch is lagged, so the first fill on a side can never be scored --
    there is no prior price to have rested against. No future information."""
    fills = [{"timestamp": "100", "outcome": "Up", "price": "0.50", "quantity": "1"},
             {"timestamp": "101", "outcome": "Up", "price": "0.50", "quantity": "1"}]
    got = H.replay(fills, 4)
    assert len(got) == 1 and got[0]["t"] == 101.0


def test_required_rungs_is_a_floor_not_an_estimate():
    """Two distinct same-side prices inside the bucket require two live orders."""
    f = [{"timestamp": "10", "outcome": "Up", "price": "0.50", "quantity": "1"},
         {"timestamp": "10", "outcome": "Up", "price": "0.51", "quantity": "1"},
         {"timestamp": "40", "outcome": "Up", "price": "0.52", "quantity": "1"}]
    assert I.required_rungs(f, bucket_s=1.0) == 2


def test_geometry_sums_absolute_residual_per_window():
    """A long window must not cancel a short one. Two windows each carrying 1
    unit of residual in OPPOSITE directions are 2 units of residual, not 0."""
    a = [{"t": 1, "outcome": "Up", "price": .5, "quantity": 2},
         {"t": 2, "outcome": "Down", "price": .5, "quantity": 1}]
    b = [{"t": 1, "outcome": "Up", "price": .5, "quantity": 1},
         {"t": 2, "outcome": "Down", "price": .5, "quantity": 2}]
    g = H.geometry([a, b])
    assert g["residual"] == 2.0 and g["matched"] == 2.0


@pytest.mark.parametrize("a,b", [(10, 10), (13, 7), (100, 1), (55, 45)])
def test_flow_imbalance_floor_is_an_identity_not_a_bound(a, b):
    """R/M = 2I/(1-I) EXACTLY, for any acquisitions whatsoever.

    CORRECTED 2026-09-21. This test previously claimed the identity showed the
    geometry gap was a property of the flow rather than the execution surface. It
    shows no such thing. Both sides are computed from the SAME acquisition counts,
    so the equality is arithmetic and holds for a perfect execution, a terrible one
    or a random one. Reporting that an arm "sits on its floor" was circular.

    The real evidence for STOP CONDITION B is empirical and lives elsewhere: a
    zero-queue 20-rung +/-10-tick upper bound produced I = 0.327 against the 4-rung
    arm's 0.315, i.e. vastly more reach did not improve balance."""
    I_ = abs(a - b) / (a + b)
    rm = abs(a - b) / min(a, b)
    assert rm == pytest.approx(2 * I_ / (1 - I_), rel=1e-9)


def test_ladder_replay_drops_crossing_rungs():
    """No taker crossing anywhere: a rung at or through the opposing touch is
    never placed, whatever the band asks for."""
    import ladder_replay as LR
    r = {"bid": 0.50, "ask": 0.52}
    pxs = LR.rung_prices(0.50, True, r, 4, (2, 1))
    assert max(pxs) < 0.52 and 0.51 in pxs
    tight = LR.rung_prices(0.50, True, {"bid": 0.50, "ask": 0.51}, 4, (2, 1))
    assert max(tight) <= 0.50


def test_ladder_replay_preserves_identical_rungs():
    """Re-placing the same ladder must not reset queue state."""
    import ladder_replay as LR
    live, stats = {}, {"placed": 0, "resets": 0, "preserved": 0, "lifetimes": [], "comp_log": []}
    LR._place(live, stats, True, [0.48, 0.49, 0.50], 0.0, 100.0, "open", True, 0.50, 1.0)
    live[True][0].cum = 42.0
    LR._place(live, stats, True, [0.48, 0.49, 0.50], 5.0, 100.0, "open", True, 0.50, 1.0)
    assert stats["preserved"] == 3 and stats["resets"] == 0
    assert live[True][0].cum == 42.0
