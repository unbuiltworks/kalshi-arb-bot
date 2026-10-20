"""One owner approval, many windows, and no PMUS_Q1 rotation.

WHAT THIS REPLACES. The campaign layer was built to end the per-test Railway
ceremony, and then required the legacy `PMUS_Q1_*` handshake on top of every
campaign window anyway. Because the durable MANDATE claim is keyed on
`PMUS_Q1_SESSION_ID`, a campaign whose scope differed from the recorded
mandate refused every window with MANDATE_SCOPE_CHANGED -- so activating one
meant rotating the owner's session id and arm token by hand. That is the
ceremony, back again under another name.

A valid campaign now authorizes campaign windows ON ITS OWN, with its own
durable identity derived from its nonce, and it is not weaker: the owner
still holds an object the executor cannot write. The legacy handshake is
untouched for one-window probes.

The second half of this file pins the two WASTED WINDOWS of 2026-10-20:
1600z, spent by a verification run under the shadow lock, and 1630z, spent
by an arm that MANDATE_SCOPE_CHANGED then refused. Neither could have
traded.

Nothing here reaches a venue.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH
from src import l5_live_execution as LX
from src import l5_pmus_live_runner as R
from src import pmus_auth as Q1

SERIES = "cpc-btc-updown-15m-"
W1 = SERIES + "2026-10-20-1600z"
W2 = SERIES + "2026-10-20-1615z"
W3 = SERIES + "2026-10-20-1630z"
W4 = SERIES + "2026-10-20-1645z"
W5 = SERIES + "2026-10-20-1700z"
NONCE = "campaign-authorizes-nonce-01"


def doc(**over):
    now = dt.datetime.now(dt.timezone.utc)
    base = {"approved": True, "nonce": NONCE, "series": SERIES,
            "issued_utc": (now - dt.timedelta(minutes=1)).isoformat(),
            "expires_utc": (now + dt.timedelta(hours=4)).isoformat(),
            "max_windows": 4, "max_total_exposure_usd": "5",
            "max_daily_loss_usd": "5"}
    base.update(over)
    return base


class _Store:
    def __init__(self, residual=D(0)) -> None:
        self._r = residual

    def orders_uncertain(self):
        return None

    def residual(self, market):
        return self._r


class _Transport:
    def __init__(self, market=W1) -> None:
        self.armed = False
        self.market_slug = market
        self.tick_size = "0.01"
        self.arms = 0

    def arm(self):
        self.arms += 1
        self.armed = True
        return {"armed": True, "market": self.market_slug}


class _Executor:
    def __init__(self) -> None:
        self.limits = LX.SafetyLimits(max_total_exposure_usd=D("5"),
                                      daily_realized_loss_halt_usd=D("5"))
        self.halt_gate = object()


class _Runner:
    """Drives the REAL `try_arm`."""

    try_arm = R.Runner.try_arm
    campaign_window_is_usable = R.Runner.campaign_window_is_usable

    def __init__(self, root, *, market=W1, into_window_s=120.0,
                 residual=D(0)) -> None:
        self.root = str(root)
        self.transport = _Transport(market)
        self.store = _Store(residual)
        self.executor = _Executor()
        self.preflight_status = "PASSED"
        self.reconciled = True
        self.into = float(into_window_s)
        self.now_t = 1_760_000_000.0
        self.win = collections.Counter()
        self.stats = collections.Counter()

    def venue_window_bounds(self):
        s = dt.datetime.fromtimestamp(self.now_t - self.into,
                                      dt.timezone.utc)
        return {"source": "VENUE", "start": s.isoformat(),
                "end": (s + dt.timedelta(seconds=900)).isoformat()}

    def halted(self) -> bool:
        return False

    def reconcile_if_needed(self, now: float) -> None:
        pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Everything the boundary needs EXCEPT an authorization, which is what
    each test supplies. Notably: PMUS_Q1_* is explicitly UNSET."""
    monkeypatch.delenv(Q1.LIVE_ENV, raising=False)
    monkeypatch.delenv(Q1.SID_ENV, raising=False)
    monkeypatch.delenv(Q1.ARM_ENV, raising=False)
    monkeypatch.setenv("L5_SHADOW_ONLY", "0")
    monkeypatch.setenv(AUTH.LEDGER_ENV, str(tmp_path / "auth.jsonl"))
    monkeypatch.setenv(AUTH.OWNER_ROLE_ENV, "test-auth-holder")
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, "test-auth-holder")
    monkeypatch.delenv(AUTH.MARKET_ENV, raising=False)
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.delenv(AUTH.SERIES_ENV, raising=False)
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    for e in AUTH.CLASS_BUDGET_ENV.values():
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setattr(AUTH, "durability_refusal", lambda p: None)
    # the executor must be able to NAME itself; production has these from
    # Railway. The campaign path requires identity to be knowable, not to be
    # unchanged -- see the holder note in authorize_arming.
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "test-deployment")
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc()))
    return tmp_path


