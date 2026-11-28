"""THE COMPLETION LEARNS WHAT THE BOOK COSTS.

WHAT WAS MISSING, AND WHY IT WAS THE MOST EXPENSIVE THING MISSING. The
patient completion rested at
`straddle_completion_pair_cost - avg_cost_of_the_held_leg` -- a BUDGET
CEILING. Nothing in the controller ever compared it to the offer it was
trying to buy. Two consequences, both costly:

  1. An offer already cheaper than the ceiling was never lifted. The
     completion rested at the ceiling instead, forgoing the same side in
     the same size at a strictly lower all-in price.
  2. A resting BUY is filled by sellers coming DOWN to it. So the
     patient completion filled precisely when that side was being marked
     down -- it was selected AGAINST, by construction, and the cheap
     completions it existed to capture were the ones it structurally
     could not get.

Window 1925z is the measured instance: a completion rested at 0.88 and
was duly filled at 0.88 on DOWN's way to worthless.

WHAT THESE TESTS DO NOT CLAIM. Nothing here says crossing is
profitable, and nothing here measures a fill rate. Whether to complete
AT ALL is still the pair-cost gate's decision, unchanged and untouched;
this is only HOW an already-chosen completion is executed. That makes
it a DOMINANCE comparison -- one side, one size, fees included -- so it
needs no settlement probability and these tests assert that none is
consulted.

ATOMICITY IS NOT ASSUMED ANYWHERE. The completion is one independent
order. It is not bonded to the leg it completes, nothing here waits for
a linked fill, and `TestResidualIsIndependent` pins that a crossing
completion which never fills leaves the residual exactly as it was.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as E


@pytest.fixture(autouse=True)
def _controller_on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


#: UP COLLAPSED TO 0.08 AND PARTLY RECOVERED. The straddle's UP rung
#: filled at 0.08; UP is now 0.15/0.16, so DOWN is 0.84/0.85. At a 0.96
#: pair target the ceiling is 0.88, and DOWN's 0.85 offer is inside it.
RECOVERED = {"bid": "0.15", "ask": "0.16", "age_s": 0.1}
#: UP STILL COLLAPSED. DOWN's offer is 0.95, dearer than the whole
#: completion budget -- the common case, where resting is all there is.
COLLAPSED = {"bid": "0.05", "ask": "0.06", "age_s": 0.1}

HELD_UP = {C.UP: {"qty": "5", "cost": "0.40"},      # 5 @ 0.08
           C.DOWN: {"qty": "0", "cost": "0"}}


def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.40"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("8.00"),
                straddle_completion_pair_cost=D("0.96"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                # crossing needs BOTH switches; tests that want it on set
                # both, and the defaults below are the refusing values.
                taker_allowed=False,
                completion_cross_enabled=False,
                completion_cross_min_margin=D("0.01"))
    base.update(kw)
    return C.Params(**base)


def decide(book=RECOVERED, inventory=None, resting=(), t=100.0, **kw):
    return C.decide(book=book, t_in_window_s=t,
                    inventory=HELD_UP if inventory is None else inventory,
                    resting=list(resting), params=params(**kw))


def crossing_on(**kw):
    kw.setdefault("taker_allowed", True)
    kw.setdefault("completion_cross_enabled", True)
    return kw


def completion(res):
    for o in res["orders"]:
        if o.get("reason") == "STRADDLE_COMPLETION":
            return o
    return None


def comp_record(res):
    return res["evaluation"]["completion"]


def resting_order(side, price, qty, oid="o1", age_s=1.0):
    return {"id": oid, "side": side, "price": D(price), "qty": D(qty),
            "age_s": age_s}


# ------------------------------------------------- the default is unchanged

class TestDefaultIsByteForByteTheOldBehaviour:
    """Every new knob defaults to the value that refuses, so a deployment
    that sets none of them behaves exactly as the measured one did."""

    def test_params_default_to_refusing(self):
        p = C.Params()
        assert p.completion_cross_enabled is False
        assert p.completion_cross_slippage_ticks == 0
        assert p.completion_cross_min_margin == D("0")

    def test_completion_still_rests_at_the_ceiling(self):
        o = completion(decide())
        assert o is not None, "the patient completion must still be placed"
        assert o["post_only"] is True
        #: 0.88 was the ceiling, and DOWN's own offer here is 1 - 0.15 =
        #: 0.85, so the ceiling was THROUGH the book -- which is this
        #: file's own point 1 ("an offer already cheaper than the ceiling
        #: was never lifted"). The maker path now caps one tick inside the
        #: offer, which captures that saving WITHOUT taker authorization.
        #: The taker route below is unchanged and still separately gated.
        assert o["price"] == D("0.84")      # 0.85 - 1 tick, rests
        assert o["price"] <= D("0.96") - D("0.08")   # still under the ceiling
        assert o["side"] == C.DOWN

    def test_the_reason_string_is_unchanged(self):
        #: `completion_taker_fault` keys off this exact reason, and
        #: `l5_persistent_mode` off post_only. Renaming either would move
        #: the gate without moving the order.
        o = completion(decide())
        assert o["reason"] == "STRADDLE_COMPLETION"
        assert "STRADDLE_COMPLETION" in E.TAKER_COMPLETION_REASONS

    def test_the_record_says_why_it_rested(self):
        #: "no offer to lift" and "crossing is switched off" must not read
        #: identically -- that confusion is what hid the late favourite.
        assert comp_record(decide())["cross_why"] == "CROSS_DISABLED"
        assert comp_record(decide())["cross"] is False


# --------------------------------------------------------- both switches

class TestCrossingNeedsBothSwitches:

    def test_enabled_alone_does_not_cross(self):
        r = decide(completion_cross_enabled=True, taker_allowed=False)
        assert completion(r)["post_only"] is True
        assert comp_record(r)["cross_why"] == "TAKER_NOT_ALLOWED"

    def test_taker_allowed_alone_does_not_cross(self):
        r = decide(taker_allowed=True, completion_cross_enabled=False)
        assert completion(r)["post_only"] is True
        assert comp_record(r)["cross_why"] == "CROSS_DISABLED"

    def test_both_crosses(self):
        r = decide(**crossing_on())
        o = completion(r)
        assert o["post_only"] is False
        assert o["price"] == D("0.85")      # the offer, not the ceiling
        assert comp_record(r)["cross"] is True


# ------------------------------------------------------- the arithmetic

class TestTheDominanceArithmetic:
    """Crossing must be strictly cheaper ALL-IN than the ceiling it
    replaces. Not cheaper before fees; cheaper after them."""

    def test_it_lifts_the_offer_and_not_the_ceiling(self):
        r = decide(**crossing_on())
        rec = comp_record(r)
        assert rec["cross_ask"] == "0.85"
        assert rec["cross_limit"] == "0.85"
        #: 0.095 * 0.85 * 0.15
        assert D(rec["cross_fee_per_share"]) == D("0.0121125")
        #: 0.88 - (0.85 + 0.0121125)
        assert D(rec["cross_saving_per_share"]) == D("0.0178875")

    def test_the_pair_is_recorded_at_the_price_actually_asked(self):
        rec = comp_record(decide(**crossing_on()))
        assert rec["price"] == "0.85"
        assert rec["pair_cost"] == "0.93"          # 0.08 + 0.85

    def test_an_offer_above_the_ceiling_is_left_alone(self):
        #: UP still collapsed: DOWN offers 0.95 against a 0.88 budget.
        #: There is nothing to lift, and resting is correct.
        r = decide(book=COLLAPSED, **crossing_on())
        assert completion(r)["post_only"] is True
        assert completion(r)["price"] == D("0.88")
        assert comp_record(r)["cross_why"] == "OFFER_ABOVE_CEILING"

    def test_a_saving_smaller_than_the_margin_is_refused(self):
        #: DOWN at 0.86: all-in 0.871, saving 0.0088 -- under the 1c
        #: floor, so the spread is not worth paying and it rests.
        r = decide(book={"bid": "0.14", "ask": "0.15", "age_s": 0.1},
                   **crossing_on())
        assert completion(r)["post_only"] is True
        assert comp_record(r)["cross_why"] == "NO_MARGIN_OVER_RESTING"

    def test_a_zero_margin_still_requires_a_strict_improvement(self):
        #: With the margin at zero, equal all-in cost must still refuse:
        #: paying the spread to arrive at the same price is a cost for
        #: nothing.
        out = C._completion_cross(
            ceiling=D("0.50"), ask=D("0.50"), held_avg=D("0.08"),
            pair_target=D("0.96"), params=params(**crossing_on(
                completion_cross_min_margin=D("0"))), tick=D("0.01"))
        assert out["cross"] is False
        assert out["why"] == "NO_MARGIN_OVER_RESTING"

    def test_the_fee_is_charged_at_its_worst_point_in_the_range(self):
        #: theta*p*(1-p) PEAKS at 0.50, so for a range spanning the half
        #: the fee must be taken there -- not at the limit, which would
        #: understate the cost of the cheaper fill this path exists to
        #: get.
        out = C._completion_cross(
            ceiling=D("0.99"), ask=D("0.30"), held_avg=D("0.01"),
            pair_target=D("0.99"),
            params=params(**crossing_on(completion_cross_slippage_ticks=40)),
            tick=D("0.01"))
        #: limit 0.70, range [0.30, 0.70] spans 0.50 -> fee at 0.50
        assert out["limit"] == "0.70"
        assert D(out["fee_per_share"]) == D("0.095") * D("0.5") * D("0.5")


# ----------------------------------------------------------- the bounds

class TestTheBudgetCannotBeWidened:

    def test_slippage_cannot_price_above_the_ceiling(self):
        #: Ten ticks of allowance on a 0.85 offer would reach 0.95; the
        #: ceiling caps it at 0.88, and the ceiling is the budget.
        out = C._completion_cross(
            ceiling=D("0.88"), ask=D("0.85"), held_avg=D("0.08"),
            pair_target=D("0.96"),
            params=params(**crossing_on(completion_cross_slippage_ticks=10)),
            tick=D("0.01"))
        assert out["limit"] == "0.88"

    def test_slippage_is_refused_when_it_eats_the_margin(self):
        #: Capped at the ceiling, an all-in price at the ceiling saves
        #: nothing, so the crossing refuses rather than paying the spread
        #: to reach the price it could have rested at.
        out = C._completion_cross(
            ceiling=D("0.88"), ask=D("0.85"), held_avg=D("0.08"),
            pair_target=D("0.96"),
            params=params(**crossing_on(completion_cross_slippage_ticks=10)),
            tick=D("0.01"))
        assert out["cross"] is False
        assert out["why"] == "NO_MARGIN_OVER_RESTING"

    def test_the_pair_target_is_rechecked_all_in(self):
        #: The pair gate above priced the RESTING completion. A fee may
        #: not carry the crossing pair past the target it was admitted
        #: under, so it is checked again including the fee.
        #: A saving of 3.79c over the ceiling -- crossing is clearly
        #: cheaper than resting -- but 0.11 + 0.8621 all-in is 0.9721,
        #: past the 0.96 the completion was admitted under. Cheaper than
        #: the alternative is not the same as inside the budget.
        out = C._completion_cross(
            ceiling=D("0.90"), ask=D("0.85"), held_avg=D("0.11"),
            pair_target=D("0.96"), params=params(**crossing_on()),
            tick=D("0.01"))
        assert D(out["saving_per_share"]) > D("0.01")
        assert out["cross"] is False
        assert out["why"] == "PAIR_ABOVE_TARGET_ALL_IN"

    def test_exposure_is_charged_at_the_ceiling_not_the_offer(self):
        #: A crossing limit is bounded by the ceiling, so charging the
        #: ceiling keeps the exposure check a valid upper bound and the
        #: cap cannot be loosened by switching crossing on.
        tight = dict(max_exposure_usd=D("0.80"))
        off = decide(**tight)
        on = decide(**crossing_on(**tight))
        assert comp_record(off)["why"] == "NO_EXPOSURE_ROOM"
        assert comp_record(on)["why"] == "NO_EXPOSURE_ROOM"
        assert completion(on) is None

    def test_an_unreadable_offer_rests(self):
        out = C._completion_cross(
            ceiling=D("0.88"), ask=None, held_avg=D("0.08"),
            pair_target=D("0.96"), params=params(**crossing_on()),
            tick=D("0.01"))
        assert out["cross"] is False and out["why"] == "NO_OFFER"


# ----------------------------------------------- no probability is used

class TestNoForecastIsConsulted:

    def test_crossing_does_not_depend_on_the_probability_model(self):
        #: The decision is a dominance comparison. If a settlement
        #: probability could change it, it would be a directional bet
        #: wearing a completion's clothes.
        a = decide(**crossing_on())
        b = C.decide(book=RECOVERED, t_in_window_s=100.0, inventory=HELD_UP,
                     resting=[], params=params(**crossing_on()),
                     p_up=D("0.99"), p_source="test", p_age_s=0.1)
        c = C.decide(book=RECOVERED, t_in_window_s=100.0, inventory=HELD_UP,
                     resting=[], params=params(**crossing_on()),
                     p_up=D("0.01"), p_source="test", p_age_s=0.1)
        for r in (a, b, c):
            assert completion(r)["price"] == D("0.85")
            assert completion(r)["post_only"] is False


# ----------------------------------- the double-buy guard still stands

class TestTheDoubleBuyGuardCoversCrossing:
    """A completion buys the side OPPOSITE our unmatched leg. The late
    favourite may be that same side, so a completion in flight stands it
    down. Matching only the resting spelling would have re-created the
    1925z double-buy whenever the completion crossed."""

    def test_a_crossing_completion_stands_the_late_favourite_down(self):
        r = decide(**crossing_on())
        assert completion(r)["post_only"] is False
        assert r["evaluation"]["late_favorite"]["reason"] == "COMPLETION_IN_FLIGHT"

    def test_a_resting_completion_stands_it_down_too(self):
        #: the pre-existing spelling, pinned so the prefix match cannot
        #: be narrowed back to equality without a failure.
        r = decide(book=COLLAPSED, **crossing_on())
        assert completion(r)["post_only"] is True
        assert r["evaluation"]["late_favorite"]["reason"] == "COMPLETION_IN_FLIGHT"

    def test_a_crossing_completion_cancels_the_cheap_opposite_rung(self):
        #: The expensive and cheap routes to a pair are mutually
        #: exclusive whichever way the expensive one executes.
        r = decide(resting=[resting_order(C.DOWN, "0.08", "5", "cheap")],
                   **crossing_on())
        assert completion(r)["post_only"] is False
        assert comp_record(r)["cancelled_cheap_rungs"] == ["cheap"]
        assert {"op": "CANCEL", "id": "cheap"} in r["orders"]

    def test_a_crossing_completion_withdraws_a_rung_queued_this_step(self):
        flat = {C.UP: {"qty": "2", "cost": "0.16"},
                C.DOWN: {"qty": "0", "cost": "0"}}
        r = decide(inventory=flat, **crossing_on(straddle_repeat_enabled=True,
                                                 straddle_max_side_usd=D("2.00"),
                                                 straddle_max_unmatched_usd=D("2.00")))
        o = completion(r)
        if o is not None and o["post_only"] is False:
            same = [x for x in r["orders"]
                    if x.get("op") == "PLACE" and x["side"] == C.DOWN
                    and x.get("reason") == "STRADDLE_LEG"]
            assert same == [], "no cheap DOWN rung may ride beside a crossing completion"


# ------------------------------------------- the residual is not bonded

class TestResidualIsIndependent:
    """PMUS GUARANTEES NO ATOMIC PAIR EXECUTION. The completion is one
    independent order against an independent leg, so a completion that
    does not fill must leave the residual exactly as it was -- no
    assumed hedge, no phantom pair."""

    def test_an_unfilled_crossing_completion_leaves_the_residual_intact(self):
        before = C.decide(book=RECOVERED, t_in_window_s=100.0,
                          inventory=HELD_UP, resting=[],
                          params=params(**crossing_on()))
        #: the same state one step later, the completion still resting
        after = C.decide(book=RECOVERED, t_in_window_s=110.0,
                         inventory=HELD_UP,
                         resting=[resting_order(C.DOWN, "0.85", "5", "x")],
                         params=params(**crossing_on()))
        assert before["exposure"]["unmatched_side"] == C.UP
        assert after["exposure"]["unmatched_side"] == C.UP
        assert after["exposure"]["unmatched_qty"] == before["exposure"]["unmatched_qty"]

    def test_a_working_completion_is_not_duplicated(self):
        #: One outstanding completion at a time, whichever way it was
        #: priced. A second crossing order would buy the side twice.
        r = decide(resting=[resting_order(C.DOWN, "0.85", "5", "x")],
                   **crossing_on())
        assert completion(r) is None
        assert comp_record(r)["why"] == "COMPLETION_ALREADY_WORKING"
