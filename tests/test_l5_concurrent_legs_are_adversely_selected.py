"""Two concurrent maker legs are STRUCTURALLY worse than one sequential leg.

This pins a negative result so the mechanism is not re-proposed. It is the
answer to "would resting both legs at once raise our completion rate?", and
the answer is no -- not marginally, and not for a tunable reason.

THE GEOMETRY. Both legs are BUYs, and the two tokens are complementary, so in
UP-price terms P:

    BUY UP   resting at  up_px            fills when  P <= up_px
    BUY DOWN resting at  dn_px            fills when  P >= 1 - dn_px

With `build_orders` pricing at `depth_ticks` behind each touch,

    up_px     = bid - d*tick
    1 - dn_px = ask + d*tick

so the two fill conditions are separated by a DEAD BAND of

    (ask + d*tick) - (bid - d*tick) = spread + 2*d*tick

which at the BTC15m norm (spread 1 tick, d=2) is 5 cents. The conditions are
mutually exclusive at every instant: no price satisfies both. Both legs fill
only if P traverses BELOW the lower barrier and ABOVE the upper one inside the
same window -- a two-sided barrier crossing, not two independent draws. Any
directional move fills one leg and carries the other further away, which is
adverse selection by construction rather than by bad luck.

WHY SEQUENTIAL IS BETTER, which is the part worth keeping. `build_orders` runs
per tick against the live book, so when leg one fills, leg two is priced off
the book AS IT THEN IS -- near the current touch. Resting both at once freezes
two levels 5 cents apart and forfeits that adaptation. The one-order limit was
not merely a risk cap; it is also what keeps the second leg adaptive.

THE ARITHMETIC, under an INDEPENDENCE model that FAVOURS concurrency (the real
legs are anti-correlated, so the true figure is worse):

    sequential pairing = q          = 0.834196   EV/unit -0.007215
    concurrent pairing = q/(2-q)    = 0.715554   EV/unit -0.053328
    breakeven required              = 0.852758

Concurrency moves EV 7.4x in the WRONG direction. q/(2-q) < q for every
q < 1, so no fill probability short of certainty makes concurrency preferable.

NOT A STATEMENT ABOUT L5. L5 pairs 91.07% of inventory (2*matched/(up+dn) over
5,546 windows of raw venue activity) with a median 26s gap between first Up and
first Down buy. That works because his per-leg fill probability is near one --
at 107 fills a window he is much of the queue. Concurrency is safe only in that
regime; copying the timing without the queue position imports the adverse
selection and none of the fills.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

TICK = D("0.01")

#: measured, not assumed: 667 of 671 observed shadow ticks were 1 tick wide
BTC15M_TYPICAL_SPREAD = D("0.01")

#: src/l5_complete_requote_gate.py BASELINE
PAIR_PROFIT = D("0.057230")
RESIDUAL_LOSS = D("-0.331449")
OBSERVED_COMPLETION = D("0.834196")
BREAKEVEN_COMPLETION = D("0.852758")


def legs(bid, ask, depth_ticks):
    """The prices `build_orders` would rest at, in UP-price terms."""
    up_px = D(bid) - D(depth_ticks) * TICK
    dn_px = (D(1) - D(ask)) - D(depth_ticks) * TICK
    return up_px, dn_px


def fill_barriers(bid, ask, depth_ticks):
    """(lower, upper): UP fills at P <= lower, DOWN fills at P >= upper."""
    up_px, dn_px = legs(bid, ask, depth_ticks)
    return up_px, D(1) - dn_px


def ev(p):
    return D(p) * PAIR_PROFIT + (D(1) - D(p)) * RESIDUAL_LOSS


class TestTheLegsCannotBothFillAtOneInstant:

    @pytest.mark.parametrize("bid,ask", [
        ("0.66", "0.67"), ("0.40", "0.41"), ("0.48", "0.49"),
        ("0.80", "0.81"), ("0.05", "0.06"), ("0.20", "0.21"),
    ])
    def test_the_fill_conditions_are_mutually_exclusive(self, bid, ask):
        lo, hi = fill_barriers(bid, ask, 2)
        assert lo < hi, (bid, ask, lo, hi)
        # no price can satisfy both
        for p in (lo, hi, (lo + hi) / 2, D("0.5")):
            assert not (p <= lo and p >= hi), p

    def test_the_dead_band_is_spread_plus_two_depth_ticks(self):
        for d in (1, 2, 3):
            lo, hi = fill_barriers("0.66", "0.67", d)
            assert hi - lo == BTC15M_TYPICAL_SPREAD + 2 * D(d) * TICK

    def test_at_the_btc15m_norm_the_band_is_five_cents(self):
        lo, hi = fill_barriers("0.66", "0.67", 2)
        assert hi - lo == D("0.05")

    def test_a_deeper_quote_widens_the_band_it_must_traverse(self):
        """Resting further back makes pairing HARDER, not safer."""
        w = [fill_barriers("0.66", "0.67", d)[1]
             - fill_barriers("0.66", "0.67", d)[0] for d in (1, 2, 3)]
        assert w[0] < w[1] < w[2]


class TestConcurrencyLowersThePairingRate:

    def test_concurrent_is_worse_than_sequential_at_every_fill_rate(self):
        """q/(2-q) < q for all q < 1. There is no q that rescues it."""
        for q in ("0.10", "0.50", "0.834196", "0.90", "0.95", "0.99"):
            q = D(q)
            assert q / (D(2) - q) < q, q

    def test_they_converge_only_at_certainty(self):
        q = D("1")
        assert q / (D(2) - q) == q

    def test_the_measured_case_moves_ev_the_wrong_way(self):
        seq = OBSERVED_COMPLETION
        conc = seq / (D(2) - seq)
        assert conc < seq
        assert ev(conc) < ev(seq)
        # and neither reaches breakeven
        assert ev(seq) < 0 and ev(conc) < 0
        # the degradation is large, not marginal
        assert ev(conc) / ev(seq) > D(5)

    def test_concurrency_cannot_reach_breakeven_from_our_fill_rate(self):
        conc = OBSERVED_COMPLETION / (D(2) - OBSERVED_COMPLETION)
        assert conc < BREAKEVEN_COMPLETION

    def test_the_fill_rate_concurrency_would_need_is_implausible(self):
        """Solve q/(2-q) >= breakeven. The required q is the real verdict."""
        b = BREAKEVEN_COMPLETION
        need = 2 * b / (1 + b)          # invert q/(2-q) = b
        assert need > D("0.92")
        # i.e. concurrency needs a per-leg fill rate above L5's own 0.9107
        # pairing rate, from behind a 2,000-10,000 share queue at 1.33 shares
        assert need > D("0.9107")


class TestSequentialKeepsTheSecondLegAdaptive:

    def test_the_second_leg_reprices_to_the_book_that_exists_then(self):
        """The structural advantage concurrency would forfeit.

        Leg one rests against the book at placement. If the price then moves
        far enough to fill it, the book has MOVED -- and a sequential leg two
        is priced off that new book, close to the new touch, instead of at a
        level fixed 5 cents away.
        """
        _, hi_at_entry = fill_barriers("0.66", "0.67", 2)
        # UP fills: price fell to 0.64. The book is now around 0.64.
        moved_bid, moved_ask = "0.63", "0.64"
        dn_fixed = legs("0.66", "0.67", 2)[1]            # frozen plan
        dn_reprice = legs(moved_bid, moved_ask, 2)[1]    # sequential plan
        # the repriced complement is CHEAPER against the new book, i.e. it
        # rests nearer the live touch rather than across a stale band
        assert dn_reprice > dn_fixed
        assert hi_at_entry == D("0.69")
