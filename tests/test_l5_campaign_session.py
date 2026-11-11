"""The reusable campaign authorization: lifecycle, restarts, expiry, halts.

WHAT THIS IS FOR. Every live test so far has needed four Railway variables
edited by hand, and a redeploy, per window. This layer lets ONE approval
cover a bounded run -- and the whole risk of that convenience is that a
budget stops binding. So the tests below are mostly about the ways it could
stop binding: a restart resetting the counter, an expired document falling
back to the old one-window grant, an unreadable ledger reading as "nothing
spent", a cap widened after approval.

Nothing here arms anything or touches a venue.
"""
import datetime as dt
import json
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_live_authorization as AUTH

SERIES = "cpc-btc-updown-15m-"
W1 = SERIES + "2026-10-20-1600z"
W2 = SERIES + "2026-10-20-1615z"
W3 = SERIES + "2026-10-20-1630z"
W4 = SERIES + "2026-10-20-1645z"
W5 = SERIES + "2026-10-20-1700z"
NOW = dt.datetime(2026, 10, 20, 15, 0, tzinfo=dt.timezone.utc)


class Store:
    """The approval document's storage. Absent by default -- default NO."""

    def __init__(self, doc=None, raises=None):
        self.doc = doc
        self.raises = raises

    def get(self, key):
        if self.raises:
            raise self.raises
        if self.doc is None:
            return None
        return json.dumps(self.doc).encode()


def _bound(base, over):
    """Derive the opening bound from the expiry the caller ACTUALLY ends up
    with, one window inside it.

    Several tests here override `expires_utc`, and the settlement grace must
    outlast the last permitted open -- while `eff_expires` is itself clamped
    to issued + MAX_CAMPAIGN_S. A fixed bound would sit outside a shortened
    expiry and be refused as inconsistent, which is a fixture artefact and
    not the property under test. An explicit `last_open_utc` from the caller
    always wins.
    """
    if "last_open_utc" in over:
        return base
    iss = dt.datetime.fromisoformat(base["issued_utc"])
    exp = dt.datetime.fromisoformat(base["expires_utc"])
    eff = min(exp, iss + dt.timedelta(seconds=14400))
    # THE EFFECTIVE EXPIRY ITSELF, not one window inside it. This file's
    # properties are about the WINDOW BUDGET and the expiry: W5 sits exactly
    # on the expiry, so insetting the bound would refuse it as out of
    # schedule and mask the exhaustion and expiry rules under test. Equal to
    # the effective expiry is permitted -- the bound is checked with `>`.
    base["last_open_utc"] = eff.isoformat()
    return base


def doc(**over):
    base = {"approved": True, "nonce": "nonce-abcdef123456",
            "series": SERIES,
            "issued_utc": (NOW - dt.timedelta(minutes=5)).isoformat(),
            "expires_utc": (NOW + dt.timedelta(hours=2)).isoformat(),
            "max_windows": 4,
            "max_total_exposure_usd": "5",
            "max_daily_loss_usd": "5"}
    base.update(over)
    return _bound(base, over)


def gate(d=None, raises=None):
    return CA.ApprovalGate(Store(d, raises), now_fn=lambda: NOW)


def led(tmp_path):
    return CA.WindowLedger(str(tmp_path))


# ================================================================ default NO

def test_with_NO_document_the_layer_says_nothing_and_preserves_the_old_path():
    """The single-window handshake must survive untouched. A campaign layer
    that replaced it would be a widening disguised as a feature."""
    out = CA.authorize_window(gate(None), W1, now=NOW)
    assert out["present"] is False
    assert out["ok"] is False
    # and the arming boundary treats an absent campaign as silence
    assert AUTH.authorize_arming.__doc__


def test_an_UNREADABLE_document_is_present_and_REFUSING_never_absent(tmp_path):
    """A storage fault must not read as "no campaign, carry on"."""
    g = gate(raises=CA.ApprovalReadUnavailable("boom"))
    out = CA.authorize_window(g, W1, ledger=led(tmp_path), now=NOW)
    assert out["present"] is True and out["ok"] is False
    assert out["reason"] == "APPROVAL_UNREADABLE"


