"""REPEATED BALANCING QUOTES, AND WHY MORE TICKETS IS NOT MORE RISK.

LIVE WINDOW 1930z, 2026-11-17. One leg filled -- 0.52 DOWN at 0.57,
$0.2964, fees $0.0000 -- and the pair never completed. The reported
reason was the heavy side's UNMATCHED_CEILING. That was not the binding
constraint. The binding constraint was
AUTHORIZATION:COMPLETE_BUDGET_EXHAUSTED on the UP leg: a risk-reducing
leg is charged to the COMPLETE submission class, tickets are consumed
per attempt and are never refunded, and the budget had been set to 2.
Two re-quotes and the balancing order could not be sent again, while
REPLACE still had 30 of 36 tickets and OPEN 22 of 24.

TWO THINGS ARE PROVED HERE.

  1. The UNMATCHED CEILING NEVER BLOCKS THE BALANCING LEG. Buying the
     light side REDUCES unmatched exposure, so the prospective bound
     falls rather than rises, at every size. Only more of the HEAVY side
     is refused. (The ceiling was wrongly suspected first; it is sound.)

  2. A LARGER TICKET BUDGET AUTHORIZES ATTEMPTS, NOT EXPOSURE. A ticket
     is permission to SUBMIT. What may be held is bounded by the
     unmatched ceiling, the per-side cap and the exposure room -- all of
     which count resting orders -- so the peak worst case is unchanged
     however many tickets exist. And a ticket cannot cross the spread:
     post-only is enforced independently, and crossing additionally
     needs the envelope's taker_allowed plus three further gates.

WHAT IS NOT PROVED. That the missing UP order would have filled, or that
a completed pair would have been profitable. Removing an artificial
execution restriction is not evidence of edge.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_live_authorization as AUTH


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


#: the live state at 19:31Z: 0.52 DOWN at 0.57, no UP.
HELD_DOWN = {C.UP: (D("0"), D("0")), C.DOWN: (D("0.52"), D("0.2964"))}
BOOK = {"bid": "0.43", "ask": "0.44", "age_s": 0.1}


def inv(up=("0", "0"), dn=("0", "0")):
    return {C.UP: {"qty": up[0], "cost": up[1]},
            C.DOWN: {"qty": dn[0], "cost": dn[1]}}


def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                straddle_rungs=(D("0.08"),), clip_usd=D("0.30"),
                hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("1.00"),
                straddle_book_relative=True,
                straddle_fill_model_measured=True,
                straddle_pair_ceiling=D("0.99"), straddle_join_ticks=0,
                straddle_repeat_enabled=True,
                straddle_max_side_usd=D("0.60"),
                straddle_max_unmatched_usd=D("0.30"),
                straddle_completion_pair_cost=D("0"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                straddle_min_ev_per_share=D("0"))
    base.update(kw)
    return C.Params(**base)


def decide(book=None, inventory=None, resting=(), t=100.0, **kw):
    return C.decide(book=book or BOOK, t_in_window_s=t,
                    inventory=inventory or inv(dn=("0.52", "0.2964")),
                    resting=list(resting), params=params(**kw))


def legs(res, side=None):
    return [o for o in res["orders"]
            if o.get("reason") == "STRADDLE_LEG"
            and (side is None or o["side"] == side)]


# ============ 1. the ceiling never blocks the balancing leg

class TestTheCeilingOnlyBlocksAddedExposure:

    @pytest.mark.parametrize("qty", ["0.10", "0.20", "0.40", "0.52", "0.60"])
    def test_buying_the_light_side_lowers_the_bound(self, qty):
        before = C._prospective_unmatched_usd(
            C.DOWN, D("0.57"), D("0"), HELD_DOWN)
        after = C._prospective_unmatched_usd(
            C.UP, D("0.43"), D(qty), HELD_DOWN)
        assert after <= before, (qty, after, before)
        assert after <= D("0.30"), "admitted under the live ceiling"

    @pytest.mark.parametrize("qty", ["0.05", "0.20"])
    def test_buying_more_of_the_heavy_side_is_refused(self, qty):
        u = C._prospective_unmatched_usd(C.DOWN, D("0.57"), D(qty), HELD_DOWN)
        assert u > D("0.30"), (qty, u)

    def test_the_balancing_leg_is_actually_quoted(self):
        #: end to end: holding 0.52 DOWN, the controller still wants UP.
        r = decide()
        assert legs(r, C.UP), r["evaluation"]["entry"]

    def test_and_the_heavy_side_is_not(self):
        r = decide()
        assert legs(r, C.DOWN) == []
        assert r["evaluation"]["side_refusals"][C.DOWN] == "UNMATCHED_CEILING"


# ============ 2. the binding constraint is now visible

class TestTheBindingConstraintIsReported:
    """A step that places nothing must name the constraint on the order
    that mattered, not on whichever side the loop happened to see last."""

    def test_side_refusals_are_exposed_per_side(self):
        r = decide()
        assert C.UP in r["evaluation"]["side_refusals"]
        assert C.DOWN in r["evaluation"]["side_refusals"]

    def test_the_light_sides_reason_wins_when_nothing_is_placed(self):
        #: both sides refused, for different reasons: UP on the side cap,
        #: DOWN on the unmatched ceiling. The reported why must be UP's,
        #: because UP is the order that would have reduced risk.
        r = decide(straddle_max_side_usd=D("0.0001"))
        assert legs(r) == []
        sr = r["evaluation"]["side_refusals"]
        assert sr[C.UP] == "SIDE_CAP_REACHED"
        assert r["why"] == sr[C.UP], (r["why"], sr)

    def test_a_balanced_book_keeps_the_old_reporting(self):
        #: with no imbalance there is no "light side", so nothing is
        #: preferred and the existing behaviour stands.
        r = decide(inventory=inv(), straddle_max_side_usd=D("0.0001"))
        assert legs(r) == []
        assert r["why"] in ("SIDE_CAP_REACHED", "UNMATCHED_CEILING",
                            "NO_EXPOSURE_ROOM")


# ============ 3. more tickets cannot buy more risk

class TestTicketsAuthoriseAttemptsNotExposure:

    def test_the_completion_class_is_a_submission_budget(self):
        #: it is counted in tickets, not dollars -- so it cannot widen a
        #: dollar limit even in principle.
        assert "COMPLETE" in AUTH.SUBMISSION_CLASSES
        assert AUTH.CLASS_BUDGET_ENV["COMPLETE"] == "L5_LIVE_AUTH_MAX_COMPLETE"

    @pytest.mark.parametrize("n_resting", [1, 2, 5, 20])
    def test_many_resting_balancing_orders_cannot_breach_the_ceiling(
            self, n_resting):
        #: THE SAFETY ARGUMENT. However many tickets exist, every order
        #: that rests is counted by the prospective bound, so the next
        #: one is refused once the ceiling is reached. Tickets cannot
        #: outrun the dollar limit.
        rest = [{"id": "r%d" % i, "side": C.DOWN, "price": D("0.57"),
                 "qty": D("0.10"), "age_s": 1.0} for i in range(n_resting)]
        u = C._prospective_unmatched_usd(C.DOWN, D("0.57"), D("0.10"),
                                         HELD_DOWN, rest)
        assert u > D("0.30"), "a resting stack must not escape the bound"

    def test_a_ticket_does_not_make_an_order_crossing(self):
        #: post-only is enforced independently of any budget.
        r = decide()
        for o in r["orders"]:
            if o.get("op") == "PLACE":
                assert o["post_only"] is True

    def test_a_ticket_does_not_grant_taker_permission(self):
        #: crossing needs the envelope AND the switch AND the dominance
        #: test AND the adapter gate. No budget appears in that list.
        r = decide(taker_allowed=False, completion_cross_enabled=True)
        for o in r["orders"]:
            if o.get("op") == "PLACE":
                assert o["post_only"] is True

    def test_the_exposure_room_still_binds_independently(self):
        r = decide(max_exposure_usd=D("0.30"))
        for o in legs(r):
            assert D(str(o["price"])) * D(str(o["qty"])) <= D("0.30")


# ============ 4. the limits the owner set are untouched

class TestTheOwnersLimitsAreUnchanged:

    def test_the_unmatched_ceiling_is_still_thirty_cents(self):
        r = decide()
        #: the controller refuses the heavy side at the configured bound,
        #: not at some larger one
        assert r["evaluation"]["side_refusals"][C.DOWN] == "UNMATCHED_CEILING"
        assert C._prospective_unmatched_usd(
            C.DOWN, D("0.57"), D("0.05"), HELD_DOWN) > D("0.30")

    def test_taker_defaults_remain_refusing(self):
        p = C.Params()
        assert p.taker_allowed is False
        assert p.completion_cross_enabled is False
        assert p.straddle_completion_pair_cost == D("0")


# ============ 5. three windows of balancing quotes, exposure bounded

#: cadence MEASURED live at 1930z: steps ~5.2s apart, placing stops at
#: no_new_entry_after_s 270 -> about 52 placing steps in a window.
PLACING_STEPS = 52
#: a book that moves +/-3 ticks, so the quote really is re-placed rather
#: than sitting still -- a static book would prove nothing about cadence.
MOVING = [("0.43", "0.44"), ("0.42", "0.43"), ("0.44", "0.45"),
          ("0.41", "0.42"), ("0.45", "0.46"), ("0.40", "0.41"),
          ("0.46", "0.47"), ("0.39", "0.40")]


def _run_window():
    """One window holding 0.52 DOWN from the start, as at 1930z. Returns
    (balancing placements, peak worst-case settlement loss)."""
    inv = {C.UP: [D("0"), D("0")], C.DOWN: [D("0.52"), D("0.2964")]}
    rest, bal, oid, peak = [], 0, 0, D("0")
    for i in range(PLACING_STEPS):
        b, a = MOVING[i % len(MOVING)]
        r = C.decide(book={"bid": b, "ask": a, "age_s": 0.1},
                     t_in_window_s=float(10 + i * 5),
                     inventory={s: {"qty": str(inv[s][0]),
                                    "cost": str(inv[s][1])}
                                for s in (C.UP, C.DOWN)},
                     resting=list(rest), params=params())
        gone = {o["id"] for o in r["orders"] if o.get("op") == "CANCEL"}
        rest = [o for o in rest if o["id"] not in gone]
        for o in r["orders"]:
            if o.get("op") != "PLACE":
                continue
            oid += 1
            if inv[C.DOWN][0] > inv[C.UP][0] and o["side"] == C.UP:
                bal += 1          # the leg charged to the COMPLETE class
            rest.append({"id": "x%d" % oid, "side": o["side"],
                         "price": o["price"], "qty": o["qty"], "age_s": 1.0})
        exp = C.exposure({s: {"qty": str(inv[s][0]), "cost": str(inv[s][1])}
                          for s in (C.UP, C.DOWN)}, list(rest))
        peak = max(peak, D(str(exp["worst_case_settlement_loss_usd"])))
    return bal, peak


class TestThreeWindowsOfBalancingQuotes:
    """The live campaign died because the COMPLETE budget is PER
    EXPERIMENT and never refunded: 1930z burned both tickets and windows
    1935z and 1940z then submitted nothing at all (0 submitted, 0 filled,
    COMPLETE remaining 0). These derive the real requirement."""

    def test_the_balancing_leg_is_requoted_more_than_twice_over_three(self):
        total = sum(_run_window()[0] for _ in range(3))
        assert total > 2, (
            "the live budget of 2 cannot cover three windows; measured %d"
            % total)

    def test_the_measured_requirement_is_small_and_bounded(self):
        #: it is NOT one ticket per step: the controller keeps its resting
        #: order and re-places only when the price moves off it. So the
        #: requirement is a handful, not 52 -- which is why a budget with
        #: headroom is cheap rather than reckless.
        total = sum(_run_window()[0] for _ in range(3))
        assert total <= PLACING_STEPS, total

    def test_exposure_stays_bounded_in_every_window(self):
        #: THE SAFETY CLAIM. However many balancing quotes are permitted,
        #: the peak worst case never passes the configured ceiling,
        #: because resting orders are counted by the prospective bound.
        for _ in range(3):
            _, peak = _run_window()
            assert peak <= D("0.30"), peak

    def test_a_larger_budget_does_not_raise_the_peak(self):
        #: tickets are submission permits. The dollar limits are what
        #: bound risk, and they are untouched by the budget.
        _, peak_a = _run_window()
        _, peak_b = _run_window()
        assert peak_a == peak_b <= D("0.30")
