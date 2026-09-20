"""A fill may never be credited before the fill that preceded it.

Regression for the defect found in the first prospective PMUS replay, where an
episode reported a recycle latency of MINUS 92 SECONDS. Decisions are made in
sequence, but a fill is stamped at the PRINT that filled it, so a later
decision whose print lands early could be stamped ahead of an earlier
decision's late print. That builds an inventory path running backwards in time.

The invariant under test: the compatible-print window for a decision starts at
max(decision time, previous fill time), so every credited fill time is
non-decreasing along a sequence.
"""
import importlib.util
import os
import sys
import types

import pytest

MOD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "analysis", "pmus", "l5_sequential_replay_same_market.py")


def _load():
    """Import the replay module with its scratchpad-only deps stubbed.

    The module reaches for the live analysis harness at import time; the
    causality helpers depend on nothing but `compatible`, so a stub keeps the
    test hermetic.
    """
    stub = types.ModuleType("cohorts")

    def compatible(prints, t0, t1, px, side_yes):
        lim = px if side_yes else (1.0 - px)
        return [p for p in prints
                if t0 < p["t"] <= t1
                and (p["px"] <= lim if side_yes else p["px"] >= lim)]

    stub.compatible = compatible
    stub.M = None
    stub.books = lambda: {}
    stub.flow = lambda: {}
    sys.modules.setdefault("cohorts", stub)
    spec = importlib.util.spec_from_file_location("_l5_seq_replay", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


R = _load()


def _prints(*pairs):
    return [{"t": t, "px": px, "size": 10.0} for t, px in pairs]


def test_window_never_opens_before_the_previous_fill():
    """A print older than the last fill is not a candidate, even though it is
    newer than this decision."""
    prints = _prints((100.0, 0.30), (500.0, 0.30))
    comp = R.compatible_window(prints, t_decision=50.0, t_last_fill=400.0,
                               px=0.30, side_yes=True)
    assert [p["t"] for p in comp] == [500.0], (
        "a print at t=100 precedes the previous fill at t=400 and must not "
        "be usable")


def test_queue_aware_fill_time_is_monotone_along_a_sequence():
    """Walk decisions in order; each credited fill must be >= the last."""
    prints = _prints((10.0, 0.20), (20.0, 0.20), (30.0, 0.20),
                     (40.0, 0.20), (50.0, 0.20))
    t_last = 0.0
    seen = []
    # decision times deliberately NOT aligned with print order
    for t_decision in (5.0, 35.0, 8.0, 45.0):
        _, t_fill = R.fill_candidates(prints, t_decision, t_last, px=0.20,
                                      side_yes=True, depth=0.0)
        if t_fill is None:
            continue
        assert t_fill >= t_last, (
            "fill at %s credited before previous fill at %s" % (t_fill, t_last))
        t_last = t_fill
        seen.append(t_fill)
    assert seen == sorted(seen) and seen, "sequence must be non-decreasing"


def test_out_of_order_decision_cannot_backdate_a_fill():
    """The -92s case, minimally reproduced.

    An early decision fills late; a later decision would otherwise fill from an
    early print. The later fill must not precede the earlier one.
    """
    prints = _prints((100.0, 0.10), (900.0, 0.10))
    _, first = R.fill_candidates(prints, t_decision=800.0, t_last_fill=0.0,
                                 px=0.10, side_yes=True, depth=0.0)
    assert first == 900.0
    _, second = R.fill_candidates(prints, t_decision=810.0, t_last_fill=first,
                                  px=0.10, side_yes=True, depth=0.0)
    assert second is None or second >= first, (
        "second fill %s backdated before first fill %s" % (second, first))


def test_depth_must_be_strictly_exceeded_before_a_queue_aware_fill():
    """Guards the queue rule itself while we are here: cumulative compatible
    volume must EXCEED the depth resting ahead, not merely equal it."""
    prints = [{"t": 10.0, "px": 0.40, "size": 25.0}]
    _, t_fill = R.fill_candidates(prints, 1.0, 0.0, px=0.40, side_yes=True,
                                  depth=25.0)
    assert t_fill is None, "equal volume must not clear the queue"
    _, t_fill = R.fill_candidates(prints, 1.0, 0.0, px=0.40, side_yes=True,
                                  depth=24.0)
    assert t_fill == 10.0


def test_side_compatibility_is_respected():
    """Sides are read in YES terms.

    A resting BUY YES at 0.30 is filled by a print at or below 0.30. A resting
    BUY NO at 0.30 is an offer of YES at 0.70, so it takes a print at or above
    0.70 -- not merely any print richer than 0.30.
    """
    comp = R.compatible_window(_prints((10.0, 0.45)), 1.0, 0.0,
                               px=0.30, side_yes=True)
    assert comp == []
    comp = R.compatible_window(_prints((10.0, 0.45)), 1.0, 0.0,
                               px=0.30, side_yes=False)
    assert comp == [], "0.45 is below the 0.70 the NO rung needs"
    comp = R.compatible_window(_prints((10.0, 0.75)), 1.0, 0.0,
                               px=0.30, side_yes=False)
    assert [p["t"] for p in comp] == [10.0]
    comp = R.compatible_window(_prints((10.0, 0.25)), 1.0, 0.0,
                               px=0.30, side_yes=True)
    assert [p["t"] for p in comp] == [10.0]
