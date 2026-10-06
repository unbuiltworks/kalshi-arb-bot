"""LIVE-L5-MICRO-1 safety and execution-path tests. Probe stays DISARMED."""
from __future__ import annotations

import os
from decimal import Decimal as D

import pytest

from analysis.l5.live_micro import ledger as L
from analysis.l5.live_micro.spec import (LIFECYCLE, MARKOUT_HORIZONS, SPEC,
                                         MICRO_FINGERPRINT, assert_frozen,
                                         fingerprint)

FP = "0522b0dac3fc5dec5ec0a01c6dbbb6369fde3f7062cede16f965fdfc459d1b31"
PROBE_1_FP = "69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118"


# ---------------- frozen spec ----------------
def test_fingerprint_matches_literal_and_guard_is_live():
    assert MICRO_FINGERPRINT == FP and fingerprint() == FP
    assert_frozen()
    import copy
    s = copy.deepcopy(SPEC)
    s["per_market_risk"]["cumulative_first_leg_notional_cap_usd"] = "5.00"
    assert fingerprint(s) != FP


@pytest.mark.parametrize("path,expected", [
    (("armed_by_default",), False),
    (("is_new_strategy",), False),
    (("market", "markets_concurrent"), 1),
    (("orders", "max_simultaneous_resting_orders"), 1),
    (("orders", "maker_only"), True),
    (("orders", "taker_permitted"), False),
    (("orders", "partial_fills_permitted"), True),
    (("per_market_risk", "MAX_DOLLARS_LOSABLE_IN_ONE_MARKET_USD"), "0.05"),
    (("directional_bound", "max_residual_dollars_usd"), "0.02"),
    (("directional_bound", "no_martingale"), True),
    (("daily_limit", "daily_realized_loss_stop_usd"), "-1.00"),
    (("withdrawal", "tte_shutdown_s"), 120),
    (("placement", "offset_from_touch_ticks"), 0),
    (("evidence", "touch_equals_fill"), False),
    (("evidence", "estimated_rewards_counted"), False),
    (("arming", "claude_may_not_set_these"), True),
    (("constraints", "orders_submitted_by_this_session"), 0),
    (("constraints", "wallet_signing_by_this_session"), 0),
])
def test_reported_envelope_values(path, expected):
    node = SPEC
    for k in path:
        node = node[k]
    assert node == expected


def test_probe_1_fingerprint_is_preserved_and_unrunnable():
    """The authorized PROBE_1 record must survive verbatim and must not run."""
    from analysis.l5.live_micro import spec_probe1_frozen as p1
    assert p1.PROBE_1_FINGERPRINT == PROBE_1_FP
    assert p1.fingerprint(p1.SPEC) == PROBE_1_FP, "archive must reproduce it"
    assert p1.ARCHIVE_LABEL == "PROBE_1_INFRASTRUCTURE_SKIP"
    with pytest.raises(SystemExit):
        p1.assert_frozen()


def test_v2_changed_instrumentation_only_not_risk():
    """Every risk value must be byte-identical to the authorized PROBE_1."""
    from analysis.l5.live_micro import spec_probe1_frozen as p1
    for section, keys in (
            ("per_market_risk", ("cumulative_first_leg_notional_cap_usd",
                                 "max_gross_pair_capital_usd",
                                 "MAX_DOLLARS_LOSABLE_IN_ONE_MARKET_USD")),
            ("directional_bound", ("max_residual_shares",
                                   "max_residual_dollars_usd",
                                   "no_martingale",
                                   "no_averaging_up_to_recover")),
            ("daily_limit", ("daily_realized_loss_stop_usd",)),
            ("orders", ("max_simultaneous_resting_orders", "maker_only",
                        "taker_permitted", "partial_fills_permitted")),
            ("withdrawal", ("tte_shutdown_s",)),
            ("market", ("markets_concurrent", "series")),
            ("deadman", ("mechanism", "on_every_exit_path",
                         "no_auto_restart", "no_auto_scaling")),
    ):
        for k in keys:
            assert SPEC[section][k] == p1.SPEC[section][k], (section, k)
    assert SPEC["placement"]["offset_from_touch_ticks"] == 0
    assert SPEC["armed_by_default"] is False


