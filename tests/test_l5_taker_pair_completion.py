"""OPTIONAL TAKER PAIR COMPLETION, and the gates that keep it bounded.

WHAT IS NEW AND WHAT IS NOT. No execution machinery was added. Three
gates already existed and all three are kept:

  1. `l5_btc5m_execution._order_fault` refuses every non-post_only order
     (`TAKER_NEVER_SENT`) -- the adapter's hard block.
  2. `l5_persistent_mode.may_submit` refuses a non-post_only order unless
     the standing envelope grants `taker_allowed`.
  3. `pmus_q1_executor.taker_completion_ok` prices the all-in pair with
     the venue's real taker fee and requires >= 1c locked.

The change is that (1) gained a COMPLETION-ONLY exception whose
economics are re-checked against executable depth, book freshness and a
slippage allowance. These tests pin the exception's narrowness, not the
profitability of crossing.

A TAKER ENTRY IS NEVER PERMITTED, at any setting. That is asserted
directly rather than left to configuration.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_btc5m_execution as E
from src import l5_btc5m_controller as C
from src import pmus_q1_executor as q1


FRESH = {"ask": "0.55", "ask_shares": "50", "age_s": 0.5}


@pytest.fixture(autouse=True)
def _notional_cap(monkeypatch):
    """The completion NOTIONAL cap defaults to zero, which refuses
    completion outright. Tests about the other gates set it so they are
    testing what they say they test; `TestTheNotionalCap` removes it
    again."""
    monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, "1.00")


def order(**kw):
    #: qty 1.5 so the clip fits the $1.00 completion notional cap; the cap
    #: itself is exercised in TestTheNotionalCap.
    base = dict(reason="STRADDLE_COMPLETION", side=C.DOWN, qty=D("1.5"),
                book=dict(FRESH), post_only=False, intent="BUY",
                price=D("0.55"))
    base.update(kw)
    return base


def fault(o=None, held_qty=D("5"), basis=D("0.08"), resting=D("0")):
    """`basis` is the DEAREST-LOT price, not a blended average."""
    return E.completion_taker_fault(o or order(), held_qty, basis, resting)


# ------------------------------------------------------ default is off

class TestDefaultsAreOff:
    def test_the_env_switch_defaults_off(self, monkeypatch):
        monkeypatch.delenv(E.TAKER_COMPLETION_ENV, raising=False)
        assert E.taker_completion_allowed() is False

    def test_only_an_explicit_YES_enables_it(self, monkeypatch):
        for raw, want in (("YES", True), ("yes", True), ("Y", False),
                          ("1", False), ("TRUE", False), ("", False)):
            monkeypatch.setenv(E.TAKER_COMPLETION_ENV, raw)
            assert E.taker_completion_allowed() is want, raw

    def test_the_adapter_block_is_unconditional_by_default(self):
        """`taker_ok` defaults False, so every existing caller keeps the
        maker-only block exactly."""
        assert E.Btc5mExecutor._order_fault(order()) == "TAKER_NEVER_SENT"

    def test_a_maker_order_is_unaffected(self):
        assert E.Btc5mExecutor._order_fault(
            order(post_only=True, price=D("0.08"))) is None


# ------------------------------------------- a taker ENTRY is never ok

class TestATakerEntryIsNeverPermitted:
    def test_an_entry_is_refused_even_with_perfect_economics(self):
        assert fault(order(reason="STRADDLE_LEG")) == \
            "TAKER_ONLY_FOR_COMPLETION"

    def test_a_late_favorite_entry_is_refused(self):
        assert fault(order(reason="LATE_FAVORITE")) == \
            "TAKER_ONLY_FOR_COMPLETION"

    def test_only_the_named_completion_reasons_may_cross(self):
        assert E.TAKER_COMPLETION_REASONS == ("STRADDLE_COMPLETION",)


# --------------------------------------------------------- economics

class TestTheEconomicsMustBePositive:
    def test_a_profitable_pair_passes(self):
        assert fault() is None

    def test_an_expensive_ask_is_refused(self):
        assert fault(order(book=dict(FRESH, ask="0.93"))) == \
            "TAKER_PAIR_NOT_PROFITABLE"

    def test_a_dear_first_leg_is_refused(self):
        """The pair is what matters, not the completion price alone: a
        0.50 first leg plus a 0.55 ask cannot lock a cent."""
        assert fault(basis=D("0.50")) == "TAKER_PAIR_NOT_PROFITABLE"

    def test_the_real_taker_fee_is_charged(self):
        """At this account's Tier-1 coefficient, not the top tier."""
        from src import pmus_fees as F
        assert F.taker_theta() == D("0.095")
        assert q1.taker_fee(D("0.55")) > 0

    def test_slippage_is_charged_AGAINST_us(self):
        """A larger allowance must be more conservative, never less."""
        px = D("0.88")
        o = order(book=dict(FRESH, ask=str(px)))
        import os
        os.environ[E.TAKER_SLIPPAGE_TICKS_ENV] = "0"
        try:
            lenient = fault(o)
            os.environ[E.TAKER_SLIPPAGE_TICKS_ENV] = "5"
            strict = fault(o)
        finally:
            os.environ.pop(E.TAKER_SLIPPAGE_TICKS_ENV, None)
        #: a price that passes with no allowance must not pass with five
        #: ticks charged against it
        assert not (lenient is not None and strict is None)

    def test_the_economics_are_priced_at_OUR_LIMIT_when_it_is_worse(self):
        """A hole I left on the first pass: the gate priced against the
        book's ask plus slippage and never compared that to the order's
        own limit. A limit set above that figure can FILL above it, so
        the economics would have been verified at a price we never paid.
        The charged price is now the worse of the two."""
        #: qty 1 so the NOTIONAL cap is not what binds -- this test is
        #: about which PRICE the economics are charged at
        ok = order(qty=D("1"), price=D("0.70"))     # above ask, still locks
        assert fault(ok, held_qty=D("1"), basis=D("0.08")) is None
        dear = order(qty=D("1"), price=D("0.93"))   # our own limit sinks it
        assert fault(dear, held_qty=D("1"),
                     basis=D("0.08")) == "TAKER_PAIR_NOT_PROFITABLE"

    def test_an_unreadable_limit_is_refused(self):
        assert fault(order(price=D("0"))) == "ORDER_UNREADABLE"

    def test_the_slippage_allowance_is_clamped(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_SLIPPAGE_TICKS_ENV, "999")
        assert E.taker_slippage_ticks() == E.TAKER_SLIPPAGE_TICKS_CEILING

    @pytest.mark.parametrize("bad", ["", "abc", "-1"])
    def test_an_unusable_slippage_setting_keeps_the_default(
            self, monkeypatch, bad):
        monkeypatch.setenv(E.TAKER_SLIPPAGE_TICKS_ENV, bad)
        assert E.taker_slippage_ticks() == E.TAKER_SLIPPAGE_TICKS_DEFAULT


