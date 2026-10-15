"""The worst-case settlement deficit gate.

`deficit = C - min(up, dn)`: the most the book can lose on EITHER outcome.
Below zero it pays out whichever way the window resolves. Default disabled,
fail-closed when configured, and no order is exempt -- a completing order is
priced exactly like an opening one.
"""
from decimal import Decimal as D

import pytest

from src.l5_live_validation_gates import ValidationGates

LONG = "ORDER_INTENT_BUY_LONG"
SHORT = "ORDER_INTENT_BUY_SHORT"


def G(**kw):
    kw.setdefault("max_cash_deployed_usd", D("1000"))
    kw.setdefault("max_unmatched_contracts", D("10000"))
    kw.setdefault("max_cumulative_loss_usd", D("10000"))
    return ValidationGates(**kw)


def flat():
    return {"up_qty": D(0), "dn_qty": D(0),
            "up_cost": D(0), "dn_cost": D(0)}


# ---------------------------------------------------------------- default off

def test_the_gate_is_disabled_by_default_and_changes_nothing():
    g = G()
    assert g.max_worst_case_deficit_usd is None
    assert g.as_dict()["max_worst_case_deficit_usd"] is None
    # a wildly deficient order passes, because the bound is not configured
    assert g.refuse(order_qty=D(50), order_price=D("0.90"),
                    order_intent=LONG, inventory=flat()) is None


def test_configured_and_given_no_inventory_FAILS_CLOSED():
    g = G(max_worst_case_deficit_usd=D("2"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=LONG)
    assert r == "WORST_CASE_DEFICIT_NO_INVENTORY"


# ------------------------------------------------------------ the arithmetic

def test_the_owners_asymmetric_book_is_profitable_on_both_outcomes():
    """20 YES @0.70 + 25 NO @0.20 -> cost 19, min 20: +1 on YES, +6 on NO."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    wc = g.worst_case_deficit(up_qty=D(20), dn_qty=D(0),
                              up_cost=D(14), dn_cost=D(0),
                              order_side="DOWN",
                              order_qty=D(25), order_price=D("0.20"))
    assert wc["ok"] is True
    assert wc["deficit"] == D(-1)           # 19 - min(20,25) = -1
    assert g.refuse(order_qty=D(25), order_price=D("0.20"),
                    order_intent=SHORT,
                    inventory={"up_qty": D(20), "dn_qty": D(0),
                               "up_cost": D(14), "dn_cost": D(0)}) is None


def test_excess_on_the_CHEAP_side_beyond_the_minimum_is_refused():
    """20 YES + 30 NO@0.20 = cost 20, min 20 -> deficit 0, no longer > 0 both."""
    g = G(max_worst_case_deficit_usd=D("-0.0001"), fee_theta_worst=D(0))
    r = g.refuse(order_qty=D(30), order_price=D("0.20"), order_intent=SHORT,
                 inventory={"up_qty": D(20), "dn_qty": D(0),
                            "up_cost": D(14), "dn_cost": D(0)})
    assert r is not None and r.startswith("WORST_CASE_DEFICIT:")


def test_excess_on_the_EXPENSIVE_side_is_refused():
    """25 YES@0.70 + 20 NO@0.20 = 21.5, min 20 -> deficit +1.5."""
    g = G(max_worst_case_deficit_usd=D("1"), fee_theta_worst=D(0))
    wc = g.worst_case_deficit(up_qty=D(20), dn_qty=D(20),
                              up_cost=D(14), dn_cost=D(4),
                              order_side="UP",
                              order_qty=D(5), order_price=D("0.70"))
    assert wc["deficit"] == D("1.5000")
    assert g.refuse(order_qty=D(5), order_price=D("0.70"), order_intent=LONG,
                    inventory={"up_qty": D(20), "dn_qty": D(20),
                               "up_cost": D(14), "dn_cost": D(4)}
                    ).startswith("WORST_CASE_DEFICIT:1.5000>1")


def test_the_opening_leg_is_bounded_by_the_deficit_itself():
    """With an empty book min(a,b)=0, so the bound IS the cash at risk."""
    g = G(max_worst_case_deficit_usd=D("2"), fee_theta_worst=D(0))
    # 2 contracts at 0.90 = 1.80 at risk: allowed
    assert g.refuse(order_qty=D(2), order_price=D("0.90"),
                    order_intent=LONG, inventory=flat()) is None
    # 3 at 0.90 = 2.70: refused
    assert g.refuse(order_qty=D(3), order_price=D("0.90"),
                    order_intent=LONG, inventory=flat()) is not None


def test_a_completing_order_is_NOT_exempt_it_is_simply_cheaper():
    """The expensive completion is refused even though it reduces residual."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    inv = {"up_qty": D(10), "dn_qty": D(0),
           "up_cost": D("5.00"), "dn_cost": D(0)}     # 10 UP at 0.50
    # completing at 0.49 -> cost 9.90, min 10 -> -0.10 : allowed
    assert g.refuse(order_qty=D(10), order_price=D("0.49"),
                    order_intent=SHORT, inventory=inv) is None
    # completing at 0.55 -> cost 10.50, min 10 -> +0.50 : REFUSED
    r = g.refuse(order_qty=D(10), order_price=D("0.55"),
                 order_intent=SHORT, inventory=inv)
    assert r is not None and "WORST_CASE_DEFICIT:0.5000" in r


