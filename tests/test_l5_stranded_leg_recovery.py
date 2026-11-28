"""Stranded-leg recovery: the dwell gate, and the actions that are dominated.

THE DEFECT THIS FIXES, measured in production on 2026-10-23. `DEFENSIVE_ARM_S`
(60s) less `DEFENSIVE_MIN_EXECUTION_S` (15s) leaves a 45-SECOND actionable
interval, [T+540, T+585) at the deployed margin. The executor's evaluation
cadence is irregular: gaps of 32s, 63s and 131s were all observed inside one
window. So whether the exit fires is decided by tick alignment:

    0230z  T+512 (outside) -> T+544 (INSIDE)  -> exited, realized -0.0468
    0300z  T+524 (outside) -> T+587 (too late) -> held,   realized -0.3580

0300z straddled the entire interval by 16 seconds. A trigger narrower than its
own sampling jitter fires by luck, and the 7.65x difference in realized loss
between two otherwise identical sequences is attributable to that, not to the
market.

WHAT THE HELD-OUT EVIDENCE SAYS, 555 fifteen-minute windows, lookahead-free
(side, size and cost basis read from the as-of-tick cumulative state; only the
settlement outcome comes from the end of the window):

    policy                     mean      p05    < -0.30/unit
    HOLD always             -0.0570  -0.5740        29.5%
    EXISTING arm window     -0.0699  -0.5007        20.0%
    DWELL 30s               -0.0407  -0.1470         1.6%

The MEANS ARE NOT SEPARABLE -- ARM(G) minus EXISTING is +0.0171 at t=+1.42, and
this module must never be sold as edge. The TAIL is the claim: 9 windows below
-0.30/unit instead of 111, which on n=555 at p=0.20 is z=-10.8.

NOT AN EV IMPROVEMENT, AND THE ARITHMETIC SAYS WHY. From the current state the
first leg's cost is sunk, so holding is worth q*(1-px) and exiting at the touch
is worth q*(1-px) - fee: identical minus the fee, at every price, which is what
`improves_expected_value: False` has always asserted. Crossing buys variance,
never expectation.

No order is submitted, no credential is used, no socket is opened.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_defensive_completion as DEF

#: the deployed geometry: 900s window, 300s cancel margin -> terminal T+600
W0 = 1000.0
TERMINAL = 600.0
CUTOFF = 720.0
CANCEL = 600.0

#: the 0230z position: DOWN 1.17 at own 0.07, complement executable at 0.97
BASE = dict(window_start=W0, complete_cutoff_s=CUTOFF, cancel_cutoff_s=CANCEL,
            unmatched_qty=D("1.17"), first_leg_cost=D("0.07"),
            exit_price=D("0.97"), displayed_depth=D("8012"),
            taker_fee_per_contract=D("0.002023"), buy_side="UP",
            simulate=True)


def at(off, **kw):
    return DEF.decide(now_t=W0 + off, **dict(BASE, **kw))


class TestTheArmWindowIsTooNarrowToBeSampledReliably:

    def test_the_actionable_interval_is_only_45_seconds(self):
        lo = TERMINAL - DEF.DEFENSIVE_ARM_S
        hi = TERMINAL - DEF.DEFENSIVE_MIN_EXECUTION_S
        assert (lo, hi) == (540.0, 585.0)
        assert hi - lo == 45.0

    @pytest.mark.parametrize("off,inside", [
        (512, False), (524, False), (544, True), (584, True), (599, True)])
    def test_the_raw_arm_window_is_the_last_sixty_seconds(self, off, inside):
        assert DEF.arm_window_open(
            W0 + off, W0, CUTOFF, cancel_cutoff_s=CANCEL) is inside

    @pytest.mark.parametrize("off,reason", [
        (512, "OUTSIDE_DEFENSIVE_ARM_WINDOW"),
        (524, "OUTSIDE_DEFENSIVE_ARM_WINDOW"),
        (544, None),                            # 0230z: acted
        (584, None),
        (587, "INSUFFICIENT_EXECUTION_TIME")])  # 0300z: two seconds too late
    def test_the_two_production_windows_land_where_they_did(self, off, reason):
        """0230z ticked at T+544 and acted; 0300z stepped T+524 -> T+587 and
        got exactly the refusal production recorded. The actionable interval
        is the INTERSECTION of the arm window and the execution-time floor,
        which is why it is 45s wide and not 60s."""
        out = at(off)
        if reason is None:
            assert out["act"] is True, (off, out["reason"])
        else:
            assert out["act"] is False and out["reason"] == reason

    def test_0300z_straddled_the_interval_by_sixteen_seconds(self):
        assert 524 < 540 and 587 > 585
        assert 587 - 585 == 2          # and missed the close by two seconds


class TestTheDwellGateIsContinuousToTheTerminal:

    def test_within_the_dwell_the_complement_keeps_the_position(self):
        out = at(320, stranded_since_t=W0 + 300)
        assert out["act"] is False
        assert out["reason"] == "COMPLEMENT_STILL_WITHIN_DWELL"
        assert out["timing_gate"] == "DWELL"
        assert out["stranded_for_s"] == 20.0

    def test_after_the_dwell_it_acts(self):
        out = at(335, stranded_since_t=W0 + 300)
        assert out["act"] is True
        assert out["reason"] == "DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"
        assert out["timing_gate"] == "DWELL"

    @pytest.mark.parametrize("off", [335, 400, 480, 524, 544, 580])
    def test_it_is_open_at_every_offset_the_arm_window_missed(self, off):
        """The whole point: 524 and 587-16 are no longer dead zones."""
        out = at(off, stranded_since_t=W0 + 300)
        assert out["act"] is True, (off, out["reason"])

    def test_it_still_closes_for_insufficient_execution_time(self):
        out = at(590, stranded_since_t=W0 + 300)
        assert out["act"] is False
        assert out["reason"] == "INSUFFICIENT_EXECUTION_TIME"

    def test_a_negative_dwell_is_implausible_not_permissive(self):
        out = at(300, stranded_since_t=W0 + 400)
        assert out["act"] is False
        assert out["reason"] == "DEFENSIVE_INPUT_IMPLAUSIBLE"

    def test_an_unreadable_stranding_time_refuses(self):
        out = at(400, stranded_since_t="not-a-time")
        assert out["act"] is False
        assert out["reason"] == "DEFENSIVE_INPUT_UNREADABLE"


class TestNoCallerChangesBehaviourByUpgrading:

    def test_omitting_the_stranding_time_keeps_the_arm_window(self):
        assert at(400)["reason"] == "OUTSIDE_DEFENSIVE_ARM_WINDOW"
        assert at(400).get("timing_gate") is None

    def test_and_still_acts_inside_it(self):
        out = at(544)
        assert out["act"] is True and out["timing_gate"] == "ARM_WINDOW"


class TestEveryEconomicGateStillBinds:

    def test_priority_one_is_still_untouchable(self):
        """A profitable maker completion is never pre-empted, dwell or not."""
        out = at(400, stranded_since_t=W0 + 300,
                 maker_complement_price=D("0.88"), exit_price=D("0.80"))
        assert out["act"] is False
        assert out["reason"] == "MAKER_COMPLETION_STILL_REACHABLE"

    def test_an_unconfirmed_cancel_still_blocks(self):
        out = at(400, stranded_since_t=W0 + 300,
                 resting_complement={"venue_order_id": "X",
                                     "cancel_confirmed": False})
        assert out["act"] is False
        assert out["reason"] == "RESTING_COMPLEMENT_NOT_CONFIRMED_CANCELLED"

    def test_the_pair_ceiling_still_binds(self):
        out = at(400, stranded_since_t=W0 + 300, first_leg_cost=D("0.50"),
                 exit_price=D("0.80"))
        assert out["act"] is False
        assert out["reason"] == "DEFENSIVE_PAIR_CEILING"

    def test_the_loss_budget_still_binds(self):
        out = at(400, stranded_since_t=W0 + 300,
                 defensive_spent_usd=DEF.DEFENSIVE_LOSS_BUDGET_USD)
        assert out["act"] is False
        assert out["reason"] == "DEFENSIVE_LOSS_BUDGET"

    def test_depth_is_still_required(self):
        out = at(400, stranded_since_t=W0 + 300, displayed_depth=D("0.5"))
        assert out["act"] is False
        assert out["reason"] == "INSUFFICIENT_DISPLAYED_DEPTH"

    def test_a_pending_defensive_order_still_owns_the_slot(self):
        out = at(400, stranded_since_t=W0 + 300, defensive_pending=True)
        assert out["reason"] == "DEFENSIVE_ALREADY_PENDING"

    def test_it_is_still_never_sold_as_edge(self):
        for off in (400, 544):
            assert at(off, stranded_since_t=W0 + 300)[
                "improves_expected_value"] is False


class TestTheGatesAreAStopLossOnMoneyness:
    """Solved for `o`, our side's own price, the two gates are a BAND.

        priority one reachable   c + (1-o) <= 0.96  <=>  o >= c + 0.04
        pair ceiling             c + (1-o) <= 1.20  <=>  o >= c - 0.20

    So the exit is permitted only while  c - 0.20 <= o < c + 0.04: the 1.20
    pair ceiling IS a -0.20/unit stop. The arm window is what kept it from
    being reachable in time, which is the whole defect.
    """

    def test_above_the_band_the_complement_is_kept(self):
        c = D("0.40")
        out = at(400, stranded_since_t=W0 + 300, first_leg_cost=c,
                 maker_complement_price=D("0.55"), exit_price=D("0.55"))
        assert out["reason"] == "MAKER_COMPLETION_STILL_REACHABLE"
        assert c + D("0.55") <= DEF.MAKER_COMPLETE_CEILING

    def test_inside_the_band_it_exits(self):
        out = at(400, stranded_since_t=W0 + 300, first_leg_cost=D("0.40"),
                 exit_price=D("0.70"))
        assert out["act"] is True
        assert D(out["pair_cost"]) == D("1.10")

    def test_below_the_band_the_ceiling_refuses(self):
        out = at(400, stranded_since_t=W0 + 300, first_leg_cost=D("0.40"),
                 exit_price=D("0.85"))
        assert out["reason"] == "DEFENSIVE_PAIR_CEILING"
        assert D("0.40") + D("0.85") > DEF.DEFENSIVE_PAIR_CEILING

    def test_the_ceiling_expressed_as_a_loss_is_twenty_cents(self):
        out = at(400, stranded_since_t=W0 + 300, first_leg_cost=D("0.40"),
                 exit_price=D("0.70"))
        assert D(out["ceiling_as_loss_per_contract"]) == D("0.20")


class TestRepricingTheComplementCloserToTouchIsDominated:
    """THE NEGATIVE RESULT, pinned so action 2 is not re-proposed.

    A limit sell of the held leg ABOVE the market fills only when the market
    rises to it -- which is when our side was going to win anyway. It sells the
    winners and keeps the losers, so it is adversely selected in the opposite
    direction from the entry fill.

    Held-out, 15-minute windows, decision at T+600 (n=538):

        action                      mean      p05   fill
        A5  hold                 -0.0104  -0.5960      -
        A3/A4 market sell now    -0.0385  -0.3245      -
        A1/A2 limit sell +0t     -0.0461  -0.5007  64.5%
        A1/A2 limit sell +1t     -0.0440  -0.5141  59.9%
        A1/A2 limit sell +2t     -0.0439  -0.5162  54.8%
        A1/A2 limit sell +3t     -0.0419  -0.5162  51.5%

    It is worse than holding on the mean AND keeps holding's tail: it buys
    nothing at either end. Repricing is therefore not implemented, and
    `decide()` offers no reprice action.
    """

    HELD_OUT = {"hold": (D("-0.0104"), D("-0.5960")),
                "cross": (D("-0.0385"), D("-0.3245")),
                "limit_0t": (D("-0.0461"), D("-0.5007")),
                "limit_1t": (D("-0.0440"), D("-0.5141")),
                "limit_2t": (D("-0.0439"), D("-0.5162")),
                "limit_3t": (D("-0.0419"), D("-0.5162"))}

    @pytest.mark.parametrize("k", ["limit_0t", "limit_1t", "limit_2t", "limit_3t"])
    def test_every_reprice_is_worse_than_holding_on_the_mean(self, k):
        assert self.HELD_OUT[k][0] < self.HELD_OUT["hold"][0]

    @pytest.mark.parametrize("k", ["limit_0t", "limit_1t", "limit_2t", "limit_3t"])
    def test_and_buys_no_tail_protection(self, k):
        """Only crossing compresses the tail; every reprice keeps hold's."""
        assert self.HELD_OUT[k][1] < self.HELD_OUT["cross"][1]

    def test_crossing_is_the_only_action_that_moves_the_tail(self):
        assert self.HELD_OUT["cross"][1] > self.HELD_OUT["hold"][1]

    def test_the_module_exposes_no_reprice_action(self):
        out = at(400, stranded_since_t=W0 + 300,
                 maker_complement_price=D("0.94"))
        actions = {a["action"] for a in out["alternatives"]}
        assert actions == {"HOLD_TO_SETTLEMENT", "MAKER_COMPLETE_AT_CEILING",
                           "DEFENSIVE_UNWIND"}
        assert not any("REPRICE" in a for a in actions)
        # and the three are the five actions after 3 and 4 collapse
        assert len(actions) == 3


