"""The BTC 5-minute strategy is authorized by the SAME mechanism as the
15-minute one, and by nothing else.

Owner decision, 2026-11-11: "Extend the existing single-use authorization
mechanism to recognize explicitly approved BTC5m markets. Do not create a
second, weaker authorization path. Each strategy must have its own approved
market, time window, exposure cap, and order ownership."

These tests drive the REAL `claim_btc5m_submission` against a real temporary
directory: the real approval gate, the real window ledger, the real class
tickets and the real O_EXCL locks. Nothing is stubbed except where a test
says so, because a stubbed claim is exactly what would hide a hole.

They prove mechanism only: what is refused, what is spent, and that one
strategy's approval can never be spent by the other. They are not evidence
about a venue or about economics.
"""
from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import pathlib
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 12, 15, 1, 0, tzinfo=UTC)       # 60 s into 15:00
W1 = dt.datetime(2026, 11, 12, 15, 0, 0, tzinfo=UTC)
W2 = dt.datetime(2026, 11, 12, 15, 5, 0, tzinfo=UTC)
M1 = "cpc-btc-updown-5m-2026-11-12-1500z"
M2 = "cpc-btc-updown-5m-2026-11-12-1505z"
M_NOT_APPROVED = "cpc-btc-updown-5m-2026-11-12-1510z"
M15 = "cpc-btc-updown-15m-2026-11-12-1500z"                 # same start as M1
COID = "b5m-" + "a" * 24
COID2 = "b5m-" + "b" * 24
COID3 = "b5m-" + "c" * 24

ALL_ENV = (CA.APPROVAL_ENV, CA.ROOT_ENV, AUTH.EXPERIMENT_ENV,
           AUTH.MAX_SUBMISSIONS_ENV, AUTH.SUBMIT_PAUSE_ENV,
           AUTH.LIVE_HALT_ENV, AUTH.SHADOW_ONLY_ENV, AUTH.OWNER_ROLE_ENV,
           AUTH.SERVICE_ROLE_ENV, AUTH.LEDGER_ENV,
           AUTH.REDUCING_REPLACE_USES_COMPLETE_ENV,
           *AUTH.CLASS_BUDGET_ENV.values())


