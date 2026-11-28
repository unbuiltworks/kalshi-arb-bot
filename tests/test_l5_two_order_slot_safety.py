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


# =========== OWNER 2026-11-04: the UP-held + two overlapping DOWN case
#
# The exact scenario to prove: an existing UP position and TWO overlapping
# DOWN orders ALL fill. What inventory results, and is it permitted by every
# existing control?

LIVE_LIMITS = dict(max_usd_per_market=D("20"), max_abs_residual=D("10"),
                   max_total_exposure_usd=D("20"), max_concurrent_markets=1,
                   daily_realized_loss_halt_usd=D("5"))
MKT = "cpc-btc-updown-15m-2026-11-04-0400z"


def _limits(slots):
    return LX.SafetyLimits(max_live_orders=slots, **LIVE_LIMITS)


def test_UP1_PLUS_TWO_OVERLAPPING_DOWN_all_filling_is_one_contract_short():
    """ARITHMETIC OF THE WORST CASE. Hold UP 1; rest DOWN 1; overlap a second
    DOWN 1; every one fills. Inventory becomes UP 1 / DOWN 2, which is ONE
    matched pair plus ONE DOWN residual -- not two contracts of residual.
    That is why the cap permits the overlap lap."""
    r = cap(resid=D(1), same_side=D(1), other_side=D(0),
            qty=D(1), side="DOWN")
    assert r["binds"] is False
    # worst case the cap measured, as an absolute residual
    assert D(r["cap_contracts"]) == D(1)
    # and the realised inventory: 1 UP vs 2 DOWN -> 1 short, within the cap
    up_held, dn_filled = D(1), D(2)
    assert abs(up_held - dn_filled) == D(1)


def test_a_THIRD_overlapping_DOWN_would_breach_and_is_refused():
    """Two laps is the limit: a third would land UP 1 / DOWN 3 = 2 contracts
    of residual, and the cap refuses it before the wire."""
    r = cap(resid=D(1), same_side=D(2), other_side=D(0),
            qty=D(1), side="DOWN")
    assert r["binds"] is True


def test_the_live_order_gate_admits_the_overlap_at_TWO_slots_only():
    """The slot is what changes, and nothing else. At one slot the overlap is
    MAX_LIVE_ORDERS; at two it is admitted -- and every other bound is
    evaluated identically in both cases."""
    common = dict(price=D("0.45"), qty=D(1), market=MKT,
                  committed_by_market={MKT: D("0.34")},
                  residual_after=D(1), realized_today=D(0),
                  open_markets=[MKT],
                  outstanding_by_market={MKT: D("1.00")},
                  outstanding_residual=D(-1))
    assert _limits(1).refuse(live_order_count=1, **common) == "MAX_LIVE_ORDERS"
    assert _limits(2).refuse(live_order_count=1, **common) is None
    # a THIRD live order is still refused at two slots
    assert _limits(2).refuse(live_order_count=2, **common) == "MAX_LIVE_ORDERS"


def test_two_slots_do_not_loosen_exposure_or_per_market_bounds():
    """The dollar bounds are blind to the slot count: an outstanding order is
    priced as a filled one either way."""
    over = dict(price=D("0.45"), qty=D(1), market=MKT,
                committed_by_market={MKT: D("19.90")},
                residual_after=D(0), realized_today=D(0),
                open_markets=[MKT], outstanding_by_market={MKT: D("0")},
                outstanding_residual=D(0), live_order_count=0)
    for slots in (1, 2):
        why = _limits(slots).refuse(**over)
        assert why in ("MAX_USD_PER_MARKET", "MAX_TOTAL_EXPOSURE"), (slots, why)


def test_two_slots_do_not_loosen_the_daily_halt():
    common = dict(price=D("0.45"), qty=D(1), market=MKT,
                  committed_by_market={}, residual_after=D(0),
                  realized_today=D("-5"), open_markets=[],
                  outstanding_by_market={}, outstanding_residual=D(0),
                  live_order_count=0)
    for slots in (1, 2):
        assert _limits(slots).refuse(**common) == "DAILY_REALIZED_LOSS_HALT"


