"""The probe preflight and the post-settlement audit, exercised.

PRIORITY 3. The pre-flight conditions had been written in prose three
times; prose cannot be run before a window or re-run after settlement.
These tests drive the module that can, including against the REAL live
heartbeat values read from deployment a912f813.

The property that matters most: an UNKNOWN is never a pass, and a probe
whose cost basis is unknown is classified **UNINTERPRETABLE** rather than
reported as a loss or a profit.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_probe_preflight as PF

MARKET = "cpc-btc-updown-15m-2026-11-08-1200z"


def live_hb(**over):
    """The gate values actually observed on the running deployment."""
    hb = {
        "submission_gate": {
            "blocked": True, "reason": "SHADOW_ONLY_LOCK", "armed": False,
            "halt_latch": "CLEAR",
            "halt_latch_telemetry": {"kind": "PRESENT", "halt": False}},
        "effective_cash_gate": {
            "max_cash_deployed_usd": "12", "cash_policy": "NO_RECYCLING",
            "max_unmatched_contracts": "5", "max_usd_per_market": "20",
            "max_worst_case_deficit_usd": "1.00",
            "worst_case_deficit_check": "ACTIVE"},
        "day_realized": {"utc_day_realized_usd": "0", "halt_usd": "5",
                         "would_halt": False, "room_usd": "5"},
        "campaign": {"completion_reserve": {
            "submissions_per_obligation": 2, "may_open": True,
            "why_not": None}},
        "auth_book": {"connected": True, "stall_reconnects": 0,
                      "gap_suspected": False, "stream_age_s": 0.0},
        "realized": {"unknown_basis_keys": [
            "cpc-btc-updown-15m-2026-10-15-1915z|DOWN"]},
        "accounting": {"unknown_detail": {
            "post_baseline_settlements_not_attested": [
                {"market": "cpc-btc-updown-15m-2026-11-05-2300z",
                 "pnl": "-0.40", "provenance": "UNKNOWN_COST_UPPER_BOUND"}]}},
    }
    hb.update(over)
    return hb


def good_plan(**over):
    p = {"ok": True, "market": MARKET, "depth_ticks": 2,
         "pair_cost": "0.95",
         "legs": [{"side": "UP", "price": "0.60", "qty": "1",
                   "mode": "MAKER", "post_only": True},
                  {"side": "DOWN", "price": "0.35", "qty": "1",
                   "mode": "MAKER", "post_only": True}],
         "fee_floor": {"ok": True, "requires_post_only": True,
                       "net_if_maker": "0.05584375",
                       "net_if_taker": "0.01750875"}}
    p.update(over)
    return p


def calib(**over):
    c = {"orders": 3, "events_appended": 11, "errors": {}}
    c.update(over)
    return c


# ============================================================== preflight
def test_the_live_gate_values_pass_the_preflight():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=[MARKET], calibration=calib())
    assert out["ok"] is True, (out["failed"], out["unknown"])


def test_an_absent_input_is_UNKNOWN_and_never_a_pass():
    out = PF.preflight(heartbeat=live_hb(), plan=None, markets=[MARKET])
    assert out["ok"] is False
    assert "plan_admissible" in out["unknown"]
    assert "recorder_can_calibrate" in out["unknown"]
    assert out["failed"] == []          # unknown is not failure either


@pytest.mark.parametrize("patch,expect", [
    ({"worst_case_deficit_check": "NOT_CONFIGURED_NO_CHECK_RUNS"},
     "worst_case_deficit_check_active"),
    ({"max_worst_case_deficit_usd": None},
     "worst_case_deficit_check_active"),
    ({"cash_policy": "RECYCLING"}, "cash_policy_is_no_recycling"),
    ({"max_unmatched_contracts": "0"}, "unmatched_contract_cap_present"),
])
def test_a_missing_or_widened_limit_fails_the_named_check(patch, expect):
    hb = live_hb()
    hb["effective_cash_gate"].update(patch)
    out = PF.preflight(heartbeat=hb, plan=good_plan(), markets=[MARKET],
                       calibration=calib())
    assert expect in out["failed"], out


def test_an_exhausted_daily_budget_fails():
    hb = live_hb()
    hb["day_realized"].update({"room_usd": "0", "would_halt": True})
    out = PF.preflight(heartbeat=hb, plan=good_plan(), markets=[MARKET],
                       calibration=calib())
    assert "daily_loss_room_remains" in out["failed"]


def test_a_disconnected_or_gapped_book_fails():
    for patch in ({"connected": False}, {"gap_suspected": True}):
        hb = live_hb()
        hb["auth_book"].update(patch)
        out = PF.preflight(heartbeat=hb, plan=good_plan(), markets=[MARKET],
                           calibration=calib())
        assert "authenticated_book_connected_and_fresh" in out["failed"]


def test_a_refused_plan_fails_and_names_its_reason():
    out = PF.preflight(heartbeat=live_hb(),
                       plan=good_plan(ok=False,
                                       reason="PAIR_COST_ABOVE_CAP",
                                       pair_cost="0.99"),
                       markets=[MARKET], calibration=calib())
    assert "plan_admissible" in out["failed"]
    assert "pair_cost_inside_ceiling" in out["failed"]


def test_a_depth_0_plan_fails_the_expected_depth_check():
    """Belt and braces on the depth the owner selects."""
    out = PF.preflight(heartbeat=live_hb(),
                       plan=good_plan(depth_ticks=0, pair_cost="0.99"),
                       markets=[MARKET], calibration=calib(),
                       expect_depth_ticks=2)
    assert "plan_depth_is_the_expected_depth" in out["failed"]


def test_a_taker_leg_fails_the_post_only_check():
    p = good_plan()
    p["legs"][1] = dict(p["legs"][1], post_only=False, mode="TAKER")
    out = PF.preflight(heartbeat=live_hb(), plan=p, markets=[MARKET],
                       calibration=calib())
    assert "both_legs_post_only_maker" in out["failed"]


def test_a_plan_for_a_market_nobody_pre_registered_fails():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=["cpc-btc-updown-15m-2026-01-01-0000z"],
                       calibration=calib())
    assert "plan_market_is_pre_registered" in out["failed"]


def test_no_pre_registered_markets_at_all_fails():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(), markets=[],
                       calibration=calib())
    assert "markets_pre_registered" in out["failed"]


def test_a_recorder_with_write_failures_fails():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=[MARKET],
                       calibration=calib(errors={"ledger_write_failed": 2}))
    assert "recorder_has_no_write_failures" in out["failed"]


def test_an_armed_gate_is_reported_rather_than_assumed_safe():
    """If the gate is already open the preflight must SAY so -- it is not
    this module's job to clear or to hide it."""
    hb = live_hb()
    hb["submission_gate"].update({"blocked": False, "armed": True,
                                  "reason": None})
    out = PF.preflight(heartbeat=hb, plan=good_plan(), markets=[MARKET],
                       calibration=calib())
    assert "submission_gate_blocked_until_owner_clears" in out["failed"]