# ----------------------------------------------- stale books and depth

class TestStaleBooksAndDepth:
    def test_a_missing_book_is_refused(self):
        o = order()
        del o["book"]
        assert fault(o) == "TAKER_BOOK_ABSENT"

    def test_a_stale_book_cannot_price_a_crossing_order(self):
        assert fault(order(book=dict(FRESH, age_s=9.0))) == \
            "TAKER_BOOK_STALE"

    def test_an_ageless_book_is_refused_rather_than_assumed_fresh(self):
        b = dict(FRESH)
        del b["age_s"]
        assert fault(order(book=b)) == "TAKER_BOOK_STALE"

    def test_depth_must_cover_the_WHOLE_clip(self):
        """A partial completion leaves exactly the unmatched leg we were
        retiring, so partial depth is refused rather than partly taken."""
        assert fault(order(book=dict(FRESH, ask_shares="0.5"))) == \
            "TAKER_DEPTH_SHORT"

    def test_absent_depth_is_not_read_as_unlimited_depth(self):
        b = dict(FRESH)
        del b["ask_shares"]
        assert fault(order(book=b)) == "TAKER_DEPTH_SHORT"

    def test_an_unreadable_ask_is_refused(self):
        assert fault(order(book=dict(FRESH, ask="nonsense"))) == \
            "TAKER_ASK_UNREADABLE"