def test_uncertain_order_state_still_refuses_at_two_slots():
    """STALE / UNRESOLVED ACKNOWLEDGEMENT. An unestablished book refuses
    before any arithmetic, and the extra slot does not change that."""
    for slots in (1, 2):
        assert _limits(slots).refuse(
            price=D("0.45"), qty=D(1), market=MKT, committed_by_market={},
            residual_after=D(0), realized_today=D(0), open_markets=[],
            outstanding_by_market={}, outstanding_residual=D(0),
            live_order_count=0,
            orders_uncertain="UNMANAGED_RESTING_ORDER") == "ORDER_STATE_UNCERTAIN"


def test_a_PARTIAL_fill_is_priced_on_what_can_still_fill():
    """A partially filled resting order still carries its UNFILLED remainder
    as potential exposure. Holding UP 1 with a half-filled DOWN resting, the
    remaining half plus a fresh lap must stay inside one contract."""
    # DOWN 0.5 filled, 0.5 still fillable, plus a 0.5 overlap lap
    r = cap(resid=D("0.5"), same_side=D("1.0"), other_side=D(0),
            qty=D("0.5"), side="DOWN")
    assert r["binds"] is False
    # but a lap that would carry the short past one contract is refused
    r2 = cap(resid=D("0.5"), same_side=D("2.0"), other_side=D(0),
             qty=D("0.5"), side="DOWN")
    assert r2["binds"] is True


def test_the_cash_envelope_binds_on_BOTH_overlapping_DOWN_orders():
    """Two resting shorts reserve $2.00, not 2 * price. With $0.34 already
    committed that is $2.34 of the $12 ceiling -- and a ceiling below it
    refuses."""
    g = VG.ValidationGates(max_cash_deployed_usd=D("12"),
                           cash_policy=VG.NO_RECYCLING)
    two_shorts = (
        {"intent": "ORDER_INTENT_BUY_SHORT", "qty": "1", "price": "0.45"},
        {"intent": "ORDER_INTENT_BUY_SHORT", "qty": "1", "price": "0.50"},
    )
    assert g.pending_obligation(two_shorts) == D("2.00")
    tight = VG.ValidationGates(max_cash_deployed_usd=D("1.50"),
                               cash_policy=VG.NO_RECYCLING)
    why = tight.refuse(order_price=D("0.50"), order_qty=D("1"),
                       order_intent="ORDER_INTENT_BUY_SHORT",
                       outstanding=two_shorts[:1],
                       cumulative_realized=D(0))
    assert why is not None and "CASH" in why.upper(), why


# ===== the verifier's approved value must track the approval exactly

def test_the_approved_max_live_orders_is_FOUR():
    """OWNER 2026-11-04 approved two; OWNER 2026-11-10 approved four ("the
    four-order configuration", deployed disarmed, no live trading
    authorized by it). The approved-values table IS the approval, so a
    verifier that disagrees with it reports the authorized change as a
    regression and makes FAILED CHECKS permanently non-empty."""
    from src import l5_runtime_config_verify as C
    assert C.APPROVED["max_live_orders"] == 4


@pytest.mark.parametrize("value,ok", [("1", False), ("2", False), ("4", True),
                                      ("40", False)])
def test_the_row_is_an_EXACT_match_so_an_unclamped_typo_still_fails(
        value, ok, monkeypatch):
    """`R.max_live_orders()` is max(1, n) with NO upper clamp, so a typo
    setting 20 is accepted by the accessor in silence. Grading this row
    against exactly 2 is what makes that detectable -- the row is tighter
    after the change than before it, not looser."""
    from src import l5_runtime_config_verify as C
    monkeypatch.setenv("L5_MAX_LIVE_ORDERS", value)
    row = [r for r in C.verify()["rows"] if r["name"] == "max_live_orders"][0]
    assert row["ok"] is ok, row
