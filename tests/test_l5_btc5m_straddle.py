"""STRADDLE: fixed limits on both sides, for the BTC5m five-minute windows.

WHAT IT IS. Leave one resting buy per side at a FIXED own-price for the
whole window and never requote. If both fill, the pair cost is exactly
twice that price and pays 1.00 -- so there is no adverse selection on the
PRICE at all, only on which legs fill. Measured on the 189-window BTC5m
tape (`analysis/l5/btc5m_accumulation/straddle.py`): positive at every
level from 0.03 to 0.15 and sign-stable across a chronological split,
best around 0.05-0.10, with its 19 double-fills verified as real
reversals rather than crossed books.

NOT ESTABLISHED: t +1.67 overall, OOS t +0.48, and all the profit is 19
windows of 187. These tests pin the MECHANISM, not the edge.

THE STRUCTURAL FACT IT RESTS ON, which is settlement and not
microstructure: a binary resolves, so the losing side's price collapses
toward zero and a resting bid below 0.50 on it fills with near-certainty,
while the winner climbs away. You almost always buy the loser cheaply;
occasionally the window reverses and you buy the winner too.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_controller as C

@pytest.fixture(autouse=True)
def _controller_on(monkeypatch):
    """`decide()` is off unless explicitly switched on, so every test here
    turns it on rather than relying on ambient environment."""
    monkeypatch.setenv("L5_BTC5M_CONTROLLER", "ON")


BOOK = {"bid": "0.60", "ask": "0.61", "age_s": 0.1}
FLAT = {C.UP: {"qty": "0", "cost": "0"}, C.DOWN: {"qty": "0", "cost": "0"}}


def params(**kw):
    #: THE ENTRY-EV GATE IS CONFIGURED HERE because these tests are
    #: about the LADDER's mechanics, not about the gate. The rates are
    #: the measured ones (503 shadow windows); an unset rate refuses
    #: every rung, which is the gate's own behaviour and is covered in
    #: tests/test_l5_straddle_ev.py instead.
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.40"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("3.00"),
                straddle_p_pair_natural=D("0.070"),
                straddle_p_complete=D("0.131"),
                straddle_p_lone_win=D("0.0981"),
                #: AND THE ADVERSE-SELECTION CHARGE IS NEUTRALISED, for the
                #: same reason the rates are fixed: these are mechanics
                #: tests. At the default 0.06 the DEAREST ladder rungs stop
                #: clearing the gate -- correctly, since a 0.10 rung has a
                #: smaller pair payoff and a larger lone-leg cost than a
                #: 0.05 one -- and five ladder-structure tests then measured
                #: the gate instead of the ladder. The charge's own
                #: behaviour, including that truncation, is pinned in
                #: tests/test_l5_straddle_adverse_selection.py.
                adverse_selection_per_share=D("0"))
    base.update(kw)
    return C.Params(**base)


def decide(inventory=None, resting=(), t=10.0, **kw):
    return C.decide(book=BOOK, t_in_window_s=t,
                    inventory=inventory or FLAT, resting=list(resting),
                    params=params(**kw))


class TestItRestsBothSidesAtOneFixedPrice:

    def test_a_flat_window_places_one_leg_on_each_side(self):
        out = decide()
        assert out["action"] == C.OPEN_FAVORITE
        by = {o["side"]: o for o in out["orders"]}
        assert set(by) == {C.UP, C.DOWN}
        for o in by.values():
            assert o["op"] == "PLACE"
            assert o["price"] == D("0.08")
            assert o["post_only"] is True
            assert o["reason"] == "STRADDLE_LEG"

    def test_the_price_does_not_follow_the_book(self):
        """The whole point: a fixed limit cannot drift, so the pair cost
        is known in advance. A requoting straddle would reintroduce the
        adverse selection it exists to avoid."""
        for bid, ask in (("0.20", "0.21"), ("0.60", "0.61"),
                         ("0.88", "0.89")):
            out = C.decide(book={"bid": bid, "ask": ask, "age_s": 0.1},
                           t_in_window_s=10.0, inventory=FLAT, resting=[],
                           params=params())
            assert {o["price"] for o in out["orders"]} == {D("0.08")}

    def test_it_does_not_replace_a_leg_already_resting(self):
        rest = [{"id": "a", "side": C.UP, "price": D("0.08"),
                 "qty": D("5"), "age_s": 3.0}]
        out = decide(resting=rest)
        assert [o["side"] for o in out["orders"]] == [C.DOWN]

    def test_a_side_that_has_filled_is_finished(self):
        """One fill a side, not accumulation. A second leg on the same
        side would be a directional bet, which is a different strategy."""
        inv = {C.UP: {"qty": "5", "cost": "0.40"},
               C.DOWN: {"qty": "0", "cost": "0"}}
        out = decide(inventory=inv)
        assert [o["side"] for o in out["orders"]] == [C.DOWN]

    def test_both_filled_means_nothing_left_to_do(self):
        inv = {C.UP: {"qty": "5", "cost": "0.40"},
               C.DOWN: {"qty": "5", "cost": "0.40"}}
        out = decide(inventory=inv)
        assert out["orders"] == []
        assert out["action"] == C.HOLD_OR_SKIP


class TestThePriceMustMakeAProfitablePairPossible:

    @pytest.mark.parametrize("x", ["0.50", "0.51", "0.64", "0.99"])
    def test_a_price_at_or_above_half_is_refused(self, x):
        """2x >= 1.00 pays 1.00: a locked loss whatever happens. That is a
        contradiction, not an aggressive setting, so it reads as unreadable
        params rather than quietly trading."""
        out = decide(straddle_price=D(x))
        assert out["action"] == C.HOLD_OR_SKIP
        assert out["why"] == "PARAMS_UNREADABLE"
        assert out["orders"] == []

    @pytest.mark.parametrize("x", ["0", "-0.01"])
    def test_a_non_positive_price_is_refused(self, x):
        assert decide(straddle_price=D(x))["why"] == "PARAMS_UNREADABLE"

    def test_the_default_price_trades_nothing(self):
        """The default is the do-nothing value, so enabling the MODE
        without choosing a price cannot put money at risk."""
        assert C.Params().straddle_price == D("0")
        assert decide(straddle_price=C.Params().straddle_price)["why"] \
            == "PARAMS_UNREADABLE"

    def test_the_pair_cost_is_reported(self):
        out = decide(straddle_price=D("0.08"))
        assert out["evaluation"]["entry"]["pair_cost_if_both_fill"] \
            == "0.16"


class TestItStaysInsideTheCap:

    def test_a_leg_that_would_breach_the_cap_is_not_placed(self):
        out = decide(clip_usd=D("0.40"), max_exposure_usd=D("0.30"))
        assert out["orders"] == []
        assert out["evaluation"]["entry"]["why"] == "NO_EXPOSURE_ROOM"

    def test_room_for_one_leg_places_one(self):
        out = decide(clip_usd=D("0.40"), max_exposure_usd=D("0.45"))
        assert len(out["orders"]) == 1

    def test_resting_notional_counts_against_the_cap(self):
        rest = [{"id": "a", "side": C.UP, "price": D("0.08"),
                 "qty": D("5"), "age_s": 1.0}]
        out = decide(resting=rest, max_exposure_usd=D("0.45"))
        assert out["orders"] == []


class TestItDoesNotDisturbTheOtherModes:

    def test_accumulate_is_unchanged_by_a_straddle_price(self):
        """A stray straddle_price must not leak into the other rules."""
        kw = dict(entry_mode=C.ACCUMULATE, hedge_enabled=False,
                  reserve_mode="NONE", max_exposure_usd=D("3.00"))
        a = C.decide(book=BOOK, t_in_window_s=10.0, inventory=FLAT,
                     resting=[], params=C.Params(**kw))
        b = C.decide(book=BOOK, t_in_window_s=10.0, inventory=FLAT,
                     resting=[],
                     params=C.Params(**dict(kw, straddle_price=D("0.08"))))
        assert a["orders"] == b["orders"]
        assert a["why"] == b["why"]

    def test_edge_gated_is_still_the_default_mode(self):
        assert C.Params().entry_mode == C.EDGE_GATED

    def test_straddle_is_a_known_mode(self):
        assert C.STRADDLE in C.ENTRY_MODES


class TestWindowEnd:

    def test_no_new_leg_after_the_entry_window_closes(self):
        out = decide(t=C.Params().no_new_entry_after_s + 1.0)
        assert out["orders"] == []
        assert out["evaluation"]["entry"]["why"] == "ENTRY_WINDOW_CLOSED"


# ======================================================================
class TestTheExecutorWiring:
    """Selectable by env, and OFF until a price is chosen."""

    def test_the_mode_is_selectable(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        assert X.entry_mode() == C.STRADDLE

    def test_an_unknown_mode_narrows_to_edge_gated(self, monkeypatch):
        """A typo must not widen what trades."""
        from src import l5_btc5m_execution as X
        for bad in ("STRADLE", "straddle_", "ACCUMULATED", ""):
            monkeypatch.setenv(X.ENTRY_MODE_ENV, bad)
            assert X.entry_mode() == C.EDGE_GATED

    def test_the_mode_is_case_insensitive(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "straddle")
        assert X.entry_mode() == C.STRADDLE

    @pytest.mark.parametrize("raw", ["0.03", "0.08", "0.49"])
    def test_a_price_inside_the_bound_is_taken(self, monkeypatch, raw):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_PRICE_ENV, raw)
        assert X.straddle_price() == D(raw)

    @pytest.mark.parametrize("raw", ["0.50", "0.51", "0.9", "0", "-0.1",
                                     "abc", ""])
    def test_a_price_outside_the_bound_falls_back_to_off(self, monkeypatch,
                                                         raw):
        """0.50 and above is a locked loss; the fallback is 0, which the
        controller refuses, so the mode trades nothing."""
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_PRICE_ENV, raw)
        assert X.straddle_price() == D("0")

    def test_absent_price_is_off(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.delenv(X.STRADDLE_PRICE_ENV, raising=False)
        assert X.straddle_price() == D("0")

    def test_selecting_the_mode_without_a_price_trades_nothing(self,
                                                               monkeypatch):
        """The two halves together: mode on, price absent, no orders."""
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        monkeypatch.delenv(X.STRADDLE_PRICE_ENV, raising=False)
        out = C.decide(book=BOOK, t_in_window_s=10.0, inventory=FLAT,
                       resting=[],
                       params=C.Params(entry_mode=X.entry_mode(),
                                       straddle_price=X.straddle_price(),
                                       hedge_enabled=False,
                                       reserve_mode="NONE"))
        assert out["orders"] == []
        assert out["why"] == "PARAMS_UNREADABLE"


# ======================================================================
class TestTheLadder:
    """Multiple rungs a side. A LADDER IS LEVERAGE, NOT DIVERSIFICATION:
    every rung fills on the same loser collapse and pays on the same
    reversal, so widening it multiplies both the stake and the variance.
    Measured on the 189-window tape, 0.05-0.20 returns 10x the absolute
    profit of a single rung for 27x the capital with t falling 1.67 ->
    1.17, and 0.05-0.25 and 0.10-0.25 go NEGATIVE out of sample. These
    tests pin the mechanics; the economics are in the research files."""

    LADDER = (D("0.05"), D("0.06"), D("0.07"), D("0.08"), D("0.09"),
              D("0.10"))

    def test_the_charge_truncates_the_ladder_from_the_dear_end(self):
        """NOT HIDDEN BY THE FIXTURE ABOVE. With the adverse-selection
        charge at its production default the dearest rungs stop clearing
        the entry-EV gate, and they go in order: a 0.10 rung pays less on a
        pair and costs more when left alone than a 0.05 rung does."""
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"),
                     adverse_selection_per_share=D("0.06"))
        kept = sorted({o["price"] for o in out["orders"]})
        assert kept, "the cheap end still clears"
        assert kept == sorted(self.LADDER)[:len(kept)], \
            "whatever survives must be the CHEAPEST rungs, contiguously"
        assert D("0.10") not in kept, "the dearest rung is the first to go"

    def test_one_order_per_rung_per_side(self):
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"))
        by_side = {}
        for o in out["orders"]:
            by_side.setdefault(o["side"], []).append(o["price"])
        assert set(by_side) == {C.UP, C.DOWN}
        for prices in by_side.values():
            assert sorted(prices) == sorted(self.LADDER)

    def test_each_rung_gets_its_own_slot(self):
        """Two clips at different prices must be distinguishable, or the
        executor reads the second as a replay of the first."""
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"))
        for side in (C.UP, C.DOWN):
            slots = [o["slot"] for o in out["orders"] if o["side"] == side]
            assert len(slots) == len(set(slots)), slots

    def test_one_side_costs_exactly_the_clip(self):
        """Sizing rule: qty = clip / sum(rungs), so the whole ladder on one
        side costs `clip` and the worst case stays 2 x clip whatever the
        ladder's width. That is what keeps the cap meaningful."""
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"))
        up = [o for o in out["orders"] if o["side"] == C.UP]
        cost = sum(o["price"] * o["qty"] for o in up)
        assert cost <= D("0.45")
        assert cost > D("0.44")          # and it really uses the clip

    def test_the_single_rung_case_is_unchanged(self):
        """The whole point of sum(rungs): with one rung the sum IS the
        price, so the live configuration must be byte-identical."""
        a = decide(straddle_price=D("0.08"), clip_usd=D("0.35"))
        b = decide(straddle_price=D("0.08"), straddle_rungs=(D("0.08"),),
                   clip_usd=D("0.35"))
        assert a["orders"] == b["orders"]

    def test_a_rung_already_resting_is_not_duplicated(self):
        rest = [{"id": "a", "side": C.UP, "price": D("0.07"),
                 "qty": D("1"), "age_s": 2.0}]
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"),
                     resting=rest)
        up = [o["price"] for o in out["orders"] if o["side"] == C.UP]
        assert D("0.07") not in up
        assert len(up) == len(self.LADDER) - 1

    def test_rungs_are_deduplicated_and_sorted(self):
        out = decide(straddle_rungs=(D("0.10"), D("0.05"), D("0.10")),
                     clip_usd=D("0.45"))
        up = [o["price"] for o in out["orders"] if o["side"] == C.UP]
        assert up == [D("0.05"), D("0.10")]

    @pytest.mark.parametrize("bad", [(D("0.05"), D("0.50")),
                                     (D("0.05"), D("0.90")),
                                     (D("0"), D("0.08")),
                                     (D("-0.01"), D("0.08"))])
    def test_any_rung_outside_the_bound_refuses_the_whole_ladder(self, bad):
        """One bad rung is not silently dropped: a ladder that was not
        specified correctly must not half-trade."""
        out = decide(straddle_rungs=bad)
        assert out["why"] == "PARAMS_UNREADABLE"
        assert out["orders"] == []

    def test_the_rungs_are_reported(self):
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"))
        ev = out["evaluation"]["entry"]
        assert ev["rungs"] == [str(r) for r in self.LADDER]
        assert D(ev["ladder_cost_one_side"]) <= D("0.45")

    def test_the_cap_still_bounds_the_ladder(self):
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("0.45"),
                     max_exposure_usd=D("0.20"))
        spent = sum(o["price"] * o["qty"] for o in out["orders"])
        assert spent <= D("0.20")