# ------------------------------------------------- inventory limits

class TestInventoryLimits:
    def test_nothing_held_means_nothing_to_complete(self):
        assert fault(held_qty=D("0"), basis=D("0.08")) == \
            "NOTHING_TO_COMPLETE"

    def test_completing_more_than_held_is_refused(self):
        """Otherwise a 'hedge' opens fresh directional risk on the other
        side."""
        assert fault(order(qty=D("50"))) == "COMPLETION_EXCEEDS_HELD"

    def test_completing_exactly_what_is_held_is_allowed(self):
        assert fault(order(qty=D("1.5")), held_qty=D("1.5")) is None

    def test_a_partial_first_leg_completes_only_that_much(self):
        """Held 1 after a partial fill: a 1.5-lot completion is refused,
        a 1-lot completion is not."""
        assert fault(order(qty=D("1.5")), held_qty=D("1"),
                     basis=D("0.08")) == "COMPLETION_EXCEEDS_HELD"
        assert fault(order(qty=D("1")), held_qty=D("1"),
                     basis=D("0.08")) is None

    def test_the_first_leg_cost_is_taken_from_OUR_ledger_not_the_order(self):
        """A caller that supplied the first leg's cost could make any
        completion look profitable, so the signature takes it separately
        and `_place` passes our own inventory."""
        import inspect
        sig = inspect.signature(E.completion_taker_fault)
        assert list(sig.parameters) == ["order", "held_qty", "basis_px",
                                        "resting_same_side_qty"]
        src = inspect.getsource(E.Btc5mExecutor._place)
        assert "self.inventory(market)" in src


# ------------------------------------------------- the envelope gate

class TestTheEnvelopeStillRefusesIndependently:
    def test_may_submit_refuses_a_crossing_order_without_taker_allowed(self):
        """Gate 2 is untouched: even with the adapter exception open, the
        standing envelope refuses unless it granted taker_allowed."""
        from src import l5_persistent_mode as PM
        import inspect
        src = inspect.getsource(PM.may_submit)
        assert 'post_only is not True and not scope["taker_allowed"]' in src


# ------------------------- the downstream protections still apply

#: REUSING the existing adapter harness rather than building a second
#: one. `Venue` is a strict double -- any transport call the adapter did
#: not declare raises -- and `Rig` already does ledger restart.
from tests.test_l5_btc5m_execution import Rig, MARKET      # noqa: E402


def _crossing(side=C.DOWN, qty="5", ask="0.55"):
    return dict(reason="STRADDLE_COMPLETION", side=side, qty=D(qty),
                price=D(ask), post_only=False, intent="BUY",
                book={"ask": ask, "ask_shares": "500", "age_s": 0.2},
                slot=0)


def _held(ex, monkeypatch, up=("5", "0.40"), down=("0", "0")):
    """THE REAL SHAPE. `Btc5mExecutor.inventory` returns
    {side: {"qty","cost"}}, not tuples -- an earlier version of this
    helper returned tuples and only worked because the code under test
    indexed them positionally. A double that does not match the real
    return makes every assertion about it worthless."""
    monkeypatch.setattr(ex, "inventory", lambda _m: {
        C.UP: {"qty": D(up[0]), "cost": D(up[1])},
        C.DOWN: {"qty": D(down[0]), "cost": D(down[1])}})
    #: lots too, so the dearest-lot basis is derivable
    monkeypatch.setattr(ex, "dearest_basis",
                        lambda _m, side, _q: (
                            D(up[1]) / D(up[0]) if side == C.UP
                            and D(up[0]) > 0 else None))