@pytest.mark.parametrize("over,why", [
    ({"approved": False}, "approval_not_asserted"),
    ({"nonce": "short"}, "approval_nonce_too_short"),
    ({"series": "cpceth-eth-updown-15m-"}, "approval_series_not_allowed"),
    ({"max_windows": 0}, "approval_max_windows_below_one"),
    ({"max_total_exposure_usd": "0"}, "approval_limits_not_positive"),
])
def test_a_malformed_document_is_present_and_refusing(over, why, tmp_path):
    out = CA.authorize_window(gate(doc(**over)), W1, ledger=led(tmp_path),
                              now=NOW)
    assert out["present"] is True and out["ok"] is False
    assert out["reason"] == "APPROVAL_NOT_VALID"
    assert out["detail"]["why"] == why


# ================================================== ceilings cannot be widened

def test_the_code_ceilings_clamp_a_greedier_document(tmp_path):
    g = gate(doc(max_windows=99, max_total_exposure_usd="500",
                 max_daily_loss_usd="500",
                 expires_utc=(NOW + dt.timedelta(days=7)).isoformat()))
    out = CA.authorize_window(g, W1, ledger=led(tmp_path), now=NOW)
    assert out["ok"] is True
    s = out["scope"]
    assert s["max_windows"] == CA.MAX_WINDOWS == 8
    assert D(s["max_total_exposure_usd"]) == CA.MAX_TOTAL_EXPOSURE_USD == D("20")
    assert D(s["max_daily_loss_usd"]) == CA.MAX_DAILY_LOSS_USD == D("5")
    assert s["clamped"]["max_windows"] is True
    assert s["clamped"]["max_total_exposure_usd"] is True
    assert s["clamped"]["expires_utc"] is True
    # the 4-hour campaign ceiling, not the document's 7 days
    assert CA._ts(s["expires_utc"]) <= NOW + dt.timedelta(
        seconds=CA.MAX_CAMPAIGN_S)


def test_a_series_outside_the_document_is_refused(tmp_path):
    out = CA.authorize_window(gate(doc()), "cpcsol-sol-updown-15m-2026-10-20-1600z",
                              ledger=led(tmp_path), now=NOW)
    assert out["ok"] is False and out["reason"] == "CAMPAIGN_SERIES_MISMATCH"


# ============================================ windows, and spending them once

def test_four_windows_are_granted_in_order_then_exhausted(tmp_path):
    g, L = gate(doc()), led(tmp_path)
    for i, w in enumerate((W1, W2, W3, W4), start=1):
        out = CA.authorize_window(g, w, ledger=L, now=NOW)
        assert out["ok"] is True, (w, out)
        assert out["window"]["index"] == i
        assert out["window"]["reserved_now"] is True
        assert out["window"]["windows_remaining"] == 4 - i
    out = CA.authorize_window(g, W5, ledger=L, now=NOW)
    assert out["ok"] is False
    assert out["reason"] == "CAMPAIGN_WINDOWS_EXHAUSTED"


def test_RE_ARMING_THE_SAME_WINDOW_does_not_spend_a_second(tmp_path):
    """A restart inside a window already reserved must resume it. Spending
    again would make a crash loop eat the whole budget."""
    g, L = gate(doc()), led(tmp_path)
    first = CA.authorize_window(g, W1, ledger=L, now=NOW)
    again = CA.authorize_window(g, W1, ledger=L, now=NOW)
    assert first["window"]["reserved_now"] is True
    assert again["window"]["reserved_now"] is False
    assert again["ok"] is True
    assert again["window"]["index"] == 1
    assert again["window"]["windows_spent"] == 1


def test_consumption_SURVIVES_A_RESTART_so_redeploying_cannot_reset_it(
        tmp_path):
    """THE DEFECT THIS EXISTS TO PREVENT. A counter in memory is reset by
    every redeploy, and this service redeploys whenever a variable changes --
    so a four-window approval would authorize four windows per restart,
    without limit."""
    g = gate(doc())
    for w in (W1, W2):
        assert CA.authorize_window(g, w, ledger=led(tmp_path),
                                   now=NOW)["ok"] is True

    # a brand new process: new gate, new ledger object, SAME volume
    g2, L2 = gate(doc()), led(tmp_path)
    assert L2.read(CA.nonce_sid("nonce-abcdef123456"))["windows"] == [W1, W2]
    out = CA.authorize_window(g2, W3, ledger=L2, now=NOW)
    assert out["ok"] is True and out["window"]["index"] == 3
    assert CA.authorize_window(g2, W4, ledger=L2, now=NOW)["ok"] is True
    assert CA.authorize_window(g2, W5, ledger=L2, now=NOW)["reason"] == (
        "CAMPAIGN_WINDOWS_EXHAUSTED")