# ======================================================================
class TestTheLadderEnv:

    def test_a_ladder_parses(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, "0.05,0.06,0.07")
        assert X.straddle_rungs() == (D("0.05"), D("0.06"), D("0.07"))

    def test_it_sorts_and_deduplicates(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, " 0.10 , 0.05 ,0.10 ")
        assert X.straddle_rungs() == (D("0.05"), D("0.10"))

    @pytest.mark.parametrize("raw", ["0.05,0.50", "0.05,0.9", "0.05,abc",
                                     "0.05,0", "0.05,-0.02", "0.05,,0.06"])
    def test_one_bad_rung_rejects_the_whole_ladder(self, monkeypatch, raw):
        """A ladder specified wrongly must not half-trade."""
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, raw)
        assert X.straddle_rungs() == ()

    def test_absent_is_the_single_rung(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.delenv(X.STRADDLE_RUNGS_ENV, raising=False)
        assert X.straddle_rungs() == ()


# ======================================================================
class TestTheClipCeilingIsPerMode:
    """STRADDLE got a higher clip bound by owner decision 2026-11-12. The
    directional modes must NOT have been widened as a side effect."""

    def test_the_straddle_ceiling_is_five_times_the_other(self):
        from src import l5_btc5m_execution as X
        assert X.CLIP_MAX_USD == D("0.45")
        assert X.STRADDLE_CLIP_MAX_USD == D("2.25")

    @pytest.mark.parametrize("raw", ["0.50", "1.00", "2.25"])
    def test_a_big_clip_is_taken_for_the_straddle(self, monkeypatch, raw):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.CLIP_USD_ENV, raw)
        assert X.clip_usd(max_usd=X.STRADDLE_CLIP_MAX_USD) == D(raw)

    @pytest.mark.parametrize("raw", ["0.50", "1.00", "2.25"])
    def test_the_same_clip_is_REFUSED_for_the_other_modes(self, monkeypatch,
                                                          raw):
        """The whole point of scoping it: accumulation keeps $0.45."""
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.CLIP_USD_ENV, raw)
        assert X.clip_usd(default=D("0.10")) == D("0.10")

    @pytest.mark.parametrize("raw", ["2.26", "5.00", "100"])
    def test_above_the_straddle_ceiling_still_falls_back(self, monkeypatch,
                                                         raw):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.CLIP_USD_ENV, raw)
        assert X.clip_usd(default=D("0.10"),
                          max_usd=X.STRADDLE_CLIP_MAX_USD) == D("0.10")

    def test_the_worst_case_is_still_two_clips(self):
        """Whatever the ladder's width, one side costs `clip`."""
        out = decide(straddle_rungs=(D("0.05"), D("0.06"), D("0.07"),
                                     D("0.08"), D("0.09"), D("0.10")),
                     clip_usd=D("2.25"), max_exposure_usd=D("5.00"))
        per_side = {}
        for o in out["orders"]:
            per_side[o["side"]] = per_side.get(o["side"], D(0)) \
                + o["price"] * o["qty"]
        for cost in per_side.values():
            assert cost <= D("2.25")
        assert sum(per_side.values()) <= D("4.50")


