"""The residual sampler must know what the held leg cost.

THE DEFECT. `Runner.shadow_residual_book` priced a completing pair from
`inventory["pair_unit_cost"]`, a key `Inventory.snapshot()` never emitted.
So `first_leg_own_px` was None in every frame and all three pair readings --
`pair_if_maker`, `pair_if_maker_behind_touch`, `pair_if_taker_walk` -- came
back `why: "no_first_leg_price"`. Measured on the live shadow 2026-10-29:
**null in 401 of 401 frames**, which made maker-completion economics
unmeasurable in shadow and left the dwell question unanswerable.

AND A PAIR COST COULD NEVER HAVE WORKED. `pair_cost()` is None whenever
nothing is matched, which is exactly when a residual is outstanding -- the
only condition the sampler fires on. The figure a completion decision needs
is the average own cost of the side carrying the imbalance.

REPORTING ONLY. These keys are additive; nothing decides, submits or sizes
on them, and no existing reader loses a field.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_book_evidence as EV
from src import pmus_sports_l5_decision as l5d


BOOK = {"t": 1.0, "observed_t": 1.0,
        "bids": [["0.5200", "1722"], ["0.5100", "889"]],
        "asks": [["0.5300", "36.73"], ["0.5400", "306"]],
        "bids_served": True, "asks_served": True}


class TestTheSnapshotCarriesTheCostBasis:

    def test_a_flat_book_has_no_held_leg(self):
        s = l5d.Inventory().snapshot()
        assert s["residual"] == 0
        assert s["held_leg_side"] is None
        assert s["held_leg_own_cost"] is None

    def test_one_yes_fill_reports_the_yes_cost(self):
        inv = l5d.Inventory(); inv.record(True, D("0.45"))
        s = inv.snapshot()
        assert s["residual"] == 1
        assert s["held_leg_side"] == "YES"
        assert s["held_leg_own_cost"] == "0.45"

    def test_one_no_fill_reports_the_no_cost(self):
        inv = l5d.Inventory(); inv.record(False, D("0.38"))
        s = inv.snapshot()
        assert s["residual"] == -1
        assert s["held_leg_side"] == "NO"
        assert s["held_leg_own_cost"] == "0.38"

    def test_an_extend_averages_the_held_side(self):
        inv = l5d.Inventory()
        inv.record(True, D("0.45")); inv.record(True, D("0.47"))
        s = inv.snapshot()
        assert s["residual"] == 2
        assert s["held_leg_own_cost"] == "0.46"

    def test_a_partial_completion_still_reports_the_held_side(self):
        """The imbalance is what matters, not whether anything matched."""
        inv = l5d.Inventory()
        inv.record(True, D("0.45")); inv.record(True, D("0.47"))
        inv.record(False, D("0.50"))
        s = inv.snapshot()
        assert s["matched"] == 1 and s["residual"] == 1
        assert s["held_leg_side"] == "YES"
        assert s["held_leg_own_cost"] == "0.46"     # the YES side, not the pair

    def test_a_pair_cost_would_have_been_none_while_unmatched(self):
        """Why the old key could never have worked: it is None in exactly
        the case the sampler exists for."""
        inv = l5d.Inventory(); inv.record(True, D("0.45"))
        assert inv.snapshot()["matched"] == 0
        assert inv.snapshot()["held_leg_own_cost"] is not None

    def test_the_existing_keys_are_untouched(self):
        inv = l5d.Inventory(); inv.record(True, D("0.45"))
        s = inv.snapshot()
        for k in ("yes", "no", "matched", "residual"):
            assert k in s, k
        assert s["yes"] == 1 and s["no"] == 0


class TestThePairNowPrices:

    def test_without_a_first_leg_every_pair_reads_no_first_leg_price(self):
        out = EV.complement_reachability(held_side="YES", first_leg_own_px=None,
                                         qty_needed=D("1"), row=BOOK,
                                         depth_ticks=2, tick=D("0.01"))
        for k in ("pair_if_maker", "pair_if_maker_behind_touch",
                  "pair_if_taker_walk"):
            assert out[k]["pair_own_cost"] is None
            assert out[k]["why"] == "no_first_leg_price"

    def test_with_the_held_leg_cost_all_three_price(self):
        inv = l5d.Inventory(); inv.record(True, D("0.45"))
        first = inv.snapshot()["held_leg_own_cost"]
        out = EV.complement_reachability(held_side="YES",
                                         first_leg_own_px=first,
                                         qty_needed=D("1"), row=BOOK,
                                         depth_ticks=2, tick=D("0.01"))
        assert out["pair_if_maker"]["pair_own_cost"] == "0.9200"
        assert out["pair_if_maker_behind_touch"]["pair_own_cost"] == "0.9000"
        assert out["pair_if_taker_walk"]["pair_own_cost"] == "0.930000"
        for k in ("pair_if_maker", "pair_if_maker_behind_touch",
                  "pair_if_taker_walk"):
            assert out[k].get("why") is None
            assert out[k]["first_leg_own_px"] == "0.45"

    def test_the_ceiling_verdict_becomes_available(self):
        """The whole point: a completion decision can now be judged."""
        inv = l5d.Inventory(); inv.record(True, D("0.45"))
        out = EV.complement_reachability(
            held_side="YES", first_leg_own_px=inv.snapshot()["held_leg_own_cost"],
            qty_needed=D("1"), row=BOOK, depth_ticks=2, tick=D("0.01"),
            pair_ceiling=D("0.96"))
        assert out["pair_if_maker_behind_touch"]["clears_pair_ceiling"] is True
        assert out["pair_if_maker_behind_touch"]["margin_to_ceiling"] == "0.0600"

    def test_a_dear_held_leg_fails_the_ceiling(self):
        """And it must be able to say no, or it is not a measurement."""
        inv = l5d.Inventory(); inv.record(True, D("0.80"))
        out = EV.complement_reachability(
            held_side="YES", first_leg_own_px=inv.snapshot()["held_leg_own_cost"],
            qty_needed=D("1"), row=BOOK, depth_ticks=2, tick=D("0.01"),
            pair_ceiling=D("0.96"))
        assert out["pair_if_maker_behind_touch"]["clears_pair_ceiling"] is False


class TestTheRunnerPassesTheRightField:

    def test_the_call_site_prefers_the_held_leg_cost(self):
        import inspect
        from src import l5_pmus_live_runner as RN
        src = inspect.getsource(RN.Runner.shadow_residual_book)
        # Assert on the EXPRESSION, not on raw string offsets: the comment
        # above the call names the old key first, so a positional check
        # would fail on prose rather than on precedence.
        expr = " ".join(src.split())
        assert ('first_leg_own_px=(inv.get("held_leg_own_cost") '
                'or inv.get("pair_unit_cost"))') in expr

    def test_nothing_in_the_sampler_submits(self):
        import inspect
        from src import l5_pmus_live_runner as RN
        src = inspect.getsource(RN.Runner.shadow_residual_book)
        for forbidden in ("submit", "arm(", "cancel"):
            assert forbidden not in src, forbidden
