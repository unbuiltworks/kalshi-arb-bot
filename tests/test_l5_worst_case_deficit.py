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
    # exact is 0.173750; the venue bills whole cents, so the worst case is
    # the cent ABOVE, never the fraction below
    assert D("0.0695") * D(10) * D("0.5") * D("0.5") == D("0.173750")
    # 0.18 rounded up, plus the one-cent per-order rounding allowance
    assert f == D("0.19")
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
    # 2.40 at the up_only corner, plus the 0.02 non-convexity slack for the
    # one still-fillable order
    assert wc["scenarios"]["rounding_slack"] == "0.02"
    assert wc["deficit"] == D("2.4200")
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
    assert wc["deficit"] == D("2.4700")        # 2.45 + 0.02 slack
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


# ------------------------------------------- fee rounding and config parsing

def test_a_sub_cent_fee_is_charged_a_whole_cent():
    """A tiny clip's fee is a fraction of a cent. The venue bills cents, so
    reading it as very nearly free understates the worst case."""
    g = G(max_worst_case_deficit_usd=D("99"))
    raw = D("0.0695") * D("0.22") * D("0.5") * D("0.5")
    assert raw < D("0.01")                       # 0.00382...
    assert g.fee_worst(D("0.22"), D("0.50")) == D("0.02")


def test_fee_rounding_is_applied_per_leg_not_once_at_the_end():
    """Three 0.1-contract legs are three separate cent charges, not one
    rounding of their summed fraction."""
    g = G(max_worst_case_deficit_usd=D("99"))
    one = g.fee_worst(D("0.1"), D("0.5"))
    assert one == D("0.02")               # 0.01 ceiling + 0.01 allowance
    per_leg = one * 3
    raw_total = D("0.0695") * D("0.3") * D("0.5") * D("0.5")
    assert raw_total < D("0.01")          # 0.0052...: one rounding gives 0.01
    assert per_leg == D("0.06")           # three legs, three charges
    assert per_leg > g.fee_worst(D("0.3"), D("0.5"))


def test_a_zero_fee_configuration_rounds_to_nothing():
    g = G(max_worst_case_deficit_usd=D("99"), fee_theta_worst=D(0))
    assert g.fee_worst(D(10), D("0.5")) == D(0)


def test_multiple_partial_fills_at_different_price_levels():
    """Two orders already partly filled on each side, plus two still resting
    at different prices. The gate must take the worst corner, which is the
    expensive resting leg on the side that does not lift the minimum."""
    g = G(max_worst_case_deficit_usd=D("0"), fee_theta_worst=D(0))
    # confirmed: 3 UP for 1.20 (0.40 avg from two partials), 3 DOWN for 1.50
    inv = {"up_qty": D(3), "dn_qty": D(3),
           "up_cost": D("1.20"), "dn_cost": D("1.50")}
    out = [{"qty": D(2), "price": D("0.90"), "intent": LONG},
           {"qty": D(1), "price": D("0.10"), "intent": LONG}]
    wc = g.worst_case_deficit(up_qty=D(3), dn_qty=D(3),
                              up_cost=D("1.20"), dn_cost=D("1.50"),
                              order_side="DOWN", order_qty=D(1),
                              order_price=D("0.20"), outstanding=out)
    # none fills: cost 2.90, min(3,4) = 3 -> -0.10
    assert D(wc["scenarios"]["none"]) == D("-0.10")
    # both UP legs fill: cost 2.90+1.80+0.10 = 4.80, min(6,4) = 4 -> +0.80,
    # plus 0.04 of slack for the two still-fillable orders
    assert wc["scenarios"]["rounding_slack"] == "0.04"
    assert wc["deficit"] == D("0.84")
    assert wc["scenario"] in ("up_only", "all")
    assert g.refuse(order_qty=D(1), order_price=D("0.20"),
                    order_intent=SHORT, inventory=inv,
                    outstanding=out) is not None


# ----------------------------------------------- the cumulative-loss gate

