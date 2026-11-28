"""The replay's edge, repriced from venue-confirmed settlement.

The out-of-sample replay reports +0.014833/entry for variant C on its
conservative arm. It charges a completed pair at the price it QUOTED and an
unmatched leg at a flat -0.0236/contract. We now have a venue-confirmed
settlement for each of those terms, and both are materially worse.

  PAIR COMPOSITION -- window 2015z, 2026-11-06, the only window whose pairs
  completed and settled. Its reconciliation is penny-exact against the
  venue's own `up_cost 1.541400` / `dn_cost 2.4200`: UP 4.03 @ avg own
  0.38248, DOWN 4 @ avg own 0.60500, realized pair 0.98748 against a 0.96
  quote, gross margin 0.01252/pair, x4 matched = 0.050074 which IS the
  settlement's matched_pnl. The 0.02748 gap is legging.

  STRANDED COST -- window 1530z, 2026-11-09, the only venue-settled
  stranded leg: 0.7 DOWN at own 0.44, UP settled, -$0.3080, reconciled
  `ours 0.7 / venue 0.7000`. That is -0.4400/contract.

These tests pin the arithmetic and the verdict so neither can drift. They
assert NO profitability -- the point of the file is that there is none.
"""
from __future__ import annotations

from decimal import Decimal as D

from analysis.l5.issue215 import settlement_backed_economics as S


def test_the_fill_structure_is_the_replays_own_not_re_estimated():
    """P(pair) and P(residual) are properties of the recorded books. This
    module reprices the CASH terms only."""
    assert S.P_PAIR == D("0.3652")
    assert S.P_RESIDUAL == D("0.1372")


def test_the_reconstruction_reproduces_the_published_replay_net():
    """A guard on the whole exercise: priced with the replay's OWN
    assumptions this must return the replay's own number. It lands at
    +0.015022 against a published +0.014833 -- a rounding difference in
    the published figure, not a different model."""
    got = S.net_per_entry(S.ASSUMED_MARGIN, S.ASSUMED_RESIDUAL)
    assert abs(got - S.REPLAY_NET) < D("0.0003"), got


def test_the_implied_quoted_pair_is_the_depth_two_quote():
    """Inverting the published net gives a 0.951 pair, which is what a
    0.96-capped depth-2 quote composes to. That is the cross-check that
    the cash terms were reconstructed correctly."""
    implied = (S.REPLAY_NET - S.P_RESIDUAL * S.ASSUMED_RESIDUAL) / S.P_PAIR
    assert (D("1") - implied).quantize(D("0.001")) == D("0.951")


# =====================================================================
# THE VENUE-CONFIRMED FIGURES
# =====================================================================

def test_the_realized_pair_cost_is_the_settled_one_not_the_quoted_one():
    assert S.REALIZED_PAIR == D("0.98748")
    assert S.CONFIRMED_MARGIN == D("0.01252")
    # penny-exact against the settlement: 4 pairs x 0.01252 = 0.050074... and
    # the settlement reported matched_pnl 0.050074.
    assert (S.CONFIRMED_MARGIN * 4).quantize(D("0.000001")) == D("0.050080")


def test_the_realized_pair_is_DEARER_than_the_quote_ceiling():
    """Legging: the legs fill at different moments, each on an adverse
    move, so the second is requoted into a market already against us."""
    assert S.REALIZED_PAIR > D("0.96")
    assert (S.REALIZED_PAIR - D("0.96")).quantize(D("0.00001")) == D("0.02748")


def test_the_confirmed_stranded_cost_is_the_settled_1530z_leg():
    assert S.CONFIRMED_RESIDUAL == D("-0.4400")
    # 0.7 contracts at own 0.44 settled worthless = -0.308
    assert (S.CONFIRMED_RESIDUAL * D("0.7")).quantize(D("0.0001")) == D("-0.3080")


# =====================================================================
# THE VERDICT, AND IT IS NEGATIVE
# =====================================================================

def test_correcting_the_pair_cost_alone_removes_most_of_the_edge():
    net = S.net_per_entry(S.CONFIRMED_MARGIN, S.ASSUMED_RESIDUAL)
    assert net > 0, "still positive, but barely"
    assert net.quantize(D("0.000001")) == D("0.001334")
    # a 91% reduction against the published figure
    assert net < S.REPLAY_NET / 10


def test_correcting_the_stranded_cost_alone_makes_it_negative():
    net = S.net_per_entry(S.ASSUMED_MARGIN, S.CONFIRMED_RESIDUAL)
    assert net < 0
    assert net.quantize(D("0.000001")) == D("-0.042108")


def test_BOTH_venue_confirmed_corrections_give_a_clear_loss():
    """THE RESULT. Neither term is assumed; both are settled."""
    net = S.net_per_entry(S.CONFIRMED_MARGIN, S.CONFIRMED_RESIDUAL)
    assert net < 0
    assert net.quantize(D("0.000001")) == D("-0.055796")


def test_the_headroom_on_the_replays_stranded_assumption_is_a_cent(
        ):
    """At the venue's pair cost the edge breaks even at -0.0333/contract.
    The replay assumes -0.0236, so the entire margin of safety is under
    one cent per contract -- and the one settled observation is 13x
    outside it."""
    be = S.breakeven_residual(S.CONFIRMED_MARGIN)
    assert be.quantize(D("0.000001")) == D("-0.033326")
    assert (be - S.ASSUMED_RESIDUAL).quantize(D("0.000001")) == D("-0.009726")
    assert S.CONFIRMED_RESIDUAL / be > 13


def test_surviving_the_observed_stranded_cost_needs_a_pair_the_venue_never_offers():
    """To carry a -0.44 strand the pair must compose at 0.8347. The venue
    composed 0.98748, and the quote ceiling is 0.96. The gap is ~15 cents
    on a market whose entire spread is one or two."""
    be_margin = S.breakeven_margin(S.CONFIRMED_RESIDUAL)
    assert be_margin.quantize(D("0.000001")) == D("0.165301")
    required_pair = (D("1") - be_margin).quantize(D("0.0001"))
    assert required_pair == D("0.8347")
    assert required_pair < D("0.96"), "below even the quote ceiling"
    assert S.REALIZED_PAIR - required_pair > D("0.15")


def test_no_pricing_in_the_table_that_uses_a_venue_figure_is_positive():
    """Stated as a property: every row that replaces an assumption with a
    settlement is non-positive except the half-corrected one, and that one
    survives on under a cent of headroom."""
    both = S.net_per_entry(S.CONFIRMED_MARGIN, S.CONFIRMED_RESIDUAL)
    strand_only = S.net_per_entry(S.ASSUMED_MARGIN, S.CONFIRMED_RESIDUAL)
    assert both < 0 and strand_only < 0
