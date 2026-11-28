"""Where the BTC15m edge actually is, and why our depth setting misses it.

THE FINDING, from L5's own 527 BTC15m windows (63,569 fills), net per
DOLLAR deployed so it is scale-free against our $1-3 clips. Keyed on the
MATCHED PAIR COST ACHIEVED:

    pair <= 0.95   n=182   strongly positive
    pair  > 0.95   n=324   negative
    all windows            approximately ZERO

That last line is why a univariate or aggregate read says "no edge": the
dear completions cancel the cheap ones exactly. The edge is CONDITIONAL on
the pair cost achieved, and it survives a chronological holdout:

    TRAIN   (earlier half, n=263)  <=0.95  +0.0513/$ t=+2.67
                                    >0.95  -0.0390/$ t=-2.38
    HOLDOUT (later half,  n=264)  <=0.95  +0.0229/$ t=+2.29
                                    >0.95  -0.0166/$ t=-3.61

Both signs preserved, both significant out of sample. The effect shrinks,
as an honest holdout should.

AND FILL COUNT IS NOT THE LEVER. Bucketed by fills per window, every
bucket is insignificant (|t| < 1.6). Working the inventory harder does not
pay; achieving a cheaper pair does. That is what licenses trading fill
FREQUENCY for pair QUALITY.

WHY OUR ENGINE SITS IN THE LOSING ZONE. The planned pair is
`1 - spread - 2 * depth_ticks * tick`. At the deployed depth of 2 on a
one-cent book that is exactly 0.95 -- the threshold itself, with ZERO
margin. Any adverse drift between the first fill and the completion lands
the achieved pair above it. The 2026-11-02 campaign did exactly that:

    window  planned  achieved  drift
    1330z   0.95     1.00      +0.05
    1345z   0.95     0.96      +0.01
    1400z   0.95     0.99      +0.04
                      mean     +0.033

All three completions landed in the >0.95 zone. The one that looked good
(0.96) is in the bucket L5's data scores at -0.0014/$, not the +0.0429/$
bucket.

THE CHANGE THESE TESTS SUPPORT: quote deeper so the PLANNED pair carries
enough margin that the ACHIEVED pair still lands <= 0.95. `L5_TS_DEPTH_TICKS`
already exists and defaults to 2; nothing here is new machinery.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

TICK = D("0.01")
SPREAD = D("0.01")
#: the profitability threshold the holdout validated
EDGE_ZONE = D("0.95")
#: planned -> achieved drift measured on the 2026-11-02 campaign, n=3
MEASURED_DRIFT = D("0.033")


def planned_pair(depth_ticks: int, spread: D = SPREAD) -> D:
    """pair ~= 1 - spread - 2 * depth * tick, the module's own identity."""
    return D(1) - spread - (D(2) * D(depth_ticks) * TICK)


def test_the_deployed_depth_plans_exactly_at_the_threshold():
    """ZERO MARGIN is the defect, stated as arithmetic."""
    assert planned_pair(2) == EDGE_ZONE
    assert EDGE_ZONE - planned_pair(2) == D(0)


@pytest.mark.parametrize("depth,pair,margin", [
    (2, D("0.95"), D("0.00")),
    (3, D("0.93"), D("0.02")),
    (4, D("0.91"), D("0.04")),
    (5, D("0.89"), D("0.06")),
])
def test_depth_buys_margin_against_drift(depth, pair, margin):
    assert planned_pair(depth) == pair
    assert EDGE_ZONE - planned_pair(depth) == margin


def test_only_depth_four_or_more_survives_the_measured_drift():
    """THE RECOMMENDATION, derived rather than chosen.

    Mean measured drift is +0.033. A depth whose margin does not cover it
    lands the achieved pair in the losing zone on average.
    """
    survives = [d for d in (2, 3, 4, 5)
                if planned_pair(d) + MEASURED_DRIFT <= EDGE_ZONE]
    assert survives == [4, 5], survives
    # and depth 4 lands comfortably inside, not marginally
    assert planned_pair(4) + MEASURED_DRIFT == D("0.943")


def test_the_campaign_completions_were_all_outside_the_edge_zone():
    """Our three venue-confirmed pairs, against the validated threshold."""
    achieved = [D("1.00"), D("0.96"), D("0.99")]
    assert all(p > EDGE_ZONE for p in achieved)
    assert sum(1 for p in achieved if p <= EDGE_ZONE) == 0


def test_a_deeper_plan_still_clears_the_entry_ceiling():
    """The 0.96 opening cap is not in the way: deeper is cheaper."""
    for depth in (3, 4, 5):
        assert planned_pair(depth) <= TS.MAX_PAIR_COST


def test_the_depth_knob_exists_and_defaults_to_two(monkeypatch):
    """No new machinery: this is an existing environment variable."""
    monkeypatch.delenv("L5_TS_DEPTH_TICKS", raising=False)
    assert TS.depth_ticks() == 2
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", "4")
    assert TS.depth_ticks() == 4


@pytest.mark.parametrize("bad", ["-1", "abc", ""])
def test_an_unreadable_depth_falls_back_to_the_deployed_default(monkeypatch, bad):
    monkeypatch.setenv("L5_TS_DEPTH_TICKS", bad)
    assert TS.depth_ticks() == 2


def test_deeper_quoting_trades_frequency_for_quality_knowingly():
    """The cost is fill rate, and the same dataset says that is the cheap
    thing to spend: fills-per-window was insignificant in every bucket
    (|t| < 1.6) while pair cost was significant out of sample."""
    assert planned_pair(4) < planned_pair(2)        # cheaper pair
    assert D(1) - planned_pair(4) > D(1) - planned_pair(2)   # more edge
