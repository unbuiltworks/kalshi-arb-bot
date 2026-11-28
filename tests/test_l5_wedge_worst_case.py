"""$3.60 bounds the worst case under EXECUTOR FAILURE and partial fills.

THE SCENARIO THIS PRICES. The credential-free watchdog is accepted as an
independent HALT DETECTOR that cannot cancel a resting order. So the
unmitigated failure is: the executor wedges with both authorized legs
resting, nothing withdraws them, they rest to window close and settle at
whatever the venue decides. These tests assert the authorized
max_worst_case_deficit_usd of 3.60 bounds EVERY reachable outcome of that
scenario, across all fill combinations, not merely the all-or-nothing ones.
"""
from __future__ import annotations

import itertools
import os
import sys
from decimal import Decimal as D

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import l5_live_validation_gates as VG   # noqa: E402
from src import l5_two_sided_probe as TS         # noqa: E402

DEFICIT = D("3.60")
CASH = D("7")
# the authorized envelope: two post-only maker legs, 4.86 a side maximum
QTY = D("4.86")


def _gates():
    return VG.ValidationGates(
        max_cash_deployed_usd=CASH, max_unmatched_contracts=D("5"),
        max_cumulative_loss_usd=D("20"), max_l5_daily_loss_usd=D("5"),
        max_l5_cumulative_loss_usd=D("20"),
        max_worst_case_deficit_usd=DEFICIT, cash_policy=VG.NO_RECYCLING)


def _settle(up_q, up_px, dn_q, dn_px):
    """Realised cash outcome of a wedged pair, worst settlement.

    An UP share costs up_px and pays 1 if UP settles. A DOWN share costs
    dn_px and pays 1 if DOWN settles. Nothing is withdrawn, so both legs
    settle as filled. The worst case is the cheaper payout side.
    """
    cost = up_q * up_px + dn_q * dn_px
    pay_up = up_q           # UP settles: each UP share redeems $1
    pay_dn = dn_q
    return cost - min(pay_up, pay_dn)


@pytest.mark.parametrize("up_px,dn_px", [
    (D("0.44"), D("0.55")),   # the measured 2100z book, pair 0.99
    (D("0.50"), D("0.50")),
    (D("0.12"), D("0.87")),
    (D("0.05"), D("0.92")),
])
def test_the_deficit_bound_holds_at_whatever_size_the_gate_permits(
        up_px, dn_px):
    """THE CORRECTION THIS ENCODES. 4.86 a side is NOT universally safe under
    3.60: a DOWN-only fill at 0.92 loses 4.4712 and at 0.87 loses 4.2282.
    The authorized envelope is safe because the DEFICIT BOUND binds and the
    size shrinks -- the contract count was only ever safe for the moderate
    book it was measured on (0.44/0.55 -> 4.86 x 0.55 = 2.673).

    So the property worth asserting is not "4.86 is fine" but "whatever size
    the gate permits, the wedge stays inside 3.60".
    """
    g = _gates()
    dear = max(up_px, dn_px)
    permitted = None
    q = QTY
    while q > 0:
        reason = g.refuse(
            order_qty=q, order_price=dear,
            order_intent="BUY_SHORT" if dn_px >= up_px else "BUY_LONG",
            filled_legs={}, outstanding=[], book_uncertain=False,
            unmatched_after=D(0), cumulative_realized=D(0),
            l5_scoped_realized=D(0), cumulative_unrealized_loss=D(0),
            available_cash_usd=D("350"),
            inventory={"market": "m", "up_qty": D(0), "dn_qty": D(0),
                       "up_cost": D(0), "dn_cost": D(0)})
        if reason is None:
            permitted = q
            break
        q = (q - D("0.01")).quantize(D("0.01"))
    assert permitted is not None, (
        "no size at all was permitted at %s/%s" % (up_px, dn_px))
    # the one-sided fill of the permitted size is the wedge's worst shape
    worst = permitted * dear
    assert worst <= DEFICIT, (
        "permitted %s at %s gives a wedge loss of %s, over %s"
        % (permitted, dear, worst, DEFICIT))
    # and every partial fill of it is no worse
    for frac in (D(0), D("0.25"), D("0.5"), D("0.75"), D(1)):
        assert (permitted * frac) * dear <= DEFICIT


