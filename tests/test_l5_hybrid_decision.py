"""THE HYBRID DECISION, THROUGH THE RUNNER, AGAINST A SCRIPTED VENUE.

WHAT WAS WIRED. `src/l5_inventory_decision.rank()` already priced HOLD,
MAKER_COMPLETE, TAKER_COMPLETE and TAKER_PARTIAL on one arithmetic and
ranked them by expected worst-case-after. The BTC5m lane never called
it: it had its own narrower completion branch, which could rest at the
pair-cost ceiling or cross it, but could not compare completing against
reducing against holding on one scale. This is that wiring.

THE LAYERING, which these tests pin because it is the thing most easily
broken by a well-meaning change:

  the RANKER OPTIMISES  -- it will choose to cross at 0.95 to complete a
      0.08 leg, because turning a possible -0.16 into a certain -0.06
      genuinely improves the worse settlement outcome. That is
      risk-reducing AND loss-realizing, and `ev_class` says so.
  the POLICY BOUNDS     -- spring discipline caps a completed pair at
      0.90, so that cross is refused and the completion rests at the
      budget instead. The policy wins.
  the GUARDS VERIFY     -- `_completion_cross`'s all-in dominance test,
      the envelope's taker_allowed, and the adapter's
      `completion_taker_fault` (haircut depth walk, slippage charged
      against us, dearest-lot basis, >=1c locked, notional cap) each
      re-check independently. Any one refusing keeps the order resting.

A ranked TAKER choice is therefore a REQUEST, never a permission.

NOTHING HERE CLAIMS PROFIT. No fill rate is measured and no edge is
asserted. These prove that the decision is taken once, on one
arithmetic, and that no path can spend capital twice.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X
from src import l5_inventory_decision as ID

from test_l5_btc5m_execution import armed, rig          # noqa: F401


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


def inv(up=("0", "0"), dn=("0", "0")):
    return {C.UP: {"qty": up[0], "cost": up[1]},
            C.DOWN: {"qty": dn[0], "cost": dn[1]}}


def params(**kw):
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.40"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("8.00"),
                straddle_completion_pair_cost=D("0.90"),
                straddle_max_unmatched_usd=D("3.00"),
                straddle_max_side_usd=D("4.00"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                inventory_ranking_enabled=True)
    base.update(kw)
    return C.Params(**base)


#: UP collapsed to 0.08 and recovered to 0.15/0.16, so DOWN offers 0.85.
#: With UP's basis at 0.08 the pair budget leaves 0.82 for DOWN.
RECOVERED = {"bid": "0.15", "ask": "0.16", "age_s": 0.1}
#: UP still collapsed: DOWN offers 0.95, past the whole budget.
COLLAPSED = {"bid": "0.05", "ask": "0.06", "age_s": 0.1}


def decide(book=RECOVERED, inventory=None, resting=(), t=100.0, **kw):
    return C.decide(book=book, t_in_window_s=t,
                    inventory=inventory or inv(up=("2", "0.16")),
                    resting=list(resting), params=params(**kw))


def comp(res):
    return res["evaluation"]["completion"]


def order(res, reason="STRADDLE_COMPLETION"):
    for o in res["orders"]:
        if o.get("op") == "PLACE" and o.get("reason") == reason:
            return o
    return None


# ================================================ the one comparison

class TestOneDecisionOverEveryAlternative:

    def test_the_ranker_is_consulted_and_recorded(self):
        r = comp(decide())["ranking"]
        assert r["on"] is True and r["ok"] is True
        assert r["chosen"] in (ID.HOLD, ID.MAKER_COMPLETE,
                               ID.TAKER_COMPLETE, ID.TAKER_PARTIAL)
        assert r["considered"] is not None

    def test_it_is_off_by_default_and_changes_nothing(self):
        r = decide(inventory_ranking_enabled=False)
        assert comp(r)["ranking"] == {"on": False}
        assert order(r) is not None, "the existing branch still completes"

    def test_a_ranked_hold_carries_the_leg(self):
        #: HOLD is chosen when nothing strictly improves the worse
        #: outcome. Acting anyway would take a decision the one
        #: comparison declined to take.
        import unittest.mock as m
        with m.patch.object(ID, "rank", lambda **_k: {
                "ok": True, "chosen": ID.HOLD, "alternatives": [],
                "considered": [], "why_hold": "NOTHING_IMPROVES_WORST"}):
            r = decide()
        assert order(r) is None
        assert comp(r)["why"] == "RANKED_HOLD"

    def test_a_ranker_failure_falls_back_to_holding(self):
        import unittest.mock as m
        def boom(**_k):
            raise RuntimeError("nope")
        with m.patch.object(ID, "rank", boom):
            r = decide()
        assert order(r) is None
        assert comp(r)["why"] == "RANKED_HOLD"
        assert "RANKER_RAISED" in str(comp(r)["ranking"]["reason"])


# ============================ positive EV vs risk-reducing loss-taking

class TestTheRankingDistinguishesEvFromRiskReduction:
    """The ranking basis is expected WORST-case, not expected value. So a
    chosen action can remove the bad tail and PAY expected value for it.
    A loss-realizing risk reduction must never be reported as a
    profitable completion."""

    def test_the_chosen_action_is_classified(self):
        cls = comp(decide())["ranking"]["ev_class"]
        assert cls in ("POSITIVE_EV", "RISK_REDUCING_EV_NEGATIVE",
                       "EV_UNKNOWN")

    def test_a_positive_ev_completion_reports_a_nonpositive_giveup(self):
        rk = comp(decide())["ranking"]
        if rk["ev_class"] == "POSITIVE_EV":
            assert D(rk["ev_giveup_usd"]) <= 0

    def test_an_ev_negative_choice_is_named_not_hidden(self):
        import unittest.mock as m
        with m.patch.object(ID, "rank", lambda **_k: {
                "ok": True, "chosen": ID.TAKER_COMPLETE, "considered": [],
                "alternatives": [{"alternative": ID.TAKER_COMPLETE,
                                  "available": True,
                                  "ev_giveup_usd": "0.0400"}]}):
            rk = comp(decide())["ranking"]
        assert rk["ev_class"] == "RISK_REDUCING_EV_NEGATIVE"
        assert D(rk["ev_giveup_usd"]) > 0

    def test_an_unmeasurable_giveup_is_not_called_positive(self):
        import unittest.mock as m
        with m.patch.object(ID, "rank", lambda **_k: {
                "ok": True, "chosen": ID.TAKER_COMPLETE, "considered": [],
                "alternatives": [{"alternative": ID.TAKER_COMPLETE,
                                  "available": True,
                                  "ev_giveup_usd": None}]}):
            rk = comp(decide())["ranking"]
        assert rk["ev_class"] == "EV_UNKNOWN"


# ======================================= the policy bounds the ranker

class TestThePolicyBoundsTheRanker:

    def test_a_ranked_cross_above_the_pair_budget_is_refused(self):
        #: DOWN offers 0.95 against a 0.82 budget. The ranker may well
        #: prefer crossing -- a certain small loss beats a possible
        #: larger one -- but spring discipline caps the pair at 0.90, so
        #: the completion RESTS at the budget and does not chase.
        r = decide(book=COLLAPSED, taker_allowed=True,
                   completion_cross_enabled=True)
        o = order(r)
        assert o is not None and o["post_only"] is True
        assert o["price"] == D("0.82")
        assert comp(r)["cross_why"] == "OFFER_ABOVE_CEILING"

    #: DOWN offers 0.78 against a 0.82 budget, so the dominance test
    #: GENUINELY permits crossing here (all-in 0.7963, saving 2.37c).
    #: At 0.85 it would be refused on budget alone and the test could
    #: not show what it claims to show.
    CROSSABLE = {"bid": "0.22", "ask": "0.23", "age_s": 0.1}

    def test_the_cross_is_genuinely_available_on_this_book(self):
        #: the positive control, so the next test cannot pass vacuously.
        r = decide(book=self.CROSSABLE, taker_allowed=True,
                   completion_cross_enabled=True)
        assert comp(r)["cross"] is True
        assert order(r)["post_only"] is False

    def test_a_ranked_maker_complete_is_not_allowed_to_cross(self):
        #: crossing was not the action that was ranked, so the dominance
        #: test finding a cheap offer does not license it.
        import unittest.mock as m
        with m.patch.object(ID, "rank", lambda **_k: {
                "ok": True, "chosen": ID.MAKER_COMPLETE, "considered": [],
                "alternatives": [{"alternative": ID.MAKER_COMPLETE,
                                  "available": True,
                                  "ev_giveup_usd": "-0.01"}]}):
            r = decide(book=self.CROSSABLE, taker_allowed=True,
                       completion_cross_enabled=True)
        o = order(r)
        assert o is not None and o["post_only"] is True, comp(r)
        assert comp(r)["cross"] is False
        assert comp(r)["cross_why"] == "RANKED_MAKER_COMPLETE"

    def test_taker_stays_impossible_without_the_envelope(self):
        #: the ranker may choose TAKER_COMPLETE all it likes; without
        #: taker_allowed nothing crosses.
        r = decide(completion_cross_enabled=True, taker_allowed=False)
        for o in r["orders"]:
            if o.get("op") == "PLACE":
                assert o["post_only"] is True
        assert comp(r)["cross_why"] == "TAKER_NOT_ALLOWED"


# ==================== capital cannot be spent twice (requirements 2, 6)

class TestCapitalIsReservedOnce:

    def test_outstanding_orders_count_against_the_unmatched_ceiling(self):
        #: THE HOLE THIS CLOSES. Reasoning from inventory plus the clip
        #: being placed let two same-side orders rest at once, each
        #: admitted against an inventory excluding the other -- so the
        #: reachable unmatched cost was the ceiling PLUS a clip. That is
        #: why a 3-window campaign at $0.30 clips could not be proved
        #: inside $1.00.
        rested = [{"id": "o1", "side": C.UP, "price": D("0.08"),
                   "qty": D("5"), "age_s": 1.0}]
        tight = dict(straddle_repeat_enabled=True,
                     straddle_max_side_usd=D("4.00"),
                     straddle_max_unmatched_usd=D("0.45"),
                     straddle_completion_pair_cost=D("0"))
        a = C._prospective_unmatched_usd(
            C.UP, D("0.08"), D("5"),
            {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))})
        b = C._prospective_unmatched_usd(
            C.UP, D("0.08"), D("5"),
            {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))},
            rested)
        assert b > a, "a resting same-side order must raise the bound"
        assert b == D("0.80")

    def test_the_opposite_side_resting_is_not_credited(self):
        #: crediting it would net away exactly the exposure the bound
        #: exists to measure.
        other = [{"id": "o2", "side": C.DOWN, "price": D("0.08"),
                  "qty": D("5"), "age_s": 1.0}]
        a = C._prospective_unmatched_usd(
            C.UP, D("0.08"), D("5"),
            {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))})
        b = C._prospective_unmatched_usd(
            C.UP, D("0.08"), D("5"),
            {C.UP: (D("0"), D("0")), C.DOWN: (D("0"), D("0"))}, other)
        assert b == a

    def test_a_completion_and_a_cheap_rung_never_both_spend(self):
        #: exactly one route to a pair: the completion CANCELS or
        #: WITHDRAWS the cheap opposite rung in the same decision. This
        #: is the 1925z defect, which cost 1.76 in one window.
        rest = [{"id": "cheap", "side": C.DOWN, "price": D("0.08"),
                 "qty": D("2"), "age_s": 1.0}]
        r = decide(resting=rest)
        o = order(r)
        assert o is not None
        assert {"op": "CANCEL", "id": "cheap"} in r["orders"]
        assert not [x for x in r["orders"]
                    if x.get("op") == "PLACE"
                    and x.get("reason") == "STRADDLE_LEG"
                    and x["side"] == C.DOWN]

    def test_the_completion_is_charged_against_the_same_room(self):
        #: a completion that does not fit the remaining room is refused,
        #: not funded out of a second budget.
        r = decide(max_exposure_usd=D("0.20"))
        assert order(r) is None
        assert comp(r)["why"] in ("NO_EXPOSURE_ROOM", "RANKED_HOLD")


# ============================== through the runner, with real fills

class TestThroughTheRunner:

    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv(X.ENTRY_MODE_ENV, C.STRADDLE)
        monkeypatch.setenv(X.STRADDLE_PRICE_ENV, "0.08")
        monkeypatch.setenv(X.CLIP_USD_ENV, "0.40")
        monkeypatch.setenv(X.STRADDLE_REPEAT_ENV, "YES")
        monkeypatch.setenv(X.STRADDLE_MAX_SIDE_USD_ENV, "1.20")
        monkeypatch.setenv(X.STRADDLE_MAX_UNMATCHED_USD_ENV, "0.90")
        monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.90")
        monkeypatch.setenv(X.STRADDLE_P_PAIR_NATURAL_ENV, "0.070")
        monkeypatch.setenv(X.STRADDLE_P_COMPLETE_ENV, "0.131")
        monkeypatch.setenv(X.STRADDLE_P_LONE_WIN_ENV, "0.0981")
        #: attested as measured conditional on a fill (required since
        #: eed10118c); without it every decision held and nothing was placed
        monkeypatch.setenv(X.STRADDLE_P_BASIS_ENV, X.P_BASIS_CONDITIONAL)

    def _leg(self, rig_, side):
        for oid, i in rig_.venue.by_oid.items():
            if i["side"] == side and oid not in rig_.venue.full:
                return oid
        raise AssertionError("no live %s order" % side)

    def test_replenishment_after_a_fill(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        rig.venue.fill(self._leg(rig, C.UP))
        rig.tick(2)
        rep = rig.step(bid="0.15", ask="0.16")
        assert any(D(str(f["qty"])) > 0 for f in rep["fills"]), rep
        assert D(str(rep["inventory"][C.UP]["qty"])) > 0

    def test_a_partial_fill_is_a_normal_state(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        up = self._leg(rig, C.UP)
        want = D(str(rig.venue.by_oid[up]["qty"]))
        rig.venue.fill(up, want / 2)
        rig.tick(2)
        rep = rig.step(bid="0.15", ask="0.16")
        held = D(str(rep["inventory"][C.UP]["qty"]))
        assert 0 < held < want
        assert rep["halted_reason"] is None

    def test_concurrent_orders_on_both_sides(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        sides = {i["side"] for i in rig.venue.by_oid.values()}
        assert sides == {C.UP, C.DOWN}

    def test_a_cancel_that_loses_to_a_fill_is_booked(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        up = self._leg(rig, C.UP)
        rig.venue.fill_on_cancel.add(up)
        rig.tick(2)
        rep = rig.ex.end_of_window(rig.step.__defaults__ and None
                                   or __import__("test_l5_btc5m_execution").MARKET)
        assert D(str(rep["inventory"][C.UP]["qty"])) > 0, rep
        assert [x for x in rep["filled"] if x.get("venue_order_id") == up]

    def test_restart_recovers_inventory_and_does_not_double_count(
            self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        up = self._leg(rig, C.UP)
        rig.venue.fill(up)
        rig.tick(2)
        before = rig.step(bid="0.15", ask="0.16")["inventory"]
        rig.restart()
        rig.tick(2)
        after = rig.step(bid="0.15", ask="0.16")["inventory"]
        assert (D(str(after[C.UP]["qty"])) >= D(str(before[C.UP]["qty"]))), \
            (before, after)
        assert D(str(after[C.UP]["cost"])) == D(str(before[C.UP]["cost"]))

    def test_an_uncertain_order_state_fails_closed(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        up = self._leg(rig, C.UP)
        rig.venue.read_error[up] = 99
        rig.tick(2)
        rep = rig.step(bid="0.15", ask="0.16")
        assert str(rep["halted_reason"]).startswith(X.HALT_ORDER)
        assert rep["placed"] == []

    def test_nothing_crosses_without_authorization(self, rig, armed):
        rig.step(bid="0.15", ask="0.16")
        for s in rig.venue.sent:
            assert s.get("post_only") is True, s
