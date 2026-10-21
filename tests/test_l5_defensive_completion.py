"""Defensive completion, validated against the two real contrasting windows.

Every constant here is a production record, not a fixture:

  2315z  first leg UP 3.39 @ own 0.3700 (venue order D7HT1H9G10JH), cost
         $1.2543, no complement, settled worthless -> realized -$1.2543.
         Book after the fill: bid 0.20 -> 0.11, displayed 1831-7139 shares.
  2300z  first leg UP 3.75 @ own 0.2400 (D7HHE1P010JB), complement
         D7HKMA6G10JQ filled at own 0.7100 at T+392 -> realized +$0.1875.

The policy has to act in the first and stay out of the second.
"""
from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DC


TAKER = D("0.026986850000")      # fee_floor from the live 2300z plan record
WIN_START = 0.0
CUTOFF = 599.0                   # completion cutoff at cancel margin 300


def call(**kw):
    base = dict(
        now_t=CUTOFF - 10, window_start=WIN_START, complete_cutoff_s=CUTOFF,
        unmatched_qty=D("3.39"), first_leg_cost=D("0.37"),
        exit_price=D("0.80"),            # own complement = 1 - bid(0.20)
        displayed_depth=D("1831.25"),
        taker_fee_per_contract=TAKER,
    )
    base.update(kw)
    return DC.decide(**base)


# ---------------------------------------------------------------- 2315z

def test_2315z_defensive_exit_is_taken_and_bounds_the_loss():
    r = call()
    assert r["act"] is True
    assert r["reason"] == "DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"
    # holding risked the whole $1.2543, which is what it actually cost
    assert D(r["hold_downside_usd"]) == pytest.approx(D("1.2543"), abs=1e-4)
    # exiting bounds it strictly below that
    assert D(r["exit_downside_usd"]) < D(r["hold_downside_usd"])
    assert D(r["saves_vs_holding_usd"]) > 0


def test_the_depth_claim_is_reported_not_assumed():
    r = call()
    # 1831.25 displayed against 3.39 needed
    assert D(r["depth_ratio"]) > D("500")


def test_unreadable_depth_refuses_rather_than_assuming_a_deep_book():
    assert call(displayed_depth=None)["reason"] == "DEPTH_UNREADABLE"


def test_thin_depth_refuses():
    r = call(displayed_depth=D("1.00"))
    assert r["act"] is False
    assert r["reason"] == "INSUFFICIENT_DISPLAYED_DEPTH"


# ---------------------------------------------------------------- 2300z

def test_2300z_is_never_touched_because_the_arm_window_is_closed():
    """The complement filled at T+392. The guard opens at T+539. The whole
    point of a time guard is that this case is unreachable."""
    r = call(now_t=392.0, unmatched_qty=D("3.75"),
             first_leg_cost=D("0.24"), exit_price=D("0.82"),
             displayed_depth=D("5000"))
    assert r["act"] is False
    assert r["reason"] == "OUTSIDE_DEFENSIVE_ARM_WINDOW"


def test_the_guard_is_shut_before_its_window_and_after_the_cutoff():
    assert not DC.arm_window_open(100.0, WIN_START, CUTOFF)
    assert DC.arm_window_open(CUTOFF - 1, WIN_START, CUTOFF)
    # past the cutoff the venue will not complete anyway
    assert not DC.arm_window_open(CUTOFF, WIN_START, CUTOFF)
    assert not DC.arm_window_open(CUTOFF + 50, WIN_START, CUTOFF)


# ------------------------------------------------- bounds and refusals

def test_it_never_claims_to_improve_expected_value():
    """At the touch an exit is EV-neutral before fees and negative by the
    fee. No input may make this field true."""
    for r in (call(), call(displayed_depth=None), call(now_t=10.0),
              call(unmatched_qty=0)):
        assert r["improves_expected_value"] is False


def test_worst_case_comparison_always_favours_exiting_which_is_the_point():
    """DOCUMENTING A NON-PROTECTION. Holding risks the whole leg; exiting
    recovers (1 - exit_price) per contract. So the worst-case comparison
    favours the exit for every valid binary price, and cannot be the thing
    that keeps us out of a 2300z. Only the clock does that."""
    r = call(first_leg_cost=D("0.10"), exit_price=D("0.95"))
    assert r["act"] is True
    assert D(r["exit_downside_usd"]) < D(r["hold_downside_usd"])


def test_the_fee_dominated_corner_is_still_refused():
    """The one case the comparison does catch: recovery below the fee."""
    # inside the 1.20 ceiling (0.20 + 0.97 = 1.17), but recovery of 0.03
    # per contract is below the 0.05 fee, so the exit loses more than it
    # saves and is refused on its own merits.
    r = call(first_leg_cost=D("0.20"), exit_price=D("0.97"),
             taker_fee_per_contract=D("0.05"))
    assert r["act"] is False
    assert r["reason"] == "DOES_NOT_REDUCE_BOUNDED_DOWNSIDE"


def test_the_defensive_pair_ceiling_binds():
    r = call(first_leg_cost=D("0.60"), exit_price=D("0.75"))   # pair 1.35
    assert r["act"] is False
    assert r["reason"] in ("DEFENSIVE_PAIR_CEILING",
                           "EXIT_NOT_BOUNDED_BETTER_THAN_HOLDING")


def test_the_campaign_loss_budget_binds_and_reports_what_is_left():
    r = call(defensive_spent_usd=D("1.95"))
    assert r["act"] is False
    assert r["reason"] == "DEFENSIVE_LOSS_BUDGET"
    assert D(r["budget_remaining_usd"]) == D("0.05")


def test_no_inventory_is_not_a_defensive_opportunity():
    assert call(unmatched_qty=0)["reason"] == "NO_UNMATCHED_INVENTORY"


def test_unreadable_input_refuses_and_is_never_read_as_zero():
    assert call(first_leg_cost="banana")["reason"] \
        == "DEFENSIVE_INPUT_UNREADABLE"
    assert call(unmatched_qty=None)["reason"] == "DEFENSIVE_INPUT_UNREADABLE"


# ------------------------------------------- the resting complement rule

def test_a_resting_complement_blocks_until_its_cancel_is_CONFIRMED():
    """Placing a defensive order while the profitable complement may still
    fill is how both fill and the position doubles instead of closing."""
    r = call(resting_complement={"venue_order_id": "D7HKMA6G10JQ",
                                 "cancel_confirmed": False})
    assert r["act"] is False
    assert r["reason"] == "RESTING_COMPLEMENT_NOT_CONFIRMED_CANCELLED"
    assert r["resting_complement"]["venue_order_id"] == "D7HKMA6G10JQ"


def test_a_confirmed_cancellation_releases_the_position():
    r = call(resting_complement={"venue_order_id": "D7HKMA6G10JQ",
                                 "cancel_confirmed": True})
    assert r["act"] is True


def test_a_missing_confirmation_flag_is_treated_as_not_confirmed():
    r = call(resting_complement={"venue_order_id": "D7HKMA6G10JQ"})
    assert r["act"] is False
    assert r["reason"] == "RESTING_COMPLEMENT_NOT_CONFIRMED_CANCELLED"