def test_a_NEW_nonce_gets_its_own_budget_and_cannot_see_the_old_one(tmp_path):
    L = led(tmp_path)
    for w in (W1, W2, W3, W4):
        CA.authorize_window(gate(doc()), w, ledger=L, now=NOW)
    fresh = gate(doc(nonce="nonce-zzzzzzz99999"))
    out = CA.authorize_window(fresh, W5, ledger=L, now=NOW)
    assert out["ok"] is True and out["window"]["index"] == 1


def test_an_UNREADABLE_ledger_refuses_rather_than_reading_as_nothing_spent(
        tmp_path):
    L = led(tmp_path)
    CA.authorize_window(gate(doc()), W1, ledger=L, now=NOW)
    p = L._path(CA.nonce_sid("nonce-abcdef123456"))
    with open(p, "w") as fh:
        fh.write("{not json")
    out = CA.authorize_window(gate(doc()), W2, ledger=L, now=NOW)
    assert out["ok"] is False
    assert out["reason"] == "WINDOW_LEDGER_UNREADABLE"


def test_a_malformed_ledger_shape_is_also_refused(tmp_path):
    L = led(tmp_path)
    CA.authorize_window(gate(doc()), W1, ledger=L, now=NOW)
    p = L._path(CA.nonce_sid("nonce-abcdef123456"))
    with open(p, "w") as fh:
        json.dump({"windows": "not-a-list"}, fh)
    assert CA.authorize_window(gate(doc()), W2, ledger=L,
                               now=NOW)["reason"] == "WINDOW_LEDGER_UNREADABLE"


# ====================================================================== expiry

def test_an_EXPIRED_campaign_refuses(tmp_path):
    g = gate(doc(expires_utc=(NOW - dt.timedelta(minutes=1)).isoformat()))
    out = CA.authorize_window(g, W1, ledger=led(tmp_path), now=NOW)
    assert out["ok"] is False
    assert out["reason"] in ("APPROVAL_NOT_VALID", "CAMPAIGN_EXPIRED")


def test_a_document_issued_LONG_AGO_is_stale_before_any_other_test(tmp_path):
    """The staleness guard fires first, and that ordering is right: a
    document issued five hours ago should be refused for being old rather
    than for anything about its contents."""
    g = gate(doc(issued_utc=(NOW - dt.timedelta(hours=5)).isoformat(),
                 expires_utc=(NOW + dt.timedelta(hours=5)).isoformat()))
    out = CA.authorize_window(g, W1, ledger=led(tmp_path), now=NOW)
    assert out["ok"] is False
    assert out["detail"]["why"] == "approval_stale"


def test_the_FOUR_HOUR_ceiling_CLAMPS_a_longer_document(tmp_path):
    """A fresh document asking for five hours is granted four. The ceiling
    is a code constant and cannot be widened by what the document asks."""
    g = gate(doc(expires_utc=(NOW + dt.timedelta(hours=5)).isoformat()))
    out = CA.authorize_window(g, W1, ledger=led(tmp_path), now=NOW)
    assert out["ok"] is True
    s = out["scope"]
    assert s["clamped"]["expires_utc"] is True
    issued = CA._ts(s["issued_utc"])
    assert CA._ts(s["expires_utc"]) == issued + dt.timedelta(
        seconds=CA.MAX_CAMPAIGN_S)


