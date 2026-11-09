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
    """A recorder report that a LIVE gate may pass.

    THE `readback` BLOCK IS PART OF THAT NOW, and this fixture was updated
    rather than the gate loosened. `events_appended` is a per-process
    counter zeroed by every restart, so it cannot establish that order
    events survive one; `recorder_events_survive_a_restart` reads
    `events_replayed_from_disk`, which can only be non-zero if a previous
    process's write came back. A report without the block is UNKNOWN and
    still blocks a pass -- see
    `test_a_report_without_readback_no_longer_passes_a_live_gate` below.
    """
    c = {"orders": 3, "events_appended": 11, "errors": {},
         "events_replayed_from_disk": 11,
         "readback": {"readable": True, "error": None,
                      "events_readable": 11, "orders_readable": 3,
                      "orders_with_full_capture": 3,
                      "unreadable_lines": 0,
                      "events_appended_this_process": 0,
                      "events_replayed_from_disk": 11}}
    c.update(over)
    return c


# ============================================================== preflight
def test_the_live_gate_values_pass_the_preflight():
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=[MARKET], calibration=calib())
    assert out["ok"] is True, (out["failed"], out["unknown"])


def test_a_report_without_readback_no_longer_passes_a_live_gate():
    """The counter alone is no longer enough, and its absence is UNKNOWN
    rather than either a pass or a failure: a report from before the field
    existed cannot answer the question."""
    out = PF.preflight(heartbeat=live_hb(), plan=good_plan(),
                       markets=[MARKET],
                       calibration={"orders": 3, "events_appended": 11,
                                    "errors": {}})
    assert out["ok"] is False
    assert "recorder_ledger_reads_back" in out["unknown"]
    assert "recorder_events_survive_a_restart" in out["unknown"]
    assert "recorder_ledger_reads_back" not in out["failed"]


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


def basis_evidence():
    """THE POSITIVE EVIDENCE every MEASURED verdict now requires.

    `leg_cost > 0` alone is not evidence: an adopted leg is priced at
    qty * $1.00 and would satisfy it. `fill_count` says OUR fills built
    the leg and an empty `gross_quarantine` says adoption never replaced
    our book. Both are read from the store's own state rather than from
    the heartbeat, so the audit does not depend on a heartbeat field
    having caught up.
    """
    return {"fill_count": {"%s|UP" % MARKET: 1, "%s|DOWN" % MARKET: 1},
            "gross_quarantine": {}}


def good_latency():
    return {"book_age_at_decision_s": {"n": 7, "median": 0.4, "p95": 0.9},
            "detection_to_first_decision_s": 3.2}


def test_a_clean_probe_is_MEASURED():
    """`verdict` and `ok` are DIFFERENT questions, deliberately.

    `verdict` answers "is the cost basis sound", which is what decides
    whether a settled figure may be quoted at all. `ok` additionally
    requires every other check -- fees, restart evidence, reconciliation.
    So a probe can be MEASURED while `ok` is false: the P&L is real but
    some supporting evidence is still outstanding. Supplying the whole
    evidence set here is what makes `ok` true.
    """
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"},
                          {"venue_order_id": "V-2"}],
                   latency=good_latency(), live_orders={}, restarts=0,
                   fees={"fee_usd": "0.00", "rebate_usd": "0.00727",
                         "source": "venue", "state": "CREDITED"},
                   **basis_evidence())
    assert out["verdict"] == "MEASURED", (out["failed"], out["unknown"])
    assert out["ok"] is True, (out["failed"], out["unknown"])


def test_a_sound_basis_with_outstanding_evidence_is_MEASURED_but_not_ok():
    """The distinction above, as its own case: no fee record yet."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency(), **basis_evidence())
    assert out["verdict"] == "MEASURED"
    assert out["ok"] is False
    assert "actual_fees_and_rebates_present" in out["unknown"]


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
                   fills=[{"venue_order_id": "V-1"}], latency=lat,
                   **basis_evidence())
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
                   latency=good_latency(), **basis_evidence())
    assert out["verdict"] == "MEASURED"


# ===================== durable restart and order reconciliation (P3 list)

def test_an_UNCERTAIN_live_order_refuses_the_audit():
    """`nothing_held` reading OUR ledger instead of the venue is what cost
    us 1445z. An order whose state could not be established is a refusal,
    not a footnote."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   live_orders={"l5f-a": {"state": "ORDER_STATE_UNCERTAIN",
                                          "venue_order_id": "V-9"}})
    assert "no_order_is_in_an_UNCERTAIN_state" in out["failed"]
    assert out["ok"] is False


