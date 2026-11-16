"""One choice a step between the straddle and the late favourite.

The property these tests protect is that neither strategy can spend
what the other reserved, and that a handoff is a COMPARISON rather than
a consequence of the clock.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_strategy_arbiter as A

RUNG = {"op": "PLACE", "side": "UP", "price": D("0.08"), "qty": D("2"),
        "post_only": True, "intent": "BUY", "reason": "STRADDLE_LEG",
        "slot": 0}
COID = "b5m-" + "a" * 24
CAND = {"op": "PLACE", "side": "UP", "price": D("0.97"), "qty": D("2"),
        "post_only": True, "intent": "BUY", "reason": "LATE_FAVORITE",
        "slot": 0, "strategy": "LATE_FAVORITE"}


def ok_late(ev="0.06"):
    return {"ok": True, "reason": None, "order": CAND, "ev_usd": ev}


def blocked_late(ev="0.06", coid=COID):
    return {"ok": False, "reason": "SIDE_ALREADY_WORKED", "order": None,
            "blocked_order": CAND, "ev_usd": ev,
            "conflicts": [{"side": "UP", "price": "0.08", "qty": "2",
                           "id": coid}]}


def call(**kw):
    base = dict(straddle_orders=[RUNG], completion_order=None,
                completion_ev_usd=None, late={"ok": False,
                                              "reason": "LATE_FAVORITE_OFF"},
                continuation_usd="0.0014", handoff_margin_usd="0.01")
    base.update(kw)
    return A.choose(**base)


class TestACompletionBeatsWhatItCanBeComparedWith:

    def test_a_due_positive_completion_is_taken(self):
        comp = {"op": "PLACE", "side": "DOWN", "price": D("0.75"),
                "qty": D("2"), "reason": "COMPLETE"}
        r = call(completion_order=comp, completion_ev_usd="0.34",
                 late=ok_late())
        assert r["action"] == A.COMPLETE_PAIR
        assert r["orders"] == [comp] and r["cancels"] == []

    def test_a_negative_completion_is_not_taken(self):
        comp = {"op": "PLACE", "side": "DOWN", "price": D("0.99")}
        r = call(completion_order=comp, completion_ev_usd="-0.10")
        assert r["action"] != A.COMPLETE_PAIR

    def test_an_unreadable_completion_ev_is_not_taken(self):
        r = call(completion_order={"op": "PLACE"}, completion_ev_usd="nope")
        assert r["action"] != A.COMPLETE_PAIR


class TestTheStraddleRunsWhenNothingCompetes:

    def test_no_candidate_keeps_the_straddle(self):
        r = call()
        assert r["action"] == A.KEEP_STRADDLE and r["orders"] == [RUNG]
        assert r["evaluation"]["late_eligible"] is False

    def test_nothing_at_all_holds(self):
        r = call(straddle_orders=[])
        assert r["action"] == A.HOLD and r["orders"] == []

    def test_a_late_refusal_that_is_not_a_conflict_keeps_the_straddle(self):
        r = call(late={"ok": False, "reason": "TOO_EARLY"})
        assert r["action"] == A.KEEP_STRADDLE
        assert r["evaluation"]["late_reason"] == "TOO_EARLY"


class TestBothStrategiesActTogetherWhenTheyDoNotConflict:

    def test_a_placeable_candidate_is_additive(self):
        """They are bounded by ONE budget: the late evaluator was handed
        the room left after the straddle allocated, so both sets of
        orders can go out in the same step."""
        r = call(late=ok_late())
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "STRADDLE_AND_LATE_FAVORITE"
        assert r["orders"] == [RUNG, CAND]
        assert r["cancels"] == []


class TestTheHandoffIsAComparisonNotAClock:

    def test_a_clearly_better_candidate_cancels_the_exact_rung(self):
        r = call(late=blocked_late(ev="0.50"))
        assert r["action"] == A.HANDOFF
        assert r["cancels"] == [{"op": "CANCEL", "id": COID}]

    def test_the_replacement_is_NOT_emitted_in_the_same_step(self):
        """The whole safety property: the order is deferred until a later
        step sees the rung gone from the venue-confirmed resting view."""
        r = call(late=blocked_late(ev="0.50"))
        assert r["orders"] == []
        assert r["deferred_order"] == CAND
        #: it is data for the next step, never something this step emits
        assert CAND not in r["orders"] and CAND not in r["cancels"]

    def test_a_candidate_that_does_not_beat_continuation_changes_nothing(self):
        r = call(late=blocked_late(ev="0.005"),
                 continuation_usd="0.0014", handoff_margin_usd="0.01")
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "LATE_EV_BELOW_CONTINUATION"
        assert r["orders"] == [RUNG] and r["cancels"] == []

    def test_the_threshold_is_continuation_plus_margin(self):
        at = call(late=blocked_late(ev="0.0114"), continuation_usd="0.0014",
                  handoff_margin_usd="0.01")
        over = call(late=blocked_late(ev="0.0115"), continuation_usd="0.0014",
                    handoff_margin_usd="0.01")
        assert at["action"] == A.KEEP_STRADDLE          # equal is not better
        assert over["action"] == A.HANDOFF
        assert over["evaluation"]["threshold_usd"] == "0.0114"

    def test_a_missing_continuation_estimate_never_hands_off(self):
        """An unsupplied continuation value would make every handoff look
        free. Three observed events is not a licence for a default."""
        r = call(late=blocked_late(ev="99"), continuation_usd=None)
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "NO_CONTINUATION_ESTIMATE"

    @pytest.mark.parametrize("margin", [None, "-1", "nope"])
    def test_a_missing_or_negative_handoff_margin_never_hands_off(self, margin):
        r = call(late=blocked_late(ev="99"), handoff_margin_usd=margin)
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "NO_HANDOFF_MARGIN"

    def test_an_unreadable_candidate_ev_never_hands_off(self):
        r = call(late=blocked_late(ev="nope"))
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "LATE_EV_UNREADABLE"


class TestCancellationIsExactOrItDoesNotHappen:

    @pytest.mark.parametrize("coid", [None, "", "   "])
    def test_a_conflict_without_an_order_id_is_not_cancelled(self, coid):
        """A cancel without an id would have to mean "everything on that
        side", which is not an exact cancel."""
        r = call(late=blocked_late(ev="99", coid=coid))
        assert r["action"] == A.KEEP_STRADDLE
        assert r["why"] == "CONFLICT_NOT_IDENTIFIED"
        assert r["cancels"] == []

    def test_only_the_named_orders_are_cancelled(self):
        late = blocked_late(ev="99")
        late["conflicts"].append({"side": "UP", "price": "0.12", "qty": "2",
                                  "id": "b5m-" + "z" * 24})
        r = call(late=late)
        assert [c["id"] for c in r["cancels"]] == [
            COID, "b5m-" + "z" * 24]

    def test_the_opposite_side_is_never_cancelled(self):
        """A resting order on the side the late favourite does not want is
        a potential pair completion -- the best economics this system has
        -- so no action here touches it."""
        late = blocked_late(ev="99")
        r = call(late=late)
        assert all(c["id"] == COID for c in r["cancels"])
        assert all("DOWN" not in str(c) for c in r["cancels"])


class TestEveryChoiceIsRecorded:

    def test_the_action_and_its_reason_are_always_present(self):
        for late in ({"ok": False, "reason": "LATE_FAVORITE_OFF"},
                     ok_late(), blocked_late(ev="0.001"),
                     blocked_late(ev="99")):
            r = call(late=late)
            assert r["action"] in A.ACTIONS
            assert isinstance(r["why"], str) and r["why"]

    def test_the_comparison_is_recorded_when_one_was_made(self):
        r = call(late=blocked_late(ev="0.50"))
        e = r["evaluation"]
        assert e["late_ev_usd"] == "0.50"
        assert e["continuation_usd"] == "0.0014"
        assert e["threshold_usd"] == "0.0114"