def test_missing_unrealized_exposure_is_a_refusal_not_a_zero():
    g = G(max_cumulative_loss_usd=D("10"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=LONG,
                 cumulative_realized=D("-1"),
                 cumulative_unrealized_loss=None)
    assert r == "CUMULATIVE_UNREALIZED_UNAVAILABLE"


def test_open_book_exposure_can_trip_the_cumulative_ceiling_alone():
    """Realized is flat but the open book can lose $9: the ceiling binds."""
    g = G(max_cumulative_loss_usd=D("5"))
    r = g.refuse(order_qty=D(1), order_price=D("0.10"), order_intent=LONG,
                 cumulative_realized=D(0),
                 cumulative_unrealized_loss=D("9"))
    assert r is not None and r.startswith("CUMULATIVE_LOSS_LIMIT:9>=5")


# -------------------------------------------------- runtime configuration

def test_unset_and_blank_leave_the_gate_disabled():
    from src.l5_live_validation_gates import parse_deficit_bound
    assert parse_deficit_bound(None) is None
    assert parse_deficit_bound("") is None
    assert parse_deficit_bound("   ") is None


def test_a_valid_bound_parses_including_a_strict_negative_one():
    from src.l5_live_validation_gates import parse_deficit_bound
    assert parse_deficit_bound("2") == D(2)
    assert parse_deficit_bound(" 0.50 ") == D("0.50")
    assert parse_deficit_bound("-0.01") == D("-0.01")   # demand both-ways gain


def test_a_configured_but_unusable_bound_FAILS_CLOSED():
    from src.l5_live_validation_gates import (parse_deficit_bound,
                                              DeficitBoundInvalid)
    for bad in ("two", "0.5x", "NaN", "Infinity", "1e9", "-99999"):
        with pytest.raises(DeficitBoundInvalid):
            parse_deficit_bound(bad)


def test_the_runner_reraises_a_bad_bound_instead_of_dropping_all_gates():
    """Returning None would silently remove every additive gate while the
    operator believes a bound is in force."""
    import os
    from src import l5_pmus_live_runner as R
    from src.l5_live_validation_gates import DeficitBoundInvalid
    prev = os.environ.get(R.DEFICIT_ENV)
    try:
        os.environ[R.DEFICIT_ENV] = "not-a-number"
        with pytest.raises(DeficitBoundInvalid):
            R.validation_gates()
        # and a good value builds a gate that is actually armed
        os.environ[R.DEFICIT_ENV] = "2"
        g = R.validation_gates()
        assert g is not None
        assert g.max_worst_case_deficit_usd == D(2)
    finally:
        if prev is None:
            os.environ.pop(R.DEFICIT_ENV, None)
        else:
            os.environ[R.DEFICIT_ENV] = prev


def test_the_gate_is_off_when_the_runtime_value_is_absent():
    import os
    from src import l5_pmus_live_runner as R
    prev = os.environ.pop(R.DEFICIT_ENV, None)
    try:
        g = R.validation_gates()
        assert g is not None
        assert g.max_worst_case_deficit_usd is None
    finally:
        if prev is not None:
            os.environ[R.DEFICIT_ENV] = prev


# ------------------------------- the gate is on the ONLY submission path

def test_exactly_one_gated_submission_path_and_the_gate_precedes_the_wire():
    """A second ungated submit site would make every bound above advisory."""
    import pathlib
    src = pathlib.Path("src/l5_live_execution.py").read_text()
    lines = src.splitlines()
    submits = [i for i, ln in enumerate(lines, 1)
               if "self.transport.submit(" in ln and "def " not in ln]
    gates = [i for i, ln in enumerate(lines, 1) if "vg.refuse(" in ln]
    assert len(submits) == 1, submits
    assert len(gates) == 1, gates
    assert gates[0] < submits[0], "the gate must run BEFORE the wire"


def test_the_gate_call_site_supplies_inventory_and_unrealized_exposure():
    """Both were defaulted before; a silent omission is what this pins."""
    import pathlib
    src = pathlib.Path("src/l5_live_execution.py").read_text()
    call = src[src.index("vg.refuse("):]
    call = call[:call.index(")\n")]
    assert "inventory=self.inventory_for_gates(market)" in call
    assert "cumulative_unrealized_loss=" in call


def test_inventory_and_exposure_are_read_from_the_durable_store():
    """RESTART RECOVERY. Both helpers read the reconciled ledger and hold no
    state of their own, so a process that reloads the same ledger computes
    the same figures -- there is nothing in-memory to lose."""
    import inspect
    from src.l5_live_execution import FaithfulExecutor
    inv = inspect.getsource(FaithfulExecutor.inventory_for_gates)
    exp = inspect.getsource(FaithfulExecutor.unrealized_loss_for_gates)
    for body in (inv, exp):
        assert "self.store." in body
        assert "self.state" not in body          # no private cache
    assert "position(" in inv and "leg_cost(" in inv
    assert "risk_exposure_usd(" in exp
    assert "return None" in exp                  # fail closed, never zero


# ------------------------------------------------- exhaustive correctness

def test_the_four_corners_equal_the_exhaustive_worst_case():
    """The gate maximises over four corners rather than 2^n subsets, on the
    argument that the deficit is convex in the fill fractions so its maximum
    sits at a vertex, and that within one side all-or-nothing dominates.
    This checks that claim against brute force over PARTIAL fills too."""
    import itertools
    import random
    g = G(max_worst_case_deficit_usd=D("0"))

    def brute(a0, b0, cu, cd, oside, oq, op, orders, steps=4):
        base_a = a0 + (oq if oside == "UP" else D(0))
        base_b = b0 + (oq if oside == "DOWN" else D(0))
        base_c = cu + cd + oq * op + g.fee_worst(oq, op)
        grid = [[D(i) / D(steps) for i in range(steps + 1)] for _ in orders]
        worst = None
        for combo in itertools.product(*grid):
            a, b, c = base_a, base_b, base_c
            for f, o in zip(combo, orders):
                q = o["qty"] * f
                if q <= 0:
                    continue
                # ONE rounding per order, on the filled fraction -- the
                # model fee_worst documents. Per-FILL rounding is covered
                # by its own test below.
                c += q * o["price"] + g.fee_worst(q, o["price"])
                if o["intent"] == LONG:
                    a += q
                else:
                    b += q
            d = c - min(a, b)
            if worst is None or d > worst:
                worst = d
        return worst

    rnd = random.Random(11)
    checked = 0
    for _ in range(150):
        a0 = D(rnd.randrange(0, 2000)) / D(100)
        b0 = D(rnd.randrange(0, 2000)) / D(100)
        cu = (a0 * D(rnd.randrange(1, 99)) / D(100)) if a0 > 0 else D(0)
        cd = (b0 * D(rnd.randrange(1, 99)) / D(100)) if b0 > 0 else D(0)
        oside = rnd.choice(["UP", "DOWN"])
        oq = D(rnd.randrange(1, 800)) / D(100)
        op = D(rnd.randrange(1, 99)) / D(100)
        orders = [{"qty": D(rnd.randrange(1, 600)) / D(100),
                   "price": D(rnd.randrange(1, 99)) / D(100),
                   "intent": rnd.choice([LONG, SHORT])}
                  for _ in range(rnd.randrange(0, 4))]
        wc = g.worst_case_deficit(up_qty=a0, dn_qty=b0, up_cost=cu,
                                  dn_cost=cd, order_side=oside,
                                  order_qty=oq, order_price=op,
                                  outstanding=orders)
        if not wc["ok"]:
            continue
        checked += 1
        assert brute(a0, b0, cu, cd, oside, oq, op, orders) <= wc["deficit"], (
            "gate UNDERSTATES the worst case", str(a0), str(b0), str(oq),
            str(op), orders)
    assert checked > 100


def test_per_FILL_cent_rounding_is_NOT_modelled_and_the_gap_is_quantified():
    """An order that fills in pieces pays a cent rounding on each piece if
    the venue rounds per fill. The gate rounds once per order, so it can
    understate by up to (pieces - 1) cents. This is a STATED limitation
    bounded by the live order count, not a hidden one -- and
    fee_extra_per_order_usd prices it when an owner wants it priced."""
    g = G(max_worst_case_deficit_usd=D("99"))
    whole = g.fee_worst(D("0.4"), D("0.5"))
    pieces = sum((g.fee_worst(D("0.1"), D("0.5")) for _ in range(4)), D(0))
    assert whole == D("0.02")
    assert pieces == D("0.08")
    assert pieces - whole == D("0.06")      # the exact unmodelled gap
    # the default allowance is what keeps the corner search an upper bound
    assert g.fee_extra_per_order_usd == D("0.01")
    # and it can be raised to price a heavily-partialled order outright
    priced = G(max_worst_case_deficit_usd=D("99"),
               fee_extra_per_order_usd=D("0.07"))
    assert priced.fee_worst(D("0.4"), D("0.5")) >= pieces
    assert priced.as_dict()["fee_extra_per_order_usd"] == "0.07"