def test_a_live_order_with_no_venue_id_refuses_because_it_cannot_be_cancelled():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   live_orders={"l5f-a": {"state": "LIVE",
                                          "venue_order_id": ""}})
    assert "every_live_order_has_a_venue_id_to_cancel_by" in out["failed"]


def test_clean_live_orders_pass_reconciliation():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   live_orders={"l5f-a": {"state": "LIVE",
                                          "venue_order_id": "V-9"}},
                   restarts=0, **basis_evidence())
    assert "no_order_is_in_an_UNCERTAIN_state" not in out["failed"]
    assert "every_live_order_has_a_venue_id_to_cancel_by" not in out["failed"]
    assert out["verdict"] == "MEASURED"


def test_duplicate_venue_order_ids_refuse():
    """Two fills under one id is either a double-booked poll or two orders
    we cannot tell apart. Both break reconciliation."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"},
                          {"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert "venue_order_ids_are_distinct" in out["failed"]


def test_a_straddled_restart_is_REPORTED_not_failed():
    """A record spanning a restart is still evidence; it simply may not be
    read as one continuous observation. The caller decides."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   restarts=2)
    row = next(r for r in out["checks"]
               if r["check"] == "restart_boundaries_reported")
    assert row["ok"] is True
    assert row["orders_straddling_a_restart"] == 2
    assert "not a continuous observation" in row["note"]


def test_an_unreported_restart_count_is_UNKNOWN():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency())
    assert "restart_boundaries_reported" in out["unknown"]


# ============================= actual fees and rebates (P3 list)

def test_actual_fees_and_rebates_must_be_present_and_sourced():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   fees={"fee_usd": "0.00", "rebate_usd": "0.00727",
                         "source": "venue", "state": "CREDITED"})
    assert "actual_fees_and_rebates_present" not in out["failed"]
    assert "rebate_is_credited_not_accrued" not in out["failed"]


@pytest.mark.parametrize("state", ["PENDING", "SKIPPED", "ACCRUED"])
def test_an_accrued_rebate_is_refused_as_income(state):
    """$1.48 is currently accrued and not paid. An accrued rebate counted
    as income is how a receivable becomes a performance claim."""
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   fees={"fee_usd": "0", "rebate_usd": "1.48",
                         "source": "venue", "state": state})
    assert "rebate_is_credited_not_accrued" in out["failed"]


def test_a_fee_record_with_no_source_is_refused():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   fees={"fee_usd": "0", "rebate_usd": "0", "source": ""})
    assert "actual_fees_and_rebates_present" in out["failed"]


def test_absent_fees_are_UNKNOWN_not_zero():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency())
    assert "actual_fees_and_rebates_present" in out["unknown"]


def test_the_estimate_is_reported_beside_the_actual_and_never_summed():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
                   fees={"fee_usd": "0", "rebate_usd": "0.00727",
                         "source": "venue", "state": "CREDITED",
                         "estimate": {"maker_rebate_usd": "0.00584375"}})
    row = next(r for r in out["checks"]
               if r["check"] == "estimated_and_actual_rebate_not_summed")
    assert row["estimate"] == "0.00584375"
    assert row["actual"] == "0.00727"
    assert "never added" in row["note"]


# ============ positive evidence of cost basis, not absence of a marker
#
# THE FAIL-OPEN THIS CLOSES, demonstrated before it was fixed:
# `adopt_venue_position` prices an adopted leg at qty * $1.00, so the
# placeholder had `leg_cost > 0` and PASSED `every_filled_leg_has_a_cost
# _basis`. It was caught only by the unknown-basis-keys row, which reads a
# heartbeat field that may not have caught up -- so an adopted leg with an
# empty list returned verdict MEASURED. That is exactly "interpreting an
# empty heartbeat as successful accounting".

def adopted_args(**over):
    a = dict(markets=[MARKET],
             heartbeat={"realized": {"unknown_basis_keys": []},
                        "accounting": {"unknown_detail": {
                            "post_baseline_settlements_not_attested": []}}},
             leg_cost={"%s|UP" % MARKET: "1.00"},
             positions={"%s|UP" % MARKET: "1"},
             fills=[{"venue_order_id": "V-1"}],
             latency=good_latency(),
             fill_count={"%s|UP" % MARKET: 0},
             gross_quarantine={MARKET: {
                 "leg_cost": {"%s|UP" % MARKET: "0.38"},
                 "positions": {"%s|UP" % MARKET: "1"},
                 "why": "venue net contradicts our gross"}})
    a.update(over)
    return a