class TestTheCrossingPathKeepsEveryDownstreamProtection:
    """The exception is inserted BEFORE `_order_fault`, so everything the
    maker path is subject to afterwards -- duplicate intents, pending
    cancellations, the live-order ceiling, exposure -- still applies. A
    gate that only guarded the maker path would be no gate at all."""

    def _place(self, rig, order):
        report = {"refused": [], "placed": []}
        rig.ex._place(MARKET, order, rig.now, report)
        return report

    def test_disabled_by_default_it_refuses_before_anything_else(
            self, tmp_path, monkeypatch):
        monkeypatch.delenv(E.TAKER_COMPLETION_ENV, raising=False)
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        rep = self._place(rig, _crossing())
        assert [r["reason"] for r in rep["refused"]] == \
            ["TAKER_COMPLETION_DISABLED"]
        assert rig.venue.sent == [], "nothing may reach the venue"

    def test_with_the_switch_on_but_nothing_held_it_still_refuses(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch, up=("0", "0"))
        rep = self._place(rig, _crossing())
        assert [r["reason"] for r in rep["refused"]] == ["NOTHING_TO_COMPLETE"]
        assert rig.venue.sent == []

    def test_a_stale_book_refuses_before_the_venue_is_touched(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        o = _crossing()
        o["book"]["age_s"] = 30.0
        rep = self._place(rig, o)
        assert [r["reason"] for r in rep["refused"]] == ["TAKER_BOOK_STALE"]
        assert rig.venue.sent == []

    def test_an_unprofitable_pair_never_reaches_the_venue(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        rep = self._place(rig, _crossing(ask="0.96"))
        assert [r["reason"] for r in rep["refused"]] == \
            ["TAKER_PAIR_NOT_PROFITABLE"]
        assert rig.venue.sent == []

    def test_a_NON_completion_cross_keeps_its_original_refusal(
            self, tmp_path, monkeypatch):
        """THE NARROWING, pinned. Only a completion enters the new path;
        every other crossing order -- a SELL, an exit, a flip, a taker
        entry -- falls through to `_order_fault` and keeps the original
        TAKER_NEVER_SENT. Six existing tests failed when my first version
        intercepted them all, which is how this was caught."""
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        for reason in ("STRADDLE_LEG", "LATE_FAVORITE", "EXIT", None):
            o = _crossing()
            o["reason"] = reason
            rep = self._place(rig, o)
            assert [r["reason"] for r in rep["refused"]] == \
                ["TAKER_NEVER_SENT"], reason
            assert rig.venue.sent == []

    def test_a_crossing_SELL_can_never_be_sent(self, tmp_path, monkeypatch):
        """Even named as a completion and with the switch on: the adapter
        sends BUYs only."""
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, "1.00")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        o = _crossing(qty="1")
        o["intent"] = "SELL"
        rep = self._place(rig, o)
        assert [r["reason"] for r in rep["refused"]] == ["ONLY_MAKER_BUY_SENT"]
        assert rig.venue.sent == []

    def test_the_order_fault_checks_run_after_the_taker_gate(self):
        """Ordering, from source: the crossing permission is computed, then
        `_order_fault`, then the duplicate/cancel-pending/live-order
        checks. So a crossing order is subject to all of them."""
        import inspect
        src = inspect.getsource(E.Btc5mExecutor._place)
        i_gate = src.index("taker_completion_allowed")
        i_fault = src.index("self._order_fault(order, taker_ok)")
        i_dupe = src.index("DUPLICATE_INTENT")
        i_pending = src.index("CANCEL_PENDING")
        i_max = src.index("MAX_LIVE_ORDERS")
        assert i_gate < i_fault < i_dupe < i_pending < i_max


class TestRestartRecoveryWithACrossingOrderOnTheLedger:
    def test_a_restart_rereads_the_ledger_and_keeps_the_switch_off(
            self, tmp_path, monkeypatch):
        """The switch is environment state, not ledger state, so a restart
        must not inherit a crossing permission from a previous process."""
        monkeypatch.setenv(E.TAKER_COMPLETION_ENV, "YES")
        rig = Rig(tmp_path)
        _held(rig.ex, monkeypatch)
        assert E.taker_completion_allowed() is True
        monkeypatch.delenv(E.TAKER_COMPLETION_ENV, raising=False)
        ex2 = rig.restart()
        _held(ex2, monkeypatch)
        report = {"refused": [], "placed": []}
        ex2._place(MARKET, _crossing(), rig.now, report)
        assert [r["reason"] for r in report["refused"]] == \
            ["TAKER_COMPLETION_DISABLED"]

    def test_the_ledger_survives_a_restart(self, tmp_path):
        """Reusing the existing recovery harness: the adapter rebuilds from
        its durable ledger rather than starting empty."""
        rig = Rig(tmp_path)
        before = dict(rig.ex.orders())
        ex2 = rig.restart()
        assert dict(ex2.orders()) == before


class TestTheNotionalCap:
    """What makes an owner's LOSS ceiling hold UNCONDITIONALLY.

    Under the economics gate a completed pair cannot lose, so the loss is
    just the unmatched residual -- but that argument assumes the gate is
    right. This cap bounds the damage if it is not: the worst case becomes
    the unmatched clip plus this figure plus the fee, even if every
    contract settled worthless.
    """

    def test_it_defaults_to_zero_and_refuses_completion(self, monkeypatch):
        monkeypatch.delenv(E.TAKER_COMPLETION_MAX_USD_ENV, raising=False)
        assert E.taker_completion_max_usd() == D("0")
        #: economics are checked first, so this order must be profitable
        #: for the cap to be what refuses it
        assert fault() == "TAKER_COMPLETION_NOTIONAL_UNSET"

    def test_a_clip_over_the_cap_is_refused(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, "1.00")
        #: 5 x 0.56 = 2.80, over a 1.00 cap
        assert fault(order(qty=D("5")), held_qty=D("5")) == \
            "TAKER_COMPLETION_NOTIONAL_CAP"

    def test_it_is_judged_at_the_WORST_price_we_could_pay(self, monkeypatch):
        """Not at the quoted ask: the cap must bound what we might
        actually spend, including slippage and our own limit."""
        monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, "1.00")
        monkeypatch.setenv(E.TAKER_SLIPPAGE_TICKS_ENV, "0")
        assert fault(order(qty=D("1.78"), price=D("0.55"))) is None
        monkeypatch.setenv(E.TAKER_SLIPPAGE_TICKS_ENV, "5")
        #: same clip, five ticks worse, now over the cap
        assert fault(order(qty=D("1.78"), price=D("0.55"))) == \
            "TAKER_COMPLETION_NOTIONAL_CAP"

    def test_over_asking_is_clamped_down(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, "999")
        assert E.taker_completion_max_usd() == \
            E.TAKER_COMPLETION_MAX_USD_CEILING

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-1"])
    def test_an_unusable_cap_refuses_rather_than_guessing(
            self, monkeypatch, bad):
        monkeypatch.setenv(E.TAKER_COMPLETION_MAX_USD_ENV, bad)
        assert E.taker_completion_max_usd() == D("0")


