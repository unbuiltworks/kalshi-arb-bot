"""Capital accounting kept in six parts, and correct at every staged envelope.

THE RULE THIS FILE EXISTS TO ENFORCE: a matched pair's $1.00 redemption is
certain and risk-free and is NOT buying power until the market settles.
Treating it as available is how an account spends one dollar twice.

THE SECOND RULE: cash does not recycle inside a window. Each completed
pair's outlay stays committed until settlement, so an envelope DIVIDES
rather than revolves. That is why a $1 envelope buys exactly one pair, and
it is measured against the real executor in
`test_l5_multi_round_lifecycle.py`, not asserted here.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_capital_plan as CP


def acct(**over):
    kw = dict(positions={"UP": D("1"), "DOWN": D("1")},
              leg_cost={"UP": D("0.58"), "DOWN": D("0.37")},
              live_orders=(), cash_envelope_usd=D("10"))
    kw.update(over)
    return CP.account(**kw)


# ===================== 1. THE SIX QUANTITIES ARE ACTUALLY SEPARATE

def test_all_six_are_reported_and_none_is_derived_from_another():
    a = acct()
    for k in ("cash_committed_usd", "outstanding_reserve_usd",
              "unmatched_worst_case_usd", "fees_paid_usd",
              "realized_settled_usd", "buying_power_usd"):
        assert k in a, k


def test_a_matched_pair_costs_cash_but_carries_no_worst_case():
    a = acct()
    assert D(a["cash_committed_usd"]) == D("0.9500")
    assert D(a["unmatched_worst_case_usd"]) == D("0")


def test_an_unmatched_leg_carries_its_whole_basis():
    a = acct(positions={"UP": D("2"), "DOWN": D("1")},
             leg_cost={"UP": D("0.91"), "DOWN": D("0.50")})
    assert D(a["cash_committed_usd"]) == D("1.4100")
    assert D(a["unmatched_qty"]) == D("1")
    assert D(a["unmatched_worst_case_usd"]) == D("0.4550")


def test_fees_are_never_folded_into_settlement():
    a = acct(fees_paid_usd=D("0.03"), realized_settled_usd=D("0.05"))
    assert D(a["fees_paid_usd"]) == D("0.03")
    assert D(a["realized_settled_usd"]) == D("0.05")


# ======== 2. THE REDEMPTION IS NOT BUYING POWER, WHICH IS THE WHOLE POINT

def test_a_matched_pairs_redemption_is_reported_and_excluded():
    a = acct()
    assert D(a["expected_redemption_usd"]) == D("1.0000")
    assert a["expected_redemption_is_not_buying_power"] is True
    # buying power is envelope - committed - reserve, and nothing else
    assert D(a["buying_power_usd"]) == D("10") - D("0.9500")


def test_buying_power_never_grows_when_a_pair_completes():
    """THE TRAP. Completing a pair makes $1.00 CERTAIN, not AVAILABLE."""
    flat = acct(positions={}, leg_cost={})
    paired = acct()
    assert D(paired["buying_power_usd"]) < D(flat["buying_power_usd"])
    assert D(paired["expected_redemption_usd"]) > D("0")


def test_resting_orders_reserve_per_side_not_summed():
    """If both sides fill they PAIR, so charging both double-charges it."""
    a = acct(live_orders=[{"side": "UP", "qty": D("1"), "price": D("0.58")},
                          {"side": "DOWN", "qty": D("1"),
                           "price": D("0.37")}])
    assert D(a["outstanding_reserve_usd"]) == D("0.5800"), "max, not 0.95"


def test_the_signed_net_is_never_used_for_the_reserve():
    """1415z: UP held against an equal DOWN resting netted to 0.00 and a
    gate read the book as flat. Per side, that reserve is not zero."""
    a = acct(positions={"UP": D("2.17")}, leg_cost={"UP": D("1.00")},
             live_orders=[{"side": "DOWN", "qty": D("2.17"),
                           "price": D("0.46")}])
    assert D(a["outstanding_reserve_usd"]) > D("0")
    assert D(a["unmatched_worst_case_usd"]) == D("1.00")


@pytest.mark.parametrize("order", [
    {"side": "UP", "qty": D("1"), "price": None},
    {"side": "SIDEWAYS", "qty": D("1"), "price": D("0.5")},
    {"side": "UP", "qty": D("1"), "price": D("1.5")},
])
def test_an_unreadable_order_is_charged_a_dollar_a_share(order):
    a = acct(live_orders=[order])
    assert D(a["outstanding_reserve_usd"]) == D("1")


@pytest.mark.parametrize("kw,why", [
    ({"cash_envelope_usd": None}, "ENVELOPE_UNREADABLE"),
    ({"cash_envelope_usd": D("-1")}, "ENVELOPE_UNREADABLE"),
    ({"fees_paid_usd": None}, "FEES_OR_SETTLEMENT_UNREADABLE"),
    ({"positions": {"UP": None}}, "POSITIONS_OR_COST_UNREADABLE"),
    ({"live_orders": [{"side": "UP", "qty": None}]},
     "OUTSTANDING_UNREADABLE"),
])
def test_an_unreadable_input_never_reports_headroom(kw, why):
    a = acct(**kw)
    assert a["readable"] is False
    assert a["reason"] == why
    assert "buying_power_usd" not in a


def test_an_absent_side_is_flat_and_a_null_side_is_unknown():
    assert acct(positions={"UP": D("1")},
                leg_cost={"UP": D("0.58")})["readable"] is True
    assert acct(positions={"UP": D("1"), "DOWN": None})["readable"] is False


# ================ 3. CORRECT AT EVERY STAGED ENVELOPE, $10 TO $200

@pytest.mark.parametrize("env", [D("1"), D("10"), D("25"), D("50"),
                                 D("200")])
def test_buying_power_is_exact_at_every_envelope(env):
    a = acct(cash_envelope_usd=env)
    assert D(a["buying_power_usd"]) == env - D("0.9500")


@pytest.mark.parametrize("env,expect", [
    (D("1"), 1), (D("10"), 10), (D("25"), 26), (D("50"), 52),
    (D("200"), 210),
])
def test_the_staged_envelopes_divide_into_the_right_pair_counts(env, expect):
    """At a 0.95 pair cost. FLOOR, because cash divides rather than
    revolves inside a window."""
    r = CP.rounds_affordable(cash_envelope_usd=env,
                             pair_cost_usd=D("0.95"))
    assert r["rounds_affordable"] == expect


def test_the_stages_are_the_ones_the_plan_is_designed_against():
    assert CP.STAGES == (D("10"), D("25"), D("50"), D("200"))


@pytest.mark.parametrize("env", [D("1"), D("10"), D("25"), D("50"),
                                 D("200")])
def test_no_envelope_ever_reports_more_power_than_it_holds(env):
    """The invariant that has to survive scaling."""
    a = acct(cash_envelope_usd=env,
             live_orders=[{"side": "UP", "qty": D("2"),
                           "price": D("0.58")}])
    assert D(a["buying_power_usd"]) <= env
    assert (D(a["buying_power_usd"]) + D(a["cash_committed_usd"])
            + D(a["outstanding_reserve_usd"])) == env


def test_already_committed_cash_reduces_what_remains_affordable():
    r = CP.rounds_affordable(cash_envelope_usd=D("200"),
                             pair_cost_usd=D("0.95"),
                             already_committed_usd=D("190"))
    assert r["rounds_affordable"] == 10


def test_the_worst_case_of_a_full_envelope_is_reported():
    """Half of every pair stranding is the shape 1230z produced, and at
    $200 it is ~$100 -- which is the number a stage gate must be set
    against, not the pair edge."""
    r = CP.rounds_affordable(cash_envelope_usd=D("200"),
                             pair_cost_usd=D("0.95"))
    assert D(r["worst_case_if_every_round_strands_one_leg_usd"]) == \
        D("99.7500")


def test_an_exhausted_envelope_affords_nothing_rather_than_going_negative():
    r = CP.rounds_affordable(cash_envelope_usd=D("1"),
                             pair_cost_usd=D("0.95"),
                             already_committed_usd=D("5"))
    assert r["rounds_affordable"] == 0


def test_it_is_not_a_gate_and_releases_nothing():
    import inspect
    for fn in (CP.account, CP.rounds_affordable, CP.outstanding_reserve):
        src = inspect.getsource(fn)
        for banned in ("refuse", "setenv", "os.environ", "save(",
                       "max_cash_deployed"):
            assert banned not in src, (fn.__name__, banned)