def test_expiry_mid_campaign_stops_the_NEXT_window(tmp_path):
    """REWRITTEN 2026-11-04 to stop relying on a window that STARTS AFTER
    THE CAMPAIGN EXPIRES.

    It used to grant W1 at 16:00 under a campaign expiring 15:10, because
    only `now` was checked against the expiry and nothing checked the
    window's own start. That is the 08:00Z incident in miniature, so the
    fixture is corrected rather than the rule: the expiry now contains W1,
    and the property under test -- expiry mid-campaign stops the NEXT
    window -- is exercised by moving the CLOCK past it, which is what
    "mid-campaign" actually means.
    """
    L = led(tmp_path)
    expires = (NOW + dt.timedelta(hours=1, minutes=5)).isoformat()
    live = gate(doc(expires_utc=expires))
    assert CA.authorize_window(live, W1, ledger=L, now=NOW)["ok"] is True
    later = NOW + dt.timedelta(hours=1, minutes=30)
    # MAX_CAMPAIGN_S staleness, as the production `campaign_gate()` factory
    # uses, so the EXPIRY is what refuses rather than the 1-hour default
    # staleness preempting it at an advanced clock.
    g2 = CA.ApprovalGate(Store(doc(expires_utc=expires)),
                         max_age_s=CA.MAX_CAMPAIGN_S,
                         now_fn=lambda: later)
    out = CA.authorize_window(g2, W2, ledger=L, now=later)
    assert out["ok"] is False
    # the DOCUMENT is past its expiry by now, so `state()` refuses it before
    # authorize_window reaches its own CAMPAIGN_EXPIRED branch. Either way
    # the next window is stopped, which is the property.
    assert out["reason"] == "APPROVAL_NOT_VALID", out
    assert out["detail"]["why"] == "approval_expired", out
    # the window already spent is NOT handed back
    assert L.read(CA.nonce_sid("nonce-abcdef123456"))["windows"] == [W1]


# ================================= the arming boundary refuses, never falls back

class Limits:
    def __init__(self, exp="5", loss="5"):
        self.max_total_exposure_usd = D(exp)
        self.daily_realized_loss_halt_usd = D(loss)


def _soon(hours=1):
    """A campaign expiry on the REAL clock.

    `authorize_arming` reads `dt.datetime.now()` for its own deadline
    checks, while `NOW` above is this file's fixed clock for the approval
    GATE's tests. Mixing them made the scope look expired.
    """
    return (dt.datetime.now(dt.timezone.utc)
            + dt.timedelta(hours=hours)).isoformat()


def arm(campaign, limits=None, **over):
    kw = dict(market=W1, preflight={"preflight": "PASSED"}, store=None,
              halted=False, reconciled=True, halt_gate_present=True,
              limits=limits or Limits(), shadow_locked=False,
              campaign=campaign, require_durable=False)
    kw.update(over)
    return AUTH.authorize_arming(**kw)


def test_a_present_but_refusing_campaign_REFUSES_ARMING_by_its_own_reason(
        monkeypatch):
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "s-1")
    with pytest.raises(AUTH.ArmingRefusal) as e:
        arm({"present": True, "ok": False,
             "reason": "CAMPAIGN_WINDOWS_EXHAUSTED", "detail": {}})
    assert e.value.reason == "CAMPAIGN_WINDOWS_EXHAUSTED"


def test_an_ABSENT_campaign_does_not_itself_refuse(monkeypatch):
    """It must say nothing, so the existing single-window handshake stands."""
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "s-1")
    try:
        arm({"present": False, "ok": False, "reason": "approval_absent"})
    except AUTH.ArmingRefusal as exc:
        # it may well refuse for an UNRELATED later reason; it must not be
        # the campaign that refuses
        assert not str(exc.reason).startswith("CAMPAIGN")
        assert str(exc.reason) != "APPROVAL_UNREADABLE"


def test_limits_WIDER_than_the_approval_invalidate_it(monkeypatch):
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "s-1")
    ok_campaign = {"present": True, "ok": True,
                   "scope": {"max_total_exposure_usd": "5",
                             "max_daily_loss_usd": "5",
                             "nonce_sid": CA.nonce_sid("session-test-nonce"),
                             "series": SERIES, "max_windows": 4,
                             "expires_utc": _soon()}}
    with pytest.raises(AUTH.ArmingRefusal) as e:
        arm(ok_campaign, limits=Limits(exp="7"))
    assert e.value.reason == "CAMPAIGN_LIMITS_WIDER_THAN_APPROVED"
    with pytest.raises(AUTH.ArmingRefusal) as e2:
        arm(ok_campaign, limits=Limits(loss="9"))
    assert e2.value.reason == "CAMPAIGN_LIMITS_WIDER_THAN_APPROVED"


