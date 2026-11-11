"""The venue-bounded expiry, the immutable mandate scope, and the holder.

Every test here is a REFUSAL or an exact bound. Nothing in this file arms
anything or submits anything: the boundary is exercised directly with a
temporary ledger, exactly as the existing authorization tests do.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib

import pytest

from src import l5_live_authorization as A


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 14, 18, 5, 0, tzinfo=UTC)
START = "2026-10-14T18:00:00Z"
END = "2026-10-14T18:15:00Z"
MARKET = "cpc-btc-updown-15m-2026-10-14-1800z"


def venue_bounds(start: str = START, end: str = END) -> dict:
    return {"source": "VENUE", "start": start, "end": end}


class Limits(object):
    def __init__(self, **kw):
        self.d = {"max_usd_per_market": "5", "max_total_exposure_usd": "5",
                  "daily_realized_loss_halt_usd": "5", "max_live_orders": "1"}
        self.d.update(kw)

    def as_dict(self):
        return dict(self.d)


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "auth.jsonl")


@pytest.fixture
def armed(monkeypatch, tmp_path):
    """The owner token, a declared role, and a verifiable holder identity."""
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "sid-test-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "sid-test-1")
    monkeypatch.setenv("L5_LIVE_AUTH_OWNER_ROLE", "l5-pmus-live")
    monkeypatch.setenv("PMUS_SERVICE_ROLE", "l5-pmus-live")
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-1")
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "rep-1")
    monkeypatch.delenv("L5_LIVE_PROBE_MARKET", raising=False)
    monkeypatch.delenv("L5_LIVE_AUTH_NOT_AFTER", raising=False)
    monkeypatch.delenv("L5_LIVE_AUTH_CLOSE_MARGIN_S", raising=False)
    monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", "1")
    return True


def arm(ledger, **kw):
    kw.setdefault("market", MARKET)
    kw.setdefault("preflight", {"preflight": "PASSED"})
    kw.setdefault("store", _Store())
    kw.setdefault("halted", False)
    kw.setdefault("halt_gate_present", True)
    kw.setdefault("reconciled", True)
    kw.setdefault("limits", Limits())
    kw.setdefault("window_bounds", venue_bounds())
    kw.setdefault("nominal_window_s", 900.0)
    kw.setdefault("now", NOW)
    kw.setdefault("require_durable", False)
    return A.authorize_arming(path=ledger, **kw)


class _Store(object):
    def orders_uncertain(self):
        return None


# ------------------------------------------------------------------ the margin

def test_the_margin_defaults_to_sixty_seconds():
    assert A.close_margin_s() == 60.0


def test_the_margin_can_be_widened(monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_CLOSE_MARGIN_S", "120")
    assert A.close_margin_s() == 120.0


def test_the_margin_cannot_be_narrowed(monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_CLOSE_MARGIN_S", "5")
    assert A.close_margin_s() == 60.0


def test_an_unreadable_margin_is_the_default_not_zero(monkeypatch):
    monkeypatch.setenv("L5_LIVE_AUTH_CLOSE_MARGIN_S", "soon")
    assert A.close_margin_s() == 60.0


# ------------------------------------------------------- the venue-bound expiry

def test_a_full_ttl_is_cut_back_to_the_close_less_the_margin():
    out = A.window_expiry(venue_bounds(), now=NOW, ttl_s=900, nominal_s=900)
    assert out["expires_utc"].startswith("2026-10-14T18:14:00")
    assert out["bounded_by"] == "VENUE_CLOSE"


def test_a_ttl_that_ends_inside_the_window_still_binds():
    out = A.window_expiry(venue_bounds(), now=NOW, ttl_s=120, nominal_s=900)
    assert out["expires_utc"].startswith("2026-10-14T18:07:00")
    assert out["bounded_by"] == "TTL"


def test_the_expiry_never_reaches_the_close():
    for ttl in (60, 300, 900, 36000):
        out = A.window_expiry(venue_bounds(), now=NOW, ttl_s=ttl,
                              nominal_s=900)
        assert out["expires_utc"] < "2026-10-14T18:15:00"


def test_absent_bounds_refuse():
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(None, now=NOW, ttl_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_UNKNOWN"


def test_clock_derived_bounds_refuse():
    b = dict(venue_bounds(), source="CONVENTION")
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(b, now=NOW, ttl_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_UNCONFIRMED"


def test_bounds_with_no_source_refuse():
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry({"start": START, "end": END}, now=NOW, ttl_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_UNCONFIRMED"


def test_unparseable_bounds_refuse():
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(venue_bounds(start="soon"), now=NOW, ttl_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_UNREADABLE"


def test_a_fourteen_day_contract_expiry_is_not_a_window():
    """src/pmus_event_progress.py: PMUS sports endDate is gameStart + 14d."""
    b = venue_bounds(end="2026-10-28T18:00:00Z")
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(b, now=NOW, ttl_s=900, nominal_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_IMPLAUSIBLE"


def test_a_close_before_the_open_refuses():
    b = venue_bounds(start=END, end=START)
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(b, now=NOW, ttl_s=900)
    assert e.value.reason == "WINDOW_BOUNDS_IMPLAUSIBLE"


def test_before_the_open_refuses():
    early = dt.datetime(2026, 10, 14, 17, 59, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(venue_bounds(), now=early, ttl_s=900, nominal_s=900)
    assert e.value.reason == "WINDOW_NOT_OPEN"


def test_inside_the_margin_refuses_rather_than_issuing_a_short_claim():
    late = dt.datetime(2026, 10, 14, 18, 14, 1, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(venue_bounds(), now=late, ttl_s=900, nominal_s=900)
    assert e.value.reason == "WINDOW_CLOSING"


def test_exactly_at_the_margin_refuses():
    at = dt.datetime(2026, 10, 14, 18, 14, 0, tzinfo=UTC)
    with pytest.raises(A.ArmingRefusal) as e:
        A.window_expiry(venue_bounds(), now=at, ttl_s=900, nominal_s=900)
    assert e.value.reason == "WINDOW_CLOSING"


# -------------------------------------------------- the boundary uses the bound

def test_arming_refuses_without_venue_bounds(armed, ledger):
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, window_bounds=None)
    assert e.value.reason == "WINDOW_BOUNDS_UNKNOWN"


def test_arming_leaves_no_claim_when_the_window_refuses(armed, ledger):
    with pytest.raises(A.ArmingRefusal):
        arm(ledger, window_bounds=None)
    assert A.read_claim_payload(ledger, "sid-test-1") is None


def test_the_claim_carries_the_venue_bound_expiry(armed, ledger):
    claim = arm(ledger)
    assert claim["expires_utc"].startswith("2026-10-14T18:14:00")
    assert claim["window"]["bounded_by"] == "VENUE_CLOSE"
    assert claim["window"]["close_margin_s"] == 60.0


def test_still_valid_expires_at_the_bound_not_the_ttl(armed, ledger):
    claim = arm(ledger)
    ok = dt.datetime(2026, 10, 14, 18, 13, 59, tzinfo=UTC)
    gone = dt.datetime(2026, 10, 14, 18, 14, 1, tzinfo=UTC)
    assert A.still_valid(claim, limits=Limits(), now=ok) is None
    assert A.still_valid(claim, limits=Limits(),
                         now=gone) == "AUTHORIZATION_EXPIRED"


def test_still_valid_rejects_the_old_ttl_horizon(armed, ledger):
    """18:20 is inside a 900s-from-arming TTL and outside the window."""
    claim = arm(ledger)
    at = dt.datetime(2026, 10, 14, 18, 20, tzinfo=UTC)
    assert A.still_valid(claim, limits=Limits(),
                         now=at) == "AUTHORIZATION_EXPIRED"


# ------------------------------------------------------- the scope, in the lock

def test_the_scope_and_digest_are_inside_the_exclusive_claim(armed, ledger):
    claim = arm(ledger)
    rec = A.read_claim_payload(ledger, "sid-test-1")
    assert rec["scope_digest"] == claim["scope_digest"]
    assert rec["scope"]["execution_mode"] == "MAKER_ONLY"
    assert rec["scope"]["max_live_orders"] == "1"
    assert rec["scope"]["max_cash_deployed_usd"] == "1"


def test_the_digest_covers_every_scope_field(armed, ledger):
    base = A.mandate_scope(market=MARKET, limits=Limits())
    d0 = A.scope_digest(base)
    for field in A.SCOPE_FIELDS:
        other = dict(base)
        other[field] = (base.get(field) or "") + "x"
        assert A.scope_digest(other) != d0, field


def test_the_claim_file_is_not_modified_afterwards(armed, ledger):
    arm(ledger)
    lock = A._lock_path(ledger, "sid-test-1")
    before = lock.read_bytes()
    mtime = lock.stat().st_mtime_ns
    with pytest.raises(A.Refused):
        arm(ledger)                      # a second claim is refused
    assert lock.read_bytes() == before
    assert lock.stat().st_mtime_ns == mtime


def test_a_scoped_rearm_refuses_a_widened_envelope(armed, ledger):
    claim = arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder, limits=Limits(max_usd_per_market="50"))
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_usd_per_market" in e.value.detail["fields"]
    assert claim["scope"]["max_usd_per_market"] == "5"


def test_a_scoped_rearm_refuses_a_tightened_envelope_too(armed, ledger):
    """A tightened mandate is still not the one that was approved."""
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder, limits=Limits(max_usd_per_market="1"))
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"


def test_a_scoped_rearm_refuses_a_loosened_order_count(armed, ledger):
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder, limits=Limits(max_live_orders="8"))
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_live_orders" in e.value.detail["fields"]


def test_a_scoped_rearm_refuses_a_loosened_cash_cap(armed, ledger,
                                                    monkeypatch):
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    monkeypatch.setenv("L5_MAX_CASH_DEPLOYED_USD", "25")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder)
    assert e.value.reason == "MANDATE_SCOPE_CHANGED"
    assert "max_cash_deployed_usd" in e.value.detail["fields"]


def test_an_unchanged_scope_still_scopes(armed, ledger):
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    again = arm(ledger, auth=holder)
    assert again["event"] == "SCOPED"


def test_a_claim_with_no_recorded_scope_cannot_be_scoped(armed, ledger,
                                                         tmp_path):
    lock = A._lock_path(ledger, "sid-test-1")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"event": "CLAIMED", "market": MARKET}))
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder)
    assert e.value.reason == "MANDATE_SCOPE_UNRECORDED"


def test_an_unreadable_claim_refuses(armed, ledger):
    lock = A._lock_path(ledger, "sid-test-1")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("{not json")
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder)
    assert e.value.reason == "MANDATE_SCOPE_UNREADABLE"


# ------------------------------------------------------------------ the holder

def test_the_holder_is_recorded_in_the_claim(armed, ledger):
    arm(ledger)
    rec = A.read_claim_payload(ledger, "sid-test-1")
    assert rec["holder"]["deployment_id"] == "dep-1"
    assert rec["holder"]["pid"] == os.getpid()
    assert rec["holder"]["boot_id"]


def test_a_different_deployment_is_refused(armed, ledger, monkeypatch):
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-2")
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder)
    assert e.value.reason == "MANDATE_HOLDER_MISMATCH"
    assert "deployment_id" in e.value.detail["differs"]


def test_a_restart_is_refused_rather_than_resumed(armed, ledger):
    """A restarted executor has a new pid: it may not continue the claim."""
    arm(ledger)
    rec = A.read_claim_payload(ledger, "sid-test-1")
    restarted = dict(rec["holder"], pid=rec["holder"]["pid"] + 1)
    bad = A.holder_refusal(rec["holder"], restarted)
    assert bad["reason"] == "MANDATE_HOLDER_MISMATCH"
    assert "pid" in bad["detail"]["differs"]


def test_a_second_process_in_the_same_container_is_refused(armed, ledger):
    arm(ledger)
    rec = A.read_claim_payload(ledger, "sid-test-1")
    peer = dict(rec["holder"], pid=rec["holder"]["pid"] + 7)
    assert A.holder_refusal(rec["holder"],
                            peer)["reason"] == "MANDATE_HOLDER_MISMATCH"


def test_an_executor_that_cannot_identify_its_deployment_refuses(armed,
                                                                 ledger):
    rec = {"deployment_id": "dep-1", "replica_id": "rep-1",
           "service_role": "l5-pmus-live", "boot_id": "b", "pid": 1}
    bad = A.holder_refusal(rec, {"deployment_id": None, "boot_id": "b",
                                 "pid": 1})
    assert bad["reason"] == "DEPLOYMENT_IDENTITY_UNKNOWN"


def test_an_unknown_boot_id_refuses(armed, ledger):
    rec = {"deployment_id": "dep-1", "boot_id": "b", "pid": 1}
    bad = A.holder_refusal(rec, {"deployment_id": "dep-1", "boot_id": None,
                                 "pid": 1})
    assert bad["reason"] == "DEPLOYMENT_IDENTITY_UNKNOWN"


def test_a_claim_with_no_holder_refuses(armed, ledger):
    assert A.holder_refusal({})["reason"] == "MANDATE_HOLDER_UNREADABLE"


def test_the_matching_holder_passes(armed, ledger):
    arm(ledger)
    rec = A.read_claim_payload(ledger, "sid-test-1")
    assert A.holder_refusal(rec["holder"]) is None


# --------------------------------------------------- Stage A is still one window

def test_the_default_mandate_is_one_window(armed, ledger):
    claim = arm(ledger)
    assert claim["scope"]["max_windows"] == "1"


def test_a_second_claim_is_still_refused(armed, ledger):
    arm(ledger)
    with pytest.raises(A.Refused) as e:
        arm(ledger)
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"


def test_another_market_is_still_refused(armed, ledger):
    arm(ledger)
    holder = A.Authorization(path=ledger)
    holder.sid, holder.market, holder.claimed = "sid-test-1", MARKET, True
    with pytest.raises(A.ArmingRefusal) as e:
        arm(ledger, auth=holder,
            market="cpc-btc-updown-15m-2026-10-14-1815z")
    assert e.value.reason == "AUTHORIZATION_BOUND_TO_MARKET"


def test_claim_once_is_unchanged_in_mechanism(tmp_path):
    """O_EXCL, one winner, the loser refused, the file not rewritten."""
    path = str(tmp_path / "l.jsonl")
    A.claim_once(path, "s1", {"a": 1})
    lock = A._lock_path(path, "s1")
    before = lock.read_bytes()
    with pytest.raises(A.Refused) as e:
        A.claim_once(path, "s1", {"a": 2})
    assert e.value.reason == "AUTHORIZATION_ALREADY_USED"
    assert lock.read_bytes() == before


# ================================================================== the runner
#
# The unbound-method pattern of tests/test_l5_ported_safety_fixes.py: the
# method under test is called against a stub, so no socket, no venue and no
# transport are constructed.

class _Transport(object):
    def __init__(self):
        self.armed = True
        self.disarmed = None
        self.market_slug = MARKET

    def disarm(self, reason):
        self.armed = False
        self.disarmed = reason


class _RunnerStub(object):
    def __init__(self, claim, limits=None):
        self.transport = _Transport()
        self.arm_claim = claim
        self.store = object()
        self.executor = type("Ex", (), {"limits": limits or Limits()})()
        self.ws = 1792000800
        self.window_bounds = {}


def expire(stub, now=1000.0):
    from src import l5_pmus_live_runner as R
    return R.Runner.maybe_expire_authorization(stub, now)


def bounds_of(stub):
    from src import l5_pmus_live_runner as R
    return R.Runner.venue_window_bounds(stub)


def test_an_unexpired_claim_cancels_nothing(armed, ledger, monkeypatch):
    claim = arm(ledger)
    stub = _RunnerStub(claim)
    monkeypatch.setattr(A, "still_valid", lambda c, **kw: None)
    assert expire(stub) is None
    assert stub.transport.armed is True


def test_an_expired_claim_cancels_by_venue_id_and_disarms(armed, ledger,
                                                          monkeypatch):
    from src import l5_live_execution as LX
    from src import l5_pmus_live_runner as R
    claim = arm(ledger)
    stub = _RunnerStub(claim)
    seen = {}

    def fake_deadman(store, transport, *, reason):
        seen["reason"] = reason
        return {"cancelled": ["venue-1"], "still_open": [],
                "unverified": [], "no_venue_id": []}

    monkeypatch.setattr(LX, "deadman", fake_deadman)
    monkeypatch.setattr(A, "still_valid",
                        lambda c, **kw: "AUTHORIZATION_EXPIRED")
    rec = expire(stub)
    assert rec["cancelled"] == ["venue-1"]
    assert seen["reason"].startswith("authorization_expired:")
    assert stub.transport.armed is False
    assert stub.transport.disarmed == "authorization_expired"
    assert stub.arm_claim is None


def test_an_unverified_cancel_still_disarms(armed, ledger, monkeypatch):
    """`unverified` is the dangerous case; deadman marks the store UNCERTAIN
    and this path must not report success or stay armed."""
    from src import l5_live_execution as LX
    claim = arm(ledger)
    stub = _RunnerStub(claim)
    monkeypatch.setattr(LX, "deadman", lambda s, t, *, reason: {
        "cancelled": [], "still_open": [], "unverified": ["venue-9"],
        "no_venue_id": []})
    monkeypatch.setattr(A, "still_valid", lambda c, **kw: "AUTHORIZATION_EXPIRED")
    rec = expire(stub)
    assert rec["unverified"] == ["venue-9"]
    assert stub.transport.armed is False


def test_nothing_happens_when_not_armed(armed, ledger, monkeypatch):
    claim = arm(ledger)
    stub = _RunnerStub(claim)
    stub.transport.armed = False
    monkeypatch.setattr(A, "still_valid", lambda c, **kw: "AUTHORIZATION_EXPIRED")
    assert expire(stub) is None


def test_nothing_happens_without_a_claim(armed, ledger):
    stub = _RunnerStub(None)
    assert expire(stub) is None


def test_the_venue_confirmation_is_cached_per_window(monkeypatch):
    from src import l5_pmus_live_runner as R
    calls = []

    def fake(ws):
        calls.append(ws)
        return {"aligned": True, "pmus_start_utc": START}

    monkeypatch.setattr(R.OBS, "verify_alignment", fake)
    stub = _RunnerStub(None)
    first = bounds_of(stub)
    second = bounds_of(stub)
    assert first == second
    assert first["source"] == "VENUE"
    assert len(calls) == 1


def test_an_unconfirmed_window_offers_no_bounds(monkeypatch):
    from src import l5_pmus_live_runner as R
    monkeypatch.setattr(R.OBS, "verify_alignment",
                        lambda ws: {"aligned": False, "why": "not listed"})
    stub = _RunnerStub(None)
    assert bounds_of(stub) is None


def test_an_unconfirmed_window_is_not_retried_into_confirmation(monkeypatch):
    from src import l5_pmus_live_runner as R
    calls = []

    def fake(ws):
        calls.append(ws)
        return {"aligned": False, "why": "not listed"}

    monkeypatch.setattr(R.OBS, "verify_alignment", fake)
    stub = _RunnerStub(None)
    assert bounds_of(stub) is None
    assert bounds_of(stub) is None
    assert len(calls) == 1


def test_a_raising_confirmation_offers_no_bounds(monkeypatch):
    from src import l5_pmus_live_runner as R

    def boom(ws):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(R.OBS, "verify_alignment", boom)
    stub = _RunnerStub(None)
    assert bounds_of(stub) is None


def test_the_venue_bounds_are_the_series_window(monkeypatch):
    from src import l5_pmus_live_runner as R
    monkeypatch.setattr(R.OBS, "verify_alignment",
                        lambda ws: {"aligned": True, "pmus_start_utc": START})
    stub = _RunnerStub(None)
    b = bounds_of(stub)
    s = dt.datetime.fromisoformat(b["start"])
    e = dt.datetime.fromisoformat(b["end"])
    assert (e - s).total_seconds() == R.AB.WINDOW_S == 900