def test_the_authorized_486_breaches_the_bound_on_a_skewed_book():
    """Recorded explicitly, because the envelope was approved as
    '4.86 a side AND 3.60' and those two are not simultaneously satisfiable
    at every price. The gate is what reconciles them."""
    assert QTY * D("0.92") > DEFICIT          # 4.4712
    assert QTY * D("0.87") > DEFICIT          # 4.2282
    assert QTY * D("0.55") <= DEFICIT         # 2.673 -- the measured book


def test_the_one_sided_fill_is_the_binding_case(tmp_path):
    """Only one leg fills and nothing cancels it -- the wedge's worst shape."""
    up_only = _settle(QTY, D("0.44"), D(0), D("0.55"))
    dn_only = _settle(D(0), D("0.44"), QTY, D("0.55"))
    assert up_only == QTY * D("0.44")      # pays nothing if DOWN settles
    assert dn_only == QTY * D("0.55")
    # true for the MEASURED book; the skew case is covered above
    assert max(up_only, dn_only) <= DEFICIT, (up_only, dn_only)


def test_a_completed_pair_cannot_lose_more_than_the_pair_cost_minus_one():
    """Both legs full: the pair redeems exactly $1 a matched share."""
    loss = _settle(QTY, D("0.44"), QTY, D("0.55"))
    assert loss == QTY * D("0.99") - QTY   # = -0.01 a share, a small GAIN
    assert loss <= DEFICIT


def test_the_gate_refuses_any_order_whose_wedge_loss_would_exceed_3_60():
    """The bound is enforced pre-trade, not merely true of the chosen size."""
    g = _gates()
    # a size whose one-sided fill would breach 3.60
    too_big = D("9")
    reason = g.refuse(
        order_qty=too_big, order_price=D("0.55"), order_intent="BUY_SHORT",
        filled_legs={}, outstanding=[], book_uncertain=False,
        unmatched_after=D(0), cumulative_realized=D(0),
        l5_scoped_realized=D(0), cumulative_unrealized_loss=D(0),
        available_cash_usd=D("350"),
        inventory={"market": "m", "up_qty": D(0), "dn_qty": D(0),
                   "up_cost": D(0), "dn_cost": D(0)})
    assert reason is not None, "a 9-contract short must not pass 3.60/7"


def test_the_authorized_size_passes_the_same_gate():
    g = _gates()
    reason = g.refuse(
        order_qty=QTY, order_price=D("0.44"), order_intent="BUY_LONG",
        filled_legs={}, outstanding=[], book_uncertain=False,
        unmatched_after=D(0), cumulative_realized=D(0),
        l5_scoped_realized=D(0), cumulative_unrealized_loss=D(0),
        available_cash_usd=D("350"),
        inventory={"market": "m", "up_qty": D(0), "dn_qty": D(0),
                   "up_cost": D(0), "dn_cost": D(0)})
    assert reason is None, reason


def test_missing_inventory_refuses_rather_than_pricing_a_wedge_at_zero():
    """A deficit bound configured but un-evaluable must refuse."""
    g = _gates()
    reason = g.refuse(
        order_qty=QTY, order_price=D("0.44"), order_intent="BUY_LONG",
        filled_legs={}, outstanding=[], book_uncertain=False,
        unmatched_after=D(0), cumulative_realized=D(0),
        l5_scoped_realized=D(0), cumulative_unrealized_loss=D(0),
        available_cash_usd=D("350"), inventory=None)
    assert reason == "WORST_CASE_DEFICIT_NO_INVENTORY", reason


def test_the_watchdog_cannot_cancel_and_that_is_the_accepted_premise():
    """Pinned so the accepted limitation cannot be quietly 'fixed' into a
    claim of independent cancellation."""
    from src import l5_watchdog as W

    class Src:
        name = "T"

        def open_orders(self):
            return []

    class Asserter:
        def heartbeat_no_halt(self):
            return {"halt": False}

    wd = W.Watchdog(Src(), Asserter(), cancel_enabled=True, oracle=None)
    assert wd.cancel_enabled is False          # no oracle -> cannot cancel
    assert wd.cancel_requested is True         # and it says so