# ------------------------------------------------------------------ the fee

def test_the_taker_fee_is_charged_and_the_rebate_is_never_netted():
    g = G(max_worst_case_deficit_usd=D("99"))
    assert g.fee_theta_worst == D("0.0695")
    f = g.fee_worst(D(10), D("0.50"))
    assert f == D("0.0695") * D(10) * D("0.5") * D("0.5")
    assert f > 0                                  # a cost, never a credit


def test_fees_can_turn_a_sub_dollar_pair_deficient():
    """0.99 pair is +0.01/contract gross and negative once the fee lands."""
    g = G(max_worst_case_deficit_usd=D("0"))
    inv = {"up_qty": D(10), "dn_qty": D(0),
           "up_cost": D("4.50"), "dn_cost": D(0)}      # 10 UP at 0.45
    r = g.refuse(order_qty=D(10), order_price=D("0.54"),
                 order_intent=SHORT, inventory=inv)
    assert r is not None, "fee-inclusive cost exceeds min(a,b)"
    # and with fees switched off the same book passes
    assert G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0)).refuse(
        order_qty=D(10), order_price=D("0.54"),
        order_intent=SHORT, inventory=inv) is None


# ------------------------------------------------------ outstanding orders

def test_outstanding_orders_are_an_adversary_not_an_assumption():
    """The worst corner is taken, and it is named."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    inv = {"up_qty": D(10), "dn_qty": D(0),
           "up_cost": D("5.00"), "dn_cost": D(0)}
    out = [{"qty": D(5), "price": D("0.50"), "intent": LONG}]  # more UP resting
    wc = g.worst_case_deficit(up_qty=D(10), dn_qty=D(0),
                              up_cost=D("5.00"), dn_cost=D(0),
                              order_side="DOWN", order_qty=D(10),
                              order_price=D("0.49"), outstanding=out)
    # none fills: 9.90 - 10 = -0.10 ; the UP order fills: 12.40 - 10 = +2.40
    assert wc["scenarios"]["none"] == "-0.1000"
    assert wc["deficit"] == D("2.4000")
    assert wc["scenario"] in ("up_only", "all")
    assert g.refuse(order_qty=D(10), order_price=D("0.49"),
                    order_intent=SHORT, inventory=inv,
                    outstanding=out) is not None


def test_an_outstanding_order_that_lifts_the_minimum_is_not_held_against_us():
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    # UP 10 held; this order completes only 5, so DOWN is the minimum and a
    # further resting DOWN order genuinely lifts it.
    out = [{"qty": D(5), "price": D("0.20"), "intent": SHORT}]
    wc = g.worst_case_deficit(up_qty=D(10), dn_qty=D(0),
                              up_cost=D("5.00"), dn_cost=D(0),
                              order_side="DOWN", order_qty=D(5),
                              order_price=D("0.49"), outstanding=out)
    # none fills: 7.45 - min(10,5) = +2.45 ; the DOWN fills: 8.45 - 10 = -1.55
    assert wc["scenario"] == "none"
    assert wc["deficit"] == D("2.4500")
    assert D(wc["scenarios"]["dn_only"]) < 0


def test_an_outstanding_order_with_an_unreadable_price_is_charged_a_dollar():
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    out = [{"qty": D(1), "price": None, "intent": LONG}]
    wc = g.worst_case_deficit(up_qty=D(10), dn_qty=D(0),
                              up_cost=D("5.00"), dn_cost=D(0),
                              order_side="DOWN", order_qty=D(10),
                              order_price=D("0.49"), outstanding=out)
    assert wc["scenarios"]["up_only"] == "0.9000"   # 9.90 + 1.00 - 10


def test_an_outstanding_order_of_unknown_side_FAILS_CLOSED():
    g = G(max_worst_case_deficit_usd=D("0"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=SHORT,
                 inventory=flat(),
                 outstanding=[{"qty": D(1), "price": D("0.5"),
                               "intent": "ORDER_INTENT_WHO_KNOWS"}])
    assert r == "WORST_CASE_DEFICIT:DEFICIT_OUTSTANDING_SIDE_UNKNOWN"


# --------------------------------------------------- partial fills / restart

def test_a_partial_fill_is_just_a_smaller_confirmed_book():
    """Half the intended UP leg filled; the completion is sized to it."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    inv = {"up_qty": D(5), "dn_qty": D(0),
           "up_cost": D("2.50"), "dn_cost": D(0)}
    # completing the FILLED 5, not the intended 10
    assert g.refuse(order_qty=D(5), order_price=D("0.49"),
                    order_intent=SHORT, inventory=inv) is None
    # over-completing to 10 costs 2.50+4.90=7.40 against min(5,10)=5 -> +2.40
    assert g.refuse(order_qty=D(10), order_price=D("0.49"),
                    order_intent=SHORT, inventory=inv) is not None