# ============== the campaign authorizes, with NO PMUS_Q1 anything


def test_a_valid_campaign_ARMS_without_the_legacy_handshake(env):
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True, dict(r.win)
    assert r.transport.arms == 1


def test_FOUR_WINDOWS_arm_in_a_row_without_touching_PMUS_Q1(env, monkeypatch):
    """The property the owner asked for: approve once, four windows run."""
    for i, w in enumerate((W1, W2, W3, W4), start=1):
        r = _Runner(env, market=w)          # a fresh process each window
        r.try_arm()
        assert r.transport.armed is True, (w, dict(r.win))
        led = CA.WindowLedger(str(env)).read(CA.nonce_sid(NONCE))
        assert len(led["windows"]) == i
    # the fifth is refused by the campaign's own budget, not by a handshake
    r = _Runner(env, market=W5)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_campaign_windows_exhausted"] == 1
    assert Q1.armed() is False, "no owner handshake was ever involved"


def test_with_NEITHER_authorization_arming_refuses_and_names_both(env,
                                                                  monkeypatch):
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_owner_authorization_absent"] == 1


def test_the_LEGACY_handshake_still_authorizes_with_no_campaign(env,
                                                               monkeypatch):
    """One-window probes keep exactly the authorization they had."""
    monkeypatch.delenv(CA.APPROVAL_ENV, raising=False)
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "legacy-session")
    monkeypatch.setenv(Q1.ARM_ENV, "legacy-session")
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True, dict(r.win)


def test_a_PRESENT_BUT_INVALID_campaign_refuses_even_WITH_the_handshake(
        env, monkeypatch):
    """The campaign is not a second chance: present-and-refusing still
    refuses, so an expired campaign cannot fall back to a one-window grant
    just because the legacy variables happen to be set."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc(
        issued_utc=(now - dt.timedelta(minutes=30)).isoformat(),
        expires_utc=(now - dt.timedelta(minutes=1)).isoformat())))
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "legacy-session")
    monkeypatch.setenv(Q1.ARM_ENV, "legacy-session")
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is False


def test_the_SHADOW_LOCK_still_outranks_a_valid_campaign(env, monkeypatch):
    monkeypatch.setenv("L5_SHADOW_ONLY", "1")
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_shadow_only_lock"] == 1


def test_an_absent_HALT_GATE_still_refuses_a_valid_campaign(env):
    r = _Runner(env, market=W1)
    r.executor.halt_gate = None
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_halt_gate_absent"] == 1


def test_an_UNRECONCILED_book_still_refuses_a_valid_campaign(env):
    r = _Runner(env, market=W1)
    r.reconciled = False
    r.try_arm()
    assert r.transport.armed is False


def test_a_market_OUTSIDE_the_campaign_series_is_refused(env):
    r = _Runner(env, market="cpc-eth-updown-15m-2026-10-20-1600z")
    r.try_arm()
    assert r.transport.armed is False


def test_an_EXPIRED_campaign_refuses_at_the_boundary_too(env, monkeypatch):
    """Belt and braces: the campaign layer refuses an expired document, and
    the boundary checks the scope's own expiry independently."""
    scope = {"nonce_sid": "camp1.x", "series": SERIES, "max_windows": 4,
             "max_total_exposure_usd": "5", "max_daily_loss_usd": "5",
             "expires_utc": (dt.datetime.now(dt.timezone.utc)
                             - dt.timedelta(minutes=1)).isoformat()}
    with pytest.raises(AUTH.Refused) as e:
        AUTH.authorize_arming(
            market=W1, preflight="PASSED", store=_Store(), halted=False,
            reconciled=True, halt_gate_present=True,
            limits=_Executor().limits, shadow_locked=False,
            campaign={"present": True, "ok": True, "scope": scope},
            window_bounds=_Runner(env).venue_window_bounds(),
            nominal_window_s=900.0, require_durable=False)
    assert e.value.reason == "CAMPAIGN_EXPIRED"


