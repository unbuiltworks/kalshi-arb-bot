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


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    """Every test below exercises the policy, so the flag is ON here. It is
    OFF on every deployment -- see test_the_flag_is_off_by_default."""
    monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, "1")


TAKER = D("0.026986850000")      # fee_floor from the live 2300z plan record
WIN_START = 0.0
CUTOFF = 599.0                   # completion cutoff at cancel margin 300

# far enough inside the guard to leave DEFENSIVE_MIN_EXECUTION_S for the
# cancel -> venue re-read -> submit sequence. See the execution-time tests.
BASE_T = CUTOFF - 30


def call(**kw):
    base = dict(
        now_t=BASE_T, window_start=WIN_START, complete_cutoff_s=CUTOFF,
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


# --------------------------------------------- the flag and the guards

def test_the_flag_is_off_by_default_and_fails_closed(monkeypatch):
    """Nothing but the exact enabling strings arms a policy that spends
    money, so a typo or a stale value cannot half-enable it."""
    monkeypatch.delenv(DC.DEFENSIVE_ENABLED_ENV, raising=False)
    assert DC.enabled() is False
    assert call()["reason"] == "DEFENSIVE_DISABLED"
    for bad in ("0", "no", "banana", "", "  "):
        monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, bad)
        assert DC.enabled() is False, bad
    for good in ("1", "yes", "TRUE", "on"):
        monkeypatch.setenv(DC.DEFENSIVE_ENABLED_ENV, good)
        assert DC.enabled() is True, good


def test_a_defensive_order_already_working_blocks_a_second():
    """ONE OUTSTANDING ORDER. A second could double the position if both
    fill."""
    r = call(defensive_pending=True)
    assert r["act"] is False
    assert r["reason"] == "DEFENSIVE_ALREADY_PENDING"


def test_a_settled_window_is_never_acted_on():
    """Settlement while an order is pending: the residual is already
    decided and the venue will complete nothing, so acting would invent a
    fill."""
    r = call(window_settled=True)
    assert r["act"] is False
    assert r["reason"] == "WINDOW_ALREADY_SETTLED"


def test_a_partial_fill_is_sized_to_what_remains_unmatched():
    """2315z filled 3.39 of 3.39. Had it filled 1.50, only 1.50 is exposed
    and only 1.50 may be defended."""
    full = call(unmatched_qty=D("3.39"))
    part = call(unmatched_qty=D("1.50"))
    assert part["act"] is True
    assert D(part["qty"]) == D("1.50")
    assert D(part["hold_downside_usd"]) < D(full["hold_downside_usd"])
    assert D(part["exit_downside_usd"]) < D(full["exit_downside_usd"])


# ------------------------------------- confirmed cancellation (deadman)

COID = "l5f-complement-abc"


def _dead(**buckets):
    base = {"reason": "DEFENSIVE", "cancelled": [], "errors": [],
            "unverified": [], "no_venue_id": [], "still_open": [],
            "filled": []}
    base.update(buckets)
    return base


def test_a_venue_rereaad_terminal_state_confirms():
    r = DC.cancel_confirmed(_dead(cancelled=[{"client_order_id": COID}]),
                            COID)
    assert r["confirmed"] is True
    assert r["reason"] == "CANCEL_CONFIRMED_BY_VENUE_REREAD"


def test_still_open_is_the_dangerous_case_and_never_confirms():
    """The cancel returned but the order is still working. A defensive
    order beside it is how BOTH fill and the position doubles."""
    r = DC.cancel_confirmed(_dead(still_open=[{"client_order_id": COID}]),
                            COID)
    assert r["confirmed"] is False
    assert r["reason"] == "CANCEL_NOT_CONFIRMED_STILL_OPEN"


def test_unverified_and_no_venue_id_are_treated_as_still_resting():
    for bucket in ("unverified", "no_venue_id"):
        r = DC.cancel_confirmed(_dead(**{bucket: [{"client_order_id": COID}]}),
                                COID)
        assert r["confirmed"] is False, bucket
        assert bucket.upper() in r["reason"]


def test_silence_about_our_order_is_not_confirmation():
    """Another order cancelled says nothing about ours."""
    r = DC.cancel_confirmed(
        _dead(cancelled=[{"client_order_id": "someone-else"}]), COID)
    assert r["confirmed"] is False
    assert r["reason"] == "CANCEL_NOT_REPORTED_FOR_THIS_ORDER"


def test_reported_errors_refuse():
    r = DC.cancel_confirmed(
        _dead(cancelled=[{"client_order_id": COID}],
              errors=[{"client_order_id": COID}]), COID)
    assert r["confirmed"] is False
    assert r["reason"] == "CANCEL_REPORTED_ERRORS"


