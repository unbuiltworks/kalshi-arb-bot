"""The shadow records a defensive-completion decision; it never takes one.

WHY THIS EXISTS. The 20261025 replay says autonomous ARGMAX turns positive
if defensive completion lands on **26.8%** of residual (pair_net +10.1837,
residual_net -13.3237, pre-emption -0.8080). The retained evidence is n=1 --
the single genuinely stranded episode, 0130z, completable at pair 0.9656 and
filled at both 0.5 s and 8.818 s of latency. One observation is not a rate,
and 37 of the 38 ARGMAX shadow windows have no retained book frames, so the
rate cannot be recovered retrospectively. This recorder measures it forward.

IT REUSES THE DEPLOYED POLICY. `DEF.decide(simulate=True)` is the real
decision function; there is no second copy of the rule here, and
`DEF.alternatives` inside it prices taker completion, maker completion and
hold-to-settlement side by side in dollars.

THE CEILING IS 1.00, NOT THE 1.20 DEFAULT. The venue has no maker-fee term
and a matched pair redeems exactly $1.00, so a pair above $1.00 is a locked
loss; the same bound arithmetic at 1.20 gives -8.0931, worse than doing
nothing.

Nothing here submits, cancels, claims or writes to a store.
"""
from __future__ import annotations

import collections
import inspect
from decimal import Decimal as D

import pytest

from src import l5_book_evidence as EV
from src import l5_defensive_completion as DEF
from src import l5_pmus_live_runner as M
from src import l5_two_sided_probe as TSP
from src import pmus_sports_l5_decision as l5d


BOOK = {"t": 1.0, "observed_t": 1.0,
        "bids": [["0.5200", "1722"], ["0.5100", "889"]],
        "asks": [["0.5300", "36.73"], ["0.5400", "306"]],
        "bids_served": True, "asks_served": True}
WEND = 1793300900.0
WSTART = WEND - 900.0

#: inside the defensive arm window for the deployed cutoffs. Derived, not
#: guessed: `binding_terminal_s(complete_cutoff_s, cancel_cutoff(900))` is
#: 720, the arm opens at 720-60 and the last actionable instant is 720-15.
ARMED_OFFSET = 670.0


def _sample(first_leg: str):
    inv = l5d.Inventory()
    inv.record(True, D(first_leg))
    snap = inv.snapshot()
    s = EV.complement_reachability(
        held_side="YES", first_leg_own_px=snap["held_leg_own_cost"],
        qty_needed=D("1"), row=BOOK, depth_ticks=2, tick=D("0.01"))
    return snap, s


def _run(first_leg: str, offset: float = ARMED_OFFSET):
    snap, s = _sample(first_leg)
    cap = []
    orig = M.emit
    M.emit = lambda label, payload: cap.append((label, payload))
    try:
        r = M.Runner.__new__(M.Runner)
        r.stats = collections.Counter()
        M.Runner.shadow_defensive_decision(
            r, "ARGMAX", s, inv=snap, slug="x-2100z",
            now=WSTART + offset, window_end_ts=WEND)
    finally:
        M.emit = orig
    return r, cap


class TestTheArmWindowIsDerivedNotGuessed:

    def test_the_deployed_cutoffs_put_the_arm_window_where_we_test_it(self):
        term = DEF.binding_terminal_s(TSP.complete_cutoff_s(),
                                      TSP.cancel_cutoff(900.0))
        assert term == 720.0
        opens = term - DEF.DEFENSIVE_ARM_S
        last = term - DEF.DEFENSIVE_MIN_EXECUTION_S
        assert opens == 660.0 and last == 705.0
        assert opens <= ARMED_OFFSET <= last


