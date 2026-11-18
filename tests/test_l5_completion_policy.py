"""The completion policy is off by default, conservative when unsure, and
never completes merely because the clock advanced."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_completion_policy as P


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(P.POLICY_ENV, "ON")


def prog(p, certain=True):
    return {"progress": D(str(p)), "certain": certain, "why": None}


# ------------------------------------------------------------------- switch
class TestOffByDefault:

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv(P.POLICY_ENV, raising=False)
        assert P.enabled() is False

    @pytest.mark.parametrize("v", ["on", "YES", "1", "true", "ON "])
    def test_only_exactly_ON_enables(self, monkeypatch, v):
        monkeypatch.setenv(P.POLICY_ENV, v)
        assert P.enabled() is False

    def test_decide_holds_while_off(self, monkeypatch):
        monkeypatch.delenv(P.POLICY_ENV, raising=False)
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.5", prog=prog("0.9"))
        assert d["action"] == "HOLD" and d["why"] == "POLICY_OFF"


# ----------------------------------------------------------------- progress
class TestProgressIsMeasuredFromMarketStart:

    def test_a_btc5m_window_maps_onto_the_stages(self):
        """300s window: 90s in is 30% -> still PATIENT."""
        p = P.progress(now=1000 + 90, start=1000, end=1300)
        assert p["progress"] == D("0.3")
        assert P.stage_for(p)["stage_name"] == "PATIENT"

    def test_the_same_window_at_260s_is_FINAL_and_FINAL_IS_PAR(self):
        p = P.progress(now=1000 + 260, start=1000, end=1300)
        st = P.stage_for(p)
        assert st["stage_name"] == "FINAL"
        assert st["pair_ceiling"] == D("1.00"), "above-par is not authorized"

    def test_progress_does_not_depend_on_when_the_leg_filled(self):
        """Two legs, same market, different fill times, SAME stage."""
        a = P.stage_for(P.progress(now=1200, start=1000, end=1300))
        b = P.stage_for(P.progress(now=1200, start=1000, end=1300))
        assert a["stage"] == b["stage"] == 2          # 66.7% -> BREAKEVEN

    def test_it_is_clamped_both_ends(self):
        assert P.progress(now=900, start=1000, end=1300)["progress"] == D("0")
        assert P.progress(now=9999, start=1000, end=1300)["progress"] == D("1")

    @pytest.mark.parametrize("p,name,ceil", [
        ("0.00", "PATIENT", "0.97"), ("0.30", "PATIENT", "0.97"),
        ("0.31", "STEP", "1.00"), ("0.60", "STEP", "1.00"),
        ("0.61", "BREAKEVEN", "1.00"), ("0.85", "BREAKEVEN", "1.00"),
        ("0.86", "FINAL", "1.00"), ("1.00", "FINAL", "1.00")])
    def test_the_authorized_schedule(self, p, name, ceil):
        st = P.stage_for(prog(p))
        assert (st["stage_name"], st["pair_ceiling"]) == (name, D(ceil))


class TestUncertainResolutionIsTreatedAsEarly:

    def test_an_unknown_end_time_holds_the_tightest_ceiling(self):
        p = P.progress(now=1000, start=900, end=None)
        assert p["progress"] is None
        assert p["why"] == "RESOLUTION_TIME_UNKNOWN"
        st = P.stage_for(p)
        assert st["stage"] == 0 and st["pair_ceiling"] == D("0.97")

    def test_an_ESTIMATED_end_time_does_not_advance_the_stage(self):
        """A tennis match ends when it ends. 95% of an estimate is not FINAL."""
        p = P.progress(now=1950, start=1000, end=2000,
                       resolution_certain=False)
        assert p["progress"] == D("0.95")
        st = P.stage_for(p)
        assert st["stage"] == 0, "an estimate must not unlock above-par"
        assert st["pair_ceiling"] == D("0.97")
        assert "tightest" in st["why"]

    def test_an_inverted_market_window_is_uncertain(self):
        p = P.progress(now=1000, start=1300, end=1200)
        assert p["why"] == "RESOLUTION_NOT_AFTER_START"
        assert P.stage_for(p)["stage"] == 0

    def test_unreadable_times_are_uncertain(self):
        assert P.progress(now="x", start=1, end=2)["why"] == "TIME_UNREADABLE"

    def test_no_progress_supplied_at_all_is_stage_zero(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.5")
        assert d["stage"]["stage"] == 0


# ------------------------------------------------------- the never-force rule
class TestALaterStageNeverForcesACompletion:

    def test_at_FINAL_a_loss_making_completion_is_refused(self):
        """The rule that makes an above-par ceiling safe.

        Held 10 UP at 0.40, p_win 0.70 -> holding is worth
        10*(0.70-0.40) = +3.00. Completing at 0.57 costs a pair of 0.97,
        worth 10*(1-0.97) = +0.30. Permissible at FINAL, still refused
        because holding is worth more.
        """
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.57", p_win="0.70", prog=prog("0.95"))
        assert d["stage"]["stage_name"] == "FINAL"
        assert d["options"]["MAKER_COMPLETE"]["permissible"] is True
        assert d["options"]["MAKER_COMPLETE"]["pair_cost"] == "0.97"
        assert d["action"] == "HOLD"
        assert d["why"] == "HOLD_NOT_BEATEN"

    def test_at_FINAL_a_par_completion_IS_taken_when_it_beats_holding(self):
        """The case the final stage exists for: the leg is probably worthless.

        Held 10 UP at 0.40, p_win 0.05 -> holding is worth -3.50.
        Completing at 0.60 (pair 1.00) is worth 0.00. Completing wins.
        """
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.60", p_win="0.05", prog=prog("0.95"))
        assert d["action"] == "MAKER_COMPLETE"
        assert d["expected_net_usd"] == "0.00"
        assert d["hold_net_usd"] == "-3.50"

    def test_reaching_a_later_stage_alone_changes_no_action(self):
        """Same state, every stage: the action only ever widens, never fires."""
        acts = []
        for p in ("0.1", "0.5", "0.8", "0.95"):
            d = P.decide(held_side="UP", held_qty="10",
                         held_avg_cost="0.40", complete_bid="0.62",
                         p_win="0.70", prog=prog(p))
            acts.append(d["action"])
        assert acts == ["HOLD"] * 4

    def test_a_tie_does_not_displace_holding(self):
        """Strict improvement only."""
        # hold: 10*(0.60-0.40) = 2.00 ; complete at 0.40 -> pair 0.80 -> 2.00
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.40", p_win="0.60", prog=prog("0.5"))
        assert d["options"]["MAKER_COMPLETE"]["net"] == "2.00"
        assert d["hold_net_usd"] == "2.00"
        assert d["action"] == "HOLD"


class TestTheCeilingBinds:

    def test_stage_zero_refuses_a_par_completion(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.50",
                     complete_bid="0.50", p_win="0.05", prog=prog("0.1"))
        mc = d["options"]["MAKER_COMPLETE"]
        assert mc["permissible"] is False
        assert mc["why"] == "PAIR_ABOVE_STAGE_CEILING"
        assert mc["pair_cost"] == "1.00" and mc["ceiling"] == "0.97"
        assert d["action"] == "HOLD"

    def test_the_same_completion_is_permissible_at_STEP(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.50",
                     complete_bid="0.50", p_win="0.05", prog=prog("0.5"))
        assert d["options"]["MAKER_COMPLETE"]["permissible"] is True
        assert d["action"] == "MAKER_COMPLETE"

    def test_fees_count_against_the_ceiling(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.49",
                     complete_bid="0.50", fee_per_share="0.01",
                     p_win="0.05", prog=prog("0.5"))
        mc = d["options"]["MAKER_COMPLETE"]
        assert mc["pair_cost"] == "1.00"
        d2 = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.49",
                      complete_bid="0.50", fee_per_share="0.02",
                      p_win="0.05", prog=prog("0.5"))
        assert d2["options"]["MAKER_COMPLETE"]["permissible"] is False

    def test_nothing_is_permissible_above_the_FINAL_ceiling(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.50",
                     complete_bid="0.60", p_win="0.00", prog=prog("0.99"))
        assert d["options"]["MAKER_COMPLETE"]["permissible"] is False
        assert d["action"] == "HOLD"


class TestTakerStaysBehindItsOwnAuthorization:

    def test_taker_is_refused_unless_authorized(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_ask="0.50", p_win="0.05", prog=prog("0.5"))
        t = d["options"]["TAKER_COMPLETE"]
        assert t["permissible"] is False
        assert t["why"] == "TAKER_NOT_AUTHORIZED"

    def test_an_authorized_taker_is_compared_on_its_executable_ask(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_ask="0.50", complete_bid="0.58",
                     p_win="0.05", taker_authorized=True, prog=prog("0.5"))
        assert d["options"]["TAKER_COMPLETE"]["permissible"] is True
        assert d["action"] == "TAKER_COMPLETE"      # 0.90 pair beats 0.98

    def test_authorization_alone_does_not_make_taker_win(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_ask="0.62", complete_bid="0.45",
                     p_win="0.05", taker_authorized=True, prog=prog("0.5"))
        assert d["action"] == "MAKER_COMPLETE"


class TestReductionAndHoldingBaseline:

    def test_reduction_is_valued_and_can_win(self):
        """Selling the held side ends the strand; no pair ceiling applies."""
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.80", reduce_bid="0.39",
                     p_win="0.05", prog=prog("0.5"))
        assert d["options"]["REDUCE"]["net"] == "-0.10"
        assert d["action"] == "REDUCE"

    def test_without_p_win_nothing_can_beat_holding(self):
        """Fail closed: no baseline means no completion on a guess."""
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.10", p_win=None, prog=prog("0.95"))
        assert d["action"] == "HOLD" and d["why"] == "P_WIN_UNREADABLE"

    @pytest.mark.parametrize("bad", ["-0.1", "1.5"])
    def test_an_out_of_range_p_win_holds(self, bad):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.10", p_win=bad, prog=prog("0.95"))
        assert d["why"] == "P_WIN_UNREADABLE"

    @pytest.mark.parametrize("q,c", [("0", "0.4"), ("-1", "0.4"),
                                     ("10", "-0.1"), ("x", "0.4")])
    def test_unreadable_inventory_holds(self, q, c):
        d = P.decide(held_side="UP", held_qty=q, held_avg_cost=c,
                     complete_bid="0.10", p_win="0.5", prog=prog("0.5"))
        assert d["action"] == "HOLD"
        assert d["why"] == "INVENTORY_UNREADABLE"

    def test_an_unknown_side_holds(self):
        d = P.decide(held_side="SIDEWAYS", held_qty="10",
                     held_avg_cost="0.40", complete_bid="0.10",
                     p_win="0.5", prog=prog("0.5"))
        assert d["why"] == "SIDE_UNREADABLE"


class TestStrandedLegLimit:

    def test_three_is_the_candidate_limit(self):
        assert P.MAX_STRANDED_LEGS == 3
        assert P.stranded_room(stranded_now=2)["room"] is True
        assert P.stranded_room(stranded_now=3)["room"] is False

    def test_a_stricter_caller_limit_wins(self):
        r = P.stranded_room(stranded_now=1, caller_max=1)
        assert r["room"] is False and r["limit"] == 1

    def test_a_looser_caller_limit_is_ignored(self):
        """This may only ever narrow an allowance."""
        r = P.stranded_room(stranded_now=5, caller_max=99)
        assert r["room"] is False and r["limit"] == 3

    def test_an_unreadable_count_has_no_room(self):
        assert P.stranded_room(stranded_now=None)["room"] is False

    def test_the_limit_is_reported_in_a_decision(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.5", prog=prog("0.5"),
                     stranded_now=3)
        assert d["stranded"]["room"] is False


class TestTheVerifiedAndWithdrawnClaims:

    def test_the_withdrawn_41_of_41_claim_is_recorded_as_withdrawn(self):
        doc = P.__doc__ or ""
        assert "WITHDRAWN" in doc
        assert "41" in doc and "held_to_close" in doc

    def test_the_cheap_first_claim_is_recorded_as_not_significant(self):
        doc = P.__doc__ or ""
        assert "1.8c" in doc and "P(<=0)" in doc

    def test_cfb_is_recorded_as_the_worst_sport_not_cs2(self):
        doc = P.__doc__ or ""
        assert "CFB is the worst sport, not CS2" in doc

    def test_stage_zero_is_flagged_as_unreachable_in_practice(self):
        """The 0.99 ceiling sits below every observed completion."""
        r = P.stage_would_refuse_observed_completions(D("0.99"))
        assert r["refuses_all_observed"] is True
        assert r["observed_above_ceiling"] == ["1.0001", "1.0177"]

    def test_even_the_breakeven_ceiling_refuses_BOTH_observed_arms(self):
        """THE DESIGN FINDING, and it is not a small one.

        The sports lane's measured completions land at 1.0001 (cheap leg
        first) and 1.0177 (dearer first). BOTH exceed 1.00, so stages
        0, 1 AND 2 all refuse every completion that lane actually
        achieves, and only FINAL (1.03) admits them. Under these
        candidate ceilings the schedule therefore collapses into "hold
        until the last 15% of the market, then complete" -- close to the
        opposite of "start patient, step up as time passes".
        """
        for ceil in ("0.99", "1.00"):
            r = P.stage_would_refuse_observed_completions(D(ceil))
            assert r["refuses_all_observed"] is True, ceil
            assert r["observed_above_ceiling"] == ["1.0001", "1.0177"]

    def test_the_final_ceiling_admits_both_observed_arms(self):
        r = P.stage_would_refuse_observed_completions(D("1.03"))
        assert r["observed_above_ceiling"] == []


class TestItRemainsPure:

    def test_the_module_touches_no_transport_or_execution_code(self):
        import pathlib
        src = pathlib.Path("src/l5_completion_policy.py").read_text()
        for needle in ("requests", "urllib", "socket", "subprocess",
                       "l5_live_execution", "l5_pmus_live_runner",
                       "set_variables", "os.environ["):
            assert needle not in src, needle

    def test_it_only_reads_its_own_switch_from_the_environment(self):
        import pathlib
        src = pathlib.Path("src/l5_completion_policy.py").read_text()
        assert src.count("os.environ.get") == 1
        assert "POLICY_ENV" in src


# ---------------------------------------------------------------------------
# SPORTS INTEGRATION, answering the sports session's 2026-11-18 questions.
# A lane with a venue start time but no certain end would otherwise sit at
# 0.99 for ever, making the policy a no-op there.
# ---------------------------------------------------------------------------
class TestAnObservedLateEventMayAdvanceAnUncertainMarket:

    def test_uncertain_without_the_signal_stays_at_the_tightest_stage(self):
        p = P.progress(now=1000, start=900, end=None)
        assert P.stage_for(p)["stage"] == 0

    def test_an_observed_late_signal_advances_to_BREAKEVEN(self):
        p = P.progress(now=1000, start=900, end=None)
        st = P.stage_for(p, event_late=True)
        assert st["stage_name"] == "BREAKEVEN"
        assert st["pair_ceiling"] == D("1.00")
        assert st["capped_at_uncertain_max"] is True

    def test_it_can_NEVER_reach_the_above_par_stage(self):
        """The 41/41 claim is withdrawn, so nothing uncertain goes above par."""
        assert P.UNCERTAIN_MAX_STAGE == 2
        for why_end in (None, "x"):
            p = P.progress(now=1000, start=900, end=why_end)
            st = P.stage_for(p, event_late=True)
            assert st["pair_ceiling"] <= D("1.00")
            assert st["stage_name"] != "FINAL"

    def test_an_estimated_end_time_plus_late_is_still_capped(self):
        p = P.progress(now=1950, start=1000, end=2000,
                       resolution_certain=False)
        st = P.stage_for(p, event_late=True)
        assert st["stage_name"] == "BREAKEVEN"

    def test_a_certain_clock_ignores_the_flag_and_uses_the_clock(self):
        """A real clock is better evidence than a flag."""
        early = P.stage_for(P.progress(now=1010, start=1000, end=1300),
                            event_late=True)
        assert early["stage_name"] == "PATIENT"      # 3.3% elapsed
        late = P.stage_for(P.progress(now=1290, start=1000, end=1300),
                           event_late=True)
        assert late["stage_name"] == "FINAL"         # a real clock may

    def test_the_flag_flows_through_decide(self):
        d = P.decide(held_side="YES", held_qty="10", held_avg_cost="0.50",
                     complete_bid="0.50", p_win="0.05",
                     prog=P.progress(now=1000, start=900, end=None),
                     event_late=True)
        assert d["stage"]["stage_name"] == "BREAKEVEN"
        assert d["options"]["MAKER_COMPLETE"]["permissible"] is True
        assert d["action"] == "MAKER_COMPLETE"

    def test_without_the_flag_the_same_state_refuses(self):
        d = P.decide(held_side="YES", held_qty="10", held_avg_cost="0.50",
                     complete_bid="0.50", p_win="0.05",
                     prog=P.progress(now=1000, start=900, end=None))
        assert d["options"]["MAKER_COMPLETE"]["why"] \
            == "PAIR_ABOVE_STAGE_CEILING"
        assert d["action"] == "HOLD"


class TestPWinProvenanceIsRecordedNotAssumed:

    def test_an_undeclared_source_is_labelled_as_such(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.5", prog=prog("0.5"))
        assert d["p_win_source"] == "UNDECLARED"
        assert d["p_win"] == "0.5"

    def test_a_caller_with_no_model_may_declare_an_executable_mid(self):
        """Acceptable for a lane with no model, provided it SAYS so."""
        d = P.decide(held_side="YES", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.52",
                     p_win_source="HELD_SIDE_EXECUTABLE_MID",
                     prog=prog("0.5"))
        assert d["p_win_source"] == "HELD_SIDE_EXECUTABLE_MID"

    def test_the_source_never_changes_the_decision(self):
        """Provenance is a label, not an input to the arithmetic."""
        a = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.05", prog=prog("0.5"))
        b = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.05",
                     p_win_source="ANYTHING", prog=prog("0.5"))
        assert a["action"] == b["action"]
        assert a["expected_net_usd"] == b["expected_net_usd"]


# ---------------------------------------------------------------------------
# OWNER 2026-11-18: "The $0.97 decision stands ... with $0.99 retained as a
# replay comparator" and "There is no authorization to restore the $1.03
# allowance."
# ---------------------------------------------------------------------------
class TestTheAuthorizedCeilingsAndTheComparator:

    def test_the_patient_ceiling_is_the_authorized_097(self):
        assert P.PATIENT_CEILING == D("0.97")
        assert P.stage_for(prog("0.1"))["pair_ceiling"] == D("0.97")

    def test_097_locks_three_cents_which_was_the_stated_rationale(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.57", p_win="0.05", prog=prog("0.1"))
        assert d["options"]["MAKER_COMPLETE"]["pair_cost"] == "0.97"
        assert d["options"]["MAKER_COMPLETE"]["net"] == "0.30"   # 3c x 10

    def test_the_comparator_exists_and_nothing_defaults_to_it(self):
        assert P.COMPARATOR_PATIENT_CEILING == D("0.99")
        assert P.PATIENT_CEILING != P.COMPARATOR_PATIENT_CEILING

    def test_a_replay_may_pass_the_comparator_explicitly(self):
        st = P.stage_for(prog("0.1"),
                         patient_ceiling=P.COMPARATOR_PATIENT_CEILING)
        assert st["pair_ceiling"] == D("0.99")

    def test_the_comparator_admits_a_098_pair_the_authorized_ceiling_refuses(self):
        """The whole point of running both: 0.97 is strictly tighter."""
        kw = dict(held_side="UP", held_qty="10", held_avg_cost="0.48",
                  complete_bid="0.50", p_win="0.05", prog=prog("0.1"))
        strict = P.decide(**kw)
        loose = P.decide(**kw, patient_ceiling=P.COMPARATOR_PATIENT_CEILING)
        assert strict["options"]["MAKER_COMPLETE"]["permissible"] is False
        assert loose["options"]["MAKER_COMPLETE"]["permissible"] is True
        assert strict["action"] == "HOLD"
        assert loose["action"] == "MAKER_COMPLETE"


class TestAboveParIsNotAuthorized:

    def test_the_default_final_ceiling_is_par(self):
        assert P.FINAL_CEILING == D("1.00")
        assert P.stage_for(prog("0.99"))["pair_ceiling"] == D("1.00")

    def test_no_stage_can_pay_above_par_by_default(self):
        for p in ("0.0", "0.3", "0.5", "0.7", "0.9", "1.0"):
            assert P.stage_for(prog(p))["pair_ceiling"] <= D("1.00")

    def test_an_above_par_completion_is_refused_at_every_stage(self):
        for p in ("0.1", "0.5", "0.8", "0.99"):
            d = P.decide(held_side="UP", held_qty="10",
                         held_avg_cost="0.40", complete_bid="0.62",
                         p_win="0.00", prog=prog(p))
            assert d["options"]["MAKER_COMPLETE"]["permissible"] is False
            assert d["action"] == "HOLD"

    def test_restoring_it_takes_one_explicit_argument(self):
        """So the owner's word is the only thing that enables it."""
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.62", p_win="0.00", prog=prog("0.99"),
                     final_ceiling=P.ABOVE_PAR_FINAL_CEILING)
        assert d["stage"]["pair_ceiling"] == D("1.03")
        assert d["options"]["MAKER_COMPLETE"]["permissible"] is True
        assert d["action"] == "MAKER_COMPLETE"      # -0.20 beats -4.00

    def test_even_then_it_never_forces_a_loss(self):
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.62", p_win="0.70", prog=prog("0.99"),
                     final_ceiling=P.ABOVE_PAR_FINAL_CEILING)
        assert d["action"] == "HOLD"

    def test_the_withdrawn_justification_is_recorded_next_to_the_constant(self):
        import pathlib
        src = pathlib.Path("src/l5_completion_policy.py").read_text()
        assert "ABOVE-PAR COMPLETION IS NOT AUTHORIZED" in src
        assert "41-of-41" in src


class TestTheCeilingIsNotAnEconomicGate:

    def test_every_decision_says_so(self):
        """Owner: '$1.00 is only breakeven before fees ... the actual
        economic gate must remain authoritative.'"""
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.40",
                     complete_bid="0.50", p_win="0.5", prog=prog("0.5"))
        assert d["ceiling_is_not_an_economic_gate"] is True

    def test_a_par_pair_with_fees_is_a_loss_and_is_refused_on_economics(self):
        """1.00 all-in is exactly breakeven; add a fee and it is negative."""
        d = P.decide(held_side="UP", held_qty="10", held_avg_cost="0.49",
                     complete_bid="0.50", fee_per_share="0.01",
                     p_win="0.60", prog=prog("0.5"))
        mc = d["options"]["MAKER_COMPLETE"]
        assert mc["pair_cost"] == "1.00"
        assert mc["net"] == "0.00"
        assert d["action"] == "HOLD"        # holding at 0.60 is worth 1.10