def test_restart_recovery_reads_the_same_durable_numbers():
    """The gate is a pure function of the reconciled ledger, so a restart
    that reloads the same positions and leg_cost reaches the same verdict."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    inv = {"up_qty": D(10), "dn_qty": D(0),
           "up_cost": D("5.00"), "dn_cost": D(0)}
    a = g.refuse(order_qty=D(10), order_price=D("0.55"),
                 order_intent=SHORT, inventory=inv)
    b = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0)).refuse(
        order_qty=D(10), order_price=D("0.55"),
        order_intent=SHORT, inventory=dict(inv))
    assert a == b and a is not None


# ------------------------------------------------------------ fail closed

def test_quantity_without_cost_is_an_incoherent_book_not_free_inventory():
    g = G(max_worst_case_deficit_usd=D("5"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=SHORT,
                 inventory={"up_qty": D(10), "dn_qty": D(0),
                            "up_cost": D(0), "dn_cost": D(0)})
    assert r == "WORST_CASE_DEFICIT:DEFICIT_QUANTITY_WITHOUT_COST"


def test_unreadable_inventory_fails_closed():
    g = G(max_worst_case_deficit_usd=D("5"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=LONG,
                 inventory={"up_qty": "not-a-number", "dn_qty": D(0),
                            "up_cost": D(0), "dn_cost": D(0)})
    assert r is not None and r.startswith("WORST_CASE_DEFICIT:")


def test_a_negative_position_is_implausible_and_refused():
    g = G(max_worst_case_deficit_usd=D("5"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=LONG,
                 inventory={"up_qty": D(-1), "dn_qty": D(0),
                            "up_cost": D(1), "dn_cost": D(0)})
    assert r == "WORST_CASE_DEFICIT:DEFICIT_INVENTORY_IMPLAUSIBLE"


def test_an_order_price_outside_the_unit_interval_is_charged_a_dollar():
    g = G(max_worst_case_deficit_usd=D("0.5"), fee_theta_worst=D(0))
    r = g.refuse(order_qty=D(1), order_price=D("7"), order_intent=LONG,
                 inventory=flat())
    assert r is not None and "1" in r


# ------------------------------------------------- nothing else is weakened

def test_the_gate_can_only_refuse_never_permit():
    """A book the cash gate rejects stays rejected however good the deficit."""
    g = ValidationGates(max_cash_deployed_usd=D("1"),
                        max_unmatched_contracts=D("10000"),
                        max_cumulative_loss_usd=D("10000"),
                        max_worst_case_deficit_usd=D("100"),
                        fee_theta_worst=D(0))
    r = g.refuse(order_qty=D(10), order_price=D("0.50"), order_intent=LONG,
                 inventory=flat())
    assert r is not None and r.startswith("CASH_COMMITMENT")