# ======================================================================
class TestTheLadderTruncatesEvenly:
    """A half-built ladder is a directional bet wearing a straddle's
    clothes. OBSERVED LIVE 2026-11-12 23:12Z: 12 rungs wanted, 10 orders
    allowed by MAX_LIVE_ORDERS, and because the old code drained one side
    before starting the other, UP took all 6 and DOWN got 4."""

    LADDER = (D("0.05"), D("0.06"), D("0.07"), D("0.08"), D("0.09"),
              D("0.10"))

    def test_orders_alternate_sides(self):
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("2.25"),
                     max_exposure_usd=D("10"))
        sides = [o["side"] for o in out["orders"]]
        # consecutive pairs must cover both sides, so any prefix is balanced
        for i in range(0, len(sides) - 1, 2):
            assert {sides[i], sides[i + 1]} == {C.UP, C.DOWN}, sides

    @pytest.mark.parametrize("keep", [2, 4, 6, 8, 10])
    def test_any_truncation_leaves_the_sides_balanced(self, keep):
        """However many orders an external cap lets through, the ladder
        must not end up lopsided by more than one."""
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("2.25"),
                     max_exposure_usd=D("10"))
        taken = out["orders"][:keep]
        n_up = sum(1 for o in taken if o["side"] == C.UP)
        n_dn = sum(1 for o in taken if o["side"] == C.DOWN)
        assert abs(n_up - n_dn) <= 1, (keep, n_up, n_dn)

    def test_cheap_rungs_are_placed_first(self):
        """Truncation should keep the rungs with the best breakeven."""
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("2.25"),
                     max_exposure_usd=D("10"))
        first_four = [o["price"] for o in out["orders"][:4]]
        assert set(first_four) <= {D("0.05"), D("0.06")}

    def test_a_filled_side_does_not_block_the_other(self):
        inv = {C.UP: {"qty": "5", "cost": "0.40"},
               C.DOWN: {"qty": "0", "cost": "0"}}
        out = decide(straddle_rungs=self.LADDER, clip_usd=D("2.25"),
                     max_exposure_usd=D("10"), inventory=inv)
        assert {o["side"] for o in out["orders"]} == {C.DOWN}
        assert len(out["orders"]) == len(self.LADDER)