def test_a_fill_booked_during_the_cancel_is_carried_out():
    """deadman books it with its cost basis; the defensive order must then
    be sized to what is LEFT, not to the pre-cancel unmatched."""
    r = DC.cancel_confirmed(
        _dead(cancelled=[{"client_order_id": COID}],
              filled=[{"client_order_id": COID, "qty": "1.25"}]), COID)
    assert r["confirmed"] is True
    assert r["filled_during_cancel"] == "1.25"


def test_an_unreadable_result_refuses():
    assert DC.cancel_confirmed(None, COID)["reason"] \
        == "CANCEL_RESULT_UNREADABLE"
    assert DC.cancel_confirmed(_dead(still_open="garbage"), COID)["confirmed"] \
        is False


# ================================ VERIFICATION 1: which clock actually binds

PRODUCTION_COMPLETE_CUTOFF = 720.0   # TS.complete_cutoff_s()
PRODUCTION_CANCEL_CUTOFF = 600.0     # cancel_cutoff(t_end) at margin 300


def test_the_earlier_of_the_two_window_clocks_binds():
    """`may_complete` refuses on whichever comes first, so this must too."""
    assert DC.binding_terminal_s(PRODUCTION_COMPLETE_CUTOFF,
                                 PRODUCTION_CANCEL_CUTOFF) == 600.0
    assert DC.binding_terminal_s(PRODUCTION_COMPLETE_CUTOFF) == 720.0
    assert DC.binding_terminal_s("banana", 600.0) is None
    assert DC.binding_terminal_s(None, None) is None


def test_the_originally_specified_guard_interval_is_outside_the_arm_window():
    """[T+660, T+720) was the interval asked for. Every second of it is
    PAST_TERMINAL at the deployed margin, so the guard must be shut there."""
    for t in (660.0, 700.0, 719.0):
        assert not DC.arm_window_open(
            t, WIN_START, PRODUCTION_COMPLETE_CUTOFF,
            cancel_cutoff_s=PRODUCTION_CANCEL_CUTOFF), t


def test_the_proposed_guard_interval_is_the_last_minute_before_the_terminal():
    """[T+540, T+600) -- derived from the clock, not chosen."""
    for t in (540.0, 570.0, 599.0):
        assert DC.arm_window_open(
            t, WIN_START, PRODUCTION_COMPLETE_CUTOFF,
            cancel_cutoff_s=PRODUCTION_CANCEL_CUTOFF), t
    for t in (539.0, 600.0):
        assert not DC.arm_window_open(
            t, WIN_START, PRODUCTION_COMPLETE_CUTOFF,
            cancel_cutoff_s=PRODUCTION_CANCEL_CUTOFF), t


def test_the_looser_clock_alone_cannot_arm_the_policy_in_dead_time():
    """The defect this guards: handed 720 only, the guard would arm at T+660
    and submit into PAST_TERMINAL. With both clocks it refuses."""
    r = call(now_t=700.0, complete_cutoff_s=PRODUCTION_COMPLETE_CUTOFF,
             cancel_cutoff_s=PRODUCTION_CANCEL_CUTOFF)
    assert r["act"] is False
    assert r["reason"] == "OUTSIDE_DEFENSIVE_ARM_WINDOW"
    assert r["binding_terminal_s"] == 600.0


def test_a_defensive_sequence_is_not_started_without_time_to_finish_it():
    """Cancel, venue re-read, submit. Starting that with seconds left would
    retire the profitable complement and put nothing in its place."""
    r = call(now_t=CUTOFF - 5)
    assert r["act"] is False
    assert r["reason"] == "INSUFFICIENT_EXECUTION_TIME"
    assert r["remaining_s"] == 5.0
    assert r["min_execution_s"] == DC.DEFENSIVE_MIN_EXECUTION_S
    # and the boundary is the floor itself, not a round number near it
    assert call(now_t=CUTOFF - DC.DEFENSIVE_MIN_EXECUTION_S)["act"] is True


# ============== VERIFICATION 2: one wire order, and the real alternatives

