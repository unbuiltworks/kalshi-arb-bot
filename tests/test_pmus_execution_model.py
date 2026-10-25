"""The three PMUS execution facts the first engine got wrong, pinned.

Each test here corresponds to a specific error that was shipped and corrected:
no per-order fee, the maker rebate is a credit rather than a negative fee, and
P(fill) is measured and REFUSES past the horizon our own orders have reached.
"""
from decimal import Decimal as D

import pytest

from src import pmus_fees
from src.gemini_execution import pmus_execution_model as PX
from src.gemini_execution.inventory_engine import (
    Book, Inventory, Limits, Prob, evaluate,
)

BOOK = Book(yes_bid=D("0.50"), yes_bid_qty=D(100),
            no_bid=D("0.49"), no_bid_qty=D(100))


def _p(p, sigma="0.01"):
    return Prob(p_up=D(p), sigma=D(sigma), source="TEST")


def _ev(cfg, inv=None, tte=600.0, resting=()):
    return evaluate(market="PMUS-T", t=0.0, tte=tte, inv=inv or Inventory(),
                    book=BOOK, prob=_p("0.70"), resting=resting,
                    limits=Limits(), cfg=cfg)


# ------------------------------------------------------------------- fees
def test_there_is_no_per_order_fee_on_pmus():
    """replay_b charges $0.01 an order. The published PMUS schedule does not,
    and carrying it taxed every decision."""
    model = PX.CalibratedFillModel.load()
    # a 1-contract taker fee at a 1c price is tiny; a per-order charge would
    # swamp it and show up as a floor near 0.01.
    tiny = model.taker_fee(D(1), D("0.01"))
    assert tiny < D("0.01")
    assert tiny == pmus_fees.taker_fee(D("0.01"), D(1))
    # and the fee is exactly the schedule's, with nothing added
    for px in ("0.10", "0.50", "0.90"):
        assert model.taker_fee(D(10), D(px)) == pmus_fees.taker_fee(D(px), D(10))


def test_the_maker_rebate_is_a_credit_and_is_never_netted_into_cost():
    model = PX.CalibratedFillModel.load()
    # selection sees ZERO maker fee -- not a negative one
    assert model.maker_fee(D(100), D("0.50")) == D(0)
    # the accrual exists, is POSITIVE, and is reported separately
    accrual = model.maker_rebate_accrual(D(100), D("0.50"))
    assert accrual > 0
    assert accrual == pmus_fees.maker_rebate(D("0.50"), D(100))


def test_fees_are_zero_outside_the_tradeable_price_domain():
    model = PX.CalibratedFillModel.load()
    for bad in (D(0), D(1), D("-0.1"), D("1.5")):
        assert model.taker_fee(D(10), bad) == 0
        assert model.maker_rebate_accrual(D(10), bad) == 0
    assert model.taker_fee(D(0), D("0.50")) == 0


# -------------------------------------------------------------- P(fill)
def test_p_fill_is_the_measured_curve_not_an_invented_one():
    m = PX.CalibratedFillModel.load()
    assert m.n_orders == 965
    p30 = m.p_fill(leg=PX.FIRST_LEG, rest_s=30.0)
    # measured at ~0.43, an order of magnitude above the modelled ~0.05 the
    # engine first used, and the correction runs the other way from intuition.
    assert 0.40 < p30 < 0.45
    assert m.p_fill(leg=PX.COMPLEMENT, rest_s=30.0) == pytest.approx(0.4269, abs=1e-3)
    # monotone non-decreasing in rest time, and zero at zero
    assert m.p_fill(leg=PX.FIRST_LEG, rest_s=0) == 0.0
    seq = [m.p_fill(leg=PX.FIRST_LEG, rest_s=t) for t in (5, 10, 20, 30)]
    assert seq == sorted(seq)


def test_p_fill_refuses_past_the_horizon_our_orders_have_reached():
    m = PX.CalibratedFillModel.load()
    assert m.horizon_s == pytest.approx(32.192, abs=1e-3)
    assert m.p_fill(leg=PX.FIRST_LEG, rest_s=32.0) is not None
    # None, NOT zero and NOT the last observed value -- there is no data here
    for t in (33.0, 47.0, 60.0, 120.0):
        assert m.p_fill(leg=PX.FIRST_LEG, rest_s=t) is None


def test_l5s_own_horizon_is_outside_our_evidence():
    """L5's median complement gap is 47 s; our longest rest ever is 32.2 s.
    The engine cannot price his horizon, and only a live run that rests longer
    can change that. This test exists so that fact cannot be quietly lost."""
    m = PX.CalibratedFillModel.load()
    assert 47.0 > m.horizon_s
    assert m.p_fill(leg=PX.COMPLEMENT, rest_s=47.0) is None


def test_the_leg_relation_matches_what_the_model_was_calibrated_on():
    m = PX.CalibratedFillModel.load()
    # flat -> opening a first leg
    assert m.leg_for(resid_before=D(0), side="UP") == PX.FIRST_LEG
    # long UP: buying DOWN reduces, so it is a complement; buying UP extends
    assert m.leg_for(resid_before=D(5), side="DOWN") == PX.COMPLEMENT
    assert m.leg_for(resid_before=D(5), side="UP") == PX.FIRST_LEG
    assert m.leg_for(resid_before=D(-5), side="UP") == PX.COMPLEMENT
    assert m.leg_for(resid_before=D(-5), side="DOWN") == PX.FIRST_LEG


# ------------------------------------------------------- engine integration
def test_the_engine_refuses_a_maker_order_it_cannot_price():
    cfg = PX.pmus_config(maker_rest_s=60.0, allow_taker=False)
    d = _ev(cfg)
    assert (d.action, d.reason) == ("WAIT", "FILL_UNESTIMABLE")
    assert all(c.reason == "FILL_UNESTIMABLE" for c in d.candidates)


def test_the_pmus_profile_caps_rest_and_ttl_to_the_calibrated_horizon():
    cfg = PX.pmus_config()
    m = PX.CalibratedFillModel.load()
    assert cfg.maker_rest_s <= m.horizon_s
    assert cfg.order_ttl_s <= m.horizon_s
    assert cfg.venue is not None


def test_the_measured_fill_probability_reaches_the_engines_candidates():
    cfg = PX.pmus_config(allow_taker=False)
    d = _ev(cfg)
    maker = [c for c in d.candidates if c.kind == "MAKER"]
    assert maker
    for c in maker:
        assert D("0.40") < c.fill_prob < D("0.45")
        assert c.fee == 0                      # maker pays nothing on PMUS
    # with the real fill probability a confident mispricing is now actionable,
    # where the modelled ~0.05 suppressed it
    assert d.action == "BUY_UP"


def test_pmus_config_still_waits_when_there_is_no_probability():
    cfg = PX.pmus_config()
    d = evaluate(market="PMUS-T", t=0.0, tte=600.0, inv=Inventory(), book=BOOK,
                 prob=Prob(p_up=D("0.70"), sigma=D("0.50"), source="NO_SKILL"),
                 limits=Limits(), cfg=cfg)
    assert (d.action, d.reason) == ("WAIT", "PROB_UNCERTAIN")