# ======================================================================
class TestTheLiveOrderBoundFitsThePlan:
    """A bound smaller than the plan does not shrink the strategy, it
    DEFORMS it. OBSERVED LIVE 2026-11-12 23:10Z: STRADDLE fell through to
    the EDGE_GATED bound of 2, so a six-rung ladder rested two orders,
    both on UP, and the window settled a purely directional -$0.55."""

    def test_the_single_rung_straddle_needs_two(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        monkeypatch.delenv(X.STRADDLE_RUNGS_ENV, raising=False)
        assert X.live_order_bound() == 2

    def test_a_six_rung_ladder_needs_twelve(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV,
                           "0.05,0.06,0.07,0.08,0.09,0.10")
        assert X.live_order_bound() == 12

    @pytest.mark.parametrize("n", [1, 2, 3, 6, 10])
    def test_the_bound_is_two_per_rung(self, monkeypatch, n):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        rungs = ",".join("0.%02d" % (4 + i) for i in range(n))
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, rungs)
        assert X.live_order_bound() == max(2, 2 * n)

    def test_an_unreadable_ladder_falls_back_to_the_single_rung_bound(
            self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "STRADDLE")
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, "0.05,0.90")   # bad rung
        assert X.live_order_bound() == 2

    def test_the_other_modes_are_untouched(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.STRADDLE_RUNGS_ENV, "0.05,0.06,0.07")
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "EDGE_GATED")
        assert X.live_order_bound() == 2
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "ACCUMULATE")
        monkeypatch.setenv(X.MAX_RESTING_ENV, "8")   # 8 is its ceiling
        assert X.live_order_bound() == 8

    def test_accumulate_still_refuses_an_out_of_range_slot_count(
            self, monkeypatch):
        """Worth pinning: `max_resting_entries` caps at 8, so the 10 and 15
        set live on 2026-11-12 both fell back to the default of 4."""
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.ENTRY_MODE_ENV, "ACCUMULATE")
        for raw in ("9", "10", "15"):
            monkeypatch.setenv(X.MAX_RESTING_ENV, raw)
            assert X.max_resting_entries() == 4


