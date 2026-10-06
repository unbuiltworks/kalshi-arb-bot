"""LIVE-L5-EXEC-1 safety and execution-path tests. Stays DISARMED."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from analysis.l5.live_exec import ledger as L
from analysis.l5.live_exec.spec import (EXEC1_FINGERPRINT,
                                        MAX_POSSIBLE_LOSS_USD, SPEC,
                                        VENUE_MAX_PRICE, VENUE_MIN_QTY,
                                        assert_frozen, fingerprint)
from analysis.l5.live_micro.ledger import StateError

FP = "b1dbe7f1e628e6f9fc8e6459a2a5b95098ada2f16ae872f10081d5dc9bb82e06"


# ---------------- frozen spec + loss bound ----------------
def test_fingerprint_and_live_guard():
    assert EXEC1_FINGERPRINT == FP and fingerprint() == FP
    assert_frozen()
    import copy
    s = copy.deepcopy(SPEC)
    s["size"]["MAX_POSSIBLE_LOSS_USD"] = "9.99"
    assert fingerprint(s) != FP


def test_max_possible_loss_is_closed_arithmetic():
    assert VENUE_MIN_QTY == D("0.01")
    assert VENUE_MAX_PRICE == D("0.99")
    assert MAX_POSSIBLE_LOSS_USD == D("0.0099")
    assert SPEC["size"]["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert MAX_POSSIBLE_LOSS_USD < D("0.01"), "a total loss is under a cent"


def test_all_prior_fingerprints_preserved_unchanged():
    """S3, PROBE_1 and the reserved micro candidate must be untouched."""
    from analysis.l5.s3.spec import S3_FINGERPRINT, assert_frozen as s3f
    from analysis.l5.live_micro.spec import (MICRO_FINGERPRINT,
                                             assert_frozen as mf)
    from analysis.l5.live_micro import spec_probe1_frozen as p1
    s3f(); mf()
    assert S3_FINGERPRINT == \
        "8b20a25936d3b3a406403f6424eff30d3360d22f188b28a10644f356b65287df"
    assert MICRO_FINGERPRINT == \
        "0522b0dac3fc5dec5ec0a01c6dbbb6369fde3f7062cede16f965fdfc459d1b31"
    assert p1.fingerprint(p1.SPEC) == \
        "69904fd4e64bcc62093ce70c1b63b5d825548f5d495eda63744fc91169919118"
    d = SPEC["does_not_contaminate"]
    assert d["s3_fingerprint"] == S3_FINGERPRINT
    assert d["reserved_micro_fingerprint"] == MICRO_FINGERPRINT
    assert d["all_preserved_unchanged"] is True


def test_it_does_not_claim_profitability():
    assert SPEC["is_profitable_strategy_claim"] is False
    assert SPEC["is_evidence_pair_cost_099_is_attractive"] is False
    assert SPEC["experiment_class"] == "EXECUTION_MEASUREMENT"
    assert SPEC["accounting"]["does_not_test_full_L5_economics"] is True


def test_no_pair_cost_reservation_and_the_reason_is_recorded():
    b = SPEC["behaviour"]
    assert b["pair_cost_reservation"] is None
    assert "not an observed L5 rule" in b["pair_cost_reservation_rationale"]
    assert "REFUTED" in b["pair_cost_reservation_rationale"]


@pytest.mark.parametrize("path,expected", [
    (("behaviour", "offset_from_touch_ticks"), 0),
    (("behaviour", "no_complement_chasing"), True),
    (("behaviour", "no_martingale"), True),
    (("behaviour", "no_averaging"), True),
    (("behaviour", "no_second_order"), True),
    (("behaviour", "no_automatic_repeat"), True),
    (("orders", "markets_concurrent"), 1),
    (("orders", "max_simultaneous_resting_orders"), 1),
    (("orders", "max_orders_this_experiment"), 1),
    (("orders", "maker_intended_post_only"), True),
    (("orders", "taker_permitted"), False),
    (("orders", "partial_fills_permitted"), True),
    (("book", "bbo_source"), "l5_pmus_auth_book.AuthBook"),
    (("book", "staleness_bound_s"), 2.0),
    (("book", "tick_universal_constant"), None),
    (("withdrawal", "tte_shutdown_s"), 120),
    (("evidence", "touch_equals_fill"), False),
    (("armed_by_default",), False),
    (("constraints", "orders_submitted_by_this_session"), 0),
    (("constraints", "wallet_signing_by_this_session"), 0),
])
def test_reported_envelope(path, expected):
    node = SPEC
    for k in path:
        node = node[k]
    assert node == expected


# ---------------- arming isolation ----------------
def test_disarmed_in_this_session():
    assert L.exec1_armed() is False
    assert L.may_submit("btc-updown-15m-x")["may_submit"] is False


def test_needs_all_four_own_keys_and_is_slug_scoped(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    assert L.exec1_armed() is False
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "s1")
    monkeypatch.setenv("PMUS_EXEC1_ARM", "nope")
    assert L.exec1_armed() is False
    monkeypatch.setenv("PMUS_EXEC1_ARM", "s1")
    assert L.exec1_armed() is True
    assert L.may_submit("slug-A")["may_submit"] is False
    monkeypatch.setenv("PMUS_EXEC1_GATE_EVIDENCE", "slug-A")
    assert L.may_submit("slug-A")["may_submit"] is True
    assert L.may_submit("slug-B")["may_submit"] is False


def test_other_executors_arming_cannot_arm_exec1(monkeypatch):
    for k in ("PMUS_Q1", "PMUS_MICRO", "PMUS_CANARY", "PMUS_CASE1"):
        monkeypatch.setenv(k + "_LIVE", "YES")
        monkeypatch.setenv(k + "_SESSION_ID", "s1")
        monkeypatch.setenv(k + "_ARM", "s1")
    assert L.exec1_armed() is False


def test_arming_exec1_does_not_arm_micro_or_q1(monkeypatch):
    monkeypatch.setenv("PMUS_EXEC1_LIVE", "YES")
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "s1")
    monkeypatch.setenv("PMUS_EXEC1_ARM", "s1")
    from analysis.l5.live_micro.ledger import micro_armed
    assert L.exec1_armed() is True
    assert micro_armed() is False


# ---------------- venue grid, fail closed ----------------
@pytest.mark.parametrize("minq,tick,price,ok,reason", [
    ("0.01", "0.01", "0.53", True, None),
    (None, "0.01", "0.53", False, "MIN_QTY_ABSENT"),
    ("1", "0.01", "0.53", False, "MIN_QTY_NOT_LESS_THAN_1"),
    ("0.01", None, "0.53", False, "TICK_ABSENT"),
    ("0.01", "0", "0.53", False, "TICK_IMPLAUSIBLE"),
    ("0.01", "0.01", "0.535", False, "PRICE_OFF_GRID"),
    ("0.01", "0.005", "0.535", True, None),
])
def test_size_validation_fails_closed(minq, tick, price, ok, reason):
    v = L.validate_size(minq, tick, price)
    assert v["pass"] is ok
    if reason:
        assert any(r.startswith(reason) for r in v["reasons"]), v["reasons"]


def test_tick_0005_is_still_accepted_so_001_is_not_a_new_constant():
    """The fix accepts the venue GRID; it must not hardcode 0.01 either."""
    assert L.validate_size("0.01", "0.005", "0.535")["pass"] is True
    assert L.validate_size("0.01", "0.01", "0.53")["pass"] is True


# ---------------- placement capture ----------------
def test_placement_capture_has_every_required_field():
    cap = L.capture_placement(
        side="up", price="0.53", bbo_bid="0.53", bbo_ask="0.54",
        depth_ahead="118.0", quantity="0.01", tte_s=640,
        book_timestamp="2026-10-06T13:48:00Z", book_age_s=0.0,
        order_timestamp="2026-10-06T13:48:00.2Z")
    for f in SPEC["capture_at_placement"]:
        key = {"bbo_bid": "bbo_bid", "bbo_ask": "bbo_ask"}.get(f, f)
        assert key in cap, f
    assert cap["spread"] == "0.01"
    assert cap["book_fresh_within_bound"] is True
    assert cap["depth_ahead_is_anonymous"] is True


def test_stale_book_is_flagged_not_silently_accepted():
    cap = L.capture_placement(
        side="up", price="0.53", bbo_bid="0.53", bbo_ask="0.54",
        depth_ahead=None, quantity="0.01", tte_s=10,
        book_timestamp="t", book_age_s=5.0, order_timestamp="t")
    assert cap["book_fresh_within_bound"] is False


def test_post_only_never_implies_maker_at_placement():
    cap = L.capture_placement(
        side="up", price="0.53", bbo_bid="0.53", bbo_ask="0.54",
        depth_ahead="1", quantity="0.01", tte_s=1, book_timestamp="t",
        book_age_s=0.0, order_timestamp="t")
    assert cap["submitted_post_only"] is True
    assert cap["submitted_post_only_implies_maker"] is False


# ---------------- one order only ----------------
def test_exactly_one_order_is_permitted():
    g = L.OneOrderOnly()
    o = g.request("slug", "up", "0.53", "0.01")
    assert o.state == "SUBMITTED"
    with pytest.raises(StateError) as e:
        g.request("slug", "down", "0.46", "0.01")
    assert "ONE order" in str(e.value)
    assert g.refusals == ["SECOND_ORDER_REFUSED"]


def test_a_second_order_to_repair_a_loss_is_still_refused():
    g = L.OneOrderOnly()
    o = g.request("slug", "up", "0.53", "0.01")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("0.01"), D("0.53"), at=2.0, maker_taker="maker")
    o.advance("SETTLED", at=3.0)          # suppose it settled worthless
    with pytest.raises(StateError) as e:
        g.request("slug", "up", "0.53", "0.01")
    assert "repairing a loss is not an exception" in str(e.value)


def test_quantity_must_be_the_venue_minimum():
    g = L.OneOrderOnly()
    with pytest.raises(StateError):
        g.request("slug", "up", "0.53", "1.00")


# ---------------- single-leg accounting ----------------
def test_matched_is_NA_not_zero():
    a = L.exec_account(None)
    assert a["matched_pair_economics"] == "N/A_SINGLE_LEG_EXPERIMENT"
    assert a["matched_is_not_zero_it_is_not_applicable"] is True
    assert a["DOES_NOT_TEST_FULL_L5_ECONOMICS"] is True


def test_fill_is_not_profit_and_net_is_withheld():
    g = L.OneOrderOnly()
    o = g.request("slug", "up", "0.53", "0.01")
    o.advance("ACKNOWLEDGED", at=1.0)
    o.record_fill(D("0.01"), D("0.53"), at=2.0, maker_taker="maker",
                  fee_usd=D("0.0001"))
    a = L.exec_account(o)
    assert a["directional_residual_pnl_usd"] == "UNRESOLVED_UNTIL_SETTLEMENT"
    assert a["final_net_usd"] == "UNRESOLVED_UNTIL_CASH_CONFIRMED"
    assert a["venue_confirmed_liquidity_role"] == "maker"
    assert a["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert a["orders_submitted_by_this_session"] == 0


def test_unresolved_role_is_reported_not_guessed():
    g = L.OneOrderOnly()
    o = g.request("slug", "up", "0.53", "0.01")
    a = L.exec_account(o)
    assert a["venue_confirmed_liquidity_role"] == "UNRESOLVED"


def test_markouts_reuse_the_tested_micro_implementation():
    from analysis.l5.live_micro.ledger import markouts
    g = L.OneOrderOnly()
    o = g.request("slug", "up", "0.53", "0.01")
    mk = markouts(o, {1: D("0.54"), 5: None, 10: None, 30: None, 60: None},
                  settlement_mark=D("1.00"))
    assert mk["t_plus_1"]["markout"] == "0.01"
    assert mk["t_plus_5"]["markout"] is None
    assert mk["settlement"]["markout"] == "0.47"
    assert mk["LABEL"] == "MARK_TO_MARKET_NOT_REALIZED"