def test_tick_is_the_venue_grid_not_a_constant():
    assert SPEC["quantity"]["tick_universal_constant"] is None
    assert "orderPriceMinTickSize" in SPEC["quantity"]["tick_source"]
    assert SPEC["quantity"]["fail_closed_if_tick_absent"] is True
    assert SPEC["quantity"]["fail_closed_if_price_off_grid"]


def test_stale_gateway_bbo_is_forbidden_and_bound_is_reused():
    pl = SPEC["placement"]
    assert pl["bbo_source"] == "l5_pmus_auth_book.AuthBook"
    assert "gateway" in pl["bbo_source_forbidden"]
    assert pl["staleness_bound_s"] == 2.0
    assert "REUSED, not invented" in pl["staleness_bound_provenance"]
    assert "BOOK_MAX_AGE_S" in pl["staleness_bound_provenance"]
    assert pl["fail_closed_if_stale"] is True


def test_post_only_never_implies_maker():
    ev = SPEC["evidence"]
    assert ev["post_only_never_implies_maker"] is True
    assert "passiveExecution" in ev["liquidity_role_source"]
    assert "`aggressor`" in ev["liquidity_role_field_forbidden"]
    assert ev["submitted_post_only"].startswith("RECORDED SEPARATELY")


def test_modeled_fee_is_never_reported_as_actual():
    ev = SPEC["evidence"]
    assert ev["modeled_fee_may_be_reported_as_actual"] is False
    assert "commissionNotionalCollected" in ev["fee_source"]
    assert ev["modeled_fee_label_if_used"] == "MODELLED_NOT_ACTUAL"


def test_arming_audit_reads_no_secret_and_redacts():
    from analysis.l5.live_micro.arming_audit import classify, report
    c = classify(['{"outcome":"REFUSED","refusal":"NOT_ARMED: ..."}'])
    assert c["verdict"] == "DISARMED" and c["secrets_read"] is False
    leak = classify(['{"armed": false, "k":"MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ"}'])
    assert leak["evidence_quote"] == "REDACTED_SECRET_SHAPED"
    assert classify([])["verdict"] == "UNKNOWN"
    r = report({"a": [], "b": ['{"armed": true}']})
    assert r["RAILWAY_LIST_VARIABLES_USED"] is False
    assert r["n_unknown"] == 1 and r["n_armed"] == 1


def test_s3_is_not_touched_by_this_probe():
    from analysis.l5.s3.spec import S3_FINGERPRINT, assert_frozen as s3f
    s3f()
    assert SPEC["constraints"]["s3_fingerprint"] == S3_FINGERPRINT


# ---------------- arming ----------------
def test_probe_is_disarmed_in_this_session():
    assert L.micro_armed() is False
    g = L.assert_disarmed_or_owner_armed("btc-updown-15m-x")
    assert g["may_submit"] is False and "DISARMED" in g["refusal"]


def test_arming_needs_all_four_owner_keys(monkeypatch):
    monkeypatch.setenv("PMUS_MICRO_LIVE", "YES")
    assert L.micro_armed() is False                    # no session id
    monkeypatch.setenv("PMUS_MICRO_SESSION_ID", "sid-1")
    assert L.micro_armed() is False                    # arm key missing
    monkeypatch.setenv("PMUS_MICRO_ARM", "WRONG")
    assert L.micro_armed() is False                    # mismatched
    monkeypatch.setenv("PMUS_MICRO_ARM", "sid-1")
    assert L.micro_armed() is True                     # three keys
    g = L.assert_disarmed_or_owner_armed("slug-A")
    assert g["may_submit"] is False                    # gate not asserted
    monkeypatch.setenv("PMUS_MICRO_GATE_EVIDENCE", "slug-A")
    assert L.assert_disarmed_or_owner_armed("slug-A")["may_submit"] is True
    # the gate is slug-scoped: a different market is still refused
    assert L.assert_disarmed_or_owner_armed("slug-B")["may_submit"] is False


