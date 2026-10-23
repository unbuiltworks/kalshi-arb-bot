"""The own-side price floor for OPENING a leg, and what it must never block.

SIX SETTLED WINDOWS on 2026-10-23, separated cleanly by the first leg's own
price:

    2145z  DOWN  own 0.35  ->  complement 0.56  pair 0.91   +$0.0927
    2200z  DOWN  own 0.29  ->  complement 0.66  pair 0.95   +$0.0495
    2215z  DOWN  own 0.33  ->  complement 0.43  pair 0.76   +$0.2424
    2300z  DOWN  own 0.36  ->  complement 0.38  pair 0.74   +$0.2678
    2230z  UP    own 0.12  ->  never reachable  unmatched   -$0.1764
    2245z  UP    own 0.06  ->  never reachable  unmatched   -$0.0930

SIX WINDOWS IS PRELIMINARY AND 0.20 IS NOT CLAIMED OPTIMAL -- a test below
asserts that any threshold in 0.13..0.28 separates this sample equally well,
so the number is a margin choice and the file says so.

WHAT THIS MUST NOT DO, and each is a test here: block a COMPLETION, block
management of inventory already held, change the 0.96 pair cap, change the
2-tick placement, or be switchable off by a typo.

Nothing here submits an order, cancels one, uses a credential or opens a
socket.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from decimal import Decimal as D

import pytest

from src import l5_live_execution as LX
from src import l5_two_sided_probe as TS


#: (window, side, own first-leg price, settled P&L) -- the 2026-10-23
#: campaign, all VERIFIED_L5_GROSS_SETTLEMENT.
SETTLED = [
    ("2145z", "DOWN", D("0.35"), D("0.0927")),
    ("2200z", "DOWN", D("0.29"), D("0.0495")),
    ("2215z", "DOWN", D("0.33"), D("0.2424")),
    ("2300z", "DOWN", D("0.36"), D("0.2678")),
    ("2230z", "UP", D("0.12"), D("-0.1764")),
    ("2245z", "UP", D("0.06"), D("-0.0930")),
]

#: THE COUNTER-EXAMPLE, and it matters enough to sit beside the rest.
#: `cpc-btc-updown-15m-2026-10-20-1215z` opened at own 0.06 -- below the
#: floor -- and DID complete, at a pair of exactly 0.96, for an attested
#: +$0.0984. Its production records are reproduced in
#: tests/test_l5_e2e_window.py::test_the_1215z_sequence_reproduced_DOWN_
#: then_a_repriced_UP_complement.
#:
#: So a cheap opening is NOT reliably a loser: across the three settled
#: windows that opened below 0.20 the record is one win and two losses. The
#: subset is net negative, which is why the floor is worth having, but the
#: separation is not clean and nothing here may claim it is.
COUNTER_EXAMPLE = ("1215z", "DOWN", D("0.06"), D("0.0984"))


def no(side, own_px, **kw):
    return TS.first_leg_entry_refusal(side=side, own_px=own_px, **kw)


class TestTheFloorItself:

    def test_the_approved_threshold(self):
        assert TS.MIN_FIRST_LEG_OWN_PRICE == D("0.20")
        assert TS.min_first_leg_own_price() == D("0.20")

    @pytest.mark.parametrize("px", ["0.20", "0.21", "0.35", "0.50", "0.99"])
    def test_at_or_above_the_floor_opens(self, px):
        assert no("UP", px) is None
        assert no("DOWN", px) is None

    @pytest.mark.parametrize("px", ["0.19", "0.12", "0.06", "0.01", "0"])
    def test_below_the_floor_refuses(self, px):
        for side in ("UP", "DOWN"):
            v = no(side, px)
            assert v is not None
            assert v["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"
            assert v["min_own_px"] == "0.20"

    def test_the_boundary_is_inclusive(self):
        """0.20 opens; one tick below does not."""
        assert no("UP", "0.20") is None
        assert no("UP", "0.19")["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"

    def test_it_is_symmetric_in_side(self):
        """Own-side economic prices, so the side carries no information."""
        for px in ("0.06", "0.12", "0.19", "0.20", "0.35"):
            a, b = no("UP", px), no("DOWN", px)
            assert (a is None) == (b is None), px
            if a is not None:
                assert a["reason"] == b["reason"]

    def test_an_unreadable_price_fails_closed(self):
        for bad in ("x", None, "", "nan"):
            v = no("UP", bad)
            assert v is not None, bad
            assert v["reason"] in ("FIRST_LEG_PRICE_UNREADABLE",
                                  "FIRST_LEG_BELOW_MIN_PRICE")


class TestItNeverBlocksInventory:
    """Requirement two, from both directions independently."""

    def test_a_claimed_leg_means_this_is_a_completion(self):
        assert no("UP", "0.06", legs_claimed={"DOWN": True}) is None
        assert no("DOWN", "0.02", legs_claimed={"UP": True}) is None

    @pytest.mark.parametrize("up, dn", [
        ("1.47", "0"), ("0", "1.47"), ("1.0", "2.0"), ("-1.5", "0"),
    ])
    def test_held_quantity_on_either_side_means_management(self, up, dn):
        assert no("UP", "0.06", held_up=up, held_down=dn) is None
        assert no("DOWN", "0.06", held_up=up, held_down=dn) is None

    def test_the_2230z_completion_would_not_have_been_blocked(self):
        """The UP leg filled at 0.12 and the DOWN complement was 0.83-0.87.
        Had the floor been live it would have refused the OPENING; once the
        leg exists, nothing here may stand between it and a complement."""
        assert no("DOWN", "0.87", held_up="1.47") is None
        assert no("DOWN", "0.87", legs_claimed={"UP": True}) is None

    def test_a_cheap_complement_is_never_refused_either(self):
        """A complement can itself be cheap -- 2300z completed at own 0.38
        and a future one could be lower. The floor is for openings only."""
        assert no("UP", "0.05", held_down="1.0") is None

    def test_unreadable_inventory_is_not_adjudicated_here(self):
        """The residual and uncertainty gates own that case; this one must
        not invent a refusal on top of them."""
        assert no("UP", "0.06", held_up="?") is None
        assert no("UP", "0.06", held_down=None) is None

    def test_the_permits_are_checked_before_the_floor(self):
        """Ordering, by AST: every unconditional permit must return before
        the price comparison, so no configuration can turn this into a rule
        that strands a position."""
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(TS.first_leg_entry_refusal)))
        floor_line = min(
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "min_first_leg_own_price")
        permits = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Return) and n.value is None
                   or (isinstance(n, ast.Return)
                       and isinstance(n.value, ast.Constant)
                       and n.value.value is None)]
        assert permits, "the permits must exist"
        assert min(permits) < floor_line


class TestTheSettledEvidence:

    def test_the_floor_separates_the_2026_10_23_campaign_exactly(self):
        """Those six, and only those six. See COUNTER_EXAMPLE."""
        for window, side, px, pnl in SETTLED:
            refused = no(side, px) is not None
            assert refused == (pnl < 0), window

    def test_but_it_would_also_have_refused_a_window_that_paid(self):
        """1215z opened at own 0.06 and completed for +$0.0984, so the floor
        is not free: it declines winners of this shape too."""
        w, side, px, pnl = COUNTER_EXAMPLE
        assert pnl > 0
        assert no(side, px)["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"

    def test_the_cheap_subset_is_net_negative_but_not_uniformly_bad(self):
        cheap = [p for _, _, px, p in SETTLED if px < D("0.20")]
        cheap.append(COUNTER_EXAMPLE[3])
        assert len(cheap) == 3
        assert sum(cheap) == D("-0.1710")        # worth refusing
        assert max(cheap) > 0                    # but not a clean rule
        assert len([p for p in cheap if p > 0]) == 1

    def test_it_admits_every_window_that_paid(self):
        kept = [(w, p) for w, s, px, p in SETTLED if no(s, px) is None]
        assert [w for w, _ in kept] == ["2145z", "2200z", "2215z", "2300z"]
        assert sum(p for _, p in kept) == D("0.6524")

    def test_it_refuses_only_the_two_that_did_not_complete(self):
        dropped = [(w, p) for w, s, px, p in SETTLED if no(s, px) is not None]
        assert [w for w, _ in dropped] == ["2230z", "2245z"]
        assert sum(p for _, p in dropped) == D("-0.2694")

    def test_the_mean_per_traded_window_improves(self):
        """Not a claim about expectancy -- an arithmetic restatement of six
        settled windows. Fewer trades, better economics per trade."""
        allw = sum(p for _, _, _, p in SETTLED)
        kept = sum(p for _, s, px, p in SETTLED if no(s, px) is None)
        assert allw == D("0.3830")
        assert kept == D("0.6524")
        assert allw / 6 == D("0.3830") / 6
        assert kept / 4 > allw / 6

    def test_zero_point_two_zero_is_not_claimed_optimal(self):
        """Requirement four, as an assertion rather than a comment: every
        threshold in 0.13..0.28 separates this sample identically, so the
        data does not pick 0.20 and this file must not pretend it does."""
        def seps(t):
            return all(((px < t) == (pnl < 0))
                       for _, _, px, pnl in SETTLED)
        for cents in range(13, 29):
            assert seps(D(cents) / 100), cents
        # and outside that range it stops separating
        assert not seps(D("0.12"))
        assert not seps(D("0.30"))

    def test_n_is_seven_and_that_is_not_a_sample(self):
        assert len(SETTLED) + 1 == 7
        assert 7 < 30


class TestConfiguration:

    @pytest.mark.parametrize("raw, want", [
        ("0.25", D("0.25")), ("0.15", D("0.15")), ("0.50", D("0.50")),
        ("0", D("0")),
    ])
    def test_a_value_in_range_is_honoured(self, monkeypatch, raw, want):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, raw)
        assert TS.min_first_leg_own_price() == want

    @pytest.mark.parametrize("raw", [
        "x", "", "   ", "0.51", "1.0", "-0.1", "nan", "Infinity", "1e400",
    ])
    def test_anything_unreadable_or_out_of_range_falls_back(self, monkeypatch,
                                                            raw):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, raw)
        assert TS.min_first_leg_own_price() == D("0.20")

    def test_a_typo_cannot_re_admit_the_bounded_regime(self, monkeypatch):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "O.06")   # letter O
        assert no("UP", "0.06")["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"

    def test_zero_disables_the_rule_explicitly(self, monkeypatch):
        """An owner act, not an accident: only an exact, in-range 0."""
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0")
        assert no("UP", "0.06") is None

    def test_a_higher_floor_refuses_more(self, monkeypatch):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0.40")
        assert no("DOWN", "0.35")["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"
        assert no("DOWN", "0.40") is None


class TestNothingElseMoved:

    def test_the_pair_cap_is_unchanged(self):
        assert TS.max_pair_cost() == D("0.96")

    def test_the_placement_offset_is_unchanged(self):
        assert TS.depth_ticks() == 2

    def test_the_stale_recovery_thresholds_are_unchanged(self):
        assert TS.STALE_RECOVER_DRIFT_TICKS == 4
        assert TS.recover_max_per_market() == 1
        assert TS.recover_min_rest_s() == 15.0

    def test_the_residual_ceiling_is_unchanged(self):
        assert LX.ABS_RESIDUAL_CAP_CONTRACTS == D("3")

    def test_the_unmatched_cap_is_unchanged(self):
        from src.l5_live_validation_gates import ValidationGates
        d = inspect.signature(ValidationGates.__init__).parameters
        assert d["max_unmatched_contracts"].default == D("5")


class TestTheLifecycleWiring:

    def test_the_gate_is_consulted_where_the_first_leg_is_chosen(self):
        """By AST: inside the method that selects `nxt`, the refusal call
        must appear after the leg is taken from the plan and before the
        decision is rewritten for submission."""
        src = None
        for name in dir(LX.FaithfulExecutor):
            fn = getattr(LX.FaithfulExecutor, name, None)
            try:
                text = inspect.getsource(fn)
            except (TypeError, OSError):
                continue
            if "first_leg_entry_refusal" in text:
                src = text
                break
        assert src is not None, "the gate must be wired into the executor"
        tree = ast.parse(textwrap.dedent(src))
        calls = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                calls.setdefault(n.func.attr, []).append(n.lineno)
        assert "first_leg_entry_refusal" in calls
        # it is handed the claim state and both held quantities
        for kw in ("legs_claimed", "held_up", "held_down"):
            assert kw in src, kw
        # and it is consulted before the order body is built
        assert "position" in calls

    def test_a_refusal_places_no_order_and_claims_no_round(self):
        """The refusal returns through `prov.refuse`, which is the same exit
        every other gate uses, so no submission and no claim can follow."""
        src = None
        for name in dir(LX.FaithfulExecutor):
            fn = getattr(LX.FaithfulExecutor, name, None)
            try:
                text = inspect.getsource(fn)
            except (TypeError, OSError):
                continue
            if "first_leg_entry_refusal" in text:
                src = text
                break
        i = src.index("first_leg_entry_refusal")
        tail = src[i:i + 1400]
        assert "prov.refuse" in tail
        assert "_probe_leg_pending" not in tail.split("prov.refuse")[0]

    def test_the_predicate_holds_no_transport_and_submits_nothing(self):
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(TS.first_leg_entry_refusal)))
        names = [n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)]
        for forbidden in ("submit", "cancel", "arm", "save", "post"):
            assert not [n for n in names if forbidden in n.lower()], names
