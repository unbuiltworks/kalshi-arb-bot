"""Route B must reach inventory the CONTROLLER opened, not only L5's.

THE DEFECT, 0230z 2026-10-30. The autonomous controller opened 1.91 DOWN.
`maybe_book_completion` -- the only maker-completion route with its own 5 s
cadence and its own measured break-even gate -- begins by reading
`self._last_action_row[slug]`, which is written ONLY from admitted L5 ingest
rows. A controller-opened position has no such row, so the route returned
`NO_ACTION_YET` **263 times** while the position sat unmatched, and the 1.91
contracts went to settlement as residual.

The engine could open autonomously and could not complete what it had
opened. That is a coupling defect, not a risk decision: `BC.decide` never
needed the row -- it prices the complement from the market, the book and the
ledger's own inventory -- and the row was only the vehicle for re-entering
`handle`. A row-free vehicle already existed: the controller interface
`maybe_controller_open` uses.

SEPARATELY, AND ALREADY FIXED: the same window's 163
`controller_open:COMPLEMENT_PAIR_ABOVE_CAP` refusals came from the
double-booked cost basis, not from this route. With the true basis the
completion ceiling is 0.95 and the complement is permitted; with the
$1/share placeholder `adopt_venue_position` installed after the double-book
the ceiling is unsatisfiable and `complement_order` refuses. That is pinned
in `TestTheBasisIsWhatDecidedTheCeiling` below so the two causes are never
conflated again.
"""
from __future__ import annotations

import inspect
from decimal import Decimal as D

from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS

MARKET = "cpc-btc-updown-15m-2026-10-30-0230z"
QTY = D("1.91")
TRUE_BASIS = D("0.7258")        # 1.91 x own 0.38
PLACEHOLDER_BASIS = D("1.9100")  # 1.91 x $1.00, after adoption
BUDGET = D("3.00")


class TestTheBasisIsWhatDecidedTheCeiling:
    """The 163 refusals: one root cause, already repaired upstream."""

    def _ceil(self, basis):
        return TS.completion_ceiling(
            held_side="DOWN", held_qty=QTY, held_cost_usd=basis,
            complete_qty=QTY, residual_budget_usd=BUDGET, tick=D("0.01"))

    def test_the_true_basis_yields_a_usable_ceiling(self):
        assert self._ceil(TRUE_BASIS) == D("0.95")

    def test_the_placeholder_basis_yields_no_ceiling_at_all(self):
        """`completion_ceiling` returns None when a ceiling in force is
        unsatisfiable anywhere on the grid, and the caller then falls back to
        the opening cap -- which is what refused 163 times."""
        assert self._ceil(PLACEHOLDER_BASIS) is None

    def _complement(self, basis):
        return TS.complement_order(
            side="UP", qty=QTY, cost_px=basis / QTY,
            held_qty=QTY, held_cost_usd=basis,
            deficient_qty=D(0), deficient_cost_usd=D(0),
            residual_budget_usd=BUDGET,
            bid=D("0.62"), ask=D("0.63"), tick=D("0.01"),
            min_qty=D("0.01"), depth_ticks=2, book_age_s=D("0.5"),
            market=MARKET, held_side="DOWN",
            bid_shares=D("700"), ask_shares=D("700"))

    def test_with_the_true_basis_the_complement_is_permitted(self):
        r = self._complement(TRUE_BASIS)
        assert r["ok"] is True
        assert r["judged_as"] == "COMPLETION"
        assert D(r["leg"]["price"]) == D("0.60")
        assert r["leg"]["post_only"] is True

    def test_with_the_placeholder_basis_it_is_refused(self):
        r = self._complement(PLACEHOLDER_BASIS)
        assert r["ok"] is False
        assert r["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"

    def test_so_the_duplicate_fill_repair_is_what_restores_completion(self):
        """The upstream fix keeps the basis, which is what makes the ceiling
        computable. Pinned so the causal order stays recorded."""
        assert hasattr(LX, "tracked_coid_for_venue_order")
        assert hasattr(LX, "duplicate_venue_order_coid")


class TestRouteBReachesControllerOpenedInventory:
    """The fix. Source-level, because the method needs a live transport,
    an ingest loop and a book feed to run end to end."""

    @property
    def src(self):
        return inspect.getsource(R.Runner.maybe_book_completion)

    def test_a_missing_l5_row_is_no_longer_the_end_of_the_route(self):
        s = self.src
        assert "controller_route = False" in s
        assert "controller_route = True" in s

    def test_a_flat_book_still_reports_no_action_yet(self):
        """The guard's original case -- before the first admitted action,
        with nothing held -- is unchanged."""
        s = self.src
        assert 'if (_u or 0) == 0 and (_d or 0) == 0:' in s
        assert 'self.stats["book_completion:NO_ACTION_YET"] += 1' in s

    def test_an_unreadable_ledger_fails_closed(self):
        s = self.src
        assert "book_completion:LEDGER_UNREADABLE" in s
        assert "an unreadable ledger is not a flat one" in s

    def test_it_reenters_through_the_row_free_controller_interface(self):
        s = self.src
        assert "self.executor.handle(" in s
        assert "controller={" in s
        assert "LX.CONTROLLER_GENERATED" in s

    def test_it_fabricates_no_action_key_and_no_l5_action(self):
        s = self.src
        assert '"reused_action_key": (None if controller_route else' in s
        assert "fabricates no action key" in s

    def test_the_provenance_and_route_are_both_recorded(self):
        s = self.src
        assert '"route": ("CONTROLLER_REENTRY" if controller_route' in s
        assert '"provenance": (LX.CONTROLLER_GENERATED if controller_route' in s

    def test_the_l5_route_still_reenters_by_row(self):
        s = self.src
        assert "self.handle(row, now)" in s

    def test_the_seeded_side_cannot_choose_the_leg(self):
        """The two-sided branch re-derives side and price from the held
        inventory, exactly as it does for `maybe_controller_open`."""
        s = self.src
        assert "cannot choose the leg" in s
        assert '"relation": "complete"' in s


class TestWhatTheFixMustNotDisturb:

    def test_the_one_resting_order_limit_is_still_checked_first(self):
        s = inspect.getsource(R.Runner.maybe_book_completion)
        assert 'self.stats["book_completion:ORDER_ALREADY_RESTING"] += 1' in s
        assert s.index("ORDER_ALREADY_RESTING") < s.index("REENTERED")

    def test_the_route_is_still_inert_without_its_flag(self):
        s = inspect.getsource(R.Runner.maybe_book_completion)
        assert "if not BC.enabled():" in s
        assert s.index("BC.enabled()") < s.index("controller_route = False")

    def test_the_cadence_is_still_stamped_on_a_refusal(self):
        s = inspect.getsource(R.Runner.maybe_book_completion)
        assert 'if rec.get("reason") != "DECIDE_COOLDOWN":' in s

    def test_the_measured_breakeven_gate_is_unchanged(self):
        """0.9532 is the settled break-even completion pair cost. The fix
        changes REACHABILITY, never the economics."""
        from src import l5_book_completion as BC
        assert D(str(BC.BREAKEVEN_PAIR)) == D("0.9532")

    def test_the_opening_pair_cap_is_unchanged(self):
        assert TS.max_pair_cost() == D("0.96")
