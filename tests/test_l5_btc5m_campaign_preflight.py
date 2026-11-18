"""The campaign preflight refuses by default and names what blocks.

THE TEST THAT MATTERS is
`TestTheHaltThatBurnedThreeWindows::test_the_2225z_campaign_would_have_been_blocked`,
which replays the real 2026-11-17 observation: a latched halt asserted at
21:57:04Z by l5-watchdog over a sibling lane's tennis order, with the
BTC5m lane itself clean. Three authorized windows were spent against that
state. The preflight must return BLOCKED and say why.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src import l5_btc5m_campaign_preflight as PF


NOW = dt.datetime(2026, 11, 17, 22, 20, 0, tzinfo=dt.timezone.utc)


def _iso(**delta):
    return (NOW - dt.timedelta(**delta)).isoformat()


# ---------------------------------------------------------------- fixtures
def good_halt():
    return {"halt": False, "t": _iso(seconds=5), "who": "l5-watchdog"}


def good_campaign():
    return {"approved": True, "nonce": "n0nce-abcdef",
            "issued_utc": _iso(minutes=10),
            "expires_utc": (NOW + dt.timedelta(hours=1)).isoformat(),
            "markets": ["cpc-btc-updown-5m-2026-11-17-2225z"],
            "configured_markets": ["cpc-btc-updown-5m-2026-11-17-2225z"]}


def good_deploy():
    return {"running_sha": "a12667f4a", "approved_sha": "a12667f4a",
            "status": "SUCCESS",
            "effective_config": {"L5_BTC5M_INVENTORY_RANKING": "YES"},
            "expected_config": {"L5_BTC5M_INVENTORY_RANKING": "YES"}}


def good_budgets():
    return {"OPEN": {"configured": 36, "remaining": 36, "required": 24},
            "COMPLETE": {"configured": 36, "remaining": 36, "required": 24},
            "REPLACE": {"configured": 12, "remaining": 12, "required": 6},
            "TOTAL": {"remaining": "84", "required": "54"}}


def good_ranker():
    return {"enabled": True, "faults": 0}


def good_ev():
    return {"min_ev_per_share": "0.0001", "fill_model": "UNMEASURED",
            "discovery_token": "UNMEASURED_DISCOVERY_AUTHORIZED",
            "taker_allowed": False}


def good_exposure():
    return {"aggregate_usd": "0.15", "max_aggregate_usd": "1.00",
            "per_market_usd": "0.10", "max_per_market_usd": "0.30",
            "cumulative_realized_usd": "-0.3291",
            "max_daily_loss_usd": "5.00"}


def good_watchdog():
    return {"as_of_utc": _iso(seconds=10), "cancel_enabled": True,
            "read_error": None, "overdue": [], "open_orders": 0,
            "managed_lanes": ["http://pmus-spring-l5-probe"
                              ".railway.internal:8090:OK:2"],
            "registries": ["http://l5-btc5m-live.railway.internal:8090:OK"],
            "our_registry": "http://l5-btc5m-live.railway.internal:8090"}


def good_executor():
    return {"arming_refusal": None, "running": True, "recent_exits": [],
            "not_after_utc": (NOW + dt.timedelta(minutes=20)).isoformat()}


def all_good(**over):
    kw = {"halt": good_halt(), "campaign": good_campaign(),
          "deploy": good_deploy(), "budgets": good_budgets(),
          "ranker": good_ranker(), "ev": good_ev(),
          "exposure": good_exposure(), "watchdog": good_watchdog(),
          "executor": good_executor(), "now": NOW}
    kw.update(over)
    return kw


# -------------------------------------------------------------- fail closed
class TestItRefusesWithNoEvidence:

    def test_no_arguments_is_blocked_on_every_group(self):
        v = PF.preflight(now=NOW)
        assert v["verdict"] == "BLOCKED"
        assert v["ok"] is False
        assert set(v["groups_blocked"]) == set(PF.GROUPS)

    def test_every_missing_group_says_observation_missing(self):
        v = PF.preflight(now=NOW)
        assert all(r.get("why") == "OBSERVATION_MISSING"
                   for r in v["blocking"])

    @pytest.mark.parametrize("group", PF.GROUPS)
    def test_dropping_any_one_group_blocks(self, group):
        v = PF.preflight(**all_good(**{group: None}))
        assert v["verdict"] == "BLOCKED"
        assert group in v["groups_blocked"]

    @pytest.mark.parametrize("truthy", ["UNKNOWN", "yes", 1, {"a": 1}, [1]])
    def test_a_truthy_ok_that_is_not_true_is_not_a_pass(self, truthy):
        assert PF._is_pass({"ok": truthy}) is False

    def test_only_literal_true_passes(self):
        assert PF._is_pass({"ok": True}) is True


class TestTheHappyPath:

    def test_a_fully_good_observation_is_ready(self):
        v = PF.preflight(**all_good())
        assert v["verdict"] == "READY", v["blocking"]
        assert v["blocking_count"] == 0

    def test_ready_is_not_an_authorization(self):
        v = PF.preflight(**all_good())
        assert "not an authorization" in v["note"]

    def test_every_group_is_represented_in_checks(self):
        v = PF.preflight(**all_good())
        assert {r["group"] for r in v["checks"]} == set(PF.GROUPS)


# ------------------------------------------------------------------- halt
class TestTheHaltThatBurnedThreeWindows:

    #: the real record, copied from the 21:57:04Z sweep.
    LATCH = {"halt": True, "record": {
        "halt": True, "reason": "UNMANAGED_RESTING_ORDER",
        "t": "2026-11-17T21:57:04.535614+00:00", "who": "l5-watchdog",
        "evidence": {"orders": [{"age_s": 315.36012, "leaves": "5",
                                 "market": "aec-utr-gaskan-hanbra-2026-11-17",
                                 "venue_order_id": "DSHAHBP0144P"}],
                     "who": "l5-watchdog",
                     "why": "an order resting past the executor's own "
                            "cancellation deadline means the executor is "
                            "not managing it"}}}

    def test_the_2225z_campaign_would_have_been_blocked(self):
        v = PF.preflight(**all_good(halt=self.LATCH))
        assert v["verdict"] == "BLOCKED"
        assert "halt" in v["groups_blocked"]

    def test_it_names_the_latch_reason_and_who_asserted_it(self):
        v = PF.preflight(**all_good(halt=self.LATCH))
        row = next(r for r in v["blocking"] if r["check"] == "halt_clear")
        assert row["why"] == "HALT_LATCHED"
        assert row["reason"] == "UNMANAGED_RESTING_ORDER"
        assert row["who"] == "l5-watchdog"
        assert row["at"].startswith("2026-11-17T21:57:04")

    def test_it_carries_the_evidence_so_the_order_is_attributable(self):
        v = PF.preflight(**all_good(halt=self.LATCH))
        row = next(r for r in v["blocking"] if r["check"] == "halt_clear")
        order = row["evidence"]["orders"][0]
        assert order["market"] == "aec-utr-gaskan-hanbra-2026-11-17"
        assert order["venue_order_id"] == "DSHAHBP0144P"

    def test_a_clean_btc5m_lane_does_not_clear_a_shared_halt(self):
        """The whole point: our lane was clean and still could not trade."""
        v = PF.preflight(**all_good(halt=self.LATCH,
                                    executor=good_executor(),
                                    exposure=good_exposure()))
        assert v["verdict"] == "BLOCKED"

    def test_an_absent_halt_field_is_a_halt(self):
        v = PF.preflight(**all_good(halt={"t": _iso(seconds=1)}))
        row = next(r for r in v["blocking"] if r["check"] == "halt_clear")
        assert row["why"] == "HALT_UNREADABLE"

    def test_a_stale_clear_record_is_not_a_clear(self):
        v = PF.preflight(**all_good(
            halt={"halt": False, "t": _iso(hours=1)}))
        row = next(r for r in v["blocking"] if r["check"] == "halt_clear")
        assert row["why"] == "HALT_RECORD_STALE"

    def test_an_unreadable_timestamp_blocks(self):
        v = PF.preflight(**all_good(halt={"halt": False, "t": "whenever"}))
        row = next(r for r in v["blocking"] if r["check"] == "halt_clear")
        assert row["why"] == "HALT_AGE_UNREADABLE"


class TestResetEligibilityIsAdvisoryNotAGate:

    def test_it_is_not_applicable_when_nothing_is_latched(self):
        v = PF.preflight(**all_good())
        assert v["reset_eligibility"]["why"] == "NOT_APPLICABLE"

    def test_a_latched_halt_with_an_empty_book_and_fresh_lanes_is_eligible(self):
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH))
        assert v["reset_eligibility"]["eligible"] is True

    def test_a_non_empty_book_blocks_the_reset(self):
        wd = dict(good_watchdog(), open_orders=2)
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH, watchdog=wd))
        whys = [b["why"] for b in v["reset_eligibility"]["blockers"]]
        assert "VENUE_BOOK_NOT_EMPTY" in whys

    def test_a_skew_stale_lane_blocks_the_reset(self):
        """Clearing into a stale lane just re-latches the same halt."""
        wd = dict(good_watchdog(), managed_lanes=[
            "http://pmus-spring-l5-probe.railway.internal:8090:STALE:-6.08"])
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH, watchdog=wd))
        blockers = v["reset_eligibility"]["blockers"]
        assert any(b["why"] == "MANAGED_LANE_NOT_FRESH" for b in blockers)
        assert any("re-latch" in str(b.get("consequence")) for b in blockers)

    def test_eligibility_never_turns_a_blocked_verdict_ready(self):
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH))
        assert v["reset_eligibility"]["eligible"] is True
        assert v["verdict"] == "BLOCKED"


# --------------------------------------------------------------- campaign
class TestCampaignAuthorization:

    def test_unapproved_blocks(self):
        v = PF.preflight(**all_good(
            campaign=dict(good_campaign(), approved=False)))
        assert any(r["check"] == "campaign_approved" for r in v["blocking"])

    def test_approved_must_be_literally_true(self):
        v = PF.preflight(**all_good(
            campaign=dict(good_campaign(), approved="yes")))
        assert any(r["check"] == "campaign_approved" for r in v["blocking"])

    def test_an_expired_campaign_blocks(self):
        c = dict(good_campaign(),
                 expires_utc=(NOW - dt.timedelta(minutes=1)).isoformat())
        v = PF.preflight(**all_good(campaign=c))
        row = next(r for r in v["blocking"] if r["check"] == "campaign_window")
        assert row["why"] == "CAMPAIGN_EXPIRED"

    def test_a_campaign_not_yet_in_force_blocks(self):
        c = dict(good_campaign(),
                 issued_utc=(NOW + dt.timedelta(minutes=5)).isoformat(),
                 expires_utc=(NOW + dt.timedelta(hours=2)).isoformat())
        v = PF.preflight(**all_good(campaign=c))
        row = next(r for r in v["blocking"] if r["check"] == "campaign_window")
        assert row["why"] == "CAMPAIGN_NOT_YET"

    def test_a_short_nonce_blocks(self):
        v = PF.preflight(**all_good(
            campaign=dict(good_campaign(), nonce="abc")))
        assert any(r["check"] == "campaign_nonce" for r in v["blocking"])

    def test_an_extra_configured_market_blocks(self):
        """Approval for three windows must not run a fourth."""
        c = dict(good_campaign(), configured_markets=[
            "cpc-btc-updown-5m-2026-11-17-2225z",
            "cpc-btc-updown-5m-2026-11-17-2240z"])
        v = PF.preflight(**all_good(campaign=c))
        row = next(r for r in v["blocking"]
                   if r["check"] == "campaign_markets")
        assert row["why"] == "MARKET_SET_MISMATCH"
        assert row["configured_not_approved"] == [
            "cpc-btc-updown-5m-2026-11-17-2240z"]

    def test_the_match_is_exact_not_a_prefix(self):
        c = dict(good_campaign(),
                 configured_markets=["cpc-btc-updown-5m-2026-11-17-2225"])
        v = PF.preflight(**all_good(campaign=c))
        assert any(r["check"] == "campaign_markets" for r in v["blocking"])

    def test_no_approved_markets_blocks(self):
        c = dict(good_campaign(), markets=[], configured_markets=[])
        v = PF.preflight(**all_good(campaign=c))
        row = next(r for r in v["blocking"]
                   if r["check"] == "campaign_markets")
        assert row["why"] == "NO_MARKETS_APPROVED"

    def test_too_many_windows_blocks_against_the_registry_cap(self):
        from src import l5_campaign_approval as CA
        series = CA.series_of("cpc-btc-updown-5m-2026-11-17-2225z")
        cap = CA.max_windows_for(series)
        many = ["cpc-btc-updown-5m-2026-11-17-%04dz" % i
                for i in range(cap + 1)]
        c = dict(good_campaign(), markets=many, configured_markets=many)
        v = PF.preflight(**all_good(campaign=c))
        row = next(r for r in v["blocking"]
                   if r["check"] == "campaign_window_count")
        assert row["why"] == "TOO_MANY_WINDOWS"
        assert row["cap"] == cap


# ----------------------------------------------------------------- deploy
class TestDeployedCodeAndEffectiveConfig:

    def test_a_different_sha_blocks(self):
        v = PF.preflight(**all_good(
            deploy=dict(good_deploy(), running_sha="deadbeef1")))
        row = next(r for r in v["blocking"] if r["check"] == "deployed_sha")
        assert row["why"] == "SHA_MISMATCH"

    def test_a_short_prefix_still_matches_the_same_commit(self):
        v = PF.preflight(**all_good(
            deploy=dict(good_deploy(), running_sha="a12667f4a99bb11",
                        approved_sha="a12667f4a")))
        assert v["verdict"] == "READY", v["blocking"]

    def test_a_too_short_sha_is_not_trusted(self):
        v = PF.preflight(**all_good(
            deploy=dict(good_deploy(), running_sha="a12", approved_sha="a12")))
        assert any(r["check"] == "deployed_sha" for r in v["blocking"])

    def test_a_crashed_deployment_blocks(self):
        v = PF.preflight(**all_good(
            deploy=dict(good_deploy(), status="CRASHED")))
        row = next(r for r in v["blocking"]
                   if r["check"] == "deployment_healthy")
        assert row["why"] == "DEPLOYMENT_NOT_HEALTHY"

    def test_config_not_read_back_blocks(self):
        v = PF.preflight(**all_good(
            deploy=dict(good_deploy(), effective_config={})))
        row = next(r for r in v["blocking"]
                   if r["check"] == "effective_config")
        assert row["why"] == "CONFIG_NOT_READ_BACK"

    def test_a_variable_we_set_that_the_process_did_not_take_blocks(self):
        """The 14:50z failure: variables written mid-window, never read."""
        d = dict(good_deploy(),
                 effective_config={"L5_BTC5M_INVENTORY_RANKING": "NO"})
        v = PF.preflight(**all_good(deploy=d))
        row = next(r for r in v["blocking"]
                   if r["check"] == "effective_config")
        assert row["why"] == "CONFIG_MISMATCH"
        assert row["diff"]["L5_BTC5M_INVENTORY_RANKING"]["effective"] == "NO"


# ---------------------------------------------------------------- budgets
class TestSubmissionBudgets:

    def test_every_authorization_class_is_checked(self):
        from src import l5_live_authorization as LA
        v = PF.preflight(**all_good())
        names = {r["check"] for r in v["checks"] if r["group"] == "budgets"}
        for klass in LA.SUBMISSION_CLASSES:
            assert "budget_%s" % klass.lower() in names

    def test_an_unconfigured_class_blocks_and_is_not_unlimited(self):
        b = good_budgets()
        b["REPLACE"] = {"configured": None, "remaining": 0, "required": 6}
        v = PF.preflight(**all_good(budgets=b))
        row = next(r for r in v["blocking"] if r["check"] == "budget_replace")
        assert row["why"] == "CLASS_BUDGET_NOT_CONFIGURED"

    def test_the_1930z_starved_completion_class_blocks(self):
        """MAX_COMPLETE=2 against a 23-submission window."""
        b = good_budgets()
        b["COMPLETE"] = {"configured": 2, "remaining": 2, "required": 23}
        v = PF.preflight(**all_good(budgets=b))
        row = next(r for r in v["blocking"] if r["check"] == "budget_complete")
        assert row["why"] == "CLASS_BUDGET_INSUFFICIENT"
        assert row["remaining"] == 2 and row["required"] == 23

    def test_a_zero_configured_budget_blocks_even_with_nothing_required(self):
        b = good_budgets()
        b["OPEN"] = {"configured": 0, "remaining": 0, "required": 0}
        v = PF.preflight(**all_good(budgets=b))
        assert any(r["check"] == "budget_open" for r in v["blocking"])

    def test_an_unreadable_budget_blocks(self):
        b = good_budgets()
        b["OPEN"] = {"configured": "lots", "remaining": 1, "required": 1}
        v = PF.preflight(**all_good(budgets=b))
        row = next(r for r in v["blocking"] if r["check"] == "budget_open")
        assert row["ok"] == PF.UNKNOWN

    def test_a_missing_class_observation_blocks(self):
        b = good_budgets()
        del b["COMPLETE"]
        v = PF.preflight(**all_good(budgets=b))
        row = next(r for r in v["blocking"] if r["check"] == "budget_complete")
        assert row["why"] == "CLASS_BUDGET_UNREAD"

    def test_the_aggregate_backstop_binds_independently(self):
        b = good_budgets()
        b["TOTAL"] = {"remaining": "10", "required": "54"}
        v = PF.preflight(**all_good(budgets=b))
        row = next(r for r in v["blocking"] if r["check"] == "budget_total")
        assert row["why"] == "TOTAL_BUDGET_INSUFFICIENT"


# ----------------------------------------------------------- ranker and EV
class TestRankerAndEvGates:

    def test_a_ranker_that_is_off_blocks(self):
        v = PF.preflight(**all_good(ranker={"enabled": False, "faults": 0}))
        row = next(r for r in v["blocking"] if r["check"] == "ranker_enabled")
        assert row["why"] == "RANKER_NOT_ENABLED"

    def test_enabled_must_be_literally_true_not_the_string_yes(self):
        v = PF.preflight(**all_good(ranker={"enabled": "YES", "faults": 0}))
        assert any(r["check"] == "ranker_enabled" for r in v["blocking"])

    def test_a_faulting_ranker_blocks_because_it_silently_holds(self):
        v = PF.preflight(**all_good(ranker={"enabled": True, "faults": 3}))
        row = next(r for r in v["blocking"] if r["check"] == "ranker_healthy")
        assert row["why"] == "RANKER_FAULTING"

    def test_an_ev_gate_at_zero_is_not_in_force(self):
        v = PF.preflight(**all_good(
            ev=dict(good_ev(), min_ev_per_share="0")))
        row = next(r for r in v["blocking"] if r["check"] == "ev_gate")
        assert row["why"] == "EV_GATE_NOT_IN_FORCE"

    def test_a_negative_ev_gate_blocks(self):
        v = PF.preflight(**all_good(
            ev=dict(good_ev(), min_ev_per_share="-0.01")))
        assert any(r["check"] == "ev_gate" for r in v["blocking"])

    def test_an_unmeasured_model_without_the_token_blocks(self):
        v = PF.preflight(**all_good(
            ev=dict(good_ev(), discovery_token="YES")))
        row = next(r for r in v["blocking"] if r["check"] == "ev_fill_model")
        assert row["why"] == "UNMEASURED_WITHOUT_DISCOVERY_AUTHORIZATION"

    def test_a_measured_model_needs_no_token(self):
        v = PF.preflight(**all_good(
            ev=dict(good_ev(), fill_model="MEASURED", discovery_token="")))
        assert v["verdict"] == "READY", v["blocking"]

    def test_taker_must_be_stated_off_not_merely_absent(self):
        ev = good_ev()
        del ev["taker_allowed"]
        v = PF.preflight(**all_good(ev=ev))
        row = next(r for r in v["blocking"] if r["check"] == "taker_disabled")
        assert row["why"] == "TAKER_NOT_STATED_OFF"

    def test_taker_enabled_blocks(self):
        v = PF.preflight(**all_good(
            ev=dict(good_ev(), taker_allowed=True)))
        assert any(r["check"] == "taker_disabled" for r in v["blocking"])


# --------------------------------------------------------------- exposure
class TestAccountWideExposureAndLoss:

    def test_exposure_over_the_cap_blocks(self):
        v = PF.preflight(**all_good(
            exposure=dict(good_exposure(), aggregate_usd="2.00")))
        row = next(r for r in v["blocking"]
                   if r["check"] == "account_exposure")
        assert row["why"] == "EXPOSURE_OVER_CAP"

    def test_per_market_exposure_over_the_cap_blocks(self):
        v = PF.preflight(**all_good(
            exposure=dict(good_exposure(), per_market_usd="0.95")))
        assert any(r["check"] == "per_market_exposure"
                   for r in v["blocking"])

    def test_the_lifetime_loss_so_far_does_not_block_a_five_dollar_cap(self):
        v = PF.preflight(**all_good())
        row = next(r for r in v["checks"] if r["check"] == "cumulative_loss")
        assert row["ok"] is True
        assert row["loss_usd"] == "0.3291"

    def test_a_loss_at_the_cap_blocks(self):
        v = PF.preflight(**all_good(exposure=dict(
            good_exposure(), cumulative_realized_usd="-5.01")))
        row = next(r for r in v["blocking"]
                   if r["check"] == "cumulative_loss")
        assert row["why"] == "DAILY_LOSS_LIMIT_REACHED"

    def test_a_profit_is_never_read_as_a_loss(self):
        """Sign errors here fail in the direction that keeps trading."""
        v = PF.preflight(**all_good(exposure=dict(
            good_exposure(), cumulative_realized_usd="4.00",
            max_daily_loss_usd="1.00")))
        row = next(r for r in v["checks"] if r["check"] == "cumulative_loss")
        assert row["ok"] is True
        assert row["loss_usd"] == "0"

    def test_an_unreadable_exposure_blocks(self):
        v = PF.preflight(**all_good(
            exposure=dict(good_exposure(), aggregate_usd=None)))
        row = next(r for r in v["blocking"]
                   if r["check"] == "account_exposure")
        assert row["ok"] == PF.UNKNOWN


# --------------------------------------------------------------- watchdog
class TestWatchdogHealth:

    def test_a_stale_sweep_blocks(self):
        v = PF.preflight(**all_good(
            watchdog=dict(good_watchdog(), as_of_utc=_iso(minutes=10))))
        row = next(r for r in v["blocking"]
                   if r["check"] == "watchdog_fresh")
        assert row["why"] == "SWEEP_STALE_OR_SKEWED"

    def test_a_watchdog_that_cannot_cancel_blocks(self):
        v = PF.preflight(**all_good(
            watchdog=dict(good_watchdog(), cancel_enabled=False)))
        assert any(r["check"] == "watchdog_can_cancel"
                   for r in v["blocking"])

    def test_a_venue_read_error_blocks(self):
        v = PF.preflight(**all_good(
            watchdog=dict(good_watchdog(), read_error="URLError")))
        row = next(r for r in v["blocking"]
                   if r["check"] == "watchdog_reads_venue")
        assert row["why"] == "VENUE_READ_ERROR"

    def test_an_overdue_order_blocks_before_it_latches(self):
        """The fuse, caught while it is still burning."""
        overdue = [{"venue_order_id": "DSHAHBP0144P", "age_s": 310.0,
                    "market": "aec-utr-gaskan-hanbra-2026-11-17"}]
        v = PF.preflight(**all_good(
            watchdog=dict(good_watchdog(), overdue=overdue)))
        row = next(r for r in v["blocking"]
                   if r["check"] == "no_overdue_orders")
        assert row["why"] == "OVERDUE_ORDERS_RESTING"
        assert row["count"] == 1

    def test_the_skew_stale_lane_that_caused_the_2157z_latch_blocks(self):
        """age -6.08s against a 5.0s band: the actual root cause."""
        wd = dict(good_watchdog(), managed_lanes=[
            "http://pmus-spring-l5-probe.railway.internal:8090:STALE:-6.08"])
        v = PF.preflight(**all_good(watchdog=wd))
        row = next(r for r in v["blocking"]
                   if r["check"] == "managed_lanes_fresh")
        assert row["why"] == "MANAGED_LANE_NOT_FRESH"
        assert "re-latch" not in str(row["why"])
        assert "latch UNMANAGED_RESTING_ORDER" in str(row["consequence"])

    def test_an_unreachable_lane_blocks(self):
        wd = dict(good_watchdog(), managed_lanes=[
            "http://x.railway.internal:8090:UNREACHABLE:URLError"])
        v = PF.preflight(**all_good(watchdog=wd))
        assert any(r["check"] == "managed_lanes_fresh"
                   for r in v["blocking"])

    def test_a_lane_that_does_not_claim_management_blocks(self):
        wd = dict(good_watchdog(), managed_lanes=[
            "http://x.railway.internal:8090:NOT_A_MANAGED_LANE"])
        v = PF.preflight(**all_good(watchdog=wd))
        assert any(r["check"] == "managed_lanes_fresh"
                   for r in v["blocking"])

    def test_our_own_unreadable_registry_blocks(self):
        wd = dict(good_watchdog(), registries=[
            "http://l5-btc5m-live.railway.internal:8090"
            ":REGISTRY_UNREACHABLE:URLError"])
        v = PF.preflight(**all_good(watchdog=wd))
        row = next(r for r in v["blocking"]
                   if r["check"] == "our_registry_readable")
        assert row["why"] == "OUR_REGISTRY_NOT_READABLE"

    def test_the_skew_band_matches_the_module_that_enforces_it(self):
        """Pinned equal rather than trusted: one definition of skew."""
        from src import l5_managed_lanes as ML
        assert PF.LANE_SKEW_S == ML.MAX_SKEW_S
        assert PF.LANE_MAX_AGE_S == ML.MAX_AGE_S


# --------------------------------------------------------------- executor
class TestExecutorReadiness:

    def test_an_arming_refusal_blocks_and_is_reported_verbatim(self):
        v = PF.preflight(**all_good(executor=dict(
            good_executor(), arming_refusal="TRANSPORT_NOT_ARMED")))
        row = next(r for r in v["blocking"] if r["check"] == "executor_ready")
        assert row["arming_refusal"] == "TRANSPORT_NOT_ARMED"
        assert row["why"] == "TRANSPORT_NOT_ARMED"

    def test_the_halt_refusal_surfaces_through_the_arming_chain_too(self):
        v = PF.preflight(**all_good(executor=dict(
            good_executor(), arming_refusal="L5_LIVE_HALT")))
        assert any(r["check"] == "executor_ready" for r in v["blocking"])

    def test_an_unread_arming_refusal_blocks(self):
        ex = good_executor()
        del ex["arming_refusal"]
        v = PF.preflight(**all_good(executor=ex))
        row = next(r for r in v["blocking"] if r["check"] == "executor_ready")
        assert row["why"] == "ARMING_REFUSAL_UNREAD"

    def test_a_process_that_is_not_running_blocks(self):
        v = PF.preflight(**all_good(
            executor=dict(good_executor(), running=False)))
        row = next(r for r in v["blocking"]
                   if r["check"] == "executor_running")
        assert row["why"] == "EXECUTOR_NOT_RUNNING"

    def test_a_designed_exit_is_still_an_exit(self):
        """FLEET_CHILD_EXIT code 0 on a latched halt: correct, and down."""
        v = PF.preflight(**all_good(executor=dict(
            good_executor(),
            recent_exits=[{"strategy": "BTC5M", "code": 0,
                           "utc": "2026-11-17T22:09:42Z"}])))
        row = next(r for r in v["blocking"]
                   if r["check"] == "no_premature_exit")
        assert row["why"] == "RECENT_PROCESS_EXIT"

    def test_a_missing_not_after_blocks(self):
        v = PF.preflight(**all_good(
            executor=dict(good_executor(), not_after_utc=None)))
        row = next(r for r in v["blocking"]
                   if r["check"] == "not_after_future")
        assert row["why"] == "NOT_AFTER_MISSING"

    def test_a_passed_not_after_blocks(self):
        v = PF.preflight(**all_good(executor=dict(
            good_executor(),
            not_after_utc=(NOW - dt.timedelta(minutes=5)).isoformat())))
        row = next(r for r in v["blocking"]
                   if r["check"] == "not_after_future")
        assert row["why"] == "NOT_AFTER_PASSED"


# ---------------------------------------------------------------- reading
class TestTheSummaryIsOperatorReadable:

    def test_it_names_the_verdict(self):
        assert "BLOCKED" in PF.summary(PF.preflight(now=NOW))

    def test_it_lists_each_blocking_check(self):
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH))
        text = PF.summary(v)
        assert "halt/halt_clear" in text
        assert "HALT_LATCHED" in text

    def test_a_ready_summary_counts_the_checks(self):
        text = PF.summary(PF.preflight(**all_good()))
        assert "READY" in text and "checks passed" in text

    def test_it_reports_reset_blockers_when_a_halt_is_latched(self):
        wd = dict(good_watchdog(), open_orders=3)
        v = PF.preflight(**all_good(
            halt=TestTheHaltThatBurnedThreeWindows.LATCH, watchdog=wd))
        assert "VENUE_BOOK_NOT_EMPTY" in PF.summary(v)


class TestItRemainsAPureRead:

    def test_the_module_touches_no_network_or_execution_code(self):
        import pathlib
        src = pathlib.Path("src/l5_btc5m_campaign_preflight.py").read_text()
        for needle in ("requests", "urllib", "socket", "l5_live_execution",
                       "l5_pmus_live_runner", "subprocess", "os.environ",
                       "set_variables"):
            assert needle not in src, needle

    def test_it_imports_only_read_only_threshold_modules(self):
        import pathlib
        src = pathlib.Path("src/l5_btc5m_campaign_preflight.py").read_text()
        imports = {ln.strip() for ln in src.splitlines()
                   if "from src import" in ln}
        assert imports == {
            "from src import l5_campaign_approval as CA",
            "from src import l5_live_authorization as LA"}
