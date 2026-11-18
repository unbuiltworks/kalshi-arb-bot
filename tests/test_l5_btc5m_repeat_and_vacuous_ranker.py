"""Repeated maker fills, and the vacuous-ranker veto that prevented them.

THE DEFECT, stated once. `_rank_inventory` passes `maker_fill_rate=None`
on purpose -- the rate has never been measured at these prices and
inventing one is forbidden -- so `rank` reports MAKER_COMPLETE
`available=False`. With taker completion disabled by policy every taker
route is unavailable too. The only AVAILABLE alternative is therefore
HOLD, in every book, forever. The controller read that as "RANKED_HOLD:
nothing on offer beats waiting" and withdrew the baseline completion, so
turning the ranker ON -- which production has -- REMOVED the only
risk-reducing completion the controller had.

WHAT THE FIX IS, AND IS NOT. It distinguishes a HOLD taken over at least
one available alternative (a real comparison, still binding) from a HOLD
with nothing to compare against (a veto by an empty set). It invents no
fill rate, raises no cap, and authorizes no taker. Half of this file
exists to prove that: every bound still refuses exactly what it refused.

SEPARATELY, the arithmetic that bounds repetition. A first leg from flat
is ENTIRELY unmatched, so its prospective unmatched cost IS its notional.
The mode can therefore open only when clip notional <=
straddle_max_unmatched_usd -- at production's $0.10 ceiling a $0.15 clip
places nothing at all, which is not a repetition limit but a total one.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C
from src import l5_btc5m_execution as X


@pytest.fixture(autouse=True)
def _controller_on(monkeypatch):
    #: `decide` short-circuits to CONTROLLER_OFF before any gate, so
    #: without this every assertion below passes against a decision that
    #: was never made.
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


BOOK = {"bid": "0.60", "ask": "0.61", "age_s": 0.1}
ENV = X.RANKER_VACUOUS_ENV


def params(**kw):
    #: the production-reported effective configuration, plus the two
    #: fields EFFECTIVE_CONFIG does not report (clip, completion cost).
    base = dict(entry_mode=C.STRADDLE, straddle_repeat_enabled=True,
                straddle_max_side_usd=D("0.30"),
                straddle_max_unmatched_usd=D("0.10"),
                max_exposure_usd=D("5.00"), max_resting_entries=4,
                no_new_entry_after_s=270.0,
                straddle_price=D("0.08"), clip_usd=D("0.15"),
                hedge_enabled=False, reserve_mode="NONE",
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"))
    base.update(kw)
    return C.Params(**base)


def inv(up_qty="0", up_cost="0", dn_qty="0", dn_cost="0"):
    return {C.UP: {"qty": up_qty, "cost": up_cost},
            C.DOWN: {"qty": dn_qty, "cost": dn_cost}}


def resting_order(side, price, qty, oid="o1", age_s=1.0):
    #: `age_s` IS REQUIRED -- without it `_read_resting` returns None and
    #: the whole decision bails RESTING_UNREADABLE, which makes every
    #: assertion about placed legs pass vacuously.
    return {"id": oid, "side": side, "price": D(price), "qty": D(qty),
            "age_s": age_s}


def decide(inventory=None, resting=(), t=10.0, **kw):
    return C.decide(book=BOOK, t_in_window_s=t,
                    inventory=inventory or inv(), resting=list(resting),
                    params=params(**kw))


def legs(res, side=None, reason="STRADDLE_LEG"):
    return [o for o in res["orders"]
            if o.get("reason") == reason and (side is None
                                              or o["side"] == side)]


def orders_on(res, side):
    return [o for o in res["orders"] if o.get("side") == side]


def completion(res):
    """The completion record is NESTED under `evaluation`, not top level.
    Reading `res["completion"]` returned {} and two assertions failed
    against a record that was there all along."""
    return ((res.get("evaluation") or {}).get("completion")) or {}


LOPSIDED = dict(inventory=inv(up_qty="0.5", up_cost="0.30"),
                straddle_completion_pair_cost=D("0.96"))


# ------------------------------------------------- the flag arrives OFF

def test_the_fix_is_off_by_default():
    assert C.Params().ranker_vacuous_hold_falls_back is False
    assert X.ranker_vacuous_hold_falls_back() is False


@pytest.mark.parametrize("raw", ["", "0", "YES", "ON", "1", "true",
                                 "fallback", "Fallback", "FALLBACKS",
                                 " FALLBACK"])
def test_only_the_exact_string_fallback_arms_it(monkeypatch, raw):
    """A word that READS like enabling is not enabling.
    (' FALLBACK' strips to 'FALLBACK' and does arm.)"""
    monkeypatch.setenv(ENV, raw)
    assert X.ranker_vacuous_hold_falls_back() is (raw.strip().upper()
                                                  == "FALLBACK")


def test_an_absent_variable_keeps_the_caller_default(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert X.ranker_vacuous_hold_falls_back(True) is True
    assert X.ranker_vacuous_hold_falls_back(False) is False


# ------------------------------------------- the defect, and the repair

class TestTheVacuousVeto:

    def test_without_the_ranker_the_baseline_completion_is_placed(self):
        """The control. This is the behaviour the ranker removed."""
        res = decide(**LOPSIDED)
        down = legs(res, C.DOWN, reason="STRADDLE_COMPLETION")
        assert len(down) == 1, res.get("why")
        assert res["why"] == "STRADDLE_COMPLETING"

    def test_enabling_the_ranker_suppressed_it_entirely(self):
        """THE DEFECT, pinned so it cannot return silently."""
        res = decide(inventory_ranking_enabled=True, **LOPSIDED)
        assert res["orders"] == []
        assert orders_on(res, C.DOWN) == []

    def test_the_fallback_restores_the_risk_reducing_completion(self):
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        down = legs(res, C.DOWN, reason="STRADDLE_COMPLETION")
        assert len(down) == 1, res.get("why")
        assert res["why"] == "STRADDLE_COMPLETING"

    def test_the_restored_order_is_on_the_LIGHT_side(self):
        """It must reduce imbalance, never add to the heavy side."""
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        assert orders_on(res, C.UP) == []
        assert len(orders_on(res, C.DOWN)) >= 1

    def test_the_fallback_is_recorded_not_silent(self):
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        comp = completion(res)
        assert comp.get("ranking_vacuous") is True
        assert "RANKER_NO_PRICEABLE_ALTERNATIVE" in str(
            comp.get("ranker_note"))

    def test_with_no_unmatched_inventory_the_fallback_changes_nothing(self):
        flat = decide(inventory_ranking_enabled=True,
                      ranker_vacuous_hold_falls_back=True,
                      straddle_completion_pair_cost=D("0.96"))
        assert legs(flat, reason="STRADDLE_COMPLETION") == []


# ------------------------- a REAL comparison must still bind (no weakening)

class TestARealHoldStillBinds:
    """THE OTHER HALF. If the fix honoured every HOLD as vacuous it would
    be a licence to complete regardless, which is exactly the gate-
    weakening this must not be. These stub `rank` so an alternative IS
    available -- which production inputs cannot currently produce."""

    def _stub(self, monkeypatch, alternatives):
        from src import l5_inventory_decision as ID

        def fake_rank(**kw):
            return {"ok": True, "chosen": "HOLD",
                    "alternatives": alternatives,
                    "why_hold": "stub"}
        monkeypatch.setattr(ID, "rank", fake_rank)

    def test_a_hold_over_an_available_alternative_is_honoured(
            self, monkeypatch):
        self._stub(monkeypatch, [
            {"alternative": "HOLD", "available": True},
            {"alternative": "TAKER_COMPLETE", "available": True,
             "ev_giveup_usd": "0.02"},
        ])
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        #: a real comparison chose to carry the leg, so nothing is placed
        assert legs(res, reason="STRADDLE_COMPLETION") == []
        comp = completion(res)
        assert comp.get("why") == "RANKED_HOLD"
        assert not comp.get("ranking_vacuous")

    def test_an_unavailable_alternative_does_not_count_as_a_comparison(
            self, monkeypatch):
        self._stub(monkeypatch, [
            {"alternative": "HOLD", "available": True},
            {"alternative": "MAKER_COMPLETE", "available": False},
            {"alternative": "TAKER_COMPLETE", "available": False},
        ])
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        assert len(legs(res, C.DOWN, reason="STRADDLE_COMPLETION")) == 1

    def test_a_non_hold_choice_is_untouched_by_the_fix(self, monkeypatch):
        from src import l5_inventory_decision as ID

        def fake_rank(**kw):
            return {"ok": True, "chosen": "MAKER_COMPLETE",
                    "alternatives": [{"alternative": "MAKER_COMPLETE",
                                      "available": True,
                                      "ev_giveup_usd": "0.01"}]}
        monkeypatch.setattr(ID, "rank", fake_rank)
        res = decide(inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True, **LOPSIDED)
        comp = completion(res)
        assert comp.get("why") != "RANKED_HOLD"
        assert not comp.get("ranking_vacuous")


# ------------------------------------------- the wrapper's own reporting

class TestTheRankerRecord:
    """`rank` reports its routes under `alternatives`; the wrapper read
    `considered`, a key `rank` does not set, so it reported an EMPTY
    comparison on every call. That nearly cost me the diagnosis."""

    def test_the_record_now_carries_the_priced_routes(self):
        inv_t = {C.UP: (D("0.5"), D("0.30")), C.DOWN: (D("0"), D("0"))}
        _, rec = C._rank_inventory(
            inv=inv_t, rest=[], deficient=C.DOWN, deficit=D("0.5"),
            bid=D("0.60"), ask=D("0.61"), age=D("0.1"),
            ceiling=D("0.36"), params=params(
                inventory_ranking_enabled=True,
                straddle_completion_pair_cost=D("0.96")),
            tick=D("0.01"), room=D("5.00"), t=10.0)
        assert rec["considered"], "the comparison must not report empty"
        names = {str(a.get("alternative")) for a in rec["considered"]}
        assert "HOLD" in names and "MAKER_COMPLETE" in names

    def test_production_inputs_make_every_alternative_unavailable(self):
        """THE ROOT CAUSE, as an assertion rather than a claim."""
        inv_t = {C.UP: (D("0.5"), D("0.30")), C.DOWN: (D("0"), D("0"))}
        _, rec = C._rank_inventory(
            inv=inv_t, rest=[], deficient=C.DOWN, deficit=D("0.5"),
            bid=D("0.60"), ask=D("0.61"), age=D("0.1"),
            ceiling=D("0.36"), params=params(
                inventory_ranking_enabled=True,
                straddle_completion_pair_cost=D("0.96")),
            tick=D("0.01"), room=D("5.00"), t=10.0)
        assert rec["priceable_alternatives"] == 0
        assert rec["priceable_names"] == []
        assert rec["hold_is_vacuous"] is True
        assert rec["chosen"] == "HOLD"


# --------------------------------- repeated fills: the actual arithmetic

class TestWhatBoundsRepetition:

    @staticmethod
    def _run(clip, max_steps=40, **kw):
        """Drive one window, treating each placed leg as filled at its
        own limit -- the OPTIMISTIC case for repetition."""
        iv = {C.UP: {"qty": D("0"), "cost": D("0")},
              C.DOWN: {"qty": D("0"), "cost": D("0")}}
        n = 0
        for _ in range(max_steps):
            res = C.decide(book=BOOK, t_in_window_s=10.0, inventory=iv,
                           resting=[], params=params(clip_usd=D(clip), **kw))
            placed = [o for o in res["orders"]
                      if o.get("reason") == "STRADDLE_LEG"]
            if not placed:
                return n, res.get("why")
            for o in placed:
                s = o["side"]
                p, q = D(str(o["price"])), D(str(o["qty"]))
                iv[s] = {"qty": iv[s]["qty"] + q,
                         "cost": iv[s]["cost"] + p * q}
                n += 1
        return n, "DID_NOT_TERMINATE"

    def test_a_clip_above_the_unmatched_ceiling_opens_nothing(self):
        """NOT a repetition limit -- a TOTAL one. The first leg from flat
        is entirely unmatched, so its notional IS its unmatched cost."""
        for clip in ("0.12", "0.15", "0.30"):
            n, why = self._run(clip)
            assert n == 0, (clip, n, why)
            assert why == "UNMATCHED_CEILING", (clip, why)

    def test_a_clip_within_the_ceiling_repeats_until_the_side_cap(self):
        for clip, expect in (("0.10", 6), ("0.05", 12)):
            n, why = self._run(clip)
            assert n == expect, (clip, n, why)
            assert why == "SIDE_CAP_REACHED", (clip, why)

    def test_repetition_is_bounded_by_the_side_cap_not_by_a_fill_count(self):
        n_small, _ = self._run("0.05")
        n_wide, why = self._run("0.05", straddle_max_side_usd=D("0.60"))
        assert n_wide > n_small
        assert why == "SIDE_CAP_REACHED"

    def test_the_cap_is_dollars_so_fills_scale_inversely_with_clip(self):
        assert self._run("0.05")[0] == 2 * self._run("0.10")[0]

    def test_repetition_stays_balanced_across_the_two_sides(self):
        """A half-built ladder is the one shape this must never hold."""
        iv = {C.UP: {"qty": D("0"), "cost": D("0")},
              C.DOWN: {"qty": D("0"), "cost": D("0")}}
        per = {C.UP: 0, C.DOWN: 0}
        for _ in range(40):
            res = C.decide(book=BOOK, t_in_window_s=10.0, inventory=iv,
                           resting=[], params=params(clip_usd=D("0.05")))
            placed = [o for o in res["orders"]
                      if o.get("reason") == "STRADDLE_LEG"]
            if not placed:
                break
            for o in placed:
                s = o["side"]
                p, q = D(str(o["price"])), D(str(o["qty"]))
                iv[s] = {"qty": iv[s]["qty"] + q,
                         "cost": iv[s]["cost"] + p * q}
                per[s] += 1
        assert abs(per[C.UP] - per[C.DOWN]) <= 1, per


# ------------------------------------ simultaneous two-sided quoting

class TestBothSidesRestTogether:

    def test_from_flat_both_sides_are_quoted_in_one_decision(self):
        res = decide(clip_usd=D("0.05"))
        placed = legs(res)
        assert {o["side"] for o in placed} == {C.UP, C.DOWN}, [
            (o["side"], str(o["price"])) for o in placed]

    def test_the_order_bound_admits_two_orders_per_rung(self, monkeypatch):
        monkeypatch.setenv("L5_BTC5M_ENTRY_MODE", "STRADDLE")
        assert X.live_order_bound() >= 2

    def test_a_resting_leg_on_one_side_does_not_block_the_other(self):
        res = decide(clip_usd=D("0.05"),
                     resting=[resting_order(C.UP, "0.08", "0.6")])
        assert [o for o in legs(res) if o["side"] == C.DOWN]


# ------------------------------- replenishment after a confirmed fill

class TestReplenishment:

    def test_a_filled_side_is_quoted_again_while_inside_both_bounds(self):
        """Repetition means the NEXT clip, not a second mode."""
        first = decide(clip_usd=D("0.05"))
        assert legs(first, C.UP)
        after = decide(clip_usd=D("0.05"),
                       inventory=inv(up_qty="0.6", up_cost="0.048"))
        assert legs(after, C.UP), after.get("why")

    def test_replenishment_stops_at_the_side_cap_and_says_so(self):
        res = decide(clip_usd=D("0.05"),
                     inventory=inv(up_qty="4", up_cost="0.30",
                                   dn_qty="4", dn_cost="0.30"))
        assert legs(res) == []
        assert res["why"] in ("SIDE_CAP_REACHED", "UNMATCHED_CEILING",
                              "NO_EXPOSURE_ROOM")

    def test_no_replenishment_after_the_entry_window_closes(self):
        res = decide(clip_usd=D("0.05"), t=271.0)
        assert legs(res) == []
        assert res["why"] == "ENTRY_WINDOW_CLOSED"

    def test_an_identical_resting_rung_is_not_duplicated(self):
        res = decide(clip_usd=D("0.05"),
                     resting=[resting_order(C.UP, "0.08", "0.6")])
        assert [o for o in legs(res, C.UP)
                if D(str(o["price"])) == D("0.08")] == []


# ------------------------------------- partial fills and unmatched state

class TestPartialFillsAndUnmatchedInventory:

    def test_a_partial_fill_leaves_the_side_quotable_within_the_cap(self):
        res = decide(clip_usd=D("0.05"),
                     inventory=inv(up_qty="0.3", up_cost="0.024"))
        assert legs(res, C.UP) or res["why"] in ("SIDE_CAP_REACHED",
                                                 "UNMATCHED_CEILING")

    def test_resting_orders_count_toward_the_side_cap(self):
        """An order already placed is money already committed."""
        bare = decide(clip_usd=D("0.05"))
        assert legs(bare, C.UP)
        loaded = decide(clip_usd=D("0.05"),
                        resting=[resting_order(C.UP, "0.08", "4",
                                               oid="big")])
        assert legs(loaded, C.UP) == []

    def test_the_unmatched_ceiling_still_refuses_an_adding_leg(self):
        """NOT WEAKENED. With the fallback ON, a leg that would INCREASE
        unmatched exposure past the ceiling is still refused."""
        res = decide(clip_usd=D("0.15"),
                     inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True)
        assert legs(res) == []
        assert res["why"] == "UNMATCHED_CEILING"

    def test_the_side_cap_still_refuses_with_the_fallback_on(self):
        res = decide(clip_usd=D("0.05"),
                     inventory_ranking_enabled=True,
                     ranker_vacuous_hold_falls_back=True,
                     inventory=inv(up_qty="4", up_cost="0.30",
                                   dn_qty="4", dn_cost="0.30"))
        assert legs(res) == []

    def test_exposure_accounting_includes_filled_and_resting(self):
        """Projected exposure must count both, or a cap can be breached
        by fills we have already asked for."""
        exp = C.exposure(inv(up_qty="1", up_cost="0.10"),
                         [resting_order(C.DOWN, "0.20", "1")])
        assert exp["filled_cost_usd"] == D("0.10")
        assert exp["resting_notional_usd"] == D("0.20")
        assert exp["total_exposure_usd"] == D("0.30")


# --------------------------------------------- the economic gate stands

class TestTheEconomicGateIsNotBypassed:

    def test_an_unset_probability_still_refuses_the_mode(self):
        """`straddle_probability` returns None when unset and the mode
        refuses -- the fix must not become a way past that."""
        res = C.decide(book=BOOK, t_in_window_s=10.0, inventory=inv(),
                       resting=[],
                       params=params(clip_usd=D("0.05"),
                                     ranker_vacuous_hold_falls_back=True,
                                     straddle_p_pair_natural=None))
        assert legs(res) == []

    def test_the_fix_authorizes_no_taker(self):
        import inspect
        src = inspect.getsource(C.decide)
        i = src.index("_vacuous = bool(")
        block = src[i:i + 1200]
        for forbidden in ("taker_allowed", "TAKER_COMPLETE", "cross=True"):
            assert forbidden not in block, forbidden

    def test_the_fix_invents_no_fill_rate(self):
        import inspect
        src = inspect.getsource(C._rank_inventory)
        assert "maker_fill_rate=None" in src
        assert "maker_fill_rate=D" not in src
