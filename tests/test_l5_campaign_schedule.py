"""A short run of owner-approved windows: which step is due, and when it
stops. `l5_campaign_schedule` is pure -- a plan, a record, a clock -- so
every gate is exercised here in milliseconds rather than found live.
"""
from __future__ import annotations

import copy
import datetime as dt
import inspect

import pytest

from src import l5_campaign_schedule as S

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)          # approval
W = ["2026-11-12T19:00:00Z", "2026-11-12T19:30:00Z", "2026-11-12T20:00:00Z"]
STANDING = {"L5_MAX_LIVE_ORDERS": "4", "L5_TS_CONCURRENT_ROUNDS": "2",
            "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "10.00",
            "L5_LIVE_AUTH_MAX_OPEN": "140", "L5_LIVE_AUTH_MAX_COMPLETE": "300",
            "L5_LIVE_AUTH_MAX_REPLACE": "60",
            "L5_LIVE_AUTH_MAX_SUBMISSIONS": "500",
            "L5_MAX_TOTAL_EXPOSURE_USD": "1.00"}


def plan(**over):
    p = {"sha": "ff4b5291afc8a272cd5e8ae5e873120a1d4b67d6",
         "approved_utc": "2026-11-12T18:00:00Z", "windows": list(W),
         "execution": "MAKER_ONLY", "stop_on_failure": True,
         "per_window": {"max_total_exposure_usd": "1.00",
                        "additional_loss_usd": "1.00",
                        "max_live_orders": 2, "concurrent_rounds": 1,
                        "budgets": {"OPEN": 4, "COMPLETE": 12,
                                    "REPLACE": 16, "TOTAL": 32}},
         "aggregate": {"additional_loss_usd": "2.00"},
         "switches": {"L5_INVENTORY_RECOVERY": "ON",
                      "L5_TS_COMPLETION_MAX_PAIR": "0.98",
                      "L5_CASH_POLICY": "NO_RECYCLING"}}
    p.update(over)
    return p


def at(h, m, s=0):
    return dt.datetime(2026, 11, 12, h, m, s, tzinfo=UTC)


def done(pnl="0.02", **over):
    r = {"armed_utc": "x", "restored_utc": "y", "settled": True,
         "settled_pnl_usd": pnl, "reconciled": True,
         "accounting_unresolved": 0}
    r.update(over)
    return r


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(S.SCHEDULE_ENV, "ON")


def step(p, state, now, **kw):
    kw.setdefault("approved_digest", S.plan_digest(p))
    return S.next_step(p, state, now, **kw)


