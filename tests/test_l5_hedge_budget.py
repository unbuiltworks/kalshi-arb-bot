"""Durable hedge-budget reservation: the cap must survive what happens.

THE DEFECT THIS CLOSES. The wired payoff gate passed no budgets at all, so
`cumulative_locked_loss_usd` defaulted to 0 and the cumulative cap
degenerated into a second per-hedge cap. Four hedges at $0.14 each would
every one of them pass a $0.60 budget while totalling $0.56, and nothing
counted. Nothing persisted either, so a restart -- which an arming variable
write CAUSES -- reset the cap to zero.
"""
from __future__ import annotations

import json
import os

import pytest
from decimal import Decimal as D

from src import l5_hedge_budget as HB

NONCE = "camp.testnonce0001"
T0 = "2026-11-02T04:00:00+00:00"


@pytest.fixture()
def led(tmp_path):
    return HB.HedgeBudgetLedger(root=str(tmp_path))


# ------------------------------------------------------------ fail closed

def test_a_missing_ledger_reads_as_empty_not_as_an_error(led):
    doc = led.read(NONCE)
    assert doc["orders"] == {}
    assert led.committed(NONCE)["committed_usd"] == D(0)


def test_a_corrupt_ledger_RAISES_and_is_never_an_empty_budget(led, tmp_path):
    os.makedirs(led.dir, exist_ok=True)
    with open(led._path(NONCE), "w") as fh:
        fh.write("{not json")
    with pytest.raises(HB.HedgeBudgetUnavailable):
        led.read(NONCE)
    with pytest.raises(HB.HedgeBudgetUnavailable):
        led.committed(NONCE)


def test_a_malformed_row_RAISES(led):
    os.makedirs(led.dir, exist_ok=True)
    with open(led._path(NONCE), "w") as fh:
        json.dump({"nonce_sid": NONCE, "orders": {"c1": "nope"}}, fh)
    with pytest.raises(HB.HedgeBudgetUnavailable):
        led.read(NONCE)


def test_an_unknown_state_RAISES(led):
    os.makedirs(led.dir, exist_ok=True)
    with open(led._path(NONCE), "w") as fh:
        json.dump({"nonce_sid": NONCE,
                   "orders": {"c1": {"state": "WEIRD",
                                     "locked_loss_usd": "0.1"}}}, fh)
    with pytest.raises(HB.HedgeBudgetUnavailable):
        led.read(NONCE)


def test_may_reserve_refuses_when_a_cap_is_not_supplied(led):
    r = led.may_reserve(NONCE, locked_loss_usd="0.05",
                        max_per_hedge_usd=None, max_cumulative_usd="0.60")
    assert r["ok"] is False and r["reason"] == "PER_HEDGE_CAP_NOT_SUPPLIED"
    r = led.may_reserve(NONCE, locked_loss_usd="0.05",
                        max_per_hedge_usd="0.15", max_cumulative_usd=None)
    assert r["ok"] is False and r["reason"] == "CUMULATIVE_CAP_NOT_SUPPLIED"


def test_reserve_refuses_without_an_owner_cap(led):
    r = led.reserve(NONCE, "c1", locked_loss_usd="0.05",
                    max_cumulative_usd=None, now_iso=T0)
    assert r["ok"] is False and r["reason"] == "CUMULATIVE_CAP_NOT_SUPPLIED"


# -------------------------------------------- reservations bind each other

def test_two_candidates_cannot_pass_against_the_same_capacity(led):
    """THE WHOLE REASON FOR A RESERVATION. Without it both would pass."""
    a = led.reserve(NONCE, "c1", locked_loss_usd="0.40",
                    max_cumulative_usd="0.60", now_iso=T0)
    assert a["ok"] is True and a["reserved_now"] is True
    # second candidate sees the first as already committed
    v = led.may_reserve(NONCE, locked_loss_usd="0.40",
                        max_per_hedge_usd="0.50", max_cumulative_usd="0.60")
    assert v["ok"] is False
    assert v["reason"] == "CUMULATIVE_BUDGET_EXHAUSTED"
    assert D(v["committed_usd"]) == D("0.40")
    b = led.reserve(NONCE, "c2", locked_loss_usd="0.40",
                    max_cumulative_usd="0.60", now_iso=T0)
    assert b["ok"] is False and b["reason"] == "CUMULATIVE_BUDGET_EXHAUSTED"


