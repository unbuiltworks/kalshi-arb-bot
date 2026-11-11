"""PR #220's calculator inside the existing opening decision; release defaults.

`src/l5_entry_economics.py` (PR #220) computes a conditional expectation and
a break-even pairing rate from three per-contract figures. Here the settled
record of THIS venue supplies them -- P(pair | first fill), pair net,
unmatched net -- and the existing own-settled-record test states them in
its verdict.

WHAT THE INTEGRATION MAY DO, and no more:
  * report the three figures and the break-even pairing rate;
  * REFUSE an opening the conditional figures say does not clear the floor;
  * stay silent when the record does not state the split.
It never authorizes: the calculator's `admissible` is always False and is
never read. No book, queue position, fill model, fee or rebate enters it
beyond what the venue settled.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_first_opening_priced as FO
import tests.test_l5_ladder_runner as LAD
from src import l5_entry_economics as EE
from src import l5_live_execution as E
from src import l5_portfolio_controller as PC
from src import l5_two_sided_probe as TS
from src import l5_unmatched_disposition as UD

FLOOR = D("0.01")


def seed_split(h, windows):
    """`windows` is a list of (pairs, pair_pnl, unmatched, unmatched_pnl)."""
    rows = h.r.store.state.setdefault("retired_markets", {})
    t0 = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
    for i, (m, mp, u, rp) in enumerate(windows):
        m, mp, u, rp = D(str(m)), D(str(mp)), D(str(u)), D(str(rp))
        rows["cpc-btc-updown-15m-2026-11-%02d-%02d00z"
             % (1 + i // 24, i % 24)] = {
            "t": (t0 + dt.timedelta(hours=i)).isoformat(),
            "yes_value": "1.0000", "resolution_source": "VENUE_SETTLEMENT_PX",
            "up_qty": str(m + u), "dn_qty": str(m),
            "matched_qty": str(m), "matched_pnl": str(mp),
            "residual_qty": str(u), "residual_pnl": str(rp),
            "settlement_pnl": str(mp + rp), "cost_total": "0.5",
            "redemption_total": "0.5", "unverified_cost_basis_keys": []}


#: 30 windows shaped like the later half of our record: 49% of first-leg
#: contracts pair for +3.6c, the rest strand for -8.0c
LATER_HALF = [(0.2, "0.0072", 0, 0)] * 15 + [(0, 0, 0.2, "-0.0160")] * 15 \
    + [(0.2, "0.0072", 0.2, "-0.0160")] * 0
#: and like the earlier half: 71% pair for +6.5c, 29% strand for -5.9c
EARLIER_HALF = [(0.2, "0.0130", 0, 0)] * 21 + [(0, 0, 0.2, "-0.0118")] * 9


def harness(tmp_path, monkeypatch, on=True):
    env = dict(FO.BASE)
    if on:
        env[FO.ENV] = "ON"
    h = CC.limits(LAD.harness(tmp_path, monkeypatch, **env))
    h.book.set("0.50", "0.51")
    return h


def run(h, ticks=12):
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(ticks):
        off += 3.0
        h.tick(off)
    return h


class TestTheRecordStatesTheSplit:

    def test_measured_from_settled_rows(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        seed_split(h, LATER_HALF)
        c = h.r.store.own_settled_round_evidence()["conditional"]
        assert c["status"] == "MEASURED"
        assert D(c["first_leg_contracts"]) == D("6.0")
        assert D(c["p_pair_given_first_fill"]) == D("0.5")
        assert D(c["pair_net_per_contract"]) == D("0.036")
        assert D(c["unmatched_net_per_contract"]) == D("-0.08")

    def test_the_aggregate_and_the_split_are_the_same_money(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        seed_split(h, EARLIER_HALF)
        own = h.r.store.own_settled_round_evidence()
        c = own["conditional"]
        p = D(c["p_pair_given_first_fill"])
        ev = p * D(c["pair_net_per_contract"]) \
            + (1 - p) * D(c["unmatched_net_per_contract"])
        assert abs(ev - D(own["net_per_contract"])) < D("0.0002")

    @pytest.mark.parametrize("windows,reason", [
        ([(0.2, "0.01", 0, 0)] * 30, "NO_UNMATCHED_LEG_IN_THE_SAMPLE"),
        ([(0, 0, 0.2, "-0.02")] * 30, "NO_PAIR_IN_THE_SAMPLE")])
    def test_unknown_when_one_kind_never_occurred(self, tmp_path,
                                                  monkeypatch, windows,
                                                  reason):
        h = harness(tmp_path, monkeypatch)
        seed_split(h, windows)
        c = h.r.store.own_settled_round_evidence()["conditional"]
        assert c == {"status": "UNKNOWN", "reason": reason}

    def test_unknown_when_a_window_does_not_state_it(self, tmp_path,
                                                     monkeypatch):
        h = harness(tmp_path, monkeypatch)
        LAD.seed(h, 30, D("-0.02"))            # rows with no split at all
        own = h.r.store.own_settled_round_evidence()
        assert own["conditional"]["status"] == "UNKNOWN"
        assert own["conditional"]["reason"] == \
            "A_WINDOW_DOES_NOT_STATE_ITS_SPLIT"
        assert own["windows"] == 30            # the aggregate is untouched

    def test_no_windows_at_all(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        assert h.r.store.own_settled_round_evidence()["conditional"] == {
            "status": "UNKNOWN", "reason": "NO_SETTLED_WINDOWS"}


def own(p, gain, loss, per, n=30):
    return {"windows": n, "net_per_contract": str(per),
            "conditional": {"status": "MEASURED", "first_leg_contracts": "6",
                            "p_pair_given_first_fill": str(p),
                            "pair_net_per_contract": str(gain),
                            "unmatched_net_per_contract": str(loss)}}


class TestTheVerdict:

    def test_it_reports_the_three_figures_and_the_break_even(self):
        v = UD.own_settled_verdict(own("0.487", "0.0362", "-0.0802",
                                       "-0.0235"), qty=D("0.2"),
                                   floor=FLOOR * D("0.2"))
        e = v["entry_economics"]
        assert e["status"] == "SCENARIO_ONLY"
        assert e["source"] == "OWN_VENUE_SETTLED_WINDOWS"
        assert D(e["conditional_expected_net_per_contract"]) < 0
        assert D(e["break_even_completion_probability"]).quantize(
            D("0.001")) == D("0.775")
        assert v["clears_floor"] is False

    def test_a_record_that_clears_both_ways_clears(self):
        v = UD.own_settled_verdict(own("0.712", "0.0646", "-0.0589",
                                       "0.0290"), qty=D("0.2"),
                                   floor=FLOOR * D("0.2"))
        assert v["clears_floor"] is True
        assert v["entry_economics"]["scenario_clears_edge"] is True
        # the calculator never authorizes, and nothing reads this field
        assert v["entry_economics"]["admissible"] is False

    def test_it_can_refuse_what_the_aggregate_passed(self):
        """A record whose aggregate figure and split disagree is not a
        record to open on."""
        v = UD.own_settled_verdict(own("0.487", "0.0362", "-0.0802",
                                       "0.0500"), qty=D("0.2"),
                                   floor=FLOOR * D("0.2"))
        assert v["clears_floor"] is False

    def test_it_cannot_pass_what_the_aggregate_refused(self):
        v = UD.own_settled_verdict(own("0.712", "0.0646", "-0.0589",
                                       "-0.0300"), qty=D("0.2"),
                                   floor=FLOOR * D("0.2"))
        assert v["clears_floor"] is False

    @pytest.mark.parametrize("cond", [
        None, {"status": "UNKNOWN", "reason": "x"}, "junk",
        {"status": "MEASURED", "p_pair_given_first_fill": "NaN",
         "pair_net_per_contract": "0.04",
         "unmatched_net_per_contract": "-0.08"},
        {"status": "MEASURED", "p_pair_given_first_fill": "1.4",
         "pair_net_per_contract": "0.04",
         "unmatched_net_per_contract": "-0.08"}])
    def test_when_it_cannot_compute_it_does_not_vote(self, cond):
        rec = {"windows": 30, "net_per_contract": "0.03"}
        if cond is not None:
            rec["conditional"] = cond
        v = UD.own_settled_verdict(rec, qty=D("0.2"), floor=FLOOR * D("0.2"))
        assert v["entry_economics"]["status"] == "UNKNOWN"
        assert v["clears_floor"] is True       # the aggregate stands alone

    def test_the_calculator_is_the_pr_s_own(self):
        r = EE.evaluate_opening(
            completed_net_per_contract="0.0362",
            residual_net_per_contract="-0.0802",
            completion_probability_given_first_fill="0.487",
            minimum_net_edge_per_contract="0.01")
        e = UD.entry_economics(own("0.487", "0.0362", "-0.0802", "0"),
                               floor_per_contract=D("0.01"))
        for k in r:
            assert e[k] == r[k]


class TestInTheRunner:

    def test_a_record_shaped_like_the_later_half_refuses_the_opening(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        seed_split(h, LATER_HALF)
        run(h)
        assert list(h.sent()) == []
        assert FO.refused(h, "OWN_SETTLED_EXPECTANCY_BELOW_FLOOR") >= 5

    def test_a_record_shaped_like_the_earlier_half_opens(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        seed_split(h, EARLIER_HALF)
        run(h)
        assert sorted(s for s, _p, _q in h.sent()) == ["DOWN", "UP"]

    def test_without_the_switch_the_first_opening_is_still_unpriced(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, on=False)
        seed_split(h, LATER_HALF)
        run(h)
        assert sorted(s for s, _p, _q in h.sent()) == ["DOWN", "UP"]


# ======================================================================
# THE RELEASE: every behaviour added on this branch is off until named
# ======================================================================
NEW_SWITCHES = [
    TS.COMPLETION_RESTS_AT_CEILING_ENV, TS.PARTIAL_COMPLETION_BASIS_ENV,
    TS.INVENTORY_RECOVERY_ENV, E.REQUOTE_KEEPS_ROUND_ENV,
    UD.FIRST_OPENING_PRICED_ENV, PC.BALANCE_ADD_FLOOR_ENV,
    "L5_COST_RESTATEMENT_APPROVED", "L5_FAVONLY_MIN_PRICE",
    "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE"]


class TestTheReleaseIsOffByDefault:

    def test_with_nothing_set_nothing_new_is_on(self, monkeypatch):
        for name in NEW_SWITCHES + [PC.PROFILE_ENV,
                                    TS.COMPLETION_MAX_PAIR_ENV]:
            monkeypatch.delenv(name, raising=False)
        assert TS.completion_rests_at_ceiling() is False
        assert TS.partial_completion_per_contract() is False
        assert TS.inventory_recovery() is False
        assert TS.reprice_missing_leg_on_fill() is False
        assert E.requote_keeps_round() is False
        assert UD.first_opening_priced() is False
        from src import l5_live_authorization as AUTH
        assert AUTH.reducing_replace_uses_complete() is False
        assert (AUTH.REDUCING_REPLACE_USES_COMPLETE_ENV
                == "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE")
        assert PC.favorite_only() is False
        assert PC.balance_add_floor() == float(PC.BALANCE["add_floor"])
        assert TS.completion_pair_ceiling() == D("1.00")
        assert TS.maker_completion_target() == TS.max_pair_cost()

    def test_the_hard_ceiling_cannot_be_raised_by_any_of_them(
            self, monkeypatch):
        for name in (TS.INVENTORY_RECOVERY_ENV,
                     TS.COMPLETION_RESTS_AT_CEILING_ENV):
            monkeypatch.setenv(name, "ON")
        monkeypatch.setenv(TS.PARTIAL_COMPLETION_BASIS_ENV, "PER_CONTRACT")
        monkeypatch.setenv(TS.COMPLETION_MAX_PAIR_ENV, "1.20")
        monkeypatch.setenv(TS.COMPLETION_PAIR_CEILING_ENV, "1.20")
        assert TS.completion_pair_ceiling() == D("1.00")
        assert TS.maker_completion_target() <= D("1.00")

    def test_no_correction_is_approved_by_default(self, monkeypatch):
        from src import l5_pmus_live_runner as R
        monkeypatch.delenv(R.COST_RESTATEMENT_APPROVAL_ENV, raising=False)
        assert R.approved_cost_restatements() == []