def test_the_preflight_disclaims_being_an_authorization():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=[MARKET], calibration=calib())
    assert "not an authorization" in out["labels"]


def test_the_preflight_holds_no_transport_and_submits_nothing():
    import inspect
    src = inspect.getsource(PF).lower()
    for banned in ("def submit", "self.transport", "requests.", ".post(",
                   "urllib", "http://", "https://", "arm(", "cancel("):
        assert banned not in src, banned


# ================================================================== audit
def filled_positions():
    return {"%s|UP" % MARKET: "1", "%s|DOWN" % MARKET: "1"}


def filled_legs():
    return {"%s|UP" % MARKET: "0.60", "%s|DOWN" % MARKET: "0.35"}


def good_latency():
    return {"book_age_at_decision_s": {"n": 7, "median": 0.4, "p95": 0.9},
            "detection_to_first_decision_s": 3.2}


def test_a_clean_probe_is_MEASURED():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"},
                          {"venue_order_id": "V-2"}],
                   latency=good_latency())
    assert out["verdict"] == "MEASURED", (out["failed"], out["unknown"])
    assert out["ok"] is True


def test_a_probe_in_unknown_basis_keys_is_UNINTERPRETABLE():
    """THE PRE-REGISTERED CLASSIFICATION. Not a loss, not a profit --
    the absence of a result."""
    hb = live_hb()
    hb["realized"]["unknown_basis_keys"].append("%s|UP" % MARKET)
    out = PF.audit(markets=[MARKET], heartbeat=hb,
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert out["verdict"] == "UNINTERPRETABLE"
    assert "probe_market_absent_from_unknown_basis_keys" in out["failed"]
    assert "upper bound" in out["labels"]


def test_a_probe_priced_at_the_upper_bound_is_UNINTERPRETABLE():
    hb = live_hb()
    hb["accounting"]["unknown_detail"][
        "post_baseline_settlements_not_attested"].append(
            {"market": MARKET, "pnl": "-1.00",
             "provenance": "UNKNOWN_COST_UPPER_BOUND"})
    out = PF.audit(markets=[MARKET], heartbeat=hb,
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert out["verdict"] == "UNINTERPRETABLE"
    assert "probe_market_not_priced_at_upper_bound" in out["failed"]


def test_a_filled_leg_with_no_basis_is_UNINTERPRETABLE():
    legs = dict(filled_legs())
    legs.pop("%s|DOWN" % MARKET)
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(), leg_cost=legs,
                   positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert out["verdict"] == "UNINTERPRETABLE"
    assert "every_filled_leg_has_a_cost_basis" in out["failed"]


def test_nothing_filled_is_INCOMPLETE_not_a_verdict_on_economics():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(), leg_cost={},
                   positions={}, fills=[], latency=good_latency())
    assert out["verdict"] == "INCOMPLETE"
    assert out["ok"] is False


def test_a_fill_without_a_venue_order_id_fails_reconciliation():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": ""}], latency=good_latency())
    assert "every_fill_carries_a_venue_order_id" in out["failed"]


def test_the_pre_registered_freshness_refusal_fires_on_a_bad_p95():
    """Independent of the economics: a controller that acted on books
    older than 2.0s at the tail cannot be trusted, whatever it earned."""
    lat = good_latency()
    lat["book_age_at_decision_s"] = {"n": 7, "median": 0.4, "p95": 6.8}
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=lat)
    assert "p95_book_age_within_requirement" in out["failed"]
    # the basis is still fine, so the P&L is MEASURED -- the freshness
    # failure is a separate, named blocker rather than a contamination
    assert out["verdict"] == "MEASURED"
    assert out["ok"] is False


def test_an_unmeasured_book_age_is_UNKNOWN_not_a_pass():
    lat = good_latency()
    lat["book_age_at_decision_s"] = {"n": 0, "median": None, "p95": None}
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=lat)
    assert "book_age_at_evaluation_measured" in out["failed"]
    assert "p95_book_age_within_requirement" in out["unknown"]


def test_a_missing_latency_report_is_UNKNOWN():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=None)
    assert "book_age_at_evaluation_measured" in out["unknown"]
    assert out["ok"] is False


def test_the_legacy_unattested_set_does_not_contaminate_the_probe():
    """The live heartbeat already carries a legacy key and a legacy
    upper-bounded market. Neither may touch the probe's verdict."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert out["verdict"] == "MEASURED"