def test_an_open_reservation_counts_as_committed(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    c = led.committed(NONCE)
    assert c["reserved_usd"] == D("0.10")
    assert c["consumed_usd"] == D(0)
    assert c["committed_usd"] == D("0.10")
    assert c["open_orders"] == 1


def test_reserving_the_same_coid_twice_is_a_no_op(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    again = led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                        max_cumulative_usd="0.60", now_iso=T0)
    assert again["idempotent"] is True
    assert again["reserved_now"] is False
    assert led.committed(NONCE)["committed_usd"] == D("0.10")


def test_the_per_hedge_cap_is_checked_before_the_cumulative_one(led):
    r = led.may_reserve(NONCE, locked_loss_usd="0.30",
                        max_per_hedge_usd="0.15", max_cumulative_usd="0.60")
    assert r["ok"] is False
    assert r["reason"] == "LOCKED_LOSS_EXCEEDS_PER_HEDGE"


# --------------------------------------------------- confirmed consumption

def test_a_confirmed_fill_books_the_ACTUAL_amount_not_the_estimate(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.12",
                max_cumulative_usd="0.60", now_iso=T0, qty="2.05")
    r = led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2.05",
                         actual_locked_loss_usd="0.0975", now_iso=T0)
    assert r["booked"] is True
    c = led.committed(NONCE)
    assert c["consumed_usd"] == D("0.0975")
    assert c["reserved_usd"] == D(0)
    assert c["committed_usd"] == D("0.0975")


def test_a_partial_fill_books_what_actually_filled(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.12",
                max_cumulative_usd="0.60", now_iso=T0, qty="2.05")
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="1.00",
                     actual_locked_loss_usd="0.05", now_iso=T0)
    assert led.committed(NONCE)["consumed_usd"] == D("0.05")


def test_a_later_partial_supersedes_an_earlier_one(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.15",
                max_cumulative_usd="0.60", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="1.00",
                     actual_locked_loss_usd="0.05", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2.00",
                     actual_locked_loss_usd="0.10", now_iso=T0)
    assert led.committed(NONCE)["consumed_usd"] == D("0.10")


def test_a_duplicate_fill_event_never_double_counts(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.12",
                max_cumulative_usd="0.60", now_iso=T0)
    for _ in range(5):
        led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2.05",
                         actual_locked_loss_usd="0.0975", now_iso=T0)
    assert led.committed(NONCE)["consumed_usd"] == D("0.0975")


def test_an_OUT_OF_ORDER_earlier_event_never_erases_a_later_one(led):
    """Reconciliation replays. An older, smaller fill must not reduce it."""
    led.reserve(NONCE, "c1", locked_loss_usd="0.15",
                max_cumulative_usd="0.60", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2.00",
                     actual_locked_loss_usd="0.10", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="1.00",
                     actual_locked_loss_usd="0.05", now_iso=T0)
    assert led.committed(NONCE)["consumed_usd"] == D("0.10")


def test_a_fill_with_no_reservation_is_booked_and_flagged(led):
    """Never a free hedge. The consumption is kept and the ledger is
    marked inconsistent so new hedges fail closed."""
    r = led.confirm_fill(NONCE, "ghost", cumulative_filled_qty="2",
                         actual_locked_loss_usd="0.20", now_iso=T0)
    assert r["ok"] is True and r["unreserved_fill"] is True
    assert led.committed(NONCE)["consumed_usd"] == D("0.20")
    cons = led.consistency(NONCE)
    assert cons["ok"] is False
    assert any(p.startswith("UNRESERVED_FILLS") for p in cons["problems"])


# ------------------------------------------------------------- releasing

def test_capacity_is_released_only_on_an_authoritative_terminal(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.40",
                max_cumulative_usd="0.60", now_iso=T0)
    bad = led.release(NONCE, "c1", now_iso=T0, authoritative=False,
                      reason="cancel_call_returned")
    assert bad["ok"] is False
    assert bad["reason"] == "RELEASE_REQUIRES_AUTHORITATIVE"
    assert led.committed(NONCE)["committed_usd"] == D("0.40")
    good = led.release(NONCE, "c1", now_iso=T0, authoritative=True,
                       reason="venue_reread_terminal")
    assert good["released"] is True
    assert led.committed(NONCE)["committed_usd"] == D(0)


