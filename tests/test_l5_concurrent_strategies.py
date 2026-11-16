"""Both strategies inside ONE decide(), against ONE budget.

The evidence this file exists to produce:
  * the arbiter is really called by `decide`;
  * both strategies are evaluated in the same window;
  * neither can spend what the other reserved;
  * no replacement is emitted while a cancellation is unresolved.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_late_favorite as LF
from src import l5_strategy_arbiter as AR

#: a book whose UP side is the clear favourite and has depth to sell us.
BOOK = {"bid": "0.97", "ask": "0.98", "age_s": 0.1,
        "bid_shares": "500", "ask_shares": "500"}
FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")
    monkeypatch.setenv(LF.ENABLE_ENV, "YES")


def params(**kw):
    #: the entry-EV gate is configured with the MEASURED rates because
    #: these tests are about concurrency, not the gate. Unset rates
    #: refuse every rung -- its own behaviour, covered in
    #: tests/test_l5_straddle_ev.py.
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.16"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("5.00"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                #: A REACHABLE LATE WINDOW, which these tests previously
                #: got from the module's default of 30 -- a value the
                #: session can never step in, because the step loop ends
                #: at `window_s - CANCEL_LEAD_S` = 270 and a 30-second
                #: window begins exactly there. These tests call
                #: `decide()` directly at t=275, past the end of the
                #: stepping range, which is how the gap stayed hidden:
                #: the concurrency they pin was real, the reachability
                #: was not. `tests/test_l5_live_safety.py` now refuses
                #: the unreachable combination outright.
                late_favorite_window_s=D("45"),
                late_favorite_clip_usd=D("1.94"),
                late_favorite_fee_per_share=D("0"),
                late_favorite_min_edge_per_share=D("0.005"),
                late_favorite_continuation_usd=D("0.0014"),
                late_favorite_handoff_margin_usd=D("0.01"))
    base.update(kw)
    return C.Params(**base)


def decide(*, t=275.0, inventory=None, resting=(), p_up="0.999",
           p_age_s=0.2, loss_room_usd="5.00", **kw):
    return C.decide(book=BOOK, t_in_window_s=t,
                    inventory=inventory or FLAT, resting=list(resting),
                    params=params(**kw), p_up=p_up, p_age_s=p_age_s,
                    loss_room_usd=loss_room_usd)


class TestTheArbiterIsReallyCalled:

    def test_decide_records_the_arbiters_action_and_reason(self):
        out = decide()
        arb = out["evaluation"]["arbiter"]
        assert arb is not None
        assert arb["action"] in AR.ACTIONS
        assert isinstance(arb["why"], str) and arb["why"]

    def test_decide_records_the_late_favourite_evaluation(self):
        out = decide()
        assert "late_favorite" in out["evaluation"]
        assert out["evaluation"]["late_favorite"]["enabled"] is True

    def test_it_is_silent_before_the_late_window(self):
        out = decide(t=100.0)
        assert out["evaluation"]["late_favorite"]["reason"] == "TOO_EARLY"
        assert out["evaluation"]["arbiter"]["action"] == AR.KEEP_STRADDLE


class TestBothStrategiesInTheSameWindow:

    def test_both_place_in_one_decision_when_they_do_not_conflict(self):
        """The straddle's cheap rungs and a 0.97 favourite buy, emitted
        together from one step, against one budget."""
        out = decide(t=275.0)
        arb = out["evaluation"]["arbiter"]
        assert arb["why"] == "STRADDLE_AND_LATE_FAVORITE"
        prices = sorted(str(o["price"]) for o in out["orders"]
                        if o.get("op") == "PLACE")
        assert "0.97" in prices                      # the late favourite
        assert out["why"] == "STRADDLE_AND_LATE_FAVORITE"

    def test_the_late_order_is_maker_only(self):
        out = decide(t=275.0)
        late = [o for o in out["orders"]
                if o.get("reason") == "LATE_FAVORITE"]
        assert late and all(o["post_only"] is True for o in late)
        assert all(o["intent"] == "BUY" for o in late)

    def test_each_order_carries_its_strategy(self):
        out = decide(t=275.0)
        late = [o for o in out["orders"] if o.get("reason") == "LATE_FAVORITE"]
        assert late and late[0]["strategy"] == "LATE_FAVORITE"

    def test_the_straddle_alone_runs_when_the_candidate_is_off(self, monkeypatch):
        monkeypatch.delenv(LF.ENABLE_ENV, raising=False)
        out = decide(t=275.0)
        assert out["evaluation"]["late_favorite"]["reason"] == "LATE_FAVORITE_OFF"
        assert all(o.get("reason") != "LATE_FAVORITE" for o in out["orders"])


class TestOneSharedBudget:

    @staticmethod
    def _placed_cost(out):
        return sum(o["price"] * o["qty"] for o in out["orders"]
                   if o.get("op") == "PLACE")

    def test_the_two_strategies_together_never_exceed_the_one_cap(self):
        """The candidate is SHRUNK to the room the straddle left rather
        than refused -- and the sum of both strategies' orders still fits
        the single cap. That is the shared budget, demonstrated."""
        for cap in ("0.40", "1.00", "2.50", "5.00"):
            out = decide(t=275.0, max_exposure_usd=D(cap))
            assert self._placed_cost(out) <= D(cap), cap

    def test_the_late_favourite_gets_only_what_the_straddle_left(self):
        """At a 0.40 cap the straddle's two rungs take 0.32, so the 1.94
        clip is cut to what remains instead of overdrawing."""
        out = decide(t=275.0, max_exposure_usd=D("0.40"))
        late = [o for o in out["orders"] if o.get("reason") == "LATE_FAVORITE"]
        straddle = [o for o in out["orders"]
                    if o.get("reason") == "STRADDLE_LEG"]
        s_cost = sum(o["price"] * o["qty"] for o in straddle)
        assert late, "the candidate should be shrunk, not dropped"
        assert late[0]["price"] * late[0]["qty"] <= D("0.40") - s_cost
        assert late[0]["qty"] < D("1.94") / D("0.97")     # genuinely cut

    def test_inventory_already_held_shrinks_the_room(self):
        """Exposure the straddle already HOLDS counts against the same
        budget, so the candidate shrinks again."""
        held = {C.UP: {"qty": "0", "cost": "0"},
                C.DOWN: {"qty": "40", "cost": "3.20"}}
        free = decide(t=275.0, max_exposure_usd=D("3.50"))
        used = decide(t=275.0, inventory=held, max_exposure_usd=D("3.50"))
        f = [o for o in free["orders"] if o.get("reason") == "LATE_FAVORITE"]
        u = [o for o in used["orders"] if o.get("reason") == "LATE_FAVORITE"]
        assert f and u
        assert u[0]["qty"] < f[0]["qty"]
        assert self._placed_cost(used) <= D("3.50")

    def test_the_daily_loss_budget_bounds_the_late_order(self):
        out = decide(t=275.0, loss_room_usd="0.97")
        late = [o for o in out["orders"] if o.get("reason") == "LATE_FAVORITE"]
        assert late and late[0]["qty"] * late[0]["price"] <= D("0.97")

    def test_an_absent_loss_room_refuses_the_late_order(self):
        """The controller cannot see the daily halt, so the executor must
        pass it. Absent is never read as unlimited."""
        out = C.decide(book=BOOK, t_in_window_s=275.0, inventory=FLAT,
                       resting=[], params=params(), p_up="0.999",
                       p_age_s=0.2)
        assert out["evaluation"]["late_favorite"]["reason"] \
            == "BUDGET_UNREADABLE"


class TestTheHandoffNeverReplacesWhileUncertain:

    REST_UP = [{"id": "rung-up", "side": C.UP, "price": D("0.08"),
                "qty": D("2"), "age_s": 5.0}]

    def test_a_conflicting_rung_blocks_the_candidate(self):
        out = decide(t=275.0, resting=self.REST_UP,
                     late_favorite_handoff_margin_usd=D("99"))
        ev = out["evaluation"]["late_favorite"]
        assert ev["reason"] == "SIDE_ALREADY_WORKED"
        assert out["evaluation"]["arbiter"]["action"] == AR.KEEP_STRADDLE

    def test_a_worthwhile_handoff_cancels_only_that_rung(self):
        out = decide(t=275.0, resting=self.REST_UP)
        arb = out["evaluation"]["arbiter"]
        assert arb["action"] == AR.HANDOFF
        cancels = [o for o in out["orders"] if o.get("op") == "CANCEL"]
        assert [c["id"] for c in cancels] == ["rung-up"]

    def test_the_replacement_is_not_emitted_in_the_cancelling_step(self):
        """THE safety property: the 0.97 order is recorded as deferred and
        is NOT among the orders this step sends."""
        out = decide(t=275.0, resting=self.REST_UP)
        assert out["evaluation"]["arbiter"]["deferred_order"]["price"] == "0.97"
        assert all(o.get("reason") != "LATE_FAVORITE" for o in out["orders"])

    def test_the_replacement_appears_only_once_the_rung_is_gone(self):
        """Step two: `resting` no longer shows the cancelled id, which is
        the executor's venue-confirmed view. Only then is it placed."""
        out = decide(t=275.0, resting=[])
        late = [o for o in out["orders"] if o.get("reason") == "LATE_FAVORITE"]
        assert late and str(late[0]["price"]) == "0.97"

    def test_a_still_resting_rung_keeps_refusing_indefinitely(self):
        """A rejected or uncertain cancel leaves the rung resting, so the
        candidate stays blocked however many steps pass."""
        for t in (275.0, 280.0, 285.0, 290.0):
            out = decide(t=t, resting=self.REST_UP,
                         late_favorite_handoff_margin_usd=D("99"))
            assert all(o.get("reason") != "LATE_FAVORITE"
                       for o in out["orders"])

    def test_the_opposite_side_rung_is_never_cancelled(self):
        rest = [{"id": "rung-down", "side": C.DOWN, "price": D("0.08"),
                 "qty": D("2"), "age_s": 5.0}]
        out = decide(t=275.0, resting=rest)
        assert all(o.get("id") != "rung-down" for o in out["orders"]
                   if o.get("op") == "CANCEL")


class TestUnsetKnobsRefuse:

    def test_no_continuation_estimate_never_hands_off(self):
        out = decide(t=275.0,
                     resting=[{"id": "r", "side": C.UP, "price": D("0.08"),
                               "qty": D("2"), "age_s": 5.0}],
                     late_favorite_continuation_usd=None)
        assert out["evaluation"]["arbiter"]["why"] == "NO_CONTINUATION_ESTIMATE"
        assert not [o for o in out["orders"] if o.get("op") == "CANCEL"]

    def test_no_edge_margin_refuses_the_candidate(self):
        out = decide(t=275.0, late_favorite_min_edge_per_share=D("0"))
        assert out["evaluation"]["late_favorite"]["reason"] \
            == "NO_EDGE_MARGIN_SET"

    def test_a_zero_clip_buys_nothing(self):
        out = decide(t=275.0, late_favorite_clip_usd=D("0"))
        assert out["evaluation"]["late_favorite"]["ok"] is False