def test_the_unwind_is_one_wire_order_named_in_the_venue_s_own_enums():
    r = call(buy_side="DOWN")
    assert r["wire"]["intent"] == "ORDER_INTENT_BUY_SHORT"
    assert r["wire"]["side"] == "ORDER_SIDE_SELL"
    assert r["wire"]["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert r["wire"]["own_limit_price"] == "0.80"
    assert call(buy_side="UP")["wire"]["intent"] == "ORDER_INTENT_BUY_LONG"
    # an unreadable side stamps no wire rather than guessing one
    assert "wire" not in call(buy_side="sideways")


def test_the_proceeds_view_and_the_pair_cost_view_are_the_same_number():
    """Our ledger says "pair cost 1.17"; the venue credits 0.20 a share and
    flattens. Both must be reported and they must agree."""
    r = call(first_leg_cost=D("0.37"), exit_price=D("0.80"))
    assert r["pair_cost"] == "1.17"
    assert r["sale_proceeds_per_contract"] == "0.20"
    assert D(r["loss_per_contract"]) == D("0.17")
    assert D(r["first_leg_cost"]) - D(r["sale_proceeds_per_contract"]) \
        == D(r["loss_per_contract"])
    # the 1.20 ceiling, stated as what it actually caps
    assert D(r["ceiling_as_loss_per_contract"]) == D("0.20")


def test_priority_one_is_the_profitable_completion_while_it_is_reachable():
    """A maker complement at 0.55 against a 0.37 leg is a 0.92 pair -- a
    PROFIT -- and at or inside the executable 0.80 it can fill now. Paying
    a taker fee to unwind instead would be strictly worse."""
    r = call(maker_complement_price=D("0.55"), exit_price=D("0.50"))
    assert r["act"] is False
    assert r["reason"] == "MAKER_COMPLETION_STILL_REACHABLE"
    assert r["maker_pair_cost"] == "0.92"
    assert r["maker_reachable_now"] is True


def test_an_unreachable_maker_completion_does_not_block_the_unwind():
    """The 2315z shape: our 0.55 limit sits far inside a book whose
    executable price is 0.80. It cannot fill without the market coming to
    us, and there are seconds left for it to."""
    r = call(maker_complement_price=D("0.55"), exit_price=D("0.80"))
    assert r["maker_reachable_now"] is False
    assert r["act"] is True


def test_a_maker_completion_above_the_096_ceiling_never_blocks_the_unwind():
    r = call(maker_complement_price=D("0.70"), exit_price=D("0.60"))
    assert D(r["maker_pair_cost"]) > DC.MAKER_COMPLETE_CEILING
    assert r["act"] is True


def test_the_alternatives_are_priced_side_by_side_in_dollars():
    r = call(maker_complement_price=D("0.55"), exit_price=D("0.80"))
    by = {a["action"]: a for a in r["alternatives"]}
    assert set(by) == {"HOLD_TO_SETTLEMENT", "MAKER_COMPLETE_AT_CEILING",
                       "DEFENSIVE_UNWIND"}
    # holding risks the whole leg; the unwind is certain and smaller
    assert D(by["HOLD_TO_SETTLEMENT"]["worst_usd"]) \
        < D(by["DEFENSIVE_UNWIND"]["worst_usd"]) < 0
    assert by["DEFENSIVE_UNWIND"]["certain"] is True
    assert by["HOLD_TO_SETTLEMENT"]["certain"] is False
    # and the maker completion, were it reachable, beats both
    assert D(by["MAKER_COMPLETE_AT_CEILING"]["worst_usd"]) > 0
    assert by["MAKER_COMPLETE_AT_CEILING"]["executable_now"] is False


def test_the_unwind_row_prices_the_fee_it_pays():
    r = call(exit_price=D("0.80"))
    unwind = [a for a in r["alternatives"]
              if a["action"] == "DEFENSIVE_UNWIND"][0]
    # 3.39 * (1 - 1.17) - 3.39 * fee
    assert D(unwind["worst_usd"]) == pytest.approx(D("-0.6678"), abs=1e-3)
    assert D(unwind["fees_usd"]) == D("3.39") * TAKER
    assert unwind["worst_usd"] == unwind["best_usd"]


def test_a_cancel_error_the_venue_reread_settled_does_not_refuse():
    """`deadman` records a raised cancel even when the re-read came back
    terminal, which is the common case: cancelling an order the venue has
    already retired raises. Refusing on that would make the policy unusable
    in the one situation it exists for."""
    r = DC.cancel_confirmed(
        _dead(cancelled=[{"client_order_id": COID}],
              errors=[{"client_order_id": COID,
                       "resolved_by_reread": True}]), COID)
    assert r["confirmed"] is True


def test_another_order_s_error_says_nothing_about_ours():
    r = DC.cancel_confirmed(
        _dead(cancelled=[{"client_order_id": COID}],
              errors=[{"client_order_id": "someone-else"}]), COID)
    assert r["confirmed"] is True