# ------------------------------------------------------------- off, purity
class TestOffAndPure:

    @pytest.mark.parametrize("raw", [None, "", "on", "1", "ON ", "true"])
    def test_only_the_exact_token_enables_it(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(S.SCHEDULE_ENV, raising=False)
        else:
            monkeypatch.setenv(S.SCHEDULE_ENV, raw)
        assert S.enabled() is False
        assert step(plan(), {}, at(18, 40))["action"] == S.DISABLED

    def test_it_holds_no_transport_client_or_writer(self):
        src = inspect.getsource(S)
        for word in ("l5_railway", "set_variables", "transport", "requests",
                     "urllib", "submit(", "l5_live_execution",
                     "l5_pmus_live_runner"):
            assert word not in src, word

    def test_the_same_inputs_give_the_same_step(self, on):
        p, st = plan(), {"windows": {W[0]: done()}}
        before = (copy.deepcopy(p), copy.deepcopy(st))
        a, b = step(p, st, at(19, 10)), step(p, st, at(19, 10))
        assert a == b and (p, st) == before


# -------------------------------------------------------------- the plan
class TestThePlanStatesEverything:

    def test_a_complete_plan_is_accepted(self):
        assert S.plan_refusals(plan()) == []

    @pytest.mark.parametrize("change,reason", [
        ({"sha": "ff4b5291a"}, "SHA_NOT_A_FULL_COMMIT"),
        ({"execution": "TAKER"}, "EXECUTION_NOT_MAKER_ONLY"),
        ({"execution": None}, "EXECUTION_NOT_MAKER_ONLY"),
        ({"stop_on_failure": False}, "STOP_ON_FAILURE_NOT_STATED"),
        ({"stop_on_failure": "yes"}, "STOP_ON_FAILURE_NOT_STATED"),
        ({"approved_utc": None}, "APPROVAL_TIME_NOT_STATED"),
        ({"windows": []}, "NO_WINDOWS_STATED"),
        ({"windows": None}, "NO_WINDOWS_STATED"),
        ({"windows": ["2026-11-12T19:00:00Z", "2026-11-12T19:30:00Z",
                      "2026-11-12T20:00:00Z", "2026-11-12T20:30:00Z",
                      "2026-11-12T21:00:00Z"]}, "TOO_MANY_WINDOWS"),
        ({"windows": ["next"]}, "WINDOW_NOT_AN_EXACT_UTC_START"),
        ({"windows": ["2026-11-12T19:00:00+00:00"]},
         "WINDOW_NOT_AN_EXACT_UTC_START"),
        ({"windows": ["2026-11-12T19:05:00Z"]},
         "WINDOW_NOT_ON_A_QUARTER_HOUR"),
        ({"windows": ["2026-11-12T19:00:00Z", "2026-11-12T19:15:00Z"]},
         "WINDOWS_TOO_CLOSE_OR_OUT_OF_ORDER"),
        ({"windows": ["2026-11-12T19:30:00Z", "2026-11-12T19:00:00Z"]},
         "WINDOWS_TOO_CLOSE_OR_OUT_OF_ORDER"),
        ({"windows": ["2026-11-12T19:00:00Z", "2026-11-12T19:00:00Z"]},
         "WINDOWS_TOO_CLOSE_OR_OUT_OF_ORDER"),
        ({"windows": ["2026-11-12T18:00:00Z"]},
         "FIRST_WINDOW_TOO_SOON_AFTER_APPROVAL"),
        ({"windows": ["2026-11-13T19:00:00Z"]}, "LAST_WINDOW_BEYOND_HORIZON"),
        ({"markets": ["cpc-btc-updown-15m-2026-11-12-1900z"]},
         "MARKETS_DO_NOT_MATCH_WINDOWS"),
        ({"per_window": None}, "PER_WINDOW_LIMITS_NOT_STATED"),
        ({"aggregate": None}, "AGGREGATE_LOSS_LIMIT_NOT_STATED"),
        ({"aggregate": {"additional_loss_usd": "3.01"}},
         "AGGREGATE_LOSS_ABOVE_SUM_OF_WINDOWS"),
        ({"switches": None}, "SWITCHES_NOT_STATED"),
        ({"switches": {}}, "CASH_RECYCLING_NOT_STATED"),
        ({"switches": {"L5_CASH_POLICY": "NO_RECYCLING",
                       "L5_SHADOW_ONLY": "0"}},
         "SWITCH_NOT_ALLOWED:L5_SHADOW_ONLY"),
        ({"switches": {"L5_CASH_POLICY": "NO_RECYCLING",
                       "L5_LIVE_HALT": ""}}, "SWITCH_NOT_ALLOWED:L5_LIVE_HALT"),
        ({"switches": {"L5_CASH_POLICY": "NO_RECYCLING",
                       "L5_TAKER_COMPLETION_POLICY": "YES"}},
         "SWITCH_NOT_ALLOWED:L5_TAKER_COMPLETION_POLICY"),
        ({"switches": {"L5_CASH_POLICY": "NO_RECYCLING",
                       "L5_TS_COMPLETION_MAX_PAIR": "1.05"}},
         "SWITCH_VALUE_NOT_ALLOWED:L5_TS_COMPLETION_MAX_PAIR"),
    ])
    def test_a_missing_or_wrong_field_is_named(self, change, reason):
        assert reason in S.plan_refusals(plan(**change))

    @pytest.mark.parametrize("field,value,reason", [
        ("max_total_exposure_usd", None, "EXPOSURE_NOT_STATED"),
        ("max_total_exposure_usd", "20.01", "EXPOSURE_ABOVE_CODE_CEILING"),
        ("additional_loss_usd", None, "WINDOW_LOSS_LIMIT_NOT_STATED"),
        ("additional_loss_usd", "1.01", "WINDOW_LOSS_LIMIT_ABOVE_EXPOSURE"),
        ("max_live_orders", None, "MAX_LIVE_ORDERS_NOT_STATED"),
        ("max_live_orders", 0, "MAX_LIVE_ORDERS_NOT_STATED"),
        ("max_live_orders", True, "MAX_LIVE_ORDERS_NOT_STATED"),
        ("concurrent_rounds", "one", "CONCURRENT_ROUNDS_NOT_STATED"),
        ("budgets", None, "SUBMISSION_BUDGETS_NOT_STATED"),
        ("budgets", {"OPEN": 4}, "SUBMISSION_BUDGETS_NOT_STATED"),
        ("budgets", {"OPEN": 4, "COMPLETE": 0, "REPLACE": 16, "TOTAL": 32},
         "SUBMISSION_BUDGET_NOT_A_POSITIVE_COUNT"),
        ("budgets", {"OPEN": 4, "COMPLETE": 12, "REPLACE": 16, "TOTAL": 33},
         "TOTAL_BUDGET_ABOVE_ITS_PARTS"),
    ])
    def test_every_per_window_figure_must_be_stated(self, field, value,
                                                    reason):
        p = plan()
        p["per_window"][field] = value
        assert reason in S.plan_refusals(p)

    def test_the_markets_are_derived_and_may_be_restated(self):
        slugs = ["cpc-btc-updown-15m-2026-11-12-1900z",
                 "cpc-btc-updown-15m-2026-11-12-1930z",
                 "cpc-btc-updown-15m-2026-11-12-2000z"]
        assert S.plan_refusals(plan(markets=slugs)) == []

    def test_any_change_to_the_plan_changes_what_the_owner_approved(self, on):
        p = plan()
        digest = S.plan_digest(p)
        q = plan()
        q["per_window"]["budgets"]["OPEN"] = 5
        assert S.plan_digest(q) != digest
        s = step(q, {}, at(18, 40), approved_digest=digest)
        assert (s["action"], s["why"]) == (
            S.PLAN_REFUSED, "DIGEST_NOT_THE_ONE_THE_OWNER_APPROVED")
        assert step(p, {}, at(18, 40), approved_digest=None)["action"] == \
            S.PLAN_REFUSED

    def test_a_configuration_that_lost_is_not_run_again(self, on):
        p = plan()
        lost = [S.configuration_digest(p)]
        assert S.plan_refusals(p, losing_configurations=lost) == [
            "REPEATS_A_LOSING_CONFIGURATION"]
        # other windows, same configuration: still the losing one
        later = plan(windows=["2026-11-12T21:00:00Z"])
        assert S.configuration_digest(later) == lost[0]
        # a different configuration is a different question
        other = plan()
        other["switches"]["L5_TS_COMPLETION_MAX_PAIR"] = "1.00"
        assert S.plan_refusals(other, losing_configurations=lost) == []
        # the owner may name the repeat; it is then part of the digest
        again = plan(repeats_a_losing_configuration=True)
        assert S.plan_refusals(again, losing_configurations=lost) == []
        assert S.plan_digest(again) != S.plan_digest(p)
        s = step(p, {}, at(18, 40), losing_configurations=lost)
        assert s["action"] == S.PLAN_REFUSED


# ----------------------------------------------------- what arming may set
class TestArmingOnlyTightens:

    def test_it_sets_the_stated_figures_and_switches_and_nothing_else(self):
        v = S.arming_variables(plan(), STANDING)
        assert v == {"L5_MAX_LIVE_ORDERS": "2", "L5_TS_CONCURRENT_ROUNDS": "1",
                     "L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD": "1.00",
                     "L5_LIVE_AUTH_MAX_OPEN": "4",
                     "L5_LIVE_AUTH_MAX_COMPLETE": "12",
                     "L5_LIVE_AUTH_MAX_REPLACE": "16",
                     "L5_LIVE_AUTH_MAX_SUBMISSIONS": "32",
                     "L5_INVENTORY_RECOVERY": "ON",
                     "L5_TS_COMPLETION_MAX_PAIR": "0.98",
                     "L5_CASH_POLICY": "NO_RECYCLING"}
        for forbidden in ("L5_LIVE_HALT", "L5_SHADOW_ONLY",
                          "L5_CAMPAIGN_APPROVAL", "L5_MAX_TOTAL_EXPOSURE_USD",
                          "L5_MAX_CASH_DEPLOYED_USD",
                          "L5_MAX_CUMULATIVE_LOSS_USD"):
            assert forbidden not in v

    @pytest.mark.parametrize("field,value", [
        ("max_live_orders", 5), ("concurrent_rounds", 3),
        ("additional_loss_usd", "1.00")])
    def test_a_figure_looser_than_the_one_in_force_is_refused(self, field,
                                                              value):
        p = plan()
        p["per_window"][field] = value
        standing = dict(STANDING, L5_MAX_CAMPAIGN_ADDITIONAL_LOSS_USD="0.50")
        with pytest.raises(ValueError):
            S.arming_variables(p, standing)

    def test_a_budget_looser_than_the_one_in_force_is_refused(self):
        p = plan()
        p["per_window"]["budgets"]["REPLACE"] = 61
        with pytest.raises(ValueError):
            S.arming_variables(p, STANDING)

    def test_the_exposure_limit_is_never_written_and_must_match(self):
        p = plan()
        p["per_window"]["max_total_exposure_usd"] = "2.00"
        p["per_window"]["additional_loss_usd"] = "1.00"
        with pytest.raises(ValueError):
            S.arming_variables(p, STANDING)

    def test_an_unreadable_standing_value_is_refused(self):
        with pytest.raises(ValueError):
            S.arming_variables(plan(), dict(STANDING, L5_MAX_LIVE_ORDERS=""))

    def test_restore_puts_back_exactly_what_was_there(self):
        standing = dict(STANDING, L5_CASH_POLICY="NO_RECYCLING")
        v = S.arming_variables(plan(), standing)
        back = S.restore_variables(v, standing)
        assert set(back) == set(v)
        assert back["L5_MAX_LIVE_ORDERS"] == "4"
        assert back["L5_LIVE_AUTH_MAX_REPLACE"] == "60"
        assert back["L5_INVENTORY_RECOVERY"] == ""        # did not exist: off
        assert back["L5_TS_COMPLETION_MAX_PAIR"] == ""
        assert back["L5_CASH_POLICY"] == "NO_RECYCLING"


# ------------------------------------------------------------- the steps
class TestOneWindowAtATime:

    def test_it_waits_until_the_arming_band(self, on):
        s = step(plan(), {}, at(18, 30))
        assert s["action"] == S.WAIT and s["window"] == W[0]
        assert (s["arm_from"], s["arm_until"]) == (
            "2026-11-12T18:35:00Z", "2026-11-12T18:53:00Z")

    @pytest.mark.parametrize("now", [at(18, 35), at(18, 44), at(18, 53)])
    def test_inside_the_band_it_asks_for_the_exact_market(self, on, now):
        s = step(plan(), {}, now)
        assert s["action"] == S.PREFLIGHT_AND_ARM
        assert s["market"] == "cpc-btc-updown-15m-2026-11-12-1900z"

    def test_a_missed_window_stops_the_run_and_is_not_replaced(self, on):
        s = step(plan(), {}, at(18, 53, 1))
        assert (s["action"], s["why"], s["window"]) == (
            S.STOPPED, "WINDOW_MISSED", W[0])
        # nor later: the second window is never offered in its place
        for now in (at(19, 5), at(19, 10), at(19, 20), at(19, 40)):
            s = step(plan(), {}, now)
            assert (s["action"], s["window"]) == (S.STOPPED, W[0])

    def test_a_refused_gate_stops_the_run(self, on):
        st = {"windows": {W[0]: {"arm_refused": ["watchdog_fresh"]}}}
        s = step(plan(), st, at(18, 41))
        assert (s["action"], s["why"], s["gates"]) == (
            S.STOPPED, "ARM_GATE_FAILED", ["watchdog_fresh"])
        assert step(plan(), st, at(19, 12))["action"] == S.STOPPED

    def test_armed_it_monitors_then_restores(self, on):
        st = {"windows": {W[0]: {"armed_utc": "2026-11-12T18:41:00Z"}}}
        assert step(plan(), st, at(18, 50))["action"] == S.MONITOR
        assert step(plan(), st, at(19, 9, 59))["action"] == S.MONITOR
        assert step(plan(), st, at(19, 10))["action"] == S.RESTORE
        assert step(plan(), st, at(19, 24))["action"] == S.RESTORE

    def test_a_restore_that_is_never_confirmed_stops_the_run(self, on):
        st = {"windows": {W[0]: {"armed_utc": "2026-11-12T18:41:00Z"}}}
        s = step(plan(), st, at(19, 25, 1))
        assert (s["action"], s["why"], s["must_restore"]) == (
            S.STOPPED, "RESTORE_NOT_CONFIRMED", True)

    def test_restored_it_waits_for_the_venue_to_settle(self, on):
        st = {"windows": {W[0]: {"armed_utc": "a", "restored_utc": "b"}}}
        s = step(plan(), st, at(19, 17))
        assert (s["action"], s["window"]) == (S.AWAIT_SETTLEMENT, W[0])

    def test_the_next_window_is_not_armed_on_an_unsettled_one(self, on):
        st = {"windows": {W[0]: {"armed_utc": "a", "restored_utc": "b"}}}
        assert step(plan(), st, at(19, 22))["action"] == S.AWAIT_SETTLEMENT
        s = step(plan(), st, at(19, 23, 1))
        assert (s["action"], s["why"], s["missed"]) == (
            S.STOPPED, "PREVIOUS_WINDOW_NOT_SETTLED_IN_TIME", W[1])

    def test_a_clean_settled_window_releases_the_next(self, on):
        st = {"windows": {W[0]: done("0.02")}}
        s = step(plan(), st, at(19, 18))
        assert (s["action"], s["window"], s["market"]) == (
            S.PREFLIGHT_AND_ARM, W[1], "cpc-btc-updown-15m-2026-11-12-1930z")

    @pytest.mark.parametrize("rec,why", [
        (done(reconciled=False), "RECONCILIATION_NOT_CONFIRMED"),
        (done(reconciled=None), "RECONCILIATION_NOT_CONFIRMED"),
        (done(accounting_unresolved=1), "ACCOUNTING_UNRESOLVED"),
        (done(accounting_unresolved=None), "ACCOUNTING_UNRESOLVED"),
        (done(pnl=None), "SETTLED_PNL_UNREADABLE"),
        (done(pnl="NaN"), "SETTLED_PNL_UNREADABLE"),
        (done(pnl="-0.01"), "WINDOW_SETTLED_AT_A_LOSS"),
        (done(pnl="-1.00"), "WINDOW_LOSS_LIMIT_REACHED"),
    ])
    def test_the_settlement_check_stops_the_run(self, on, rec, why):
        s = step(plan(), {"windows": {W[0]: rec}}, at(19, 18))
        assert (s["action"], s["why"], s["window"]) == (S.STOPPED, why, W[0])

    def test_a_losing_window_names_the_configuration_that_lost(self, on):
        p = plan()
        s = step(p, {"windows": {W[0]: done("-0.19")}}, at(19, 18))
        assert s["configuration"] == S.configuration_digest(p)
        assert S.plan_refusals(
            p, losing_configurations=[s["configuration"]])

    def test_the_aggregate_limit_binds_across_windows(self, on):
        p = plan(aggregate={"additional_loss_usd": "0.50"})
        p["per_window"]["additional_loss_usd"] = "0.50"
        st = {"windows": {W[0]: done("0.10"), W[1]: done("-0.50")}}
        assert step(p, st, at(19, 50))["why"] == "WINDOW_LOSS_LIMIT_REACHED"

    def test_every_window_settled_means_owner_review_not_another_window(
            self, on):
        st = {"windows": {w: done("0.02") for w in W}}
        s = step(plan(), st, at(20, 20))
        assert s["action"] == S.COMPLETE
        assert (s["settled_total_usd"], s["windows"]) == ("0.06", 3)
        # and it stays there
        assert step(plan(), st, at(23, 0))["action"] == S.COMPLETE

    def test_the_owner_halt_stops_everything(self, on):
        for st in ({"owner_halt": True},
                   {"owner_halt": True, "windows": {W[0]: done()}}):
            assert step(plan(), st, at(18, 41))["why"] == "OWNER_HALT"

    @pytest.mark.parametrize("state", [None, "x", {"windows": "x"},
                                       {"windows": {W[0]: "armed"}}])
    def test_an_unreadable_record_stops_it(self, on, state):
        assert step(plan(), state, at(18, 41))["action"] == S.STOPPED

    def test_a_record_for_a_window_outside_the_plan_stops_it(self, on):
        st = {"windows": {"2026-11-12T19:15:00Z": done()}}
        s = step(plan(), st, at(18, 41))
        assert s["why"] == "STATE_NAMES_A_WINDOW_NOT_IN_THE_PLAN"

    def test_a_clock_without_a_timezone_stops_it(self, on):
        s = step(plan(), {}, dt.datetime(2026, 11, 12, 18, 41))
        assert s["why"] == "CLOCK_HAS_NO_TIMEZONE"

    def test_a_whole_run_end_to_end(self, on):
        p, st = plan(), {"windows": {}}
        seen = []
        script = [
            (at(18, 20), None), (at(18, 41), ("armed_utc", "t")),
            (at(19, 5), None), (at(19, 11), ("restored_utc", "t")),
            (at(19, 13), None),
            (at(19, 17), ("settle", "0.03")),
            (at(19, 18), ("armed_utc", "t")), (at(19, 41), ("restored_utc", "t")),
            (at(19, 46), ("settle", "0.00")),
            (at(19, 47), ("armed_utc", "t")), (at(20, 11), ("restored_utc", "t")),
            (at(20, 17), ("settle", "0.01")), (at(20, 18), None)]
        for now, act in script:
            s = step(p, st, now)
            seen.append((s["action"], s.get("window")))
            if act:
                w = s["window"]
                rec = st["windows"].setdefault(w, {})
                if act[0] == "settle":
                    rec.update(settled=True, settled_pnl_usd=act[1],
                               reconciled=True, accounting_unresolved=0)
                else:
                    rec[act[0]] = act[1]
        assert seen == [
            (S.WAIT, W[0]), (S.PREFLIGHT_AND_ARM, W[0]), (S.MONITOR, W[0]),
            (S.RESTORE, W[0]), (S.AWAIT_SETTLEMENT, W[0]),
            (S.AWAIT_SETTLEMENT, W[0]),
            (S.PREFLIGHT_AND_ARM, W[1]), (S.RESTORE, W[1]),
            (S.AWAIT_SETTLEMENT, W[1]),
            (S.PREFLIGHT_AND_ARM, W[2]), (S.RESTORE, W[2]),
            (S.AWAIT_SETTLEMENT, W[2]), (S.COMPLETE, None)]
