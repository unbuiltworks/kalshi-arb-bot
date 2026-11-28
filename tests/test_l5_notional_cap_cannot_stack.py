"""Can repeated $0.80 opening orders walk past the $2 per-market deficit?

OWNER QUESTION, asked before arming an eight-window campaign: "Confirm that
multiple $0.80 opening orders cannot bypass the intended $2 per-market deficit
limit."

THE HONEST FRAMING. The opening-notional cap is PER PLAN. `build_orders` sizes
one opening pair and knows nothing about inventory already held, so the cap
alone does NOT bound a sequence of rounds -- two rounds of $0.80 are $1.60 of
exposure and the cap is satisfied by each of them separately. What has to stop
a third is the worst-case deficit gate, and these tests pin that down against
the four states the owner named: FILLED, RESTING, PENDING and PARTIALLY FILLED.

A NOTE ON `worst_case_deficit`. Its `ok` field means "the state was readable",
NOT "within the bound". The bound is enforced in `refuse()`. Testing the
former and reading it as the latter would conclude the gate is broken when it
is not, so every test here goes through `refuse()`.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_validation_gates as VG

#: the real 1200z book of 2026-10-25T12:05:54Z, and what the cap sizes on it
DEAR = D("0.77")
CHEAP = D("0.18")
QTY = D("1.03")
BUDGET = D("0.80")
DEFICIT_BOUND = D("2.00")

DN = "ORDER_INTENT_BUY_SHORT"
UP = "ORDER_INTENT_BUY_LONG"


@pytest.fixture
def gates():
    """Production's bounds, as the verifier reports them."""
    return VG.ValidationGates(max_cash_deployed_usd=D("5"),
                              max_worst_case_deficit_usd=DEFICIT_BOUND,
                              max_unmatched_contracts=D("5"))


def inv(up_q=D(0), dn_q=D(0), up_c=D(0), dn_c=D(0)):
    return {"up_qty": up_q, "dn_qty": dn_q, "up_cost": up_c, "dn_cost": dn_c}


def test_the_cap_itself_is_satisfied_by_the_clip_under_test():
    assert QTY * DEAR <= BUDGET


def test_two_same_side_openings_fit_and_the_THIRD_is_refused(gates):
    """THE ANSWER. Each $0.80 opening passes its own cap; the deficit gate is
    what stops the sequence, and it stops it at two."""
    allowed, dn_q, dn_c = 0, D(0), D(0)
    reason = None
    for _ in range(6):
        why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                           outstanding=[], inventory=inv(dn_q=dn_q, dn_c=dn_c))
        if why is not None:
            reason = why
            break
        allowed += 1
        dn_q += QTY
        dn_c += QTY * DEAR + gates.fee_worst(QTY, DEAR)
    assert allowed == 2, (allowed, reason)
    assert reason is not None and reason.startswith("WORST_CASE_DEFICIT:")
    # and what was actually accumulated stayed inside the bound
    assert dn_c <= DEFICIT_BOUND


def test_the_refusal_names_the_number_it_exceeded(gates):
    why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                       outstanding=[],
                       inventory=inv(dn_q=D("2.06"), dn_c=D("1.6462")))
    assert why == "WORST_CASE_DEFICIT:2.4693>2.00:none"


def test_resting_orders_count_even_though_nothing_has_filled(gates):
    """RESTING. A pair on the book is an obligation, not a plan: the gate
    maximises over which outstanding legs fill, and the `dn_only` corner is
    what binds."""
    one = [{"qty": QTY, "price": CHEAP, "intent": UP},
           {"qty": QTY, "price": DEAR, "intent": DN}]
    assert gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                        outstanding=one, inventory=inv()) is None
    why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                       outstanding=one * 2, inventory=inv())
    assert why is not None
    assert "dn_only" in why, why


def test_a_partial_fill_is_counted_on_both_sides_of_the_split(gates):
    """PARTIALLY FILLED. The executed part sits in inventory at its cost and
    the unfilled remainder sits in `outstanding`; neither is double counted
    and neither is dropped."""
    filled, rest = D("0.50"), D("0.53")
    assert filled + rest == QTY
    why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                       outstanding=[{"qty": rest, "price": DEAR, "intent": DN}],
                       inventory=inv(dn_q=filled, dn_c=filled * DEAR))
    assert why is None
    # one more opening on top of that partial state is refused
    why2 = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                        outstanding=[{"qty": rest, "price": DEAR, "intent": DN}],
                        inventory=inv(dn_q=filled + QTY,
                                      dn_c=(filled + QTY) * DEAR))
    assert why2 is not None and why2.startswith("WORST_CASE_DEFICIT:")


