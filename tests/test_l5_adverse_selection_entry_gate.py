"""The opening pair ceiling, tightened by adverse selection. DEFAULT OFF.

`max_pair_cost` is a FEE-SAFETY number -- 0.96 against a BREAKEVEN_PAIR of
0.9532 -- and says nothing about which leg fills. A resting two-sided quote
fills on the side the market runs THROUGH, so the leg that fills alone is
the adversely selected one. The 2026-11-09 15:30Z window is the worked
example: pair 0.95, inside the cap, DOWN filled alone, the bid ran
0.53 -> 0.98, UP settled, the DOWN leg paid nothing, -$0.3080 settled.

WHAT THESE TESTS DO AND DO NOT ASSERT. They pin the plumbing: the gate is
off unless configured, it can only tighten, it fails safe, and it reports
which bound set the ceiling. They do NOT assert that the gate is
profitable. The out-of-sample replay says it is not -- at any skew that
binds, the opportunity set collapses to no-trade -- and that result is
recorded in the commit message rather than asserted here as a property.
"""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

ENV = TS.ADVERSE_SKEW_ENV


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv(TS.MAX_PAIR_COST_ENV, raising=False)
    yield


# =====================================================================
# OFF UNLESS CONFIGURED
# =====================================================================

def test_unset_leaves_the_deployed_ceiling_untouched():
    out = TS.effective_pair_ceiling()
    assert out["ceiling"] == TS.max_pair_cost() == D("0.96")
    assert out["bound_by"] == "max_pair_cost"
    assert out["skew"] is None and out["economic"] is None


@pytest.mark.parametrize("raw", ["", "   ", "typo", "-0.01", "0.51", "1",
                                 "nan", "1e400"])
def test_an_UNREADABLE_OR_OUT_OF_RANGE_skew_does_not_engage_the_gate(
        monkeypatch, raw):
    """A typo must not silently start refusing entries on a number nobody
    chose. It leaves the regression-tested behaviour in force."""
    monkeypatch.setenv(ENV, raw)
    assert TS.adverse_skew() is None
    assert TS.effective_pair_ceiling()["ceiling"] == D("0.96")


@pytest.mark.parametrize("raw,want", [
    ("0", D("0")), ("0.05", D("0.05")), ("0.10", D("0.10")),
    ("0.15", D("0.15")), ("0.25", D("0.25")), ("0.50", D("0.50"))])
def test_the_configured_values_the_owner_asked_to_be_tested_all_parse(
        monkeypatch, raw, want):
    monkeypatch.setenv(ENV, raw)
    assert TS.adverse_skew() == want


# =====================================================================
# IT CAN ONLY TIGHTEN
# =====================================================================

@pytest.mark.parametrize("skew,ceiling", [
    ("0", "0.96"),        # economics return 1.0000, min keeps 0.96
    ("0.05", "0.96"),     # 0.9713, still looser than 0.96
    ("0.10", "0.9426"),
    ("0.15", "0.9138"),
    ("0.25", "0.8564"),
    ("0.50", "0.7128")])
def test_the_ceiling_at_each_tested_skew(monkeypatch, skew, ceiling):
    monkeypatch.setenv(ENV, skew)
    got = TS.effective_pair_ceiling()["ceiling"]
    assert got.quantize(D("0.0001")) == D(ceiling), (skew, got)


@pytest.mark.parametrize("skew", ["0", "0.05", "0.10", "0.15", "0.25", "0.50"])
def test_the_effective_ceiling_NEVER_exceeds_the_deployed_one(monkeypatch,
                                                              skew):
    monkeypatch.setenv(ENV, skew)
    assert TS.effective_pair_ceiling()["ceiling"] <= TS.max_pair_cost()


def test_it_tightens_monotonically_in_the_skew(monkeypatch):
    prev = None
    for skew in ("0", "0.05", "0.10", "0.15", "0.25", "0.50"):
        monkeypatch.setenv(ENV, skew)
        c = TS.effective_pair_ceiling()["ceiling"]
        if prev is not None:
            assert c <= prev, skew
        prev = c


def test_an_operator_tightened_base_still_wins_when_it_is_tighter(monkeypatch):
    """The two bounds compose: whichever is tighter governs, and the
    report says which."""
    monkeypatch.setenv(TS.MAX_PAIR_COST_ENV, "0.80")
    monkeypatch.setenv(ENV, "0.15")          # economics give 0.9138
    out = TS.effective_pair_ceiling()
    assert out["ceiling"] == D("0.80")
    assert out["bound_by"] == "max_pair_cost"


# =====================================================================
# FAIL SAFE, NOT FAIL OPEN
# =====================================================================

def test_if_the_economics_cannot_be_evaluated_the_existing_ceiling_stands(
        monkeypatch):
    monkeypatch.setenv(ENV, "0.15")

    def boom(**kw):
        raise RuntimeError("calibration unavailable")

    import src.l5_second_leg_economics as SLE
    monkeypatch.setattr(SLE, "admissible_pair_ceiling", boom)
    out = TS.effective_pair_ceiling()
    assert out["ceiling"] == D("0.96")
    assert out["bound_by"] == "max_pair_cost"
    assert "RuntimeError" in str(out["error"])


def test_the_measured_joint_distribution_is_the_PROVEN_conservative_arm():
    """Not a guess: the queue-independent arm of the recorded-book replay
    at depth 2, out of sample. Pinned so a later edit cannot quietly
    substitute an optimistic one."""
    assert TS.PROVEN_P_BOTH == D("0.525")
    assert TS.PROVEN_P_SINGLE == D("0.423")
    assert TS.PROVEN_P_BOTH + TS.PROVEN_P_SINGLE < D("1")


# =====================================================================
# THE 15:30Z ENTRY, THROUGH THE GATE
# =====================================================================

def test_the_1530z_pair_is_admitted_by_the_deployed_ceiling():
    """Which is the defect: 0.95 passes a fee-safety test it should not
    have been judged by alone."""
    assert D("0.95") <= TS.effective_pair_ceiling()["ceiling"]


@pytest.mark.parametrize("skew", ["0.10", "0.15", "0.25", "0.50"])
def test_the_1530z_pair_is_REFUSED_once_the_gate_binds(monkeypatch, skew):
    monkeypatch.setenv(ENV, skew)
    assert D("0.95") > TS.effective_pair_ceiling()["ceiling"]


@pytest.mark.parametrize("skew", ["0", "0.05"])
def test_below_the_break_even_skew_the_gate_does_not_save_us(monkeypatch,
                                                             skew):
    """Honest negative: at a skew under the measured break-even of 0.0871
    the gate admits the entry that lost money. It is not a cure."""
    monkeypatch.setenv(ENV, skew)
    assert D("0.95") <= TS.effective_pair_ceiling()["ceiling"]