def test_an_adopted_placeholder_is_UNINTERPRETABLE_even_on_an_empty_list():
    out = PF.audit(**adopted_args())
    assert out["verdict"] == "UNINTERPRETABLE", out["checks"]
    assert "every_held_leg_was_built_by_our_own_fills" in out["failed"]
    assert "no_leg_is_priced_at_the_one_dollar_bound" in out["failed"]
    assert "adoption_did_not_replace_our_book" in out["failed"]


def test_a_leg_with_no_fills_of_ours_is_refused_on_its_own():
    """fill_count 0 is quantity that arrived from the venue's net. This is
    the PRIMARY evidence, so it refuses even with everything else clean."""
    out = PF.audit(**adopted_args(leg_cost={"%s|UP" % MARKET: "0.60"},
                                  gross_quarantine={}))
    assert "every_held_leg_was_built_by_our_own_fills" in out["failed"]
    assert out["verdict"] == "UNINTERPRETABLE"


def test_the_dollar_a_share_signature_is_refused_on_its_own():
    out = PF.audit(**adopted_args(fill_count={"%s|UP" % MARKET: 1},
                                  gross_quarantine={}))
    assert "no_leg_is_priced_at_the_one_dollar_bound" in out["failed"]
    assert out["verdict"] == "UNINTERPRETABLE"


def test_adoption_having_fired_is_refused_on_its_own():
    out = PF.audit(**adopted_args(leg_cost={"%s|UP" % MARKET: "0.60"},
                                  fill_count={"%s|UP" % MARKET: 1}))
    assert "adoption_did_not_replace_our_book" in out["failed"]
    assert out["verdict"] == "UNINTERPRETABLE"


def test_the_quarantined_basis_is_reported_as_RECOVERABLE_not_as_truth():
    """0230z is why: our gross was the double-booked one and the VENUE was
    right, so the preserved basis is an input to reconciliation and never
    a figure to quote."""
    out = PF.audit(**adopted_args())
    row = next(r for r in out["checks"]
               if r["check"] == "adoption_did_not_replace_our_book")
    assert row["recovery"][MARKET]["preserved_leg_cost"] == {
        "%s|UP" % MARKET: "0.38"}
    assert "not automatically the truth" in row["note"]
    assert "do not quote either side" in row["note"]


def test_an_absent_fill_count_is_UNKNOWN_and_blocks_MEASURED():
    out = PF.audit(markets=[MARKET], heartbeat=live_hb(),
                   leg_cost=filled_legs(), positions=filled_positions(),
                   fills=[{"venue_order_id": "V-1"}],
                   latency=good_latency())
    assert "every_held_leg_was_built_by_our_own_fills" in out["unknown"]
    assert out["verdict"] == "UNINTERPRETABLE"
    assert out["ok"] is False


def test_a_genuine_depth_2_pair_with_full_evidence_is_MEASURED_and_ok():
    out = PF.audit(
        markets=[MARKET], heartbeat=live_hb(),
        leg_cost={"%s|UP" % MARKET: "0.60", "%s|DOWN" % MARKET: "0.35"},
        positions={"%s|UP" % MARKET: "1", "%s|DOWN" % MARKET: "1"},
        fills=[{"venue_order_id": "V-1"}, {"venue_order_id": "V-2"}],
        latency=good_latency(),
        fill_count={"%s|UP" % MARKET: 1, "%s|DOWN" % MARKET: 1},
        gross_quarantine={}, live_orders={}, restarts=0,
        fees={"fee_usd": "0.00", "rebate_usd": "0.00727",
              "source": "venue", "state": "CREDITED"})
    assert out["verdict"] == "MEASURED", (out["failed"], out["unknown"])
    assert out["ok"] is True
    # and the pair books at exactly the modelled depth-2 cost
    assert (D("0.60") + D("0.35")) == D("0.95")


def test_a_partial_fill_of_one_contract_is_still_positively_evidenced():
    """A leg built by one fill of 1 at 0.60 is evidenced; the check is on
    fills existing, not on the leg being complete."""
    out = PF.audit(
        markets=[MARKET], heartbeat=live_hb(),
        leg_cost={"%s|UP" % MARKET: "0.60"},
        positions={"%s|UP" % MARKET: "1"},
        fills=[{"venue_order_id": "V-1"}], latency=good_latency(),
        fill_count={"%s|UP" % MARKET: 1}, gross_quarantine={},
        live_orders={}, restarts=0,
        fees={"fee_usd": "0", "rebate_usd": "0", "source": "venue",
              "state": "CREDITED"})
    assert out["verdict"] == "MEASURED", (out["failed"], out["unknown"])