def document(series=CA.BTC5M_SERIES, starts=(W1, W2), now=NOW, **over):
    doc = {"approved": True, "nonce": "n" * 32, "series": series,
           "issued_utc": (now - dt.timedelta(minutes=5)).isoformat(),
           "expires_utc": (now + dt.timedelta(minutes=30)).isoformat(),
           "max_windows": len(starts) if starts else 2,
           "max_total_exposure_usd": "5", "max_daily_loss_usd": "5"}
    if starts:
        doc["window_starts"] = [t.isoformat() for t in starts]
    doc.update(over)
    return {k: v for k, v in doc.items() if v is not None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Everything the owner has to have set for a 5-minute order to go."""
    for k in ALL_ENV:
        monkeypatch.delenv(k, raising=False)
    # ONE CLOCK. The gate judges the document's own dates against the wall
    # clock; the tests fix that clock at NOW so a document "issued five
    # minutes ago" is that on every run. The gate itself is the real one.
    monkeypatch.setattr(CA, "approval_gate", lambda: CA.ApprovalGate(
        CA.EnvApprovalStore(), max_age_s=CA.MAX_CAMPAIGN_S,
        now_fn=lambda: NOW))
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(document()))
    monkeypatch.setenv(CA.ROOT_ENV, str(tmp_path / "root"))
    monkeypatch.setenv(AUTH.EXPERIMENT_ENV, "B5M-PILOT-1")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "6")
    monkeypatch.setenv(AUTH.SHADOW_ONLY_ENV, "0")
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "l5-btc5m-live")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "l5-btc5m-live")
    return monkeypatch


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "auth" / "auth.jsonl")


def take(ledger, market=M1, coid=COID, *, now=NOW, worst="0.10", day="0",
         klass="OPEN", reducing=False, durable=False, **payload):
    return AUTH.claim_btc5m_submission(
        market, coid, payload or {"side": "UP", "price": "0.58"},
        worst_case_usd=worst, day_realized_usd=day, klass=klass,
        reducing=reducing, path=ledger, now=now, require_durable=durable)


def verdict(ledger, market=M1, now=NOW, durable=False):
    return AUTH.btc5m_authorization(market, now=now, path=ledger,
                                    require_durable=durable)


def locks(ledger):
    d = pathlib.Path(ledger).parent
    return sorted(p.name for p in d.glob("l5_auth_claim_*.lock")) \
        if d.is_dir() else []


def windows_spent(tmp_path):
    d = tmp_path / "root" / CA.CONSUMED_DIR
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("*.json"):
        out.extend(json.loads(p.read_text())["windows"])
    return out


def refused(ledger, reason, **kw):
    with pytest.raises(AUTH.Refused) as e:
        take(ledger, **kw)
    assert e.value.reason == reason, (e.value.reason, e.value.detail)
    return e.value


# ====================================================================
# 1. THE CLAIM
# ====================================================================

def test_an_approved_window_takes_one_durable_claim(env, ledger, tmp_path):
    rec = take(ledger)
    assert rec["kind"] == "btc5m_submission_claim"
    assert rec["strategy"] == "BTC5M" and rec["market"] == M1
    assert rec["client_order_id"] == COID and rec["klass"] == "OPEN"
    assert rec["sid"] == AUTH.btc5m_sid(AUTH.experiment_id(), COID)
    assert rec["sid"].startswith("b5m1.B5M-PILOT-1.camp.")
    assert rec["approved_exposure_usd"] == "5"
    # durable before it returns: the lock file is on disk and readable
    lock = AUTH._lock_path(ledger, rec["sid"])
    assert lock.exists() and json.loads(lock.read_text())["market"] == M1
    # and the window was reserved against the approval, once
    assert windows_spent(tmp_path) == [M1]


def test_the_same_order_is_claimed_once_whatever_happened_to_it(env, ledger):
    take(ledger)
    before = locks(ledger)
    for note in ("cancelled unfilled", "partially filled", "venue said 400",
                 "timeout, ack unknown"):
        e = refused(ledger, "AUTHORIZATION_ALREADY_USED", note=note)
        assert "one client order id is submitted once" in e.detail["note"]
    assert locks(ledger) == before            # a replay spends nothing more


def test_a_restart_does_not_return_the_claim(env, ledger):
    take(ledger)
    fresh = importlib.reload(AUTH)
    try:
        with pytest.raises(fresh.Refused) as e:
            fresh.claim_btc5m_submission(
                M1, COID, {}, worst_case_usd="0.10", day_realized_usd="0",
                klass="OPEN", path=ledger, now=NOW, require_durable=False)
        assert e.value.reason == "AUTHORIZATION_ALREADY_USED"
    finally:
        importlib.reload(fresh)


def test_a_second_order_in_the_same_window_reserves_no_second_window(
        env, ledger, tmp_path):
    take(ledger)
    take(ledger, coid=COID2)
    assert windows_spent(tmp_path) == [M1]
    take(ledger, market=M2, coid=COID3, now=W2 + dt.timedelta(seconds=30))
    assert windows_spent(tmp_path) == [M1, M2]


def test_a_claim_that_cannot_be_persisted_refuses(env, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(AUTH.Refused) as e:
        take(str(blocker / "sub" / "auth.jsonl"))
    assert e.value.reason in ("BTC5M_CLAIM_UNAVAILABLE",
                              "OPEN_BUDGET_UNAVAILABLE")
    assert "NotADirectoryError" in str(e.value.detail)


# ====================================================================
# 2. EVERYTHING THAT REFUSES, AND THAT A REFUSAL SPENDS NOTHING
# ====================================================================

def _set(name, value):
    return lambda mp: mp.setenv(name, value)


def _unset(name):
    return lambda mp: mp.delenv(name, raising=False)


def _approval(**over):
    return lambda mp: mp.setenv(CA.APPROVAL_ENV, json.dumps(document(**over)))


ENV_REFUSALS = [
    ("SUBMISSION_PAUSED_FOR_DEPLOY", _set(AUTH.SUBMIT_PAUSE_ENV, "1")),
    ("OPERATOR_HALT", _set(AUTH.LIVE_HALT_ENV, "1")),
    ("OPERATOR_HALT", _set(AUTH.LIVE_HALT_ENV, "0")),   # any value halts
    ("SHADOW_ONLY_LOCK", _unset(AUTH.SHADOW_ONLY_ENV)),
    ("SHADOW_ONLY_LOCK", _set(AUTH.SHADOW_ONLY_ENV, "1")),
    ("SHADOW_ONLY_LOCK", _set(AUTH.SHADOW_ONLY_ENV, "false")),
    ("AUTH_OWNER_ROLE_NOT_DECLARED", _unset(AUTH.OWNER_ROLE_ENV)),
    ("SERVICE_ROLE_UNKNOWN", _unset(AUTH.SERVICE_ROLE_ENV)),
    ("NOT_THE_AUTHORIZATION_HOLDER",
     _set(AUTH.SERVICE_ROLE_ENV, "l5-pmus-live")),
    ("BTC5M_APPROVAL_ABSENT", _unset(CA.APPROVAL_ENV)),
    ("BTC5M_APPROVAL_ABSENT", _set(CA.APPROVAL_ENV, "   ")),
    ("APPROVAL_NOT_VALID", _set(CA.APPROVAL_ENV, "{not json")),
    ("APPROVAL_NOT_VALID", _approval(approved="true")),
    ("APPROVAL_NOT_VALID", _approval(nonce="short")),
    ("APPROVAL_NOT_VALID", _approval(series="cpc-eth-updown-5m-")),
    ("APPROVAL_NOT_VALID", _approval(
        issued_utc=(NOW - dt.timedelta(hours=5)).isoformat())),
    ("APPROVAL_NOT_VALID", _approval(
        expires_utc=(NOW - dt.timedelta(seconds=1)).isoformat())),
    ("APPROVAL_NOT_VALID", _approval(max_windows=1)),   # two starts, one window
    # a 15-MINUTE approval authorizes nothing here, even for the same start
    ("CAMPAIGN_SERIES_MISMATCH", _approval(series=CA.BTC15M_SERIES)),
    # an interval is not an explicit approval of markets
    ("BTC5M_WINDOWS_NOT_ENUMERATED", _approval(
        starts=None, last_open_utc=W2.isoformat())),
    ("CAMPAIGN_OPENING_BOUND_ABSENT", _approval(starts=None)),
    ("WINDOW_NOT_IN_AUTHORIZED_SET", _approval(starts=(W2,))),
    ("BTC5M_CLAIM_NO_EXPERIMENT", _unset(AUTH.EXPERIMENT_ENV)),
    ("BTC5M_SUBMISSION_BUDGET_NOT_SET", _unset(AUTH.MAX_SUBMISSIONS_ENV)),
    ("SUBMISSION_BUDGET_EXHAUSTED", _set(AUTH.MAX_SUBMISSIONS_ENV, "0")),
    ("SUBMISSION_BUDGET_EXHAUSTED", _set(AUTH.MAX_SUBMISSIONS_ENV, "many")),
]


@pytest.mark.parametrize("reason,change", ENV_REFUSALS,
                         ids=["%02d-%s" % (i, r)
                              for i, (r, _c) in enumerate(ENV_REFUSALS)])
def test_each_missing_condition_refuses_and_spends_nothing(
        env, ledger, tmp_path, reason, change):
    change(env)
    refused(ledger, reason)
    assert locks(ledger) == [] and windows_spent(tmp_path) == []


CALL_REFUSALS = [
    ("BTC5M_MARKET_NOT_IN_SERIES", {"market": M15}),
    ("BTC5M_MARKET_NOT_IN_SERIES", {"market": ""}),
    ("BTC5M_MARKET_NOT_IN_SERIES", {"market": None}),
    ("BTC5M_MARKET_NOT_IN_SERIES", {"market": "x" + M1}),
    ("WINDOW_NOT_IN_AUTHORIZED_SET", {"market": M_NOT_APPROVED,
                                      "now": W2 + dt.timedelta(minutes=5)}),
    ("WINDOW_START_UNREADABLE", {"market": CA.BTC5M_SERIES + "soon"}),
    ("BTC5M_WINDOW_NOT_OPEN", {"now": W1 - dt.timedelta(seconds=1)}),
    ("BTC5M_WINDOW_CLOSED", {"now": W1 + dt.timedelta(seconds=300)}),
    ("BTC5M_WINDOW_CLOSED", {"now": W1 + dt.timedelta(seconds=301)}),
    ("BTC5M_CLAIM_BAD_ORDER_ID", {"coid": "l5f-" + "a" * 24}),
    ("BTC5M_CLAIM_BAD_ORDER_ID", {"coid": "b5m-" + "a" * 23}),
    ("BTC5M_CLAIM_BAD_ORDER_ID", {"coid": "b5m-" + "A" * 24}),
    ("BTC5M_CLAIM_BAD_ORDER_ID", {"coid": ""}),
    ("BTC5M_CLAIM_BAD_ORDER_ID", {"coid": None}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": None}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": "NaN"}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": "Infinity"}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": True}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": "-0.01"}),
    ("BTC5M_EXPOSURE_UNREADABLE", {"worst": "five"}),
    ("BTC5M_EXPOSURE_ABOVE_APPROVED", {"worst": "5.01"}),
    ("BTC5M_DAY_RESULT_UNREADABLE", {"day": None}),
    ("BTC5M_DAY_RESULT_UNREADABLE", {"day": "NaN"}),
    ("BTC5M_DAILY_LOSS_LIMIT", {"day": "-5"}),
    ("BTC5M_DAILY_LOSS_LIMIT", {"day": "-5.01"}),
    ("BTC5M_CLASS_NOT_STATED", {"klass": None}),
    ("BTC5M_CLASS_NOT_STATED", {"klass": "  "}),
    ("SUBMISSION_CLASS_UNKNOWN", {"klass": "HEDGE"}),
]


@pytest.mark.parametrize("reason,kw", CALL_REFUSALS,
                         ids=["%02d-%s" % (i, r)
                              for i, (r, _k) in enumerate(CALL_REFUSALS)])
def test_each_bad_request_refuses_and_spends_nothing(env, ledger, tmp_path,
                                                     reason, kw):
    refused(ledger, reason, **kw)
    assert locks(ledger) == [] and windows_spent(tmp_path) == []


def test_ephemeral_storage_refuses(env, ledger, monkeypatch):
    monkeypatch.setattr(AUTH, "nearest_mount", lambda path: "/")
    refused(ledger, "AUTHORIZATION_LEDGER_NOT_DURABLE", durable=True)
    assert locks(ledger) == []


def test_more_than_one_replica_refuses(env, ledger, monkeypatch):
    monkeypatch.setattr(AUTH, "nearest_mount", lambda path: "/state")
    monkeypatch.setenv("RAILWAY_REPLICA_COUNT", "2")
    refused(ledger, "EXCLUSIVITY_NOT_GUARANTEED", durable=True)


def test_an_unreadable_approval_refuses(env, ledger, monkeypatch):
    def boom(self, key):
        raise OSError("store down")
    monkeypatch.setattr(CA.EnvApprovalStore, "get", boom)
    refused(ledger, "APPROVAL_UNREADABLE")
    assert locks(ledger) == []


def test_the_boundaries_of_the_limits_are_inclusive_where_stated(env, ledger):
    take(ledger, worst="5.00", day="-4.99")        # at the cap, under the loss
    refused(ledger, "BTC5M_EXPOSURE_ABOVE_APPROVED", coid=COID2,
            worst="5.0001")
    refused(ledger, "BTC5M_DAILY_LOSS_LIMIT", coid=COID2, day="-5.00")


def test_the_window_is_open_from_its_first_second_to_its_last(env, ledger):
    take(ledger, now=W1)
    take(ledger, coid=COID2, now=W1 + dt.timedelta(seconds=299.9))


# ====================================================================
# 3. THE DOCUMENT CANNOT WIDEN THE 5-MINUTE STRATEGY'S CEILING
# ====================================================================

def test_a_document_asking_for_more_exposure_gets_the_5m_ceiling(
        env, ledger):
    env.setenv(CA.APPROVAL_ENV, json.dumps(document(
        max_total_exposure_usd="20", max_daily_loss_usd="50")))
    v = verdict(ledger)
    assert v["ok"] is True
    assert v["scope"]["max_total_exposure_usd"] == "5"
    # CLAMPED STILL, to the 5-minute series' own cap rather than the
    # general one. $50 asked, $11 granted: the document is still unable to
    # widen its own ceiling, which is what this test exists to pin.
    assert v["scope"]["max_daily_loss_usd"] == str(
        CA.max_daily_loss_for(CA.BTC5M_SERIES))
    assert D(v["scope"]["max_daily_loss_usd"]) < D("50")
    assert v["scope"]["clamped"]["max_total_exposure_usd"] is True
    refused(ledger, "BTC5M_EXPOSURE_ABOVE_APPROVED", worst="5.01")
    assert take(ledger, worst="5")["approved_exposure_usd"] == "5"


def test_the_wider_daily_loss_cap_is_the_5m_series_and_nothing_else():
    """The per-series loss cap must not leak to any other series.

    SERIES_MAX_DAILY_LOSS_USD is LOOSER than the general ceiling, so the
    blast radius is the whole point: BTC15m and anything unreadable get
    MAX_DAILY_LOSS_USD, never the 5-minute value.
    """
    assert CA.max_daily_loss_for(CA.BTC5M_SERIES) == D("11")
    assert CA.max_daily_loss_for(CA.BTC15M_SERIES) == CA.MAX_DAILY_LOSS_USD
    assert CA.MAX_DAILY_LOSS_USD == D("5")
    for bad in (None, "", "   ", "not-a-series", 0, object()):
        assert CA.max_daily_loss_for(bad) == CA.MAX_DAILY_LOSS_USD


def test_a_document_asking_for_less_gets_less(env, ledger):
    env.setenv(CA.APPROVAL_ENV, json.dumps(document(
        max_total_exposure_usd="1.00", max_daily_loss_usd="0.50")))
    refused(ledger, "BTC5M_EXPOSURE_ABOVE_APPROVED", worst="1.01")
    refused(ledger, "BTC5M_DAILY_LOSS_LIMIT", day="-0.50")
    take(ledger, worst="1.00", day="-0.49")


def test_the_15m_ceiling_is_not_changed_by_the_5m_one(env):
    env.setenv(CA.APPROVAL_ENV, json.dumps(document(
        series=CA.BTC15M_SERIES, starts=(W1,), max_total_exposure_usd="50")))
    st = CA.approval_gate().state()
    assert st["approved"] is True
    assert D(st["scope"]["max_total_exposure_usd"]) == \
        CA.MAX_TOTAL_EXPOSURE_USD == D("20")
    assert CA.SERIES_MAX_TOTAL_EXPOSURE_USD[CA.BTC5M_SERIES] \
        <= CA.MAX_TOTAL_EXPOSURE_USD


def test_series_and_strategy_are_read_from_the_slug(env):
    assert CA.series_of(M1) == CA.BTC5M_SERIES
    assert CA.series_of(M15) == CA.BTC15M_SERIES
    assert CA.strategy_of(M1) == "BTC5M" and CA.strategy_of(M15) == "BTC15M"
    for other in ("", None, "cpc-eth-updown-5m-2026-11-12-1500z", "x" + M1):
        assert CA.series_of(other) is None and CA.strategy_of(other) is None
    # neither series name is a prefix of the other
    assert not CA.BTC15M_SERIES.startswith(CA.BTC5M_SERIES)
    assert not CA.BTC5M_SERIES.startswith(CA.BTC15M_SERIES)
    assert set(CA.SERIES_STRATEGY) == set(CA.ALLOWED_SERIES) \
        == set(CA.SERIES_WINDOW_S)


# ====================================================================
# 4. THE SAME BUDGETS, COUNTED IN THE SAME PLACE
# ====================================================================

def test_the_flat_total_counts_5m_claims_and_then_refuses(env, ledger):
    env.setenv(AUTH.MAX_SUBMISSIONS_ENV, "3")
    take(ledger)
    take(ledger, coid=COID2, klass="REPLACE")
    take(ledger, coid=COID3, klass="REPLACE")
    exp = AUTH.experiment_id()
    assert len(AUTH.submissions_spent(ledger, exp)) == 3
    e = refused(ledger, "SUBMISSION_BUDGET_EXHAUSTED",
                coid="b5m-" + "d" * 24, klass="REPLACE")
    assert e.detail["spent"] == 3 and e.detail["max_submissions"] == 3


def test_an_open_is_refused_when_nothing_would_be_left_to_close_it(
        env, ledger):
    """`completion_capacity_refusal`, the 15-minute strategy's own rule:
    do not open what the remaining budget could not close."""
    env.setenv(AUTH.MAX_SUBMISSIONS_ENV, "3")
    take(ledger)                                   # 0 + 1 + 2 <= 3
    e = refused(ledger, "NO_COMPLETION_CAPACITY_RESERVED", coid=COID2)
    assert e.detail["total_spent"] == 1 and e.detail["reserve_required"] == 2
    assert not AUTH.claim_held(
        AUTH.btc5m_sid(AUTH.experiment_id(), COID2), ledger)


def test_a_risk_reducing_order_is_not_stranded_by_the_flat_total(env, ledger):
    """The rule `total_backstop_binds` states for the 15-minute strategy."""
    env.setenv(AUTH.MAX_SUBMISSIONS_ENV, "3")
    take(ledger)
    take(ledger, coid=COID2, klass="REPLACE")
    take(ledger, coid=COID3, klass="REPLACE")
    late = "b5m-" + "e" * 24
    refused(ledger, "SUBMISSION_BUDGET_EXHAUSTED", coid=late, klass="REPLACE")
    rec = take(ledger, coid=late, klass="COMPLETE", reducing=True)
    assert rec["reducing"] is True and rec["klass"] == "COMPLETE"


def test_the_class_budgets_bind_the_5m_strategy_too(env, ledger):
    env.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    env.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "4")
    first = take(ledger)
    assert first["class_ticket"] == AUTH.class_sid(
        AUTH.experiment_id(), "OPEN", 0)
    refused(ledger, "OPEN_BUDGET_EXHAUSTED", coid=COID2)
    done = take(ledger, coid=COID2, klass="COMPLETE", reducing=True)
    assert done["class_ticket"].endswith(".COMPLETE.0")


def test_a_refused_class_does_not_spend_the_orders_own_claim(env, ledger):
    env.setenv(AUTH.CLASS_BUDGET_ENV["OPEN"], "1")
    env.setenv(AUTH.CLASS_BUDGET_ENV["COMPLETE"], "4")
    take(ledger)
    refused(ledger, "OPEN_BUDGET_EXHAUSTED", coid=COID2)
    assert not AUTH.claim_held(
        AUTH.btc5m_sid(AUTH.experiment_id(), COID2), ledger)


def test_a_new_approval_is_a_new_budget_and_only_the_owner_can_issue_one(
        env, ledger):
    env.setenv(AUTH.MAX_SUBMISSIONS_ENV, "3")
    first = take(ledger)
    refused(ledger, "NO_COMPLETION_CAPACITY_RESERVED", coid=COID2)
    # a restart re-reads the same nonce and counts the same claims
    refused(ledger, "NO_COMPLETION_CAPACITY_RESERVED", coid=COID2)
    # a NEW document (a new nonce) is a new namespace ...
    env.setenv(CA.APPROVAL_ENV, json.dumps(document(nonce="m" * 32)))
    second = take(ledger, coid=COID2)
    assert second["experiment"] != first["experiment"]
    # ... and the executor cannot write one
    with pytest.raises(CA.ApprovalReadUnavailable):
        CA.EnvApprovalStore().put(CA.APPROVAL_KEY, b"{}")


# ====================================================================
# 5. ONE STRATEGY'S APPROVAL IS NEVER SPENT BY THE OTHER
# ====================================================================

def test_a_5m_approval_authorizes_no_15m_order(env, ledger, tmp_path):
    """M15 opens at the same instant as M1, so the window START passes;
    only the series can tell the two strategies apart."""
    assert CA._slug_start_utc(M15) == CA._slug_start_utc(M1) == W1
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_submission(M15, {"q": "1"}, path=ledger)
    assert e.value.reason == "SUBMISSION_SERIES_NOT_AUTHORIZED"
    with pytest.raises(AUTH.Refused) as e:
        AUTH.claim_accumulation_leg(M15, 0, "UP", {"q": "1"}, path=ledger)
    assert e.value.reason == "SUBMISSION_SERIES_NOT_AUTHORIZED"
    out = CA.reserve_window(M15, now=NOW)
    assert out["ok"] is False and out["reason"] == "CAMPAIGN_SERIES_MISMATCH"
    assert out["spent"] is False
    dec = CA.campaign_decision(M15, now=NOW, reserve=True)
    assert dec["present"] is True and dec["ok"] is False
    assert dec["reason"] == "CAMPAIGN_SERIES_MISMATCH"
    assert locks(ledger) == [] and windows_spent(tmp_path) == []


def test_a_15m_approval_authorizes_no_5m_order(env, ledger, tmp_path):
    env.setenv(CA.APPROVAL_ENV, json.dumps(document(
        series=CA.BTC15M_SERIES, starts=(W1,))))
    refused(ledger, "CAMPAIGN_SERIES_MISMATCH")
    assert verdict(ledger)["ok"] is False
    # and the 15-minute claim under its own approval is unaffected
    rec = AUTH.claim_submission(M15, {"q": "1"}, path=ledger)
    assert rec["market"] == M15 and rec["sid"].startswith("sub1.")
    assert windows_spent(tmp_path) == []      # claim_submission reserves none


def test_the_15m_claim_without_any_approval_is_unchanged(env, ledger):
    """Absent approval still leaves the pre-campaign single-window handshake
    alone for the 15-minute strategy -- and still refuses the 5-minute one."""
    env.delenv(CA.APPROVAL_ENV)
    rec = AUTH.claim_submission(M15, {"q": "1"}, path=ledger)
    assert rec["sid"] == "sub1.B5M-PILOT-1"
    refused(ledger, "BTC5M_APPROVAL_ABSENT")


# ====================================================================
# 6. THE VERDICT IS READ-ONLY AND IS THE CLAIM'S OWN DECISION
# ====================================================================

def test_the_verdict_writes_nothing(env, ledger, tmp_path):
    for _ in range(3):
        v = verdict(ledger)
        assert v["ok"] is True and v["strategy"] == "BTC5M"
    assert v["window_start_utc"] == W1.isoformat()
    assert v["window_end_utc"] == (W1 + dt.timedelta(seconds=300)).isoformat()
    assert locks(ledger) == [] and windows_spent(tmp_path) == []
    assert not (tmp_path / "auth").exists()


def test_the_verdict_never_carries_the_nonce(env, ledger):
    v = verdict(ledger)
    text = json.dumps(v, default=str)
    assert "n" * 32 not in text and "nonce" not in text
    assert v["document_sha256"] and len(v["document_sha256"]) == 64


@pytest.mark.parametrize("reason,change", ENV_REFUSALS[:9],
                         ids=[r for r, _c in ENV_REFUSALS[:9]])
def test_the_verdict_and_the_claim_agree(env, ledger, reason, change):
    change(env)
    v = verdict(ledger)
    assert v["ok"] is False and v["reason"] == reason
    refused(ledger, reason)


# ====================================================================
# 7. THE SHARED LOCKS ARE READ AS THE 15-MINUTE SERVICE READS THEM
# ====================================================================

def test_the_lock_names_are_the_runners(env):
    from src import l5_pmus_live_runner as R
    assert AUTH.LIVE_HALT_ENV == R.LIVE_HALT_ENV
    assert AUTH.SHADOW_ONLY_ENV == R.SHADOW_ONLY_ENV


@pytest.mark.parametrize("value", [None, "", " ", "0", "1", "false", "no",
                                   "off", "HALT", "0 "])
def test_both_locks_read_exactly_as_the_runner_reads_them(env, value):
    from src import l5_pmus_live_runner as R
    for name in (AUTH.LIVE_HALT_ENV, AUTH.SHADOW_ONLY_ENV):
        if value is None:
            env.delenv(name, raising=False)
        else:
            env.setenv(name, value)
    assert AUTH.operator_halt_engaged() == R.operator_halted()
    assert AUTH.shadow_lock_engaged() == R.shadow_only()


# ====================================================================
# 8. NOTHING HERE CAN WRITE ITS OWN AUTHORIZATION
# ====================================================================

def test_the_claim_module_never_writes_the_approval_or_the_locks_it_reads():
    src = pathlib.Path(AUTH.__file__).read_text()
    i = src.index("THE BTC 5-MINUTE CLAIM")
    j = src.index("def scoped_limits")
    block = src[i:j]
    for forbidden in ("os.environ[", "putenv", "setenv", ".unlink(",
                      "os.remove", "shutil.rmtree"):
        assert forbidden not in block, forbidden


class TestTheLaneMayOnlyTradeItsOwnSeries:
    """The three series gates were BTC-only literals and the fleet's ETH,
    SOL and XRP lanes were refused every order -- observed live
    2026-11-16T12:02Z as AUTHORIZATION:BTC5M_MARKET_NOT_IN_SERIES.

    The property these tests pin is NOT "ETH is allowed". It is "a lane
    resolves exactly one series, and anything it cannot resolve refuses".
    """

    def test_unset_is_btc5m_so_a_single_executor_is_unchanged(self, monkeypatch):
        monkeypatch.delenv(AUTH.LANE_STRATEGY_ENV, raising=False)
        assert AUTH.lane_series() == AUTH.BTC5M_SERIES
        #: compare BEHAVIOUR, not pattern text: re.escape escapes the
        #: hyphen, so the strings differ while the language does not.
        r = AUTH.lane_coid_re()
        assert r.fullmatch("b5m-" + "a" * 24)
        assert AUTH.BTC5M_COID_RE.fullmatch("b5m-" + "a" * 24)
        assert not r.fullmatch("e5m-" + "a" * 24)
        assert not r.fullmatch("b5m-" + "a" * 23)

    def test_each_lane_resolves_its_own_series_and_id_shape(self, monkeypatch):
        want = {"BTC5M": ("cpc-btc-updown-5m-", "b5m-"),
                "ETH5M": ("cpceth-eth-updown-5m-", "e5m-"),
                "SOL5M": ("cpcsol-sol-updown-5m-", "s5m-"),
                "XRP5M": ("cpcxrp-xrp-updown-5m-", "x5m-")}
        for strat, (series, coid) in want.items():
            monkeypatch.setenv(AUTH.LANE_STRATEGY_ENV, strat)
            assert AUTH.lane_series() == series
            r = AUTH.lane_coid_re()
            assert r.fullmatch(coid + "a" * 24)
            assert not r.fullmatch("b5m-" + "a" * 24) or strat == "BTC5M"

    def test_no_two_lanes_resolve_the_same_series_or_id_shape(self, monkeypatch):
        seen_s, seen_c = set(), set()
        for strat in ("BTC5M", "ETH5M", "SOL5M", "XRP5M"):
            monkeypatch.setenv(AUTH.LANE_STRATEGY_ENV, strat)
            seen_s.add(AUTH.lane_series())
            seen_c.add(AUTH.lane_coid_re().pattern)
        assert len(seen_s) == 4 and len(seen_c) == 4

    @pytest.mark.parametrize("strat", ["BTC15M", "NOPE", "eth", "ETH15M", "  "])
    def test_anything_unresolvable_or_not_five_minute_refuses(
            self, monkeypatch, strat):
        """The permissive answer here would let a lane submit into a
        series nobody approved, so an unknown or wrong-horizon strategy
        resolves to None rather than falling back to BTC."""
        monkeypatch.setenv(AUTH.LANE_STRATEGY_ENV, strat)
        if strat.strip() == "":
            assert AUTH.lane_series() == AUTH.BTC5M_SERIES   # blank is unset
            return
        #: "eth" upper-cases to ETH, which is not a strategy name
        #: (ETH5M is), so it is unresolvable like any other typo.
        assert AUTH.lane_series() is None
        assert AUTH.lane_coid_re() is None