def test_the_campaign_identity_NEVER_CARRIES_THE_NONCE():
    sid = AUTH.campaign_claim_sid({"nonce_sid": CA.nonce_sid(NONCE)})
    assert sid.startswith("camp.")
    assert NONCE not in sid
    assert CA.nonce_sid(NONCE) not in sid
    # stable, so a restart under the same campaign finds its own claims
    assert sid == AUTH.campaign_claim_sid(
        {"nonce_sid": CA.nonce_sid(NONCE)})
    # and distinct per campaign
    assert sid != AUTH.campaign_claim_sid({"nonce_sid": "camp1.other"})


# ================================ the two wasted windows of 2026-10-20


def test_1600z_a_window_is_NOT_spent_under_the_shadow_lock(env, monkeypatch):
    """1600z. Patch A installed the campaign with the lock deliberately ON
    so the scope could be verified first, and the verification reserved the
    window: `windows_spent: 1, windows_remaining: 3` before anything could
    possibly trade."""
    monkeypatch.setenv("L5_SHADOW_ONLY", "1")
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is False
    assert not (env / CA.CONSUMED_DIR).exists(), (
        "a window was spent while submission was structurally blocked")


def test_1630z_a_window_is_NOT_spent_when_the_MANDATE_refuses(env,
                                                              monkeypatch):
    """1630z. The lock was off and the arm was refused
    MANDATE_SCOPE_CHANGED, yet the window had already been reserved -- the
    reservation happened before the boundary had ruled on anything."""
    def boom(**kw):
        raise AUTH.ArmingRefusal("MANDATE_SCOPE_CHANGED",
                                 {"fields": ["market", "series"]})

    monkeypatch.setattr(AUTH, "authorize_arming", boom)
    r = _Runner(env, market=W3)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_mandate_scope_changed"] == 1
    assert not (env / CA.CONSUMED_DIR).exists(), (
        "a window was spent on an arm the boundary refused")


def test_a_window_PAST_ITS_ENTRY_CUTOFF_is_not_spent(env):
    """Also 1630z: reserved at 16:39 when entry closed at 16:38:59. It could
    not have opened a leg even with every gate green."""
    r = _Runner(env, market=W3, into_window_s=700.0)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_window_not_usable"] == 1
    assert not (env / CA.CONSUMED_DIR).exists()
    worth = r.campaign_window_is_usable(W3)
    assert worth["usable"] is False
    assert worth["reason"] == "PAST_ENTRY_WITH_NO_INVENTORY"


def test_but_a_late_window_WITH_INVENTORY_is_still_worth_spending(env):
    """Completion runs later than entry, because completing removes risk. A
    window where we are still working inventory has work to do."""
    r = _Runner(env, market=W3, into_window_s=700.0, residual=D("2.46"))
    worth = r.campaign_window_is_usable(W3)
    assert worth["usable"] is True
    assert worth["because"] == "INVENTORY_STILL_TO_COMPLETE"


def test_UNREADABLE_window_bounds_are_NOT_usable(env):
    """Fail closed: a window we cannot reason about keeps its budget."""
    r = _Runner(env, market=W3)
    r.venue_window_bounds = lambda: {"source": "VENUE"}
    worth = r.campaign_window_is_usable(W3)
    assert worth["usable"] is False
    assert worth["reason"] == "WINDOW_BOUNDS_UNREADABLE"


def test_a_RESTART_inside_a_reserved_window_does_not_spend_a_second(env):
    """Idempotent per slug, so neither a redeploy nor a crash-restart can
    buy another window.

    The window itself is NOT resumed: `claim_once` on the window key refuses
    the second attempt AUTHORIZATION_ALREADY_USED, because one
    authorization trades once and a restart must not silently pick up a
    window it was part-way through. What matters for the budget is that the
    refusal costs nothing -- the campaign moves on to the next window on its
    own, and the owner is not asked for anything.
    """
    first = _Runner(env, market=W1)
    first.try_arm()
    assert first.transport.armed is True
    for _ in range(3):
        r = _Runner(env, market=W1)
        r.try_arm()
        assert r.transport.armed is False
        assert r.win["arm_refused_authorization_already_used"] == 1
    led = CA.WindowLedger(str(env)).read(CA.nonce_sid(NONCE))
    assert led["windows"] == [W1], "no second window was spent"


def test_the_NEXT_window_arms_normally_after_a_restart(env):
    """So a redeploy costs the window it landed in and nothing more: the
    campaign carries on unattended."""
    _Runner(env, market=W1).try_arm()
    crashed = _Runner(env, market=W1)
    crashed.try_arm()
    assert crashed.transport.armed is False
    nxt = _Runner(env, market=W2)
    nxt.try_arm()
    assert nxt.transport.armed is True, dict(nxt.win)
    assert CA.WindowLedger(str(env)).read(
        CA.nonce_sid(NONCE))["windows"] == [W1, W2]