def test_q1_arming_cannot_arm_micro(monkeypatch):
    monkeypatch.setenv("PMUS_LIVE", "YES")
    monkeypatch.setenv("PMUS_ARM", "sid-1")
    monkeypatch.setenv("PMUS_SESSION_ID", "sid-1")
    assert L.micro_armed() is False


# ---------------- lifecycle ----------------
def order(qty="2.0", price="0.45"):
    return L.OrderRecord("c1", "slug", "up", D(price), D(qty))


def test_lifecycle_states_are_the_reported_seven():
    assert LIFECYCLE == ("SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED",
                         "FILLED", "CANCELLED", "SETTLED", "CASH_CONFIRMED")


def test_submitted_is_not_a_fill():
    o = order()
    assert o.state == "SUBMITTED"
    assert o.filled_qty == 0 and o.is_realized() is False


def test_lifecycle_cannot_run_backwards():
    o = order()
    o.advance("ACKNOWLEDGED", at=1.0)
    with pytest.raises(L.StateError):
        o.advance("SUBMITTED", at=2.0)


def test_partial_fill_is_never_promoted_to_full():
    o = order(qty="2.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("0.5"), D("0.45"), at=2.0, maker_taker="maker")
    assert o.state == "PARTIALLY_FILLED"
    with pytest.raises(L.StateError):
        o.advance("FILLED", at=3.0)


def test_repeated_partials_accumulate_then_fill():
    o = order(qty="2.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("1.0"), D("0.45"), at=2.0, maker_taker="maker")
    o.record_fill(D("0.5"), D("0.45"), at=3.0, maker_taker="maker")
    assert o.state == "PARTIALLY_FILLED" and o.filled_qty == D("1.5")
    o.record_fill(D("0.5"), D("0.45"), at=4.0, maker_taker="maker")
    assert o.state == "FILLED"


def test_overfill_is_a_cap_breach_not_truncated():
    o = order(qty="1.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    with pytest.raises(L.StateError) as e:
        o.record_fill(D("1.5"), D("0.45"), at=2.0, maker_taker="maker")
    assert "CAP" in str(e.value)


def test_maker_taker_must_come_from_the_venue():
    o = order()
    o.advance("ACKNOWLEDGED", at=1.0)
    with pytest.raises(L.StateError):
        o.record_fill(D("0.5"), D("0.45"), at=2.0, maker_taker="assumed")


def test_cash_confirmed_requires_settlement_first():
    o = order(qty="1.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("1.0"), D("0.45"), at=2.0, maker_taker="maker")
    with pytest.raises(L.StateError) as e:
        o.advance("CASH_CONFIRMED", at=3.0)
    assert "REALIZED" in str(e.value)


def test_only_filled_settled_cash_confirmed_is_realized():
    o = order(qty="1.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("1.0"), D("0.45"), at=2.0, maker_taker="maker")
    assert o.is_realized() is False
    o.advance("SETTLED", at=4.0)
    assert o.is_realized() is False
    o.advance("CASH_CONFIRMED", at=5.0)
    assert o.is_realized() is True


def test_cancellation_is_reachable_from_pre_settlement_states():
    for st in ("SUBMITTED", "ACKNOWLEDGED"):
        o = order()
        if st == "ACKNOWLEDGED":
            o.advance("ACKNOWLEDGED", at=1.0)
        o.advance("CANCELLED", at=2.0)
        assert o.state == "CANCELLED"


# ---------------- rewards ----------------
def test_reward_needs_a_venue_source():
    o = order()
    with pytest.raises(L.StateError):
        o.credit_reward(D("1.0"), source="")
    o.credit_reward(D("1.0"), source="/v1/incentives/earnings")
    assert o.credited_rewards_usd == D("1.0")


def test_estimated_rewards_are_not_counted_by_spec():
    assert SPEC["evidence"]["estimated_rewards_counted"] is False
    assert SPEC["evidence"]["rewards_counted"].startswith("ONLY when actually")


# ---------------- markouts ----------------
def test_markouts_cover_every_horizon_plus_settlement():
    o = order(price="0.45")
    mk = L.markouts(o, {1: D("0.46"), 5: D("0.44"), 10: None,
                        30: D("0.40"), 60: D("0.50")},
                    settlement_mark=D("1.00"))
    for k in MARKOUT_HORIZONS:
        assert "t_plus_%d" % k in mk
    assert mk["t_plus_1"]["markout"] == "0.01"
    assert mk["t_plus_30"]["markout"] == "-0.05"
    assert mk["t_plus_10"]["markout"] is None
    assert mk["t_plus_10"]["reason"] == "NO_OBSERVATION"
    assert mk["settlement"]["markout"] == "0.55"


def test_markout_is_labelled_not_realized():
    mk = L.markouts(order(), {}, settlement_mark=None)
    assert mk["LABEL"] == "MARK_TO_MARKET_NOT_REALIZED"
    assert mk["settlement"]["reason"] == "NOT_SETTLED"


def test_missing_horizon_is_never_carried_forward():
    mk = L.markouts(order(price="0.45"), {1: D("0.46"), 5: None, 10: None,
                                          30: None, 60: None})
    assert mk["t_plus_1"]["markout"] == "0.01"
    for k in (5, 10, 30, 60):
        assert mk["t_plus_%d" % k]["markout"] is None


# ---------------- accounting ----------------
def test_five_lines_are_kept_separate_and_net_is_withheld():
    up = L.OrderRecord("u", "s", "up", D("0.45"), D("1.0"))
    up.advance("ACKNOWLEDGED", at=1.0)
    up.record_fill(D("1.0"), D("0.45"), at=2.0, maker_taker="maker",
                   fee_usd=D("0.001"))
    dn = L.OrderRecord("d", "s", "down", D("0.50"), D("1.0"))
    dn.advance("ACKNOWLEDGED", at=1.0)
    dn.record_fill(D("1.0"), D("0.50"), at=3.0, maker_taker="maker")
    acct = L.account([up, dn])
    for k in ("matched_pair_economics_usd", "directional_residual_pnl_usd",
              "fees_usd", "credited_rewards_usd", "final_net_usd"):
        assert k in acct
    assert acct["matched_shares"] == "1.0"
    assert acct["pair_cost"] == "0.95"
    assert acct["matched_pair_economics_usd"] == "0.050"
    assert acct["directional_residual_pnl_usd"] == "UNRESOLVED_UNTIL_SETTLEMENT"
    assert acct["final_net_usd"] == "UNRESOLVED_UNTIL_CASH_CONFIRMED"
    assert acct["n_orders_REALIZED"] == 0


def test_residual_drag_ratio_is_measured_against_the_L5_reference():
    up = L.OrderRecord("u", "s", "up", D("0.45"), D("2.0"))
    up.advance("ACKNOWLEDGED", at=1.0)
    up.record_fill(D("2.0"), D("0.45"), at=2.0, maker_taker="maker")
    dn = L.OrderRecord("d", "s", "down", D("0.50"), D("1.0"))
    dn.advance("ACKNOWLEDGED", at=1.0)
    dn.record_fill(D("1.0"), D("0.50"), at=3.0, maker_taker="maker")
    acct = L.account([up, dn])
    assert acct["directional_residual_shares"] == "1.0"
    assert acct["RESIDUAL_DRAG_RATIO"] is not None
    assert acct["L5_REFERENCE_DRAG_RATIO"] == 1.1217


def test_a_fill_is_not_profit_and_mtm_is_not_realized():
    o = order(qty="1.0")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("1.0"), D("0.45"), at=2.0, maker_taker="maker")
    acct = L.account([o])
    assert acct["n_orders_with_fills"] == 1
    assert acct["n_orders_REALIZED"] == 0
    assert "not profit" in acct["LANGUAGE"]