class TestPatientCompletionOfALoneLeg:
    """STRADDLE completion: when one leg fills and the other has not, rest
    an ADDITIONAL order on the unfilled side so the pair costs a fixed
    total. The cheap rung STAYS -- this is a fallback, not a replacement.

    WHY IT IS NOT THE EXISTING HEDGE. `hedge_enabled` cancels anything on
    the hedge side that is not at the hedge price, which kills the cheap
    rung. Measured on 183 recorded windows that is WORSE than doing
    nothing: -1.33 to -0.35 c/window against +2.86 for today's behaviour,
    because forfeiting fourteen +84c pairs costs more than rescuing 169
    lone legs. Keeping the rung and ADDING the completion measured +4.79
    c/window at a 0.96 pair cost, t +2.53, and stays above today's figure
    even if the completion fills only 20% of the times it is reachable
    (+3.23), because a filled completion (+4c) beats a held losing leg
    (-8c).

    These tests pin the MECHANISM and the bounds, not the edge.
    """

    HELD_UP = {C.UP: {"qty": "5", "cost": "0.40"},
               C.DOWN: {"qty": "0", "cost": "0"}}
    #: THE COMPLETION IS CAPITAL-HUNGRY and that is the headline operational
    #: fact about it, not a detail. Five shares held at 0.08 cost $0.40;
    #: completing them at 0.88 costs $4.40 -- about 5.5x the whole straddle.
    #: The default $3.00 cap in `params()` cannot fund it, so these tests
    #: raise the ceiling explicitly rather than quietly, and the refusal at
    #: a realistic ceiling is pinned below.
    ROOM = D("8.00")

    def test_off_by_default_so_the_mode_is_unchanged(self):
        out = decide(inventory=self.HELD_UP)
        assert all(o["reason"] != "STRADDLE_COMPLETION"
                   for o in out["orders"])
        comp = (out.get("evaluation") or {}).get("completion") or {}
        assert comp.get("why") == "COMPLETION_DISABLED"

    def test_it_rests_the_completion_at_pair_cost_minus_what_we_paid(self):
        out = decide(inventory=self.HELD_UP, max_exposure_usd=self.ROOM,
                     straddle_completion_pair_cost=D("0.96"))
        comp = [o for o in out["orders"] if o["reason"] == "STRADDLE_COMPLETION"]
        assert len(comp) == 1
        o = comp[0]
        assert o["side"] == C.DOWN          # the side we do NOT hold
        #: 0.96 - 0.08 = 0.88 was the BUDGET CEILING. DOWN's own offer in
        #: this fixture is 1 - 0.60 = 0.40, so the ceiling was through the
        #: book and the controller paid 0.88 for a side available at 0.39.
        #: The completion is now capped one tick inside the offer, so the
        #: pair costs 0.47 instead of 0.96 -- 49 cents a share. The CEILING
        #: still bounds it; it is no longer mistaken for a price.
        assert o["price"] == D("0.39")
        assert o["price"] <= D("0.96") - D("0.08")      # still under budget
        assert o["price"] < D("1") - D("0.60")          # and it rests
        assert o["post_only"] is True
        ev = (out.get("evaluation") or {}).get("completion") or {}
        #: the reported pair cost is now what we would ACTUALLY pay, not the
        #: budget we were willing to pay. 0.08 held + 0.39 capped = 0.47.
        assert ev["pair_cost"] == "0.47"
        assert ev["maker_capped_from"] == "0.88"
        assert ev["maker_cap_why"] == "CEILING_CROSSED_THE_OFFER"

    def test_the_cheap_rung_is_not_cancelled_which_is_the_whole_point(self):
        """Forfeiting the cheap pair measured WORSE than doing nothing."""
        out = decide(inventory=self.HELD_UP, max_exposure_usd=self.ROOM,
                     straddle_completion_pair_cost=D("0.96"))
        assert not [o for o in out["orders"] if o.get("op") == "CANCEL"]

    def test_nothing_unmatched_means_no_completion(self):
        out = decide(straddle_completion_pair_cost=D("0.96"))
        ev = (out.get("evaluation") or {}).get("completion") or {}
        assert ev["why"] == "NOTHING_UNMATCHED"

    def test_a_completion_already_resting_is_not_duplicated(self):
        rest = [{"id": "x", "side": C.DOWN, "price": D("0.88"), "qty": D("5"),
                 "age_s": 1.0}]
        out = decide(inventory=self.HELD_UP, resting=rest,
                     max_exposure_usd=self.ROOM,
                     straddle_completion_pair_cost=D("0.96"))
        ev = (out.get("evaluation") or {}).get("completion") or {}
        assert ev["why"] == "COMPLETION_ALREADY_WORKING"

    def test_it_refuses_rather_than_overspends_when_the_cap_is_realistic(self):
        """THE OPERATIONAL CONSTRAINT. At the $3.00 default cap a 5-share
        completion at 0.88 needs $4.40 and is refused, not trimmed. This is
        the behaviour that decides live sizing: funding a completion costs
        about 5.5x the straddle, so the series exposure ceiling -- not the
        daily loss cap -- is what bounds clip size once this is enabled."""
        out = decide(inventory=self.HELD_UP,
                     straddle_completion_pair_cost=D("0.96"))
        ev = (out.get("evaluation") or {}).get("completion") or {}
        assert ev["why"] == "NO_EXPOSURE_ROOM"
        assert ev["price"] == "0.88"        # it priced it correctly first
        assert not [o for o in out["orders"]
                    if o["reason"] == "STRADDLE_COMPLETION"]

    @pytest.mark.parametrize("bad", [D("0.50"), D("0.40"), D("1.01"), D("2")])
    def test_a_pair_cost_outside_0_50_to_1_00_is_refused_not_guessed(self, bad):
        out = decide(inventory=self.HELD_UP,
                     straddle_completion_pair_cost=bad)
        assert out["why"] == "PARAMS_UNREADABLE"

    def test_the_completion_price_is_reachable_not_a_forward_reference(self):
        """REGRESSION. The first cut called `priced()`, a closure defined
        AFTER the straddle branch returns, so enabling a completion raised
        NameError at runtime while every existing test still passed --
        they never enable it. This exercises the path end to end."""
        out = decide(inventory=self.HELD_UP, max_exposure_usd=self.ROOM,
                     straddle_completion_pair_cost=D("0.96"))
        assert out["action"] == C.OPEN_FAVORITE
        assert any(o["reason"] == "STRADDLE_COMPLETION" for o in out["orders"])


