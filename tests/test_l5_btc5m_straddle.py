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
    base = dict(entry_mode=C.STRADDLE, straddle_price=D("0.08"),
                clip_usd=D("0.40"), hedge_enabled=False, reserve_mode="NONE",
                max_exposure_usd=D("3.00"))
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