class TestItRecordsADecision:

    def test_it_emits_one_line_with_the_real_policy_verdict(self):
        r, cap = _run("0.45")
        assert len(cap) == 1
        label, p = cap[0]
        assert label == "L5_SHADOW_DEFENSIVE_DECISION"
        assert p["unmatched_qty"] == "1"
        assert p["first_leg_own_cost"] == "0.45"
        assert p["pair_if_taker"] == "0.930000"
        assert p["pair_ceiling_used"] == "1.00"

    def test_a_fundable_exit_is_permitted_by_the_real_policy(self):
        r, cap = _run("0.45")
        d = cap[0][1]["decision"]
        assert d["reason"] == "DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"
        assert r.stats["shadow_defensive:DEFENSIVE_EXIT_BOUNDED_AND_FUNDED"] == 1

    @pytest.mark.parametrize("first_leg", ["0.62", "0.80"])
    def test_a_pair_above_one_dollar_is_refused(self, first_leg):
        """A matched pair redeems exactly $1.00, so above it the completion
        locks a loss. The 1.00 ceiling must say so."""
        r, cap = _run(first_leg)
        d = cap[0][1]["decision"]
        assert d["reason"] == "DEFENSIVE_PAIR_CEILING"
        assert D(cap[0][1]["pair_if_taker"]) > D("1.00")

    def test_all_three_courses_are_priced_side_by_side(self):
        """Taker completion, maker completion and hold, in dollars."""
        _r, cap = _run("0.45")
        alts = {a["action"]: a for a in cap[0][1]["decision"]["alternatives"]}
        assert set(alts) == {"HOLD_TO_SETTLEMENT",
                             "MAKER_COMPLETE_AT_CEILING",
                             "DEFENSIVE_UNWIND"}
        hold = alts["HOLD_TO_SETTLEMENT"]
        assert D(hold["best_usd"]) == D("0.55")      # 1 - 0.45
        assert D(hold["worst_usd"]) == D("-0.45")    # the leg settles worthless
        assert alts["DEFENSIVE_UNWIND"]["executable_now"] is True

    def test_crossing_NO_LONGER_beats_holding_at_this_price(self):
        """THE COEFFICIENT CORRECTION FLIPPED THIS DECISION, and that is
        worth an assertion of its own rather than a quiet re-pin.

        The defensive unwind CROSSES the spread, so it pays the taker fee.
        At Theta 0.0695 the unwind returned more than holding's 0.05
        coin-flip expectation and the engine preferred it. At this
        account's Tier-1 0.095 the unwind returns 0.0463 and holding is
        the better expectation at p=0.45.

        The unwind is still the only CERTAIN outcome -- holding carries a
        +-0.50 swing -- so which one a risk-averse policy should take is
        not settled here. What is settled is that the fee correction
        removed its expectation advantage, and a policy that still
        prefers it is choosing variance reduction, not EV."""
        _r, cap = _run("0.45")
        alts = {a["action"]: a for a in cap[0][1]["decision"]["alternatives"]}
        hold_ev = (D(alts["HOLD_TO_SETTLEMENT"]["best_usd"])
                   + D(alts["HOLD_TO_SETTLEMENT"]["worst_usd"])) / 2
        unwind = D(alts["DEFENSIVE_UNWIND"]["best_usd"])
        assert hold_ev == D("0.05")
        assert unwind < hold_ev
        #: and the gap is the extra taker fee, not a modelling change
        assert D("0.04") < unwind < D("0.05")
        # and the unwind carries no variance at all
        assert (D(alts["DEFENSIVE_UNWIND"]["best_usd"])
                == D(alts["DEFENSIVE_UNWIND"]["worst_usd"]))

    def test_outside_the_arm_window_the_policy_declines(self):
        _r, cap = _run("0.45", offset=100.0)
        assert cap[0][1]["decision"]["reason"] == \
            "OUTSIDE_DEFENSIVE_ARM_WINDOW"

    def test_an_unpriceable_sample_is_counted_not_guessed(self):
        r = M.Runner.__new__(M.Runner)
        r.stats = collections.Counter()
        M.Runner.shadow_defensive_decision(
            r, "ARGMAX", {"taker_walk": {}}, inv={"residual": 1},
            slug="x", now=WSTART + ARMED_OFFSET, window_end_ts=WEND)
        assert r.stats["shadow_defensive:NOT_PRICEABLE"] == 1


class TestItCannotTrade:

    def test_the_recorder_submits_nothing(self):
        src = inspect.getsource(M.Runner.shadow_defensive_decision)
        for forbidden in ("transport.", "claim_submission", "record_submit",
                          ".arm(", "cancel(", "store."):
            assert forbidden not in src, forbidden

    def test_it_calls_the_real_policy_in_simulate_mode(self):
        src = inspect.getsource(M.Runner.shadow_defensive_decision)
        assert "DEF.decide(" in src
        assert "simulate=True" in src

    def test_it_uses_the_break_even_ceiling_not_the_default(self):
        src = inspect.getsource(M.Runner.shadow_defensive_decision)
        assert 'pair_ceiling=D("1.00")' in src
        assert DEF.DEFENSIVE_PAIR_CEILING == D("1.20")   # the default it overrides

    def test_it_reaches_for_the_probe_not_the_shadow_module(self):
        """`TS` in this module is `l5_two_sided_shadow`, which has no
        cutoffs; reaching for it was an AttributeError."""
        src = inspect.getsource(M.Runner.shadow_defensive_decision)
        assert "TSP.complete_cutoff_s()" in src
        assert "TS.complete_cutoff_s()" not in src

    def test_an_error_is_counted_and_never_raised(self):
        r = M.Runner.__new__(M.Runner)
        r.stats = collections.Counter()
        M.Runner.shadow_defensive_decision(
            r, "ARGMAX", "not-a-mapping", inv={"residual": 1},
            slug="x", now=WSTART, window_end_ts=WEND)
        assert any(k.startswith("shadow_defensive") for k in r.stats)