class TestTheCompletionPairCostCeilingIsEnforcedInCode:
    """Near-$1 pair tolerance is LOCKED by L5_OPERATING_MODEL -- it is
    later incentive-era L5 behaviour and stays locked until the economics
    are source-confirmed for this account. The ceiling is therefore in the
    code, not in whoever sets the variable."""

    def test_an_over_target_request_is_clamped_down_not_obeyed(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "1.00")
        assert X.completion_pair_cost() == X.COMPLETION_PAIR_COST_MAX
        #: 0.96 until 2026-11-16, tightened to 0.90 once a 0.96 pair was
        #: measured WORSE than completion off (+5.64 vs +7.10 c/share/window)
        assert X.COMPLETION_PAIR_COST_MAX == D("0.90")

    @pytest.mark.parametrize("raw", ["0.50", "0.30", "0", "-1", "nonsense", ""])
    def test_at_or_below_the_cheap_rung_disables_rather_than_clamps_up(
            self, monkeypatch, raw):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, raw)
        assert X.completion_pair_cost() == D("0")

    def test_absent_means_disabled(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.delenv(X.COMPLETION_PAIR_COST_ENV, raising=False)
        assert X.completion_pair_cost() == D("0")

    def test_a_legal_value_passes_through(self, monkeypatch):
        from src import l5_btc5m_execution as X
        monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, "0.83")
        assert X.completion_pair_cost() == D("0.83")

    def test_the_ceiling_refuses_the_region_measured_worse_than_off(
            self, monkeypatch):
        """0.96 was the ceiling until 2026-11-16 and is now clamped away.
        On 536 shadow windows, with the cheap rung cancelled on completion,
        a 0.96 pair is worth +5.64 c/share/window against +7.10 for
        completion OFF -- so the old ceiling let an operator buy a
        configuration that loses to doing nothing. The live 2026-11-15
        campaign ran at exactly 0.96."""
        from src import l5_btc5m_execution as X
        assert X.COMPLETION_PAIR_COST_MAX == D("0.90")
        for raw in ("0.96", "1.00", "0.93"):
            monkeypatch.setenv(X.COMPLETION_PAIR_COST_ENV, raw)
            assert X.completion_pair_cost() == D("0.90"), raw


