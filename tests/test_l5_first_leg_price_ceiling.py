"""The own-side price CEILING for OPENING a leg -- the floor's mirror image.

WHY IT EXISTS. `MIN_FIRST_LEG_OWN_PRICE` bounds the cheap end because the
complement, priced `1 - wire_ask - 2 ticks`, runs away as the cheap side gets
cheaper. The expensive end fails for the mirror-image reason and was
unguarded: a dear opening leg is most of a $1.00 pair by itself, so the
strand it leaves is nearly the whole obligation.

THE EVIDENCE IS OUT-OF-SAMPLE, and that is the whole justification for the
number. `l5_residual_state_replay` over L5's OWN BTC/ETH 5m+15m history --
2,216 windows with a first leg, 1,610 paired -- gives completion and EV by
the opening own price, which is known before the order is sent. EV turns
negative above 0.60 and stays negative, across 849 windows.

WHAT THIS FILE IS CAREFUL NOT TO CLAIM. On our own 8-window 20261025 PMUS
campaign a price cap is knife-edge -- 0.60 is worth +$1.1250 and 0.65 is
WORSE than doing nothing -- because a single strand at own 0.62 sets the
parameter. `L5_FOUR_POLICY_REPLAY.md` rejected a price cap on that ground.
Tests below assert BOTH the out-of-sample support and the in-sample
fragility, so neither can be quoted without the other.

WHAT IT MUST NEVER DO, each a test here: block a COMPLETION, block
management of inventory already held, disable or weaken the 0.20 floor, be
switched off by a typo, be switched off as a side effect of disabling the
floor, or change any size, cutoff, pair ceiling or risk limit.

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


#: OUT-OF-SAMPLE: L5's own 2,216 windows with a first leg, bucketed by the
#: opening own price. (lo, hi, n, completion_rate, EV_per_contract)
#: Source: L5_STRAND_POLICY_EVALUATION.md, `l5_residual_state_replay.py`.
OOS = [
    (D("0.0"), D("0.1"), 95, D("0.284"), D("-0.0017")),
    (D("0.1"), D("0.2"), 114, D("0.675"), D("0.0322")),
    (D("0.2"), D("0.3"), 109, D("0.807"), D("0.0485")),
    (D("0.3"), D("0.4"), 142, D("0.803"), D("0.0273")),
    (D("0.4"), D("0.5"), 315, D("0.889"), D("0.0566")),
    (D("0.5"), D("0.6"), 494, D("0.852"), D("0.0208")),
    (D("0.6"), D("0.7"), 408, D("0.762"), D("-0.0633")),
    (D("0.7"), D("0.8"), 257, D("0.607"), D("-0.2220")),
    (D("0.8"), D("0.9"), 184, D("0.467"), D("-0.3971")),
]

#: the measured mean pair profit per contract behind the EV column
MEAN_PAIR_PROFIT = D("0.1199")

#: IN-SAMPLE: the 20261025 PMUS campaign's opening episodes, by own price,
#: with the settled outcome of each. Source: L5_FOUR_POLICY_REPLAY.md.
#: (label, own_px, settled_pnl, completed)
CAMPAIGN = [
    ("0030z", D("0.55"), None, True),
    ("0045z", D("0.50"), None, True),
    ("0115z", D("0.48"), None, True),
    ("0100z-B", D("0.75"), D("0.0344"), True),
    ("0130z", D("0.62"), D("-1.1594"), False),
]

#: the replay's no-policy baseline, asserted there against the realized
#: campaign net, and its result under a 0.60 opening-price cap
CAMPAIGN_BASELINE_NET = D("0.1256")
CAMPAIGN_NET_AT_060 = D("1.2506")

#: the 2026-10-23 six settled windows the FLOOR was derived from -- none of
#: them is dear, so the ceiling must leave that evidence set alone.
FLOOR_ERA = [D("0.35"), D("0.29"), D("0.33"), D("0.36"), D("0.12"), D("0.06")]


def no(side, own_px, **kw):
    return TS.first_leg_entry_refusal(side=side, own_px=own_px, **kw)


class TestTheCeilingItself:

    def test_the_threshold(self):
        assert TS.MAX_FIRST_LEG_OWN_PRICE == D("0.60")
        assert TS.max_first_leg_own_price() == D("0.60")

    @pytest.mark.parametrize("px", ["0.20", "0.35", "0.45", "0.59", "0.60"])
    def test_at_or_below_the_ceiling_opens(self, px):
        assert no("UP", px) is None
        assert no("DOWN", px) is None

    @pytest.mark.parametrize("px", ["0.61", "0.62", "0.75", "0.90", "0.99"])
    def test_above_the_ceiling_refuses(self, px):
        for side in ("UP", "DOWN"):
            v = no(side, px)
            assert v is not None
            assert v["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"
            assert v["max_own_px"] == "0.60"
            assert v["env"] == TS.MAX_FIRST_LEG_ENV

    def test_the_boundary_is_inclusive(self):
        """0.60 opens; one cent above does not. This matches the replay's
        own cap semantics, which is why its refused sets reproduce below."""
        assert no("UP", "0.60") is None
        assert no("UP", "0.61")["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"

    def test_it_is_symmetric_in_side(self):
        for px in ("0.45", "0.60", "0.61", "0.75", "0.95"):
            a, b = no("UP", px), no("DOWN", px)
            assert (a is None) == (b is None), px
            if a is not None:
                assert a["reason"] == b["reason"]

    def test_the_permitted_band_is_exactly_the_two_bounds(self):
        """0.20 <= own <= 0.60 opens, and nothing outside it does."""
        for cents in range(0, 101):
            p = D(cents) / 100
            permitted = no("UP", str(p)) is None
            assert permitted == (D("0.20") <= p <= D("0.60")), p


class TestItNeverBlocksInventory:
    """The ceiling must be as incapable of stranding a position as the
    floor is. Each permit is checked against a DEAR price, since that is
    the only case the ceiling could otherwise refuse."""

    def test_a_claimed_leg_means_this_is_a_completion(self):
        assert no("UP", "0.95", legs_claimed={"DOWN": True}) is None
        assert no("DOWN", "0.88", legs_claimed={"UP": True}) is None

    @pytest.mark.parametrize("up, dn", [
        ("1.47", "0"), ("0", "1.47"), ("1.0", "2.0"), ("-1.5", "0"),
    ])
    def test_an_imbalance_means_management(self, up, dn):
        assert no("UP", "0.95", held_up=up, held_down=dn) is None
        assert no("DOWN", "0.95", held_up=up, held_down=dn) is None

    def test_a_dear_complement_is_never_refused(self):
        """The 2230z complement was own 0.83-0.87. Once the leg exists,
        nothing here may stand between it and a complement -- and a dear
        complement is the normal shape when the first leg was cheap."""
        for px in ("0.83", "0.87", "0.94", "0.99"):
            assert no("DOWN", px, held_up="1.47") is None
            assert no("DOWN", px, legs_claimed={"UP": True}) is None

    def test_unreadable_inventory_is_not_adjudicated_here(self):
        assert no("UP", "0.95", held_up="?") is None
        assert no("UP", "0.95", held_down=None) is None

    def test_a_completed_pair_faces_the_ceiling_again(self):
        """up == dn is not an imbalance, so a further leg is a fresh
        OPENING -- the same reasoning the floor uses for 2026-10-26 2115z."""
        v = no("UP", "0.75", held_up="2.18", held_down="2.18")
        assert v is not None
        assert v["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"

    def test_the_permits_are_checked_before_the_ceiling(self):
        """Ordering, by AST: every unconditional permit returns before the
        ceiling is read, so no configuration can turn this into a rule that
        strands a position."""
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(TS.first_leg_entry_refusal)))
        ceil_line = min(
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "max_first_leg_own_price")
        permits = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Return)
                   and (n.value is None
                        or (isinstance(n.value, ast.Constant)
                            and n.value.value is None))]
        assert permits
        assert min(permits) < ceil_line


class TestTheFloorIsUntouched:

    def test_the_floor_threshold_is_unchanged(self):
        assert TS.MIN_FIRST_LEG_OWN_PRICE == D("0.20")
        assert TS.min_first_leg_own_price() == D("0.20")

    @pytest.mark.parametrize("px", ["0.19", "0.12", "0.06", "0.01", "0"])
    def test_below_the_floor_still_refuses_with_its_own_reason(self, px):
        v = no("UP", px)
        assert v is not None
        assert v["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"
        assert v["min_own_px"] == "0.20"

    def test_an_unreadable_price_still_fails_closed(self):
        for bad in ("x", None, "", "nan"):
            v = no("UP", bad)
            assert v is not None, bad
            assert v["reason"] in ("FIRST_LEG_PRICE_UNREADABLE",
                                  "FIRST_LEG_BELOW_MIN_PRICE")

    def test_the_unreadable_payload_still_carries_the_floor(self):
        """Adding a key is safe; removing or renaming one is not."""
        v = no("UP", "x")
        if v["reason"] == "FIRST_LEG_PRICE_UNREADABLE":
            assert v["min_own_px"] == "0.20"
            assert v["max_own_px"] == "0.60"

    def test_the_floors_own_evidence_set_is_unaffected(self):
        """None of the six 2026-10-23 windows is dear, so the ceiling
        changes no verdict on the sample the floor was derived from."""
        for px in FLOOR_ERA:
            v = no("DOWN", str(px))
            if v is not None:
                assert v["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"


class TestNeitherBoundDisablesTheOther:
    """The restructure's one real hazard: the floor's `<= 0` escape used to
    return before the price was parsed."""

    def test_disabling_the_floor_leaves_the_ceiling_in_force(self, monkeypatch):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0")
        assert no("UP", "0.06") is None                  # floor is off
        v = no("UP", "0.75")
        assert v is not None and v["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"

    def test_disabling_the_ceiling_leaves_the_floor_in_force(self, monkeypatch):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "0")
        assert no("UP", "0.75") is None                  # ceiling is off
        v = no("UP", "0.06")
        assert v is not None and v["reason"] == "FIRST_LEG_BELOW_MIN_PRICE"

    def test_disabling_both_permits_everything_priceable(self, monkeypatch):
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0")
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "0")
        for px in ("0.01", "0.06", "0.60", "0.75", "0.99"):
            assert no("UP", px) is None, px

    def test_with_both_disabled_an_unreadable_price_is_permitted(
            self, monkeypatch):
        """Documenting the one behaviour that is unchanged from before this
        rule existed: with no bound to apply there is nothing to judge."""
        monkeypatch.setenv(TS.MIN_FIRST_LEG_ENV, "0")
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "0")
        assert no("UP", "x") is None


class TestConfiguration:

    @pytest.mark.parametrize("raw, want", [
        ("0.50", D("0.50")), ("0.55", D("0.55")), ("0.65", D("0.65")),
        ("0.80", D("0.80")), ("1.00", D("1.00")), ("0", D("0")),
    ])
    def test_a_value_in_range_is_honoured(self, monkeypatch, raw, want):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, raw)
        assert TS.max_first_leg_own_price() == want

    @pytest.mark.parametrize("raw", [
        "x", "", "   ", "1.01", "-0.1", "nan", "Infinity", "1e400", "O.60",
    ])
    def test_anything_unreadable_or_out_of_range_falls_back(self, monkeypatch,
                                                            raw):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, raw)
        assert TS.max_first_leg_own_price() == D("0.60")

    @pytest.mark.parametrize("raw", ["0.49", "0.30", "0.10", "0.01"])
    def test_a_ceiling_below_the_best_bucket_is_refused(self, monkeypatch,
                                                        raw):
        """DELIBERATELY ASYMMETRIC with the floor. 0.4-0.5 is the best
        measured bucket in the whole curve -- 88.9% completion, +$0.0566 per
        contract over 315 windows -- so a ceiling under 0.50 would refuse
        the most profitable openings we know of. A typo must not do that."""
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, raw)
        assert TS.max_first_leg_own_price() == D("0.60")

    def test_a_typo_cannot_re_admit_the_bounded_regime(self, monkeypatch):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "O.75")   # letter O
        assert no("UP", "0.75")["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"

    def test_zero_disables_the_rule_explicitly(self, monkeypatch):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "0")
        assert no("UP", "0.75") is None

    def test_a_lower_ceiling_refuses_more(self, monkeypatch):
        monkeypatch.setenv(TS.MAX_FIRST_LEG_ENV, "0.50")
        assert no("DOWN", "0.55")["reason"] == "FIRST_LEG_ABOVE_MAX_PRICE"
        assert no("DOWN", "0.50") is None


class TestTheOutOfSampleEvidence:
    """2,216 L5 windows. This is what the 0.60 rests on."""

    def test_the_ev_column_reproduces_from_the_stated_formula(self):
        """EV(p) = P(complete|p) * mean_pair_profit - (1 - P) * p, at the
        bucket midpoint. If this does not reproduce, the table has been
        transcribed wrongly and nothing below it means anything."""
        for lo, hi, _n, pc, ev in OOS:
            mid = (lo + hi) / 2
            got = pc * MEAN_PAIR_PROFIT - (1 - pc) * mid
            assert abs(got - ev) <= D("0.0001"), (lo, hi, got, ev)

    def test_ev_is_positive_below_the_ceiling_and_negative_above(self):
        for lo, _hi, _n, _pc, ev in OOS:
            if lo >= D("0.60"):
                assert ev < 0, lo
            elif lo >= D("0.10"):
                assert ev > 0, lo

    def test_the_ceiling_sits_exactly_at_the_sign_change(self):
        below = [ev for lo, _h, _n, _p, ev in OOS if lo == D("0.5")][0]
        above = [ev for lo, _h, _n, _p, ev in OOS if lo == D("0.6")][0]
        assert below > 0 > above
        assert TS.MAX_FIRST_LEG_OWN_PRICE == D("0.60")

    def test_the_refused_region_is_well_populated(self):
        """849 windows, not a handful -- which is what the in-sample
        campaign cannot offer."""
        n_above = sum(n for lo, _h, n, _p, _e in OOS if lo >= D("0.60"))
        assert n_above == 849
        assert n_above > 30

    def test_completion_falls_monotonically_across_the_refused_region(self):
        rates = [pc for lo, _h, _n, pc, _e in OOS if lo >= D("0.60")]
        assert rates == sorted(rates, reverse=True)
        assert rates == [D("0.762"), D("0.607"), D("0.467")]

    def test_the_sign_is_robust_to_the_pair_profit_assumption(self):
        """The 0.6-0.7 bucket turns positive only if mean pair profit
        exceeds $0.2030/contract -- 69.3% above the measured $0.1199."""
        lo, hi, _n, pc, _ev = [b for b in OOS if b[0] == D("0.6")][0]
        mid = (lo + hi) / 2
        breakeven = ((1 - pc) * mid) / pc
        assert abs(breakeven - D("0.2030")) <= D("0.0005")
        assert breakeven > MEAN_PAIR_PROFIT * D("1.69")


class TestTheInSampleCampaign:
    """The 8-window 20261025 PMUS campaign -- and its fragility."""

    def test_the_ceiling_excludes_exactly_the_two_dear_episodes(self):
        refused = [lbl for lbl, px, _p, _c in CAMPAIGN
                   if no("DOWN", str(px)) is not None]
        assert refused == ["0100z-B", "0130z"]

    def test_it_keeps_every_episode_at_or_under_the_ceiling(self):
        kept = [lbl for lbl, px, _p, _c in CAMPAIGN
                if no("DOWN", str(px)) is None]
        assert kept == ["0030z", "0045z", "0115z"]

    def test_the_stranded_loss_avoided(self):
        strands = [p for _l, px, p, c in CAMPAIGN
                   if not c and no("DOWN", str(px)) is not None]
        assert strands == [D("-1.1594")]

    def test_the_profitable_pair_lost(self):
        """The cap is not free: 0100z-B opened at own 0.75 and COMPLETED
        profitably. It is refused too."""
        lost = [p for _l, px, p, c in CAMPAIGN
                if c and p is not None and no("DOWN", str(px)) is not None]
        assert lost == [D("0.0344")]

    def test_the_net_economic_impact_decomposes_exactly(self):
        """+$1.1250 = the strand avoided minus the winner declined."""
        avoided = D("1.1594")
        declined = D("0.0344")
        assert avoided - declined == D("1.1250")
        assert CAMPAIGN_NET_AT_060 - CAMPAIGN_BASELINE_NET == D("1.1250")

    def test_but_the_in_sample_result_is_knife_edge(self):
        """THE HONEST COUNTERWEIGHT. A 0.65 ceiling refuses only the
        WINNER and is worse than doing nothing, because the single strand
        sat at 0.62. The in-sample magnitude is therefore not evidence for
        the threshold -- the out-of-sample curve is."""
        strand_px = [px for _l, px, _p, c in CAMPAIGN if not c][0]
        assert strand_px == D("0.62")
        assert D("0.60") < strand_px < D("0.65")
        # at 0.65 the strand is admitted and only the winner is refused
        refused_at_065 = [lbl for lbl, px, _p, _c in CAMPAIGN
                          if px > D("0.65")]
        assert refused_at_065 == ["0100z-B"]

    def test_n_is_one_strand_and_that_is_not_a_sample(self):
        strands = [l for l, _px, _p, c in CAMPAIGN if not c]
        assert len(strands) == 1
        assert 1 < 30


class TestNothingElseMoved:

    def test_the_pair_cap_is_unchanged(self):
        assert TS.max_pair_cost() == D("0.96")

    def test_the_opening_notional_budget_is_untouched(self):
        """Explicitly NOT changed alongside this guard."""
        assert TS.OPENING_NOTIONAL_MAX_USD == D("5")

    def test_the_opening_contract_cap_is_unchanged(self):
        assert TS.OPENING_LEG_MAX_CONTRACTS == D("3")

    def test_the_placement_offset_is_unchanged(self):
        assert TS.depth_ticks() == 2

    def test_the_cutoffs_and_runway_are_unchanged(self):
        assert TS.entry_cutoff_s() == 600.0
        assert TS.complete_cutoff_s() == 720.0
        assert TS.min_entry_runway_s() == 300.0

    def test_the_residual_ceiling_is_unchanged(self):
        assert LX.ABS_RESIDUAL_CAP_CONTRACTS == D("3")

    def test_the_round_allocation_cap_is_unchanged(self):
        assert TS.ROUND_ALLOCATIONS_MAX == 4

    def test_the_predicate_holds_no_transport_and_submits_nothing(self):
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(TS.first_leg_entry_refusal)))
        names = [n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)]
        for forbidden in ("submit", "cancel", "arm", "save", "post"):
            assert not [n for n in names if forbidden in n.lower()], names

    def test_the_accessor_reads_only_its_own_variable(self):
        src = inspect.getsource(TS.max_first_leg_own_price)
        assert "MAX_FIRST_LEG_ENV" in src
        assert "MIN_FIRST_LEG_ENV" not in src


class TestItIsWiredAndLoadBearing:

    def test_the_ceiling_is_consulted_in_the_one_opening_predicate(self):
        src = inspect.getsource(TS.first_leg_entry_refusal)
        assert "max_first_leg_own_price()" in src
        # and nowhere else, so it cannot refuse a completion from a second
        # call site
        whole = inspect.getsource(TS)
        assert whole.count("max_first_leg_own_price()") == 2   # def + 1 call

    def test_removing_the_ceiling_changes_behaviour(self):
        """Mutation check: if the guard were inert, this price would open."""
        assert no("UP", "0.75") is not None
        assert TS.max_first_leg_own_price() > 0

    def test_it_is_reported_in_the_runtime_config_snapshot(self):
        from src import l5_runtime_config_verify as V
        out = V.verify()
        names = [r["name"] for r in out.get("observed_not_graded", [])]
        assert "ts_max_first_leg_own_price" in names
        assert "ts_min_first_leg_own_price" in names
