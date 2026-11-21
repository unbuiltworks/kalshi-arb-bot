"""The 0230z complement refusal, reproduced — and it was CORRECT.

I reported a live-versus-shadow divergence in the complement decision. That
report was wrong, and this file is the arithmetic that shows why.

**What actually happened.** At 02:30:27 a maker DOWN leg filled: 1.91
contracts, `avg_px 0.6200` wire, own 0.38, `commission 0.0000`. Cash moved
343.21615 -> 342.49035, i.e. $0.7258 = 1.91 x 0.38. With YES then at
0.62/0.63, a maker UP complement would quote two ticks behind its touch at
own **0.60**, so the pair would have cost **0.98** against a 0.96 cap. The
engine refused `COMPLEMENT_PAIR_ABOVE_CAP` and was right to.

**Why I thought otherwise.** I cited a record proposing UP at 0.44 inside
the ceiling. That record is `L5_SHADOW_DECISION`, variant
`ARGMAX@ONE_TICK_BEHIND`, `relation extend`, `q1_size 1` — a SHADOW variant
reasoning about its own simulated inventory (`first_leg_own_cost` 0.41-0.42,
`unmatched_qty` 1), not the live 1.91 @ 0.38. I compared a hypothetical
position against the live book and called the difference a defect.

The tests below pin, from the real functions:

  * `paid` is a per-contract own-terms price, never total cash;
  * the refusal is exact at the observed book, and the cap is not off by a
    rounding or convention error;
  * the same inputs become admissible once the book moves, so the cap is
    binding on economics and not stuck;
  * the completion rescue is what decides a held position, and it declines
    only when completing is worse than carrying.

Nothing here changes the cap, enables crossing, or adds a strategy.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_two_sided_probe as TS

#: the live fill, from the venue's own order row
FILL_QTY = D("1.91")
FILL_WIRE = D("0.6200")
FILL_OWN = D("0.3800")
FILL_CASH = D("0.7258")          # 343.21615 -> 342.49035

#: the book at that instant, and later
BOOK_AT_FILL = (D("0.62"), D("0.63"))
BOOK_LATER = (D("0.44"), D("0.45"))

TICK = D("0.01")
DEPTH_TICKS = 2


class TestPaidIsAPerContractPriceNotTotalCash:
    """The first thing to rule out, because total cash would make the cap
    refuse a pair that is actually fine: 0.7258 + 0.60 = 1.33."""

    def test_the_cash_outlay_matches_qty_times_own_price(self):
        assert (FILL_QTY * FILL_OWN).quantize(D("0.0001")) == FILL_CASH

    def test_own_price_is_the_wire_price_complemented(self):
        """DOWN's own cost is 1 - wire, which is why 0.62 wire is 0.38."""
        assert FILL_OWN == D(1) - FILL_WIRE

    def test_leg_cost_is_accumulated_in_own_terms(self):
        """`apply_fill` is the only writer: `lc[k] += own * qty`. The 1600z
        defect was booking the WIRE number into this own-terms field, and it
        is fixed -- so the basis is 0.7258, not 1.1842."""
        import inspect
        from src import l5_live_execution as LX
        src = inspect.getsource(LX.InventoryStore.apply_fill)
        # still `own * qty`, never the wire number. Since 2026-11-11 the
        # same statement also takes off the $1/share bound that a fill
        # replaces (see tests/test_l5_fill_after_position_update.py), so
        # this pins the own-terms accumulation and not the closing bracket.
        assert "lc[k] = str(D(str(lc.get(k, 0))) + own * qty" in src
        assert "wire * qty" not in src
        assert "own = own_outcome_price(own_side, price, basis=basis)" in src
        wire_basis = FILL_QTY * FILL_WIRE
        assert wire_basis != FILL_CASH

    def test_the_call_site_divides_the_basis_by_the_quantity(self):
        """`cost_px=_avg` where `_avg = _basis / _held`, so what reaches
        `complement_order` is per-contract."""
        import inspect
        from src import l5_live_execution as LX
        src = inspect.getsource(LX.FaithfulExecutor.handle)
        assert "_avg = (_basis / _held)" in src
        assert "cost_px=_avg" in src
        assert (FILL_CASH / FILL_QTY).quantize(D("0.01")) == FILL_OWN


class TestTheRefusalIsExactAtTheObservedBook:

    def test_the_complement_would_quote_at_own_sixty(self):
        bid, ask = BOOK_AT_FILL
        px = TS.quote_own_price("UP", bid=bid, ask=ask, tick=TICK,
                                depth_ticks=DEPTH_TICKS)
        assert px == D("0.60")

    def test_the_pair_exceeds_the_cap_by_two_cents(self):
        bid, ask = BOOK_AT_FILL
        px = TS.quote_own_price("UP", bid=bid, ask=ask, tick=TICK,
                                depth_ticks=DEPTH_TICKS)
        pair = FILL_OWN + px
        assert pair == D("0.98")
        assert pair > TS.max_pair_cost()
        assert TS.max_pair_cost() == D("0.96")

    def test_the_refusal_is_not_a_rounding_artefact(self):
        """Two cents clear of the cap, not a hair over it."""
        bid, ask = BOOK_AT_FILL
        px = TS.quote_own_price("UP", bid=bid, ask=ask, tick=TICK,
                                depth_ticks=DEPTH_TICKS)
        assert (FILL_OWN + px) - TS.max_pair_cost() == D("0.02")

    def test_the_cap_is_inclusive(self):
        """0.96 exactly is permitted; the refusal is `pair > cap`."""
        px = TS.quote_own_price("UP", bid=D("0.60"), ask=D("0.61"),
                                tick=TICK, depth_ticks=DEPTH_TICKS)
        assert FILL_OWN + px == TS.max_pair_cost()
        assert not (FILL_OWN + px) > TS.max_pair_cost()

    def test_ups_touch_is_the_yes_bid(self):
        """Which is why a rising YES makes the UP complement dearer."""
        bid, ask = BOOK_AT_FILL
        assert TS.touch_own_price("UP", bid=bid, ask=ask) == bid
        assert TS.touch_own_price("DOWN", bid=bid, ask=ask) == D(1) - ask