# ======================================================================
# THE 1925z DEFECT: COMPLETION AND THE CHEAP RUNG ARE NOT INDEPENDENT
# ======================================================================
class TestCompletionCancelsTheCheapOppositeRung:
    """Completing while the cheap opposite rung still rests buys the SAME
    side twice -- once at 0.08 and once at 0.88.

    MEASURED LIVE 2026-11-15, cpc-btc-updown-5m-2026-11-15-1925z. UP filled
    at 0.08 (t=234s), the completion bought DOWN at 0.88, and the cheap
    DOWN rung filled too. The window ended holding 2 UP @0.08, 2 DOWN @0.08
    and 2 DOWN @0.88: the cheap pair alone pays +1.68, the extra 0.88 leg
    settled worthless at -1.76, realised -0.08. Completing destroyed 1.76.

    The completion was written as "additive ... it can only ever add a
    second way for the window to end matched". The two routes buy the same
    side, so when both fill the expensive one is pure loss. They are now
    mutually exclusive."""

    HELD = {C.UP: {"qty": "2", "cost": "0.16"},     # filled at 0.08
            C.DOWN: {"qty": "0", "cost": "0"}}
    CHEAP_DOWN = {"id": "v-cheap-down", "side": C.DOWN, "price": D("0.08"),
                  "qty": D("2"), "age_s": 120.0}

    def _out(self, resting, **kw):
        return decide(inventory=self.HELD, resting=resting, t=234.0,
                      straddle_completion_pair_cost=D("0.96"),
                      max_exposure_usd=D("10.00"), **kw)

    def test_the_cheap_rung_is_cancelled_when_the_completion_is_placed(self):
        out = self._out([self.CHEAP_DOWN])
        ops = out["orders"]
        placed = [o for o in ops if o["op"] == "PLACE"]
        cancels = [o for o in ops if o["op"] == "CANCEL"]
        # the completion goes on, at 0.96 - 0.08
        #: price now capped inside DOWN's own offer (1 - 0.60 = 0.40); the
        #: point of this test is the CANCEL, which is unchanged.
        assert [(o["side"], o["price"]) for o in placed] == [(C.DOWN, D("0.39"))]
        assert placed[0]["reason"] == "STRADDLE_COMPLETION"
        # and the cheap DOWN rung is withdrawn by EXACT id in the same
        # decision, so the window cannot buy DOWN twice
        assert [o["id"] for o in cancels] == ["v-cheap-down"]
        comp = out["evaluation"]["completion"]
        assert comp["why"] is None
        assert comp["cancelled_cheap_rungs"] == ["v-cheap-down"]

    def test_a_rung_queued_in_the_same_decision_is_withdrawn(self):
        #: the completion's price is capped inside the offer now; this test
        #: is about the WITHDRAWAL of the same-step rung, not the price.
        """The straddle loop runs BEFORE the completion, so with nothing
        resting the same decision would otherwise place the cheap DOWN rung
        AND the 0.88 completion -- the 1925z defect built in one step."""
        out = self._out([])
        placed = [o for o in out["orders"] if o["op"] == "PLACE"]
        #: price now capped inside DOWN's own offer (1 - 0.60 = 0.40); the
        #: point of this test is the CANCEL, which is unchanged.
        assert [(o["side"], o["price"]) for o in placed] == [(C.DOWN, D("0.39"))]
        comp = out["evaluation"]["completion"]
        assert comp["cancelled_cheap_rungs"] == []
        assert comp["withdrew_pending_rungs"] == ["0.08"]

    def test_the_rung_on_OUR_side_is_never_cancelled(self):
        """Only the side being bought expensively is cleared. A resting
        order on the side we already hold is not part of this hazard."""
        ours = {"id": "v-up", "side": C.UP, "price": D("0.08"), "qty": D("2"),
                "age_s": 120.0}
        out = self._out([ours, self.CHEAP_DOWN])
        cancels = [o["id"] for o in out["orders"] if o["op"] == "CANCEL"]
        assert cancels == ["v-cheap-down"]

    def test_an_unidentifiable_rung_never_reaches_the_completion_at_all(self):
        """Every rung here is cancellable by exact id, because
        `_read_resting` refuses the WHOLE decision when any resting order
        lacks one. A guard inside the completion for an un-withdrawable
        rung would be unreachable, so there is none -- this pins the outer
        behaviour that makes it unnecessary."""
        blind = {"id": None, "side": C.DOWN, "price": D("0.08"),
                 "qty": D("2"), "age_s": 120.0}
        out = self._out([blind])
        assert out["why"] == "RESTING_UNREADABLE"
        assert [o["op"] for o in out["orders"]] == []

    def test_the_completion_itself_is_not_cancelled_once_working(self):
        """The guard must not withdraw the completion it placed last step,
        or it would churn its own order every interval."""
        working = {"id": "v-comp", "side": C.DOWN, "price": D("0.88"),
                   "qty": D("2"), "age_s": 30.0}
        out = self._out([working])
        comp = out["evaluation"]["completion"]
        assert comp["why"] == "COMPLETION_ALREADY_WORKING"
        # and the cheap rung is STILL kept off that side: the double-buy
        # does not care which step placed the expensive leg
        assert comp["withdrew_pending_rungs"] == ["0.08"]
        assert out["orders"] == []