def test_consumption_SURVIVES_a_restart(env):
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True
    # a brand new ledger object, as a redeployed container has
    assert CA.WindowLedger(str(env)).read(
        CA.nonce_sid(NONCE))["windows"] == [W1]
    r2 = _Runner(env, market=W2)
    r2.try_arm()
    assert CA.WindowLedger(str(env)).read(
        CA.nonce_sid(NONCE))["windows"] == [W1, W2]


# ===== the legacy PMUS_Q1 variables cannot interfere with a campaign
#
# They are still present in production and they must now be INERT for
# campaign windows: neither able to block one, nor able to change the
# identity a campaign's durable claims are keyed on. The second is what
# caused the original failure -- the mandate was keyed on
# PMUS_Q1_SESSION_ID, so the owner had to rotate it by hand.


def test_a_MISMATCHED_legacy_handshake_cannot_BLOCK_a_campaign(env,
                                                               monkeypatch):
    """Q1.armed() is False when the arm token does not equal the session id.
    Under a campaign that must not matter at all."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-bbb")       # deliberately wrong
    assert Q1.armed() is False
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True, dict(r.win)


def test_PMUS_Q1_LIVE_NO_cannot_block_a_campaign(env, monkeypatch):
    monkeypatch.setenv(Q1.LIVE_ENV, "NO")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-aaa")
    assert Q1.armed() is False
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True, dict(r.win)


def test_an_ARMED_legacy_handshake_does_not_change_the_claim_IDENTITY(
        env, monkeypatch):
    """The identity must come from the CAMPAIGN even when the legacy
    handshake is fully armed. Otherwise rotating PMUS_Q1_SESSION_ID
    mid-campaign would orphan the mandate and refuse the remaining windows
    -- the exact failure this architecture removed."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-aaa")
    assert Q1.armed() is True
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is True
    # window one armed under the campaign's identity; now ROTATE the legacy
    # session id, which used to invalidate the recorded mandate
    monkeypatch.setenv(Q1.SID_ENV, "session-zzz")
    monkeypatch.setenv(Q1.ARM_ENV, "session-zzz")
    r2 = _Runner(env, market=W2)
    r2.try_arm()
    assert r2.transport.armed is True, (
        "a legacy session rotation must not disturb a campaign's mandate: "
        "%s" % dict(r2.win))
    assert CA.WindowLedger(str(env)).read(
        CA.nonce_sid(NONCE))["windows"] == [W1, W2]


def test_the_claim_identity_is_the_CAMPAIGNS_not_the_sessions(env,
                                                              monkeypatch):
    """Directly: the sid the boundary uses is derived from the nonce."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-aaa")
    seen = {}
    real = AUTH.claim_once

    def spy(path, sid, payload):
        seen.setdefault("sids", []).append(sid)
        return real(path, sid, payload)

    monkeypatch.setattr(AUTH, "claim_once", spy)
    _Runner(env, market=W1).try_arm()
    expect = AUTH.campaign_claim_sid({"nonce_sid": CA.nonce_sid(NONCE)})
    assert any(s.startswith(expect) for s in seen["sids"]), seen
    assert not any("session-aaa" in s for s in seen["sids"])


def test_an_EXPIRED_document_does_not_fall_back_to_the_legacy_path(
        env, monkeypatch):
    """THE BELT THAT PROTECTS THE NEXT DEPLOYMENT. The document in
    production is exhausted and expiring. Present-and-not-ok must refuse by
    its own name and must NOT hand the armed legacy handshake a fresh
    one-window grant."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc(
        issued_utc=(now - dt.timedelta(hours=5)).isoformat(),
        expires_utc=(now - dt.timedelta(minutes=1)).isoformat())))
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-aaa")
    r = _Runner(env, market=W1)
    r.try_arm()
    assert r.transport.armed is False
    assert not (env / CA.CONSUMED_DIR).exists()


def test_an_EXHAUSTED_document_does_not_fall_back_either(env, monkeypatch):
    """The production state exactly: four of four windows spent."""
    monkeypatch.setenv(Q1.LIVE_ENV, "YES")
    monkeypatch.setenv(Q1.SID_ENV, "session-aaa")
    monkeypatch.setenv(Q1.ARM_ENV, "session-aaa")
    for w in (W1, W2, W3, W4):
        _Runner(env, market=w).try_arm()
    r = _Runner(env, market=W5)
    r.try_arm()
    assert r.transport.armed is False
    assert r.win["arm_refused_campaign_windows_exhausted"] == 1
