"""The maker ceiling is a WIDENING, so its tests are about what it refuses.

0.96 reserves margin for a taker round trip. A maker is charged nothing, so
for a maker-both-legs completion the break-even is exactly 1.0000 and 0.96
leaves four cents unspent. This gate spends three of them -- but only when
explicitly enabled, only for post-only-both-legs, and never at or above par.

The rebate is NOT what justifies it. At the deployed $0.80/2-contract caps
the venue's per-print cent rounding credits $0.00 at almost every price, and
our own history records the floor biting below ~1.62 shares per print. So
the ceiling is set below the NO-REBATE break-even and these tests pin the
rebate at zero in the gate's own accounting.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_maker_pair_gate as G
from src import l5_defensive_completion as DC


# ===================================================== it ships disabled
def test_the_gate_is_off_unless_the_env_says_otherwise(monkeypatch):
    monkeypatch.delenv(G.ENABLED_ENV, raising=False)
    assert G.enabled() is False
    assert G.effective_ceiling(maker_both_legs=True) == G.TAKER_ERA_CEILING


@pytest.mark.parametrize("v", ["", "0", "no", "off", "YE", "true ", "maybe",
                               "L5_MAKER_PAIR_GATE"])
def test_only_the_exact_true_strings_enable_it(monkeypatch, v):
    """A typo must not half-arm a gate that widens what we may pay."""
    monkeypatch.setenv(G.ENABLED_ENV, v)
    if v.strip().lower() in G._TRUE:
        pytest.skip("that one is a true string")
    assert G.enabled() is False


@pytest.mark.parametrize("v", ["1", "yes", "true", "on", "YES", "On"])
def test_the_true_strings_do_enable_it(monkeypatch, v):
    monkeypatch.setenv(G.ENABLED_ENV, v)
    assert G.enabled() is True


def test_provenance_states_it_is_not_authorized_for_live():
    assert G.provenance()["authorized_for_live"] is False
    assert G.provenance()["rebate_valued_at"] == "0.00"


# ============================================ it never widens the TAKER path
def test_a_taker_completion_never_gets_the_maker_ceiling():
    """A taker pair at 0.99 is net-NEGATIVE: fees are charged on both legs.
    Enabling the gate must not reach that path."""
    assert G.effective_ceiling(maker_both_legs=False,
                               gate_enabled=True) == G.TAKER_ERA_CEILING
    d = G.decide(first_leg_own_cost="0.50", complement_own_cost="0.48",
                 maker_both_legs=False, gate_enabled=True)
    assert d["allowed"] is False
    assert d["refused_reason"] == G.REFUSE_NOT_MAKER


def test_the_taker_era_ceiling_constant_still_matches_the_deployed_one():
    """If anyone edits 0.96 this fails, because the two must not drift."""
    assert G.TAKER_ERA_CEILING == DC.MAKER_COMPLETE_CEILING == D("0.96")


# ==================================================== par is an absolute bound
@pytest.mark.parametrize("pair", ["1.00", "1.01", "1.05", "1.20"])
def test_it_never_pays_at_or_above_par(pair):
    """What separates this from the defensive unwind ceiling (1.20): that one
    may pay above par to cap a loss; this one only ever locks profit."""
    half = D(pair) / 2
    d = G.decide(first_leg_own_cost=half, complement_own_cost=half,
                 maker_both_legs=True, gate_enabled=True)
    assert d["allowed"] is False
    assert d["refused_reason"] == G.REFUSE_AT_OR_ABOVE_PAR


def test_the_ceiling_is_strictly_below_the_no_rebate_break_even():
    assert G.CEILING < G.NO_REBATE_BREAK_EVEN == D("1.00")


def test_every_allowed_pair_is_locked_profit_with_the_rebate_at_zero():
    for cents in range(50, 100):
        pair = D(cents) / D(100)
        d = G.decide(first_leg_own_cost=pair / 2, complement_own_cost=pair / 2,
                     maker_both_legs=True, gate_enabled=True)
        if d["allowed"]:
            assert D(d["locked_pnl_per_contract"]) > 0
            assert d["rebate_valued_at"] == "0.00"


# ============================================================ the band itself
def test_the_band_between_the_two_ceilings_needs_the_gate_on():
    for pair in ("0.97", "0.98", "0.99"):
        half = D(pair) / 2
        off = G.decide(first_leg_own_cost=half, complement_own_cost=half,
                       maker_both_legs=True, gate_enabled=False)
        on = G.decide(first_leg_own_cost=half, complement_own_cost=half,
                      maker_both_legs=True, gate_enabled=True)
        assert off["allowed"] is False
        assert off["refused_reason"] == G.REFUSE_DISABLED
        assert off["would_allow_if_enabled"] is True
        assert on["allowed"] is True


def test_a_pair_already_under_the_tight_ceiling_is_unaffected():
    """The gate must be a no-op for everything the engine already accepts."""
    for pair in ("0.50", "0.80", "0.95", "0.96"):
        half = D(pair) / 2
        for on in (False, True):
            d = G.decide(first_leg_own_cost=half, complement_own_cost=half,
                         maker_both_legs=True, gate_enabled=on)
            assert d["allowed"] is True


def test_unreadable_costs_refuse_rather_than_default_open():
    for a, b in (("x", "0.4"), (None, "0.4"), ("0.4", "y")):
        d = G.decide(first_leg_own_cost=a, complement_own_cost=b,
                     maker_both_legs=True, gate_enabled=True)
        assert d["allowed"] is False


# ================================================ the marginal justification
def test_completing_beats_stranding_everywhere_the_gate_allows():
    """The band is justified by what a strand costs, not by the rebate."""
    for pair in ("0.97", "0.98", "0.99"):
        m = G.strand_vs_complete(pair=pair)
        assert D(m["complete_per_contract"]) > D(m["strand_per_contract"])
        assert D(m["advantage_of_completing"]) > 0


def test_the_strand_number_is_labelled_as_modelled_not_venue_confirmed():
    m = G.strand_vs_complete(pair="0.99")
    assert m["evidence_grade"] == "MODELLED_FILL_NOT_VENUE_CONFIRMED"


def test_the_indifference_point_is_above_par_and_is_not_the_ceiling():
    """1.0827 is where stranding and completing tie. The gate deliberately
    stops at 0.99, far below it, so it never relies on that estimate."""
    m = G.strand_vs_complete(pair="0.99")
    assert D(m["indifference_pair"]) > D(1)
    assert G.CEILING < D(m["indifference_pair"])