class TestBuyingTheComplementAndSellingTheLegAreOneOrder:
    """Actions 3 and 4 are not two choices on this venue, so the five actions
    reduce to three orders and a do-nothing. Verification 2 of the module."""

    def test_the_wire_order_is_the_same_for_both_readings(self):
        out = at(400, stranded_since_t=W0 + 300)
        assert out["wire"]["intent"] == "ORDER_INTENT_BUY_LONG"
        assert out["wire"]["outcomeSide"] == "OUTCOME_SIDE_YES"
        assert "buying the complement of a held leg IS selling that leg" \
            in out["venue_action"]

    def test_proceeds_and_pair_cost_are_the_same_arithmetic(self):
        out = at(400, stranded_since_t=W0 + 300)
        proceeds = D(out["sale_proceeds_per_contract"])
        loss = D(out["loss_per_contract"])
        assert proceeds == D(1) - D(out["exit_price"])
        assert loss == D(out["pair_cost"]) - D(1)
        assert proceeds + loss == D(out["first_leg_cost"])


class TestTheNewGateIsAStrictSupersetOfTheOld:
    """The change may only ADD chances to act. A position the arm window would
    have exited must still be exited, so shipping this cannot leave anything
    carrying that was previously flattened."""

    @pytest.mark.parametrize("off", [540, 544, 560, 580, 584])
    def test_anything_the_arm_window_allowed_is_still_allowed(self, off):
        """Even with the dwell unsatisfied -- stranded one second ago."""
        out = at(off, stranded_since_t=W0 + off - 1)
        assert out["stranded_for_s"] == 1.0
        assert out["act"] is True, (off, out["reason"])

    @pytest.mark.parametrize("off", [335, 400, 480, 524])
    def test_and_the_dwell_adds_the_interval_it_could_not_reach(self, off):
        assert DEF.arm_window_open(
            W0 + off, W0, CUTOFF, cancel_cutoff_s=CANCEL) is False
        assert at(off, stranded_since_t=W0 + 300)["act"] is True

    def test_no_offset_is_refused_by_both_where_one_would_allow(self):
        for off in range(100, 600, 7):
            armed = DEF.arm_window_open(W0 + off, W0, CUTOFF,
                                        cancel_cutoff_s=CANCEL)
            new = at(off, stranded_since_t=W0 + off - 1)
            if armed and off < 585:
                assert new["act"] is True, off

    def test_dead_time_past_the_terminal_still_refuses(self):
        """The dwell does not reopen a window the engine cannot act in."""
        for off in (590, 600, 660, 700):
            out = at(off, stranded_since_t=W0 + 300)
            assert out["act"] is False, off
            assert out["reason"] == "INSUFFICIENT_EXECUTION_TIME"