class TestTheCapIsBindingOnEconomicsNotStuck:

    def test_the_same_inventory_is_admissible_once_the_book_moves(self):
        bid, ask = BOOK_LATER
        px = TS.quote_own_price("UP", bid=bid, ask=ask, tick=TICK,
                                depth_ticks=DEPTH_TICKS)
        pair = FILL_OWN + px
        assert px == D("0.42")
        assert pair == D("0.80")
        assert pair <= TS.max_pair_cost()

    @pytest.mark.parametrize("yes_bid,expect_refused", [
        ("0.63", True), ("0.62", True), ("0.61", True),
        # 0.60 quotes at 0.58 for a pair of EXACTLY 0.96, and the test is
        # `pair > cap`, so the cap is inclusive: 0.96 is admissible.
        ("0.60", False), ("0.59", False), ("0.44", False),
    ])
    def test_the_boundary_sits_where_the_arithmetic_puts_it(
            self, yes_bid, expect_refused):
        """`paid 0.38` leaves 0.58 of room, so the complement is refused
        exactly when `yes_bid - 2 ticks > 0.58`, i.e. from 0.61 up."""
        px = TS.quote_own_price("UP", bid=D(yes_bid), ask=D(yes_bid) + TICK,
                                tick=TICK, depth_ticks=DEPTH_TICKS)
        refused = (FILL_OWN + px) > TS.max_pair_cost()
        assert refused is expect_refused, (yes_bid, px, FILL_OWN + px)


class TestTheCompletionRescueIsWhatDecidesAHeldPosition:
    """The opening cap is not the last word on inventory already held."""

    def test_the_opening_refusal_is_offered_to_the_rescue(self):
        import inspect
        src = inspect.getsource(TS.complement_order)
        assert 'judged = "OPENING"' in src
        assert "if opening_no is not None:" in src
        assert "if ceil is None or px > ceil:" in src
        assert 'judged = "COMPLETION"' in src

    def test_the_rescue_needs_the_held_inventory_and_the_budget(self):
        import inspect
        src = inspect.getsource(TS.complement_order)
        assert ("if held_qty is not None and residual_budget_usd is not None:"
                in src)

    def test_zero_deficient_inventory_does_not_disable_the_rescue(self):
        """We held 0 UP. If that read as 'missing' the rescue would never
        run on a first completion, which is exactly when it is needed."""
        import inspect
        src = inspect.getsource(TS.complement_order)
        assert "deficient_qty=(0 if deficient_qty is None else deficient_qty)" \
            in src

    def test_the_rescue_can_only_permit_never_refuse(self):
        import inspect
        src = inspect.getsource(TS.complement_order)
        assert "STRICTLY ADDITIVE" in src
        assert "can only ever convert a" in src

    def test_a_declined_rescue_reports_the_ceiling_it_judged_against(self):
        """So the refusal is explicable. It is returned -- the gap is that
        the controller path never emits it; see the observability finding."""
        import inspect
        src = inspect.getsource(TS.complement_order)
        assert "completion_ceiling=(None if ceil is None" in src
        assert "completion_rescue_available=False" in src


class TestTheDiagnosticFiguresWereHypothetical:
    """The second thing I was asked to check: whether the completion
    diagnostic's worst-case numbers contradict the $0.7258 position."""

    def test_the_defensive_diagnostic_is_explicitly_simulated(self):
        import inspect
        from src import l5_pmus_live_runner as R
        src = inspect.getsource(R.Runner.shadow_defensive_decision)
        assert "simulate=True" in src

    def test_its_quantities_are_shadow_quantities_not_the_live_position(self):
        """Observed values 1.05 and 105.26 against a live 1.91, and
        first_leg_cost 0.41 against a live 0.38. Different objects."""
        live = FILL_QTY
        for shadow_qty in (D("1.05"), D("105.26")):
            assert shadow_qty != live
        assert D("0.41") != FILL_OWN

    def test_a_shadow_pair_cost_of_one_oh_seven_is_not_our_pair(self):
        """1.07 cannot be our pair: our leg cost 0.38 and the cap is 0.96,
        so no admissible complement could produce it."""
        assert D("0.41") + D("0.66") == D("1.07")      # the shadow's own legs
        assert FILL_OWN + D("0.66") == D("1.04")       # ours would differ
