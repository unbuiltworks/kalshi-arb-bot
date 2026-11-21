"""Under the POSITION basis a leg is admitted on its own measured worth,
never on the chance of becoming half of a pair. Off by default."""
from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from tests.test_l5_btc5m_one_sided_control import (     # noqa: F401
    _controller_on, decide, inv, legs)

POS = dict(straddle_ev_basis="POSITION")
#: the gate must actually decide: no behaviour-probe waiver
GATED = dict(straddle_ev_waiver="")
HELD = inv("0.20", "0.12", "0.10", "0.038")


def poe(**kw):
    base = dict(side=C.UP, price=D("0.60"), qty=D("0.10"),
                inv={C.UP: (D("0.20"), D("0.12")),
                     C.DOWN: (D("0.10"), D("0.038"))},
                fill_edge=None, fee_per_share=D("0"))
    base.update(kw)
    return C.position_order_economics(**base)


# ------------------------------------------------------- the arithmetic

def test_settled_pnl_both_ways_before_and_after():
    r = poe()
    #: cost 0.158; UP wins pays 0.20, DOWN wins pays 0.10
    assert D(r["pnl_if_up_before"]) == D("0.042")
    assert D(r["pnl_if_down_before"]) == D("-0.058")
    #: 0.10 more UP at 0.60: +0.04 if UP, -0.06 if DOWN
    assert D(r["pnl_if_up_after"]) == D("0.082")
    assert D(r["pnl_if_down_after"]) == D("-0.118")
    assert D(r["worst_after"]) == D("-0.118")


def test_a_completing_leg_is_reported_as_completing_but_valued_the_same():
    up = poe(fill_edge=D("-0.02"))
    dn = poe(side=C.DOWN, price=D("0.39"), fill_edge=D("-0.02"))
    assert D(up["completes_shares"]) == 0
    assert D(dn["completes_shares"]) == D("0.10")
    assert up["ev_per_share"] == dn["ev_per_share"] == "-0.02"
    assert dn["ok"] is False and dn["reason"] == "EV_NOT_POSITIVE"


def test_unmeasured_is_refused_not_read_as_zero():
    r = poe(fill_edge=None)
    assert r["ok"] is False and r["reason"] == "FILL_EDGE_NOT_SET"
    assert r["expected_change_usd"] is None


@pytest.mark.parametrize("edge,fee,ok", [
    ("0", "0", False), ("0.01", "0", True), ("0.01", "0.01", False),
    ("0.02", "0.01", True), ("-0.03", "0", False)])
def test_admitted_only_when_worth_more_than_it_costs(edge, fee, ok):
    r = poe(fill_edge=D(edge), fee_per_share=D(fee))
    assert r["ok"] is ok
    assert D(r["expected_change_usd"]) == (D(edge) - D(fee)) * D("0.10")


# ------------------------------------------------------- in the controller

def test_off_by_default_the_decision_is_unchanged():
    for i in (inv(), HELD):
        a = decide(i, "0.60", "0.61", t=20.0)
        b = decide(i, "0.60", "0.61", t=20.0, straddle_ev_basis="")
        assert a["orders"] == b["orders"]
        assert set(a["evaluation"]["position_economics"].values()) == {None}


def test_a_pair_probability_no_longer_admits_a_leg():
    #: under the pair basis a high enough natural-pair probability admits
    #: a leg whose own fill has never been measured
    rates = dict(straddle_p_pair_natural=D("0.9"),
                 adverse_selection_per_share=D("0.01"))
    pair = decide(inv(), "0.50", "0.51", t=20.0, **rates, **GATED)
    assert len(legs(pair)) >= 1, pair["why"]
    pos = decide(inv(), "0.50", "0.51", t=20.0, **rates, **GATED, **POS)
    assert legs(pos) == []
    assert pos["why"] == "ENTRY_EV_FILL_EDGE_NOT_SET"


def test_a_measured_negative_edge_refuses_every_leg():
    r = decide(HELD, "0.40", "0.41", t=20.0,
               straddle_fill_edge_per_share=D("-0.012"), **GATED, **POS)
    assert legs(r) == []
    assert r["why"] == "ENTRY_EV_EV_NOT_POSITIVE"


def test_a_measured_positive_edge_admits_and_reports_the_position():
    r = decide(inv(), "0.60", "0.61", t=20.0,
               straddle_fill_edge_per_share=D("0.01"), **GATED, **POS)
    assert sorted(o["side"] for o in legs(r)) == ["DOWN", "UP"]
    pe = r["evaluation"]["position_economics"]
    assert pe["UP"]["ok"] and pe["DOWN"]["ok"]
    assert D(pe["UP"]["pnl_if_up_before"]) == 0


def test_the_existing_risk_ceilings_still_refuse():
    #: a positive edge is not permission past the side cap
    full = inv("0.50", "0.30", "0", "0")
    r = decide(full, "0.60", "0.61", t=20.0,
               straddle_fill_edge_per_share=D("0.05"), **GATED, **POS)
    assert legs(r, C.UP) == []


def test_the_behaviour_probe_waiver_is_unchanged_by_the_basis():
    #: the waiver admits only "not set" refusals, as it always did
    a = decide(inv(), "0.60", "0.61", t=20.0, **POS)
    b = decide(inv(), "0.60", "0.61", t=20.0)
    assert [(o["side"], o["price"]) for o in legs(a)] == \
        [(o["side"], o["price"]) for o in legs(b)]
    c = decide(inv(), "0.60", "0.61", t=20.0,
               straddle_fill_edge_per_share=D("-0.012"), **POS)
    assert legs(c) == []          # a measured answer is never waived


# ------------------------------------------------------------- env readers

@pytest.mark.parametrize("raw,expect", [
    (None, ""), ("", ""), ("position", ""), ("1", ""), ("POSITION", "POSITION")])
def test_only_the_exact_token_selects_the_basis(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_EV_BASIS_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_EV_BASIS_ENV, raw)
    assert X.straddle_ev_basis() == expect


@pytest.mark.parametrize("raw,expect", [
    (None, None), ("", None), ("abc", None), ("0.5", None), ("-0.5", None),
    ("-0.012", D("-0.012")), ("0", D("0")), ("0.01", D("0.01"))])
def test_the_fill_edge_is_none_unless_readable(monkeypatch, raw, expect):
    if raw is None:
        monkeypatch.delenv(X.STRADDLE_FILL_EDGE_ENV, raising=False)
    else:
        monkeypatch.setenv(X.STRADDLE_FILL_EDGE_ENV, raw)
    assert X.straddle_fill_edge_per_share() == expect