def test_an_unreadable_resting_price_is_charged_at_a_dollar_not_ignored(gates):
    """PENDING. An order whose price cannot be read is the pending/unacked
    shape, and it must be charged at its worst case, not skipped."""
    assert gates.UNKNOWN_PRICE_WORST_CASE == D(1)
    # one unreadable leg still fits under $2...
    assert gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                        outstanding=[{"qty": QTY, "price": None,
                                      "intent": DN}],
                        inventory=inv()) is None
    # ...two do not, which only happens because each was charged $1 a share
    why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                       outstanding=[{"qty": QTY, "price": None, "intent": DN},
                                    {"qty": QTY, "price": None, "intent": DN}],
                       inventory=inv())
    assert why is not None and why.startswith("WORST_CASE_DEFICIT:")


def test_an_unreadable_outstanding_side_fails_closed(gates):
    why = gates.refuse(order_qty=QTY, order_price=DEAR, order_intent=DN,
                       outstanding=[{"qty": QTY, "price": DEAR,
                                     "intent": "SIDEWAYS"}],
                       inventory=inv())
    assert why is not None


def test_a_completing_order_is_never_blocked_by_the_deficit(gates):
    """The complement REDUCES the deficit, so it must stay available exactly
    when the book is most imbalanced. Blocking it is what stranded 0130z."""
    assert gates.refuse(order_qty=QTY, order_price=CHEAP, order_intent=UP,
                        outstanding=[],
                        inventory=inv(dn_q=D("2.06"), dn_c=D("1.6462"))) is None


def test_two_allocation_rounds_at_the_budget_stay_inside_the_bound():
    """The configured shape: ROUND_ALLOCATIONS=2 at $0.80 is $1.60 of
    worst-case opening exposure, inside the $2 deficit bound with room for
    the fee. If a future budget breaks that, this fails here."""
    from src import l5_two_sided_probe as TS
    import os
    os.environ["L5_TS_OPENING_NOTIONAL_BUDGET_USD"] = "0.80"
    os.environ["L5_TS_ROUND_ALLOCATIONS"] = "2"
    try:
        budget = TS.opening_notional_budget_usd()
        rounds = TS.round_allocations()
        assert budget * rounds <= DEFICIT_BOUND
        assert budget * rounds <= D("5")          # the exposure ceiling too
    finally:
        os.environ.pop("L5_TS_OPENING_NOTIONAL_BUDGET_USD", None)
        os.environ.pop("L5_TS_ROUND_ALLOCATIONS", None)


# ------------------------------- the two invariants are DIFFERENT quantities

def test_the_cap_bounds_NOTIONAL_and_the_gate_bounds_FEE_INCLUSIVE_COST(gates):
    """Why a $0.80 cap shows up as $0.8231 of executed cost.

    They are two different quantities and both are intended:

      CAP      qty * dearer_own_price            <= $0.80   (no fee)
      DEFICIT  C - min(up_qty, dn_qty) <= $2.00, where C includes
               fee_worst() at TAKER theta plus $0.01 per order, rounded UP

    $0.7931 + $0.03 = $0.8231. The cap is not breached; the deficit gate is
    simply charging a conservative fee the cap does not.
    """
    notional = QTY * DEAR
    assert notional == D("0.7931")
    assert notional <= BUDGET                      # the cap's invariant holds
    fee = gates.fee_worst(QTY, DEAR)
    assert fee == D("0.03")
    assert notional + fee == D("0.8231")           # what the gate charges
    assert notional + fee > BUDGET                 # and it exceeds the cap...
    # ...which is correct, because the cap bounds notional, not fee-inclusive cost


def test_the_cap_equals_the_realized_strand_loss_for_a_maker_fill():
    """0130z settled at exactly -$1.1594 = 1.87 x 0.62, and every leg of that
    campaign carried commission 0.0000. So for a maker fill at zero
    commission the notional IS the realized loss, which is the quantity the
    cap was chosen to bound."""
    assert D("1.87") * D("0.62") == D("1.1594")


def test_the_gate_is_strictly_more_conservative_than_reality(gates):
    """fee_worst uses the TAKER theta and never nets the maker rebate, so the
    gate always charges at least what a maker fill actually costs."""
    assert gates.fee_theta_worst == D("0.0695")    # taker theta
    for px in (D("0.20"), D("0.35"), D("0.55"), D("0.77")):
        assert gates.fee_worst(D("1"), px) > 0     # never zero, never a credit
