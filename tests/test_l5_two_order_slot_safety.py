"""Two resting legs must not loosen any bound. L5_MAX_LIVE_ORDERS=2.

WHAT THE CHANGE IS FOR. On window 3 of camp1.cf28b0c9 the DOWN complement
was requoted NINE times in 47 seconds while 1 UP was held. Every lap was
cancel -> verify (1-3 attempts, some with HTTPError) -> replace, because
the replacement "cannot be sent while this order is live" under a single
live-order slot. The complement therefore rested during none of the
re-pricing and surrendered queue position on every lap, while the measured
queue ahead of our resting price ran 1,300-2,950 contracts against 5-1,500
at the target.

WHY A SLOT IS NOT HEADROOM. `l5_live_execution.refuse` already prices every
outstanding order as though it fills in full -- per-market worst case,
account-wide exposure, the cash envelope and the residual ceiling all do --
so two resting legs are charged as two filled legs with no change anywhere.
The slot buys an ordering, never an allowance.

THE BOUND THAT ACTUALLY PROTECTS US is `residual_cap_decision`, on a
FILL_CONTINGENT_WORST_CASE basis: every still-fillable order on the SAME
side fills and nothing on the other side does, because "a resting opposite
leg is an intention, not a hedge -- it can be cancelled, and on 1415z it
was." These tests pin that the cap permits exactly the two shapes the
change is for and refuses every shape that would carry more than one
contract of residual, at k=1 and a 1-contract clip.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_live_validation_gates as VG

K = D("1")
CLIP = D("1")


def cap(**kw):
    return LX.residual_cap_decision(k=K, clip=CLIP,
                                    same_side_includes_this_order=False, **kw)


# ============================ the two shapes the change exists for

def test_an_opposite_leg_may_REST_BESIDE_the_first_leg():
    """Flat, UP 1 resting, now a DOWN 1 beside it. This is the pair resting
    simultaneously, which is the whole objective."""
    r = cap(resid=D(0), same_side=D(0), other_side=D(1),
            qty=D(1), side="DOWN")
    assert r["binds"] is False, r


def test_ONE_REPRICE_LAP_on_the_complement_is_permitted():
    """Holding UP 1 with a DOWN already resting, a second DOWN may be placed
    before the first is cancelled. Worst case is still 1 contract: if both
    DOWN fill against the held UP the residual is 1 DOWN, not 2."""
    r = cap(resid=D(1), same_side=D(1), other_side=D(0),
            qty=D(1), side="DOWN")
    assert r["binds"] is False, r
    assert r["cap_contracts"] == "1"


# ================= everything that would exceed one contract is refused

@pytest.mark.parametrize("name,kw", [
    ("two same-side legs resting while flat",
     dict(resid=D(0), same_side=D(1), other_side=D(0), qty=D(1), side="UP")),
    ("a third overlapping complement",
     dict(resid=D(1), same_side=D(2), other_side=D(0), qty=D(1), side="DOWN")),
    ("a same-side EXTEND on top of held inventory",
     dict(resid=D(1), same_side=D(0), other_side=D(0), qty=D(1), side="UP")),
])
def test_the_residual_cap_still_refuses(name, kw):
    r = cap(**kw)
    assert r["binds"] is True, "%s must refuse: %s" % (name, r)


def test_the_cap_is_ONE_CONTRACT_and_the_basis_is_fill_contingent():
    r = cap(resid=D(0), same_side=D(0), other_side=D(0), qty=D(1), side="UP")
    assert r["cap_contracts"] == "1"
    assert r["basis"] == "FILL_CONTINGENT_WORST_CASE"


def test_a_genuine_OPENING_leg_is_never_capped_by_its_own_size():
    """Nothing held and nothing resting on either side: the first order of a
    window cannot be refused by a bound derived from itself."""
    r = cap(resid=D(0), same_side=D(0), other_side=D(0), qty=D(1), side="UP")
    assert r["binds"] is False


# ========================= both outstanding orders are reserved in cash

def test_BOTH_resting_orders_are_charged_to_the_cash_envelope():
    """A resting BUY_SHORT is charged at FULL DOLLARS, not qty * price,
    because a short owes up to $1 a share. Two resting legs are charged as
    two filled legs, which is what makes the extra slot safe."""
    g = VG.ValidationGates(max_cash_deployed_usd=D("12"),
                           cash_policy=VG.NO_RECYCLING)
    outstanding = (
        {"intent": "ORDER_INTENT_BUY_LONG", "qty": "1", "price": "0.34"},
        {"intent": "ORDER_INTENT_BUY_SHORT", "qty": "1", "price": "0.45"},
    )
    # 1 * 0.34 long + 1.00 full-dollar short
    assert g.pending_obligation(outstanding) == D("1.34")


def test_the_cash_ceiling_binds_on_the_pair_of_resting_orders():
    """The envelope is unchanged by the extra slot: it refuses on the sum."""
    g = VG.ValidationGates(max_cash_deployed_usd=D("1.00"),
                           cash_policy=VG.NO_RECYCLING)
    outstanding = (
        {"intent": "ORDER_INTENT_BUY_SHORT", "qty": "1", "price": "0.45"},
    )
    why = g.refuse(order_price=D("0.34"), order_qty=D("1"),
                   order_intent="ORDER_INTENT_BUY_LONG",
                   outstanding=outstanding,
                   cumulative_realized=D(0))
    assert why is not None and "CASH" in why.upper(), why