class TestLotLevelCostBasis:
    """The owner's requirement: no optimistic average that hides expensive
    inventory."""

    def test_the_dearest_lot_decides_not_the_blend(self):
        """A side holding 5 at 0.08 and 5 at 0.60 blends to 0.34. A
        completion priced against 0.34 looks profitable while the 0.60 lot
        it actually retires is not."""
        assert fault(basis=D("0.34")) is None        # the blend would pass
        assert fault(basis=D("0.60")) == "TAKER_PAIR_NOT_PROFITABLE"

    def test_an_unknown_basis_refuses_rather_than_guessing(self):
        assert fault(basis=None) == "COST_BASIS_UNKNOWN"
        assert fault(basis=D("0")) == "COST_BASIS_UNKNOWN"

    def test_dearest_basis_takes_the_expensive_lots_first(self, tmp_path):
        """Unit test of the basis itself, over synthetic order rows."""
        rig = Rig(tmp_path)
        rig.ex._ledger["orders"].update({
            "a": {"market": MARKET, "side": C.UP, "filled_qty": "5",
                  "filled_cost": "0.40"},                      # 0.08
            "b": {"market": MARKET, "side": C.UP, "filled_qty": "5",
                  "filled_cost": "3.00"},                      # 0.60
        })
        lots = rig.ex.held_lots(MARKET, C.UP)
        assert [str(px) for px, _q in lots] == ["0.60", "0.08"]
        #: completing 5 retires the DEAR five
        assert rig.ex.dearest_basis(MARKET, C.UP, D("5")) == D("0.60")
        #: completing all ten is the true blend
        assert rig.ex.dearest_basis(MARKET, C.UP, D("10")) == D("0.34")
        #: asking for more than is held is unknowable, not approximated
        assert rig.ex.dearest_basis(MARKET, C.UP, D("11")) is None


