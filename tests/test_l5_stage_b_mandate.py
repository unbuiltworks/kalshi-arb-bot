"""A mandate: one owner approval, consecutive windows, two levels of claim.

Everything here exercises the boundary directly against a temporary ledger.
Nothing arms a transport or submits an order.

The invariants under test:

  * consecutive DISTINCT windows can be authorized under one mandate;
  * the window budget cannot be exceeded, including across a restart;
  * a SPENT window can never be reused;
  * Stage A (the default budget of one) is unchanged;
  * the mandate's scope and holder are re-proved at every window.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from src import l5_live_authorization as A


UTC = dt.timezone.utc
SERIES = "cpc-btc-updown-15m-"
SID = "mandate-sid-1"

#: three consecutive BTC15m windows. The ticker IS the window identity.
W = [("cpc-btc-updown-15m-2026-10-14-1800z",
      dt.datetime(2026, 10, 14, 18, 0, tzinfo=UTC)),
     ("cpc-btc-updown-15m-2026-10-14-1815z",
      dt.datetime(2026, 10, 14, 18, 15, tzinfo=UTC)),
     ("cpc-btc-updown-15m-2026-10-14-1830z",
      dt.datetime(2026, 10, 14, 18, 30, tzinfo=UTC))]


class Limits(object):
    def __init__(self, **kw):
        self.d = {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
                  "daily_realized_loss_halt_usd": "5", "max_live_orders": "1"}
        self.d.update(kw)

    def as_dict(self):
        return dict(self.d)


class _Store(object):
    def orders_uncertain(self):
        return None


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "auth.jsonl")


@pytest.fixture
def mandate(monkeypatch):
    """An owner-approved mandate: three windows of one series, with a
    deadline, a declared holder and a capital envelope."""
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", SID)
    monkeypatch.setenv("PMUS_Q1_ARM", SID)
    monkeypatch.setenv("L5_LIVE_AUTH_OWNER_ROLE", "l5-pmus-live")
    monkeypatch.setenv("PMUS_SERVICE_ROLE", "l5-pmus-live")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "rep-1")
    monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", "1")
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "3")
    monkeypatch.setenv("L5_LIVE_AUTH_MARKET_SERIES", SERIES)
    monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER", "2026-10-14T23:00:00Z")
    monkeypatch.delenv("L5_LIVE_PROBE_MARKET", raising=False)
    monkeypatch.delenv("L5_LIVE_AUTH_CLOSE_MARGIN_S", raising=False)


def bounds(start):
    return {"source": "VENUE", "start": start.isoformat(),
            "end": (start + dt.timedelta(seconds=900)).isoformat()}


def arm(ledger, i=0, at_s=300, **over):
    """Authorize window `i` of the mandate, `at_s` seconds after it opened."""
    market, start = W[i]
    kw = dict(market=market, preflight={"preflight": "PASSED"},
              store=_Store(), halted=False, halt_gate_present=True,
              reconciled=True, limits=Limits(),
              window_bounds=bounds(start), nominal_window_s=900.0,
              now=start + dt.timedelta(seconds=at_s),
              require_durable=False)
    kw.update(over)
    return A.authorize_arming(path=ledger, **kw)


def events(ledger):
    return [json.loads(x)["event"]
            for x in open(ledger).read().splitlines() if x.strip()]


# ------------------------------------------------- consecutive distinct windows

def test_three_consecutive_windows_are_authorized_under_one_mandate(mandate,
                                                                    ledger):
    claims = [arm(ledger, i) for i in range(3)]
    assert [c["event"] for c in claims] == ["WINDOW", "WINDOW", "WINDOW"]
    assert [c["market"] for c in claims] == [m for m, _ in W]
    assert [c["windows_spent"] for c in claims] == [0, 1, 2]


def test_the_mandate_itself_is_claimed_once(mandate, ledger):
    for i in range(3):
        arm(ledger, i)
    assert events(ledger) == ["MANDATE", "WINDOW", "WINDOW", "WINDOW"]


def test_each_window_gets_its_own_expiry_from_its_own_close(mandate, ledger):
    claims = [arm(ledger, i) for i in range(3)]
    assert [c["expires_utc"][:19] for c in claims] == [
        "2026-10-14T18:14:00", "2026-10-14T18:29:00", "2026-10-14T18:44:00"]
    for c in claims:
        assert c["window"]["bounded_by"] == "VENUE_CLOSE"


def test_a_claim_from_one_window_is_expired_in_the_next(mandate, ledger):
    first = arm(ledger, 0)
    _, next_start = W[1]
    assert A.still_valid(first, limits=Limits(),
                         now=next_start) == "AUTHORIZATION_EXPIRED"


def test_each_window_is_a_separate_durable_claim(mandate, ledger, tmp_path):
    for i in range(3):
        arm(ledger, i)
    locks = A.windows_spent(ledger, SID)
    assert len(locks) == 3
    assert len(set(locks)) == 3


# ------------------------------------------------------------------ the budget

def test_the_budget_cannot_be_exceeded(mandate, ledger):
    for i in range(3):
        arm(ledger, i)
    fourth = "cpc-btc-updown-15m-2026-10-14-1845z"
    start = dt.datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0, market=fourth, window_bounds=bounds(start),
            now=start + dt.timedelta(seconds=300))
    assert e.value.reason == "MANDATE_WINDOW_BUDGET_SPENT"
    assert e.value.detail["spent"] == 3
    assert e.value.detail["budget"] == 3


def test_the_budget_survives_a_restart(mandate, ledger, monkeypatch):
    """The count comes from the locks on the volume, not from memory."""
    for i in range(3):
        arm(ledger, i)
    # a "restart" is a fresh read of the same volume; the locks are still there
    assert len(A.windows_spent(ledger, SID)) == 3
    fourth = "cpc-btc-updown-15m-2026-10-14-1845z"
    start = dt.datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0, market=fourth, window_bounds=bounds(start),
            now=start + dt.timedelta(seconds=300))
    assert e.value.reason == "MANDATE_WINDOW_BUDGET_SPENT"


def test_a_lost_ledger_row_does_not_buy_an_extra_window(mandate, ledger):
    """The lock is written before the ledger row, so a crash between them
    spends the window. Counting the ledger would hand it back."""
    for i in range(3):
        arm(ledger, i)
    open(ledger, "w").write("")           # the audit log, emptied
    assert len(A.windows_spent(ledger, SID)) == 3
    fourth = "cpc-btc-updown-15m-2026-10-14-1845z"
    start = dt.datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0, market=fourth, window_bounds=bounds(start),
            now=start + dt.timedelta(seconds=300))
    assert e.value.reason == "MANDATE_WINDOW_BUDGET_SPENT"


def test_a_budget_of_one_is_stage_a(monkeypatch, mandate, ledger):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "1")
    first = arm(ledger, 0)
    assert first["event"] == "CLAIMED"
    with pytest.raises(A.Refused) as e:
        arm(ledger, 1)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_an_unreadable_budget_is_one_window(monkeypatch, mandate, ledger):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "lots")
    assert A.max_windows_env() == 1
    assert arm(ledger, 0)["event"] == "CLAIMED"


def test_a_zero_budget_is_one_window(monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "0")
    assert A.max_windows_env() == 1


def test_a_negative_budget_is_one_window(monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "-5")
    assert A.max_windows_env() == 1


def test_the_caller_cannot_widen_the_budget_by_argument(mandate, ledger,
                                                        monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "1")
    first = arm(ledger, 0, max_windows=99)
    assert first["event"] == "CLAIMED"          # still Stage A
    with pytest.raises(A.Refused) as e:
        arm(ledger, 1, max_windows=99)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


# ------------------------------------------------------- a spent window is gone

def test_a_spent_window_cannot_be_reused(mandate, ledger):
    arm(ledger, 0)
    with pytest.raises(A.Refused) as e:
        arm(ledger, 0)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_a_spent_window_cannot_be_reused_out_of_order(mandate, ledger):
    arm(ledger, 0)
    arm(ledger, 1)
    with pytest.raises(A.Refused) as e:
        arm(ledger, 0)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_reusing_a_spent_window_does_not_spend_another(mandate, ledger):
    arm(ledger, 0)
    with pytest.raises(A.Refused):
        arm(ledger, 0)
    assert len(A.windows_spent(ledger, SID)) == 1


def test_a_spent_window_is_refused_before_the_budget_is_blamed(mandate,
                                                               ledger):
    """Re-attempting a spent window must not report a budget problem."""
    for i in range(3):
        arm(ledger, i)
    with pytest.raises(A.Refused) as e:
        arm(ledger, 1)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_the_window_claim_file_is_never_modified(mandate, ledger):
    arm(ledger, 0)
    lock = A._lock_path(ledger, A.window_key(SID, W[0][0]))
    before, mtime = lock.read_bytes(), lock.stat().st_mtime_ns
    with pytest.raises(A.Refused):
        arm(ledger, 0)
    assert lock.read_bytes() == before
    assert lock.stat().st_mtime_ns == mtime


def test_distinct_windows_get_distinct_keys(mandate):
    keys = {A.window_key(SID, m) for m, _ in W}
    assert len(keys) == 3


def test_an_overlong_key_is_refused_rather_than_truncated(mandate):
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_key("x" * 200, W[0][0])
    assert e.value.reason == "WINDOW_KEY_TOO_LONG"


# -------------------------------------------------------------- the series bind

def test_a_market_outside_the_series_is_refused(mandate, ledger):
    other = "aec-nfl-bal-atl-2026-10-11"
    start = W[0][1]
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0, market=other)
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"
    assert e.value.detail["authorized_series"] == SERIES


def test_a_mandate_with_no_series_is_refused(mandate, ledger, monkeypatch):
    monkeypatch.delenv("L5_LIVE_AUTH_MARKET_SERIES")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0)
    assert e.value.reason == "MANDATE_SERIES_NOT_DECLARED"


def test_an_exact_market_still_wins_over_the_series(mandate, ledger,
                                                    monkeypatch):
    monkeypatch.setenv("L5_LIVE_PROBE_MARKET", W[0][0])
    arm(ledger, 0)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


def test_a_mandate_needs_an_absolute_deadline(mandate, ledger, monkeypatch):
    monkeypatch.delenv("L5_LIVE_AUTH_NOT_AFTER")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0)
    assert e.value.reason == "MANDATE_HAS_NO_DEADLINE"


def test_the_mandate_expires_at_its_deadline(mandate, ledger, monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_NOT_AFTER", "2026-10-14T18:20:00Z")
    arm(ledger, 0)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 2)                       # 18:30 window, past the deadline
    assert e.value.reason == "AUTHORIZATION_EXPIRED"


# ------------------------------------------- the mandate is re-proved each time

def test_a_widened_cash_cap_ends_the_mandate(mandate, ledger, monkeypatch):
    arm(ledger, 0)
    monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", "25")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_cash_deployed_usd" in e.value.detail["fields"]


def test_a_loosened_order_count_ends_the_mandate(mandate, ledger):
    arm(ledger, 0)
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1, limits=Limits(max_live_orders="8"))
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_live_orders" in e.value.detail["fields"]


def test_a_widened_budget_ends_the_mandate(mandate, ledger, monkeypatch):
    arm(ledger, 0)
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "9")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_windows" in e.value.detail["fields"]


def test_a_tightened_budget_also_ends_the_mandate(mandate, ledger,
                                                  monkeypatch):
    """A budget of 2 is not the mandate that was approved for 3."""
    arm(ledger, 0)
    monkeypatch.setenv("L5_LIVE_AUTH_MAX_WINDOWS", "2")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"


def test_a_changed_series_ends_the_mandate(mandate, ledger, monkeypatch):
    arm(ledger, 0)
    monkeypatch.setenv("L5_LIVE_AUTH_MARKET_SERIES", "cpc-btc-updown-")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "series" in e.value.detail["fields"]


def test_a_restart_ends_the_mandate(mandate, ledger, monkeypatch):
    """A restarted executor has a new pid: it may not continue the mandate."""
    arm(ledger, 0)
    rec = A.read_claim_payload(ledger, SID)
    restarted = dict(rec["holder"], pid=rec["holder"]["pid"] + 1)
    bad = A.holder_refusal(rec["holder"], restarted)
    assert bad["reason"] == "MANDATE_HOLDER_MISMATCH"


def test_another_deployment_cannot_continue_the_mandate(mandate, ledger,
                                                        monkeypatch):
    arm(ledger, 0)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-2")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_HOLDER_MISMATCH"
    assert "deployment_id" in e.value.detail["differs"]


def test_an_unidentifiable_actor_cannot_continue_the_mandate(mandate, ledger,
                                                             monkeypatch):
    arm(ledger, 0)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "DEPLOYMENT_IDENTITY_UNKNOWN"


def test_an_unreadable_mandate_refuses(mandate, ledger):
    arm(ledger, 0)
    A._lock_path(ledger, SID).write_text("{not json")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 1)
    assert e.value.reason == "MANDATE_SCOPE_UNREADABLE"


# ------------------------------------------ no Stage A check is relaxed by one

@pytest.mark.parametrize("over,reason", [
    ({"shadow_locked": True}, "SHADOW_ONLY_LOCK"),
    ({"halted": True}, "HALTED"),
    ({"halt_gate_present": False}, "HALT_GATE_ABSENT"),
    ({"reconciled": False}, "NOT_RECONCILED"),
    ({"preflight": {"preflight": "FAILED"}}, "PREFLIGHT_NOT_PASSED"),
    ({"unresolved": ["x"]}, "UNRESOLVED_ACCOUNTING"),
    ({"limits": {}}, "CAPITAL_SCOPE_UNKNOWN"),
    ({"window_bounds": None}, "WINDOW_BOUNDS_UNKNOWN"),
])
def test_every_window_re_runs_every_check(mandate, ledger, over, reason):
    arm(ledger, 0)
    with pytest.raises(A.Refused) as e:
        arm(ledger, 1, **over)
    assert e.value.reason == reason


def test_a_refused_second_window_spends_nothing(mandate, ledger):
    arm(ledger, 0)
    with pytest.raises(A.Refused):
        arm(ledger, 1, halted=True)
    assert len(A.windows_spent(ledger, SID)) == 1


def test_a_window_inside_its_close_margin_is_refused(mandate, ledger):
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, 0, at_s=841)             # 18:14:01, inside the 60s margin
    assert e.value.reason == "WINDOW_CLOSING"


def test_the_mandate_records_the_full_approved_scope(mandate, ledger):
    arm(ledger, 0)
    rec = A.read_claim_payload(ledger, SID)
    assert rec["event"] == "MANDATE"
    assert rec["scope"]["max_windows"] == "3"
    assert rec["scope"]["series"] == SERIES
    assert rec["scope"]["execution_mode"] == "MAKER_ONLY"
    assert rec["scope"]["max_live_orders"] == "1"
    assert rec["scope"]["max_cash_deployed_usd"] == "1"
    assert rec["scope"]["not_after"] == "2026-10-14T23:00:00Z"


def test_the_mandate_claim_is_never_modified_by_later_windows(mandate,
                                                              ledger):
    arm(ledger, 0)
    lock = A._lock_path(ledger, SID)
    before, mtime = lock.read_bytes(), lock.stat().st_mtime_ns
    arm(ledger, 1)
    arm(ledger, 2)
    assert lock.read_bytes() == before
    assert lock.stat().st_mtime_ns == mtime


def test_a_second_mandate_needs_a_new_session_id(mandate, ledger, monkeypatch):
    for i in range(3):
        arm(ledger, i)
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "mandate-sid-2")
    monkeypatch.setenv("PMUS_Q1_ARM", "mandate-sid-2")
    fourth = "cpc-btc-updown-15m-2026-10-14-1845z"
    start = dt.datetime(2026, 10, 14, 18, 45, tzinfo=UTC)
    fresh = arm(ledger, 0, market=fourth, window_bounds=bounds(start),
                now=start + dt.timedelta(seconds=300))
    assert fresh["event"] == "WINDOW"
    assert fresh["windows_spent"] == 0
    assert len(A.windows_spent(ledger, SID)) == 3        # the old one is spent