def test_limits_that_cannot_be_READ_are_not_assumed_inside_the_approval(
        monkeypatch):
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "s-1")
    with pytest.raises(AUTH.ArmingRefusal) as e:
        arm({"present": True, "ok": True,
             "scope": {"max_total_exposure_usd": "5",
                       "max_daily_loss_usd": "5",
                       "nonce_sid": CA.nonce_sid("session-test-nonce"),
                       "series": SERIES, "max_windows": 4,
                       "expires_utc": _soon()}}, limits=None)
    assert e.value.reason in ("CAMPAIGN_RUNNING_LIMITS_UNREADABLE",
                              "CAPITAL_SCOPE_UNKNOWN")


def test_the_SHADOW_LOCK_still_outranks_every_campaign(monkeypatch):
    monkeypatch.setenv("PMUS_Q1_LIVE", "YES")
    monkeypatch.setenv("PMUS_Q1_SESSION_ID", "s-1")
    monkeypatch.setenv("PMUS_Q1_ARM", "s-1")
    with pytest.raises(AUTH.ArmingRefusal) as e:
        arm({"present": True, "ok": True,
             "scope": {"max_total_exposure_usd": "5",
                       "max_daily_loss_usd": "5",
                       "nonce_sid": CA.nonce_sid("session-test-nonce"),
                       "series": SERIES, "max_windows": 4,
                       "expires_utc": _soon()}}, shadow_locked=True)
    assert e.value.reason == "SHADOW_ONLY_LOCK"


def test_a_valid_campaign_AUTHORIZES_without_the_legacy_handshake(
        monkeypatch):
    """SUPERSEDES "a campaign does not substitute for the owner handshake".

    That earlier rule was wrong about its own security model, and it cost
    exactly what this layer exists to abolish. The campaign document's
    property is the one the Q1 handshake has and the one `HttpHaltStore`
    states for the halt latch: THE OWNER HOLDS AN OBJECT THE EXECUTOR CANNOT
    WRITE -- `EnvApprovalStore.put` refuses by design. Requiring both bought
    no additional evidence, because both come from the same owner by the
    same means; and because the durable mandate is keyed on
    PMUS_Q1_SESSION_ID, a campaign whose scope differed from the recorded
    mandate refused every window until the owner rotated their session id
    and arm token BY HAND. One approval, many windows, was impossible.

    So a valid campaign authorizes campaign windows on its own. Everything
    that made it strict is untouched and tested elsewhere in this file: the
    code ceilings, the intersection, staleness, expiry, the series
    allowlist, the durable per-nonce window ledger, and the running limits
    having to sit inside the approval.
    """
    monkeypatch.delenv("PMUS_Q1_LIVE", raising=False)
    monkeypatch.delenv("PMUS_Q1_ARM", raising=False)
    monkeypatch.delenv("PMUS_Q1_SESSION_ID", raising=False)
    try:
        arm({"present": True, "ok": True,
             "scope": {"max_total_exposure_usd": "5",
                       "max_daily_loss_usd": "5",
                       "nonce_sid": CA.nonce_sid("session-test-nonce"),
                       "series": SERIES, "max_windows": 4,
                       "expires_utc": _soon()}})
    except AUTH.ArmingRefusal as exc:
        # it may refuse for a LATER, unrelated reason -- this helper supplies
        # no venue window and no real ledger. What must not happen is a
        # refusal about the AUTHORIZATION itself.
        assert str(exc.reason) != "OWNER_AUTHORIZATION_ABSENT", (
            "a valid campaign must authorize on its own")
        assert not str(exc.reason).startswith("CAMPAIGN"), exc.reason
        assert not str(exc.reason).startswith("MANDATE"), exc.reason


def test_with_NEITHER_a_campaign_NOR_the_handshake_it_still_refuses(
        monkeypatch):
    """The relaxation is narrow: one of the two is still required."""
    monkeypatch.delenv("PMUS_Q1_LIVE", raising=False)
    monkeypatch.delenv("PMUS_Q1_ARM", raising=False)
    monkeypatch.delenv("PMUS_Q1_SESSION_ID", raising=False)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        arm({"present": False, "ok": False, "reason": "approval_absent"})
    assert e.value.reason == "OWNER_AUTHORIZATION_ABSENT"
    assert "campaign" in e.value.detail["needs_either"]
    assert "legacy_handshake" in e.value.detail["needs_either"]