class TestRestingOrdersPreventDuplicateExposure:
    def test_a_completion_already_working_refuses(self):
        assert fault(resting=D("5")) == "COMPLETION_ALREADY_WORKING"

    def test_only_the_UNWORKED_deficit_may_be_crossed(self):
        """5 held with 4 already working leaves 1 completable: 1.5 is
        refused, 1 is allowed."""
        assert fault(order(qty=D("1.5")), resting=D("4")) == \
            "COMPLETION_EXCEEDS_HELD"
        assert fault(order(qty=D("1")), resting=D("4")) is None

    def test_the_place_path_sums_our_own_working_quantity(self):
        import inspect
        src = inspect.getsource(E.Btc5mExecutor._place)
        assert "self._resting(market, now)" in src
        assert "working" in src


class TestExecutableDepthIsWalked:
    RUNGS = [["0.55", "1.0"], ["0.56", "8.0"]]

    def test_rungs_are_walked_with_a_haircut_on_displayed_size(self):
        """Displayed size is displayed, not guaranteed. Only 25% is
        believed -- the same haircut the pmus-live lane prices against."""
        assert E.TAKER_DEPTH_HAIRCUT == D("0.25")
        deep = order(book=dict(FRESH, rungs=self.RUNGS))
        assert fault(deep) is None
        thin = order(book=dict(FRESH, rungs=[["0.55", "1.0"]]))
        #: 1.0 displayed is 0.25 usable, short of 1.5
        assert fault(thin) == "TAKER_DEPTH_SHORT"

    def test_the_walk_vwap_prices_the_economics_when_it_is_worse(self):
        dear = order(qty=D("1"), price=D("0.95"),
                     book=dict(FRESH, rungs=[["0.95", "40"]]))
        assert fault(dear, held_qty=D("1")) == "TAKER_PAIR_NOT_PROFITABLE"

    def test_unreadable_rungs_refuse(self):
        bad = order(book=dict(FRESH, rungs=[["x", "y"]]))
        assert fault(bad) in ("TAKER_DEPTH_UNREADABLE", "TAKER_DEPTH_SHORT")


class TestTheMinimumMarginBuffer:
    def test_it_cannot_go_below_the_one_cent_floor(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_MIN_MARGIN_ENV, "0.001")
        assert E.taker_min_margin() == q1.MIN_EDGE
        monkeypatch.setenv(E.TAKER_MIN_MARGIN_ENV, "-5")
        assert E.taker_min_margin() == q1.MIN_EDGE

    def test_it_can_only_ask_for_MORE(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_MIN_MARGIN_ENV, "0.15")
        assert E.taker_min_margin() == D("0.15")
        #: a pair locking ~12c passes at the 1c floor and fails at 15c
        monkeypatch.delenv(E.TAKER_MIN_MARGIN_ENV, raising=False)
        assert fault(basis=D("0.30")) is None
        monkeypatch.setenv(E.TAKER_MIN_MARGIN_ENV, "0.15")
        assert fault(basis=D("0.30")) == "TAKER_PAIR_NOT_PROFITABLE"

    def test_it_is_clamped_at_the_ceiling(self, monkeypatch):
        monkeypatch.setenv(E.TAKER_MIN_MARGIN_ENV, "9")
        assert E.taker_min_margin() == E.TAKER_MIN_MARGIN_CEILING