def test_a_consumed_reservation_is_never_released(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.12",
                max_cumulative_usd="0.60", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2",
                     actual_locked_loss_usd="0.10", now_iso=T0)
    r = led.release(NONCE, "c1", now_iso=T0, authoritative=True,
                    reason="late_cancel")
    assert r["ok"] is False and r["reason"] == "ALREADY_CONSUMED"
    assert led.committed(NONCE)["consumed_usd"] == D("0.10")


def test_releasing_twice_is_idempotent(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    led.release(NONCE, "c1", now_iso=T0, authoritative=True, reason="t")
    again = led.release(NONCE, "c1", now_iso=T0, authoritative=True,
                        reason="t")
    assert again["ok"] is True and again["released"] is False


def test_a_released_reservation_cannot_be_reused_under_its_own_coid(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    led.release(NONCE, "c1", now_iso=T0, authoritative=True, reason="t")
    r = led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                    max_cumulative_usd="0.60", now_iso=T0)
    assert r["ok"] is False
    assert r["reason"] == "RESERVATION_ALREADY_RELEASED"


# ---------------------------------------------------------- restart recovery

def test_the_ledger_survives_a_restart(led, tmp_path):
    """An arming variable write RESTARTS the container. That is the event
    the budget is most likely to meet, and it must not reset it.
    """
    led.reserve(NONCE, "c1", locked_loss_usd="0.25",
                max_cumulative_usd="0.60", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2",
                     actual_locked_loss_usd="0.22", now_iso=T0)
    fresh = HB.HedgeBudgetLedger(root=str(tmp_path))      # new process
    assert fresh.committed(NONCE)["consumed_usd"] == D("0.22")
    v = fresh.may_reserve(NONCE, locked_loss_usd="0.40",
                          max_per_hedge_usd="0.50",
                          max_cumulative_usd="0.60")
    assert v["ok"] is False, "the restored spend must still bind"


def test_the_write_is_atomic_leaving_no_tmp_behind(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    assert not os.path.exists(led._path(NONCE) + ".tmp")
    assert os.path.exists(led._path(NONCE))


# ------------------------------------------------- campaign separation

def test_a_different_campaign_nonce_has_its_own_budget(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.55",
                max_cumulative_usd="0.60", now_iso=T0)
    other = "camp.differentnonce"
    assert led.committed(other)["committed_usd"] == D(0)
    r = led.reserve(other, "c1", locked_loss_usd="0.55",
                    max_cumulative_usd="0.60", now_iso=T0)
    assert r["ok"] is True, "a new campaign starts with a fresh budget"
    assert led.committed(NONCE)["committed_usd"] == D("0.55")


# ------------------------------------------------- venue-state consistency

def test_an_open_reservation_absent_from_the_venue_fails_closed(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    c = led.consistency(NONCE, venue_open_coids=[])
    assert c["ok"] is False
    assert any(p.startswith("OPEN_RESERVATION_NOT_AT_VENUE")
               for p in c["problems"])
    assert "does NOT disable cancellation" in c["labels"]


def test_consistency_is_ok_when_the_venue_agrees(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    assert led.consistency(NONCE, venue_open_coids=["c1"])["ok"] is True


def test_a_consumed_reservation_need_not_still_be_at_the_venue(led):
    led.reserve(NONCE, "c1", locked_loss_usd="0.10",
                max_cumulative_usd="0.60", now_iso=T0)
    led.confirm_fill(NONCE, "c1", cumulative_filled_qty="2",
                     actual_locked_loss_usd="0.09", now_iso=T0)
    assert led.consistency(NONCE, venue_open_coids=[])["ok"] is True


def test_this_module_holds_no_limit_of_its_own():
    """Both caps arrive on every call. A module-level default would be a
    limit the owner never approved."""
    for name in dir(HB):
        low = name.lower()
        assert not (low.startswith("max_") and "usd" in low), name
