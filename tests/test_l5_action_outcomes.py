"""The three missing action labels, over records the executor already writes.

The point of each test is that a distinction is PRESERVED: an engine refusal
is not a venue rejection, a resting order is not a fill, and an accepted
cancel request is not a cancelled order.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

from src import l5_action_outcomes as O
from src import l5_halt_gate as HG
from src import l5_live_execution as E

NOW_DT = dt.datetime(2026, 10, 14, 17, 0, 0, tzinfo=dt.timezone.utc)
MARKET = "cpc-btc-updown-15m-2026-10-14-1700z"


# ---------------------------------------------------------------- REJECTED

def test_an_engine_refusal_is_rejected_by_engine():
    rec = {"action_key": "a", "market": MARKET, "window": "17:00",
           "refused_by_layer": "VALIDATION_GATES",
           "refused_reason": "CASH_COMMITMENT:1.35>1",
           "stage_reached": "VALIDATION_GATES"}
    r = O.rejected_from_decision(rec)
    assert r["label"] == "REJECTED" and r["by"] == "ENGINE"
    assert r["layer"] == "VALIDATION_GATES"
    assert r["reason"] == "CASH_COMMITMENT:1.35>1"


def test_a_permitted_decision_is_not_rejected():
    assert O.rejected_from_decision(
        {"action_key": "a", "refused_by_layer": None}) is None


def test_a_venue_rejection_is_rejected_by_venue():
    r = O.rejected_from_venue(
        {"id": "v1", "clientOrderId": "c1", "marketSlug": MARKET,
         "state": "ORDER_STATE_REJECTED"})
    assert r["by"] == "VENUE"
    assert r["venue_order_id"] == "v1"
    assert "never rested" in r["labels"]


def test_an_open_venue_order_is_not_rejected():
    assert O.rejected_from_venue(
        {"id": "v1", "state": "ORDER_STATE_OPEN"}) is None


def test_the_two_rejection_sources_are_never_merged():
    """Collapsing them would hide which side said no."""
    out = O.classify(
        decisions=[{"refused_by_layer": "HALT_GATE",
                    "refused_reason": "HALT:halt_asserted"}],
        venue_order_rows=[{"id": "v1", "state": "ORDER_STATE_REJECTED"}])
    assert out["REJECTED"]["counts"] == {"engine": 1, "venue": 1}
    assert out["REJECTED"]["by_engine"][0]["by"] == "ENGINE"
    assert out["REJECTED"]["by_venue"][0]["by"] == "VENUE"


# ----------------------------------------------------------------- RESTING

def test_a_live_order_with_remaining_is_resting(tmp_path):
    st = E.InventoryStore(str(tmp_path / "inv.json"))
    st.record_submit("c1", {"market": MARKET, "side": "UP",
                            "price": D("0.45"), "qty": D("2")},
                     session_id="s1", mode="MAKER")
    st.state["live_orders"]["c1"]["venue_order_id"] = "v1"
    st.save()
    rows = O.resting(st.live_orders())
    assert [r["client_order_id"] for r in rows] == ["c1"]
    assert rows[0]["remaining"] == "2"
    assert rows[0]["venue_order_id"] == "v1"


def test_a_fully_filled_order_is_not_resting():
    assert O.resting({"c1": {"remaining": "0", "price": "0.45"}}) == []


def test_a_terminal_state_is_not_resting():
    assert O.resting(
        {"c1": {"remaining": "2", "state": "ORDER_STATE_CANCELED",
                "price": "0.45"}}) == []


def test_the_resting_test_matches_the_risk_gates_reading(tmp_path):
    """An order cannot be RESTING here and missing from the exposure
    figures: both read remaining > 0 at the offered price."""
    st = E.InventoryStore(str(tmp_path / "inv.json"))
    st.record_submit("c1", {"market": MARKET, "side": "UP",
                            "price": D("0.45"), "qty": D("2")},
                     session_id="s1", mode="MAKER")
    st.save()
    out = O.classify(live_orders=st.live_orders())
    assert out["RESTING"]["usd_still_fillable"] == "0.90"
    assert st.outstanding_by_market()[MARKET] == D("0.90")


def test_an_adopted_order_is_flagged_as_such(tmp_path):
    st = E.InventoryStore(str(tmp_path / "inv.json"))
    st.reconcile_from_venue([], [{
        "id": "vf", "clientOrderId": "foreign", "marketSlug": MARKET,
        "outcomeSide": "OUTCOME_SIDE_YES", "state": "ORDER_STATE_OPEN",
        "price": {"value": "0.90", "currency": "USD"},
        "quantity": "5", "leavesQuantity": "5", "remainingQuantity": "5",
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_MANUAL"}])
    rows = O.resting(st.live_orders())
    assert rows[0]["adopted"] is True


# --------------------------------------------------------------- CANCELLED

def test_only_a_venue_confirmed_cancel_is_cancelled():
    out = O.cancelled({
        "reason": "independent_halt:halt_asserted",
        "cancelled": [{"venue_order_id": "v1", "client_order_id": "c1",
                       "venue_state": "ORDER_STATE_CANCELED"}],
        "still_open": [], "unverified": []})
    assert [r["venue_order_id"] for r in out["CANCELLED"]] == ["v1"]
    assert out["NOT_CANCELLED_STILL_OPEN"] == []


def test_an_order_still_open_after_the_cancel_is_reported_as_resting():
    """THE DANGEROUS CASE. The request returned; the order is still working.
    It is never reported as cancelled."""
    out = O.cancelled({
        "cancelled": [],
        "still_open": [{"venue_order_id": "v1",
                        "venue_state": "ORDER_STATE_OPEN"}],
        "unverified": []})
    assert out["CANCELLED"] == []
    assert out["NOT_CANCELLED_STILL_OPEN"][0]["label"] == "RESTING"


def test_an_unverified_cancel_is_unknown_not_cancelled():
    out = O.cancelled({"cancelled": [], "still_open": [],
                       "unverified": [{"venue_order_id": "v1"}]})
    assert out["CANCELLED"] == []
    assert out["NOT_CANCELLED_UNVERIFIED"][0]["label"] == "UNKNOWN"


def test_a_real_deadman_record_classifies(tmp_path):
    """End to end against the real `deadman`, not a hand-written dict."""
    class T:
        armed = True

        def __init__(self):
            self.retired = set()

        def cancel(self, oid, market_slug=None):
            self.retired.add(oid)
            return {"cancelled": oid}

        def order(self, oid):
            return {"id": oid, "state": "ORDER_STATE_CANCELED",
                    "cumQuantity": "0", "leavesQuantity": "0",
                    "avgPx": {"value": "0", "currency": "USD"},
                    "commissionNotionalTotalCollected": {"value": "0",
                                                         "currency": "USD"}}

    st = E.InventoryStore(str(tmp_path / "inv.json"))
    st.record_submit("c1", {"market": MARKET, "side": "UP",
                            "price": D("0.45"), "qty": D("2")},
                     session_id="s1", mode="MAKER")
    st.state["live_orders"]["c1"]["venue_order_id"] = "v1"
    st.save()
    rec = E.deadman(st, T(), reason="test")
    out = O.cancelled(rec)
    assert [r["venue_order_id"] for r in out["CANCELLED"]] == ["v1"]


# ------------------------------------------------------- fees and rebates

def test_fees_and_rebates_are_separate_lines_never_netted():
    out = O.classify(fees_usd="0.07", rebates_credited_usd="0.01")
    assert out["fees_usd"] == "0.07"
    assert out["rebates_credited_usd"] == "0.01"
    assert "never netted into a count" in out["labels"]


def test_no_reading_is_none_not_zero():
    """A zero commission is not a rebate, and no reading is not zero."""
    out = O.classify()
    assert out["fees_usd"] is None
    assert out["rebates_credited_usd"] is None


# ----------------------------------------------------- the label set itself

def test_the_lifecycle_is_the_seven_states_in_order():
    assert O.LIFECYCLE == ("SUBMITTED", "REJECTED", "RESTING", "FILLED",
                           "CANCELLED", "SETTLED", "REALIZED")
    assert set(O.ALREADY_LABELLED) | set(O.ADDED_HERE) == set(O.LIFECYCLE)
    assert not set(O.ALREADY_LABELLED) & set(O.ADDED_HERE)


def test_it_holds_no_state_and_gates_nothing():
    """REPORTING ONLY: no writes, no decisions. Classifying twice over the
    same inputs gives the same answer and changes nothing."""
    live = {"c1": {"remaining": "2", "price": "0.45", "market": MARKET}}
    a = O.classify(live_orders=live)
    b = O.classify(live_orders=live)
    assert a == b
    assert live == {"c1": {"remaining": "2", "price": "0.45",
                           "market": MARKET}}


def test_an_unreadable_number_does_not_raise():
    """It is a reporter on the live path's data; it must never be the thing
    that breaks a cycle."""
    out = O.classify(live_orders={"c1": {"remaining": "two",
                                         "price": "0.45"}})
    assert out["RESTING"]["count"] == 0        # unreadable -> not counted
    assert O.classify(fees_usd="not-a-number")["fees_usd"] == "0"
