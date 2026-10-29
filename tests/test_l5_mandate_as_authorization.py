"""The mandate as an ALTERNATIVE authorization source, behind an opt-in.

WHAT THIS CHANGES AND WHAT IT MUST NOT. Before this, a registered trusted
key made a signed mandate an ADDITIONAL requirement on top of a campaign
document or the legacy handshake -- so turning the signature on made the
per-campaign manual install worse, not better. With
`L5_MANDATE_AS_AUTHORIZATION` set, a verified mandate authorizes windows on
its own.

THREE OF THESE TESTS EXIST BECAUSE AN ADVERSARIAL REVIEW FOUND WHAT THE
FIRST VERSION OF THIS FILE DID NOT:

  * the signed `max_windows_per_day` was computed and thrown away -- the
    budget that actually bound was an executor-writable env var. The first
    test for it asserted only that the helper had been CALLED, not that its
    value bound. `test_the_signed_rate_is_the_budget_that_binds` now arms
    successfully and reads `window_budget` off the claim.
  * the claim namespace was derived from the environment WITHOUT verifying,
    so rotating `seq` in an unsigned document minted fresh per-day budgets.
  * that same derivation took over whenever the campaign suffix was briefly
    empty, re-namespacing a campaign- or Q1-authorized session mid-window.

NO TEST HERE REACHED A SUCCESSFUL ARMING before; the whole downstream was
unexercised. `armed_ok` fixes that.

ON THE KEYPAIR IN THIS FILE. Generated inside the test process, used only
here, and it is NOT and must never be the owner's production signing key.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import pytest
from cryptography.hazmat.primitives import serialization as _S
from cryptography.hazmat.primitives.asymmetric import ed25519

from src import l5_live_authorization as AUTH
from src import l5_mandate_signature as MS

SERIES = "cpc-btc-updown-15m-"
ENVIRONMENT = "production"
ROLE = "l5-pmus-live"
KEY_ID = "test-key-not-the-owners"
MARKET = "cpc-btc-updown-15m-2026-10-24-2200z"
MARKET2 = "cpc-btc-updown-15m-2026-10-24-2215z"


def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=_S.Encoding.Raw, format=_S.PublicFormat.Raw).hex()
    return priv, pub


def _payload(*, seq=1, exposure="5", daily="5", mode="MAKER_ONLY",
             per_day=8, subs_per_day=56, hours_valid=3,
             mandate_id="test-001"):
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "schema": MS.SCHEMA, "kind": "standing",
        "mandate_id": mandate_id, "seq": seq,
        "venue": "PMUS", "strategy": "l5-two-sided-btc15m",
        "environment": ENVIRONMENT, "service_role": ROLE, "series": SERIES,
        "not_before": (now - dt.timedelta(hours=1)).isoformat(),
        "not_after": (now + dt.timedelta(hours=hours_valid)).isoformat(),
        "allowed_actions": ["OPEN", "COMPLETE", "CANCEL"],
        "execution_mode": mode,
        "max_windows_per_day": per_day,
        "max_submissions_per_day": subs_per_day,
        "capital": {"max_cash_deployed_usd": "5",
                    "max_total_exposure_usd": exposure,
                    "campaign_budget_usd": "5"},
        "loss_limits": {"daily_realized_loss_halt_usd": daily,
                        "max_cumulative_loss_usd": "20"},
        "residual_cap_contracts": 3,
        "promotion": {"min_settled_windows": 300,
                      "min_expectancy_usd": "0.05",
                      "oos_evidence_id": "btc15m-oos-20261023",
                      "requires_bounded_live_validation": True},
    }


def _sign(priv, payload, key_id=KEY_ID):
    sig = priv.sign(MS.canonical_bytes(payload)).hex()
    return json.dumps({"payload": payload, "sig": sig, "key_id": key_id})


class _Limits:
    def __init__(self, exposure="5", daily="5"):
        self.max_usd_per_market = AUTH.D("5")
        self.max_total_exposure_usd = AUTH.D(exposure)
        self.daily_realized_loss_halt_usd = AUTH.D(daily)

    def as_dict(self):
        return {"max_usd_per_market": self.max_usd_per_market,
                "max_total_exposure_usd": self.max_total_exposure_usd,
                "daily_realized_loss_halt_usd":
                    self.daily_realized_loss_halt_usd}


class _Store:
    state = {}

    def orders_uncertain(self):
        return None

    def live_orders(self):
        return {}

    def committed(self):
        return {}


class _FakeStores:
    """The watchdog's two documents, served in-process. No sockets."""

    def __init__(self, floor=0, revoked=(), raises=None):
        self._floor, self._revoked, self._raises = floor, revoked, raises

    def floor_store(self, url, **kw):
        outer = self

        class _F:
            def floor(self, key_id, **_kw):
                if outer._raises == "floor":
                    raise MS.MandateUnavailable("floor document absent")
                return outer._floor
        return _F()

    def revocation_store(self, url, **kw):
        outer = self

        class _R:
            def revoked(self, **_kw):
                if outer._raises == "revoked":
                    raise MS.MandateUnavailable("revocation document absent")
                return frozenset(outer._revoked)
        return _R()


def _serve(monkeypatch, **kw):
    st = _FakeStores(**kw)
    monkeypatch.setattr(MS, "HttpSeqFloorStore", st.floor_store)
    monkeypatch.setattr(MS, "HttpRevocationStore", st.revocation_store)


@pytest.fixture(autouse=True)
def _clear_active_suffix():
    """The verified namespace is PROCESS STATE. Leaking it between tests
    would make one test's arming silently namespace another's claims."""
    AUTH._set_active_mandate_suffix(None)
    yield
    AUTH._set_active_mandate_suffix(None)


@pytest.fixture
def armed_env(monkeypatch, tmp_path):
    priv, pub = _keypair()
    monkeypatch.setitem(MS.MANDATE_PUBKEYS, KEY_ID, pub)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", ENVIRONMENT)
    monkeypatch.setenv(AUTH.SERVICE_ROLE_ENV, ROLE)
    monkeypatch.setenv(AUTH.SERIES_ENV, SERIES)
    monkeypatch.setenv("L5_LIVE_ROOT", str(tmp_path))
    monkeypatch.setenv(MS.STORE_URL_ENV, "http://watchdog.internal:8080")
    _serve(monkeypatch)
    return priv


def _arm(**kw):
    """Refusal-asserting call: these tests assert WHICH reason."""
    base = dict(market=MARKET, preflight=None, store=None, halted=False,
                reconciled=True, halt_gate_present=True, unresolved=(),
                limits=_Limits(), shadow_locked=False, series=SERIES,
                max_windows=8, campaign=None, path=None,
                require_durable=False)
    base.update(kw)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        AUTH.authorize_arming(**base)
    return e.value


def _reason(exc):
    return str(getattr(exc, "reason", None) or getattr(exc, "args", [""])[0])


# ===================================================================
#  A SUCCESSFUL ARMING. Everything below the authorization step was
#  unexercised until this existed.
# ===================================================================
@pytest.fixture
def armed_ok(armed_env, monkeypatch, tmp_path):
    """Returns `arm(**kw) -> claim`, a real successful mandate arming."""
    now = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV,
                       (now + dt.timedelta(hours=2)).isoformat())
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "40")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    # A STANDING MANDATE NOW TAKES THE CAMPAIGN'S HOLDER RULE, which names
    # the deployment instead of comparing pids. A deployment that cannot
    # identify itself refuses DEPLOYMENT_IDENTITY_UNKNOWN -- correct, and
    # the reason these must be set: production has them, a bare test
    # process does not.
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep-test-1")
    monkeypatch.setenv("RAILWAY_REPLICA_ID", "rep-test-1")
    ledger = str(tmp_path / "ledger.jsonl")

    def arm(market=MARKET, when=None, **kw):
        t = when or dt.datetime.now(dt.timezone.utc)
        base = dict(
            market=market, preflight={"preflight": "PASSED"}, store=_Store(),
            halted=False, reconciled=True, halt_gate_present=True,
            unresolved=(), limits=_Limits(), shadow_locked=False,
            series=SERIES, max_windows=8, campaign=None, path=ledger,
            require_durable=False, now=t,
            window_bounds={"source": "VENUE",
                           "start": (t - dt.timedelta(minutes=2)).isoformat(),
                           "end": (t + dt.timedelta(minutes=13)).isoformat()})
        base.update(kw)
        return AUTH.authorize_arming(**base)

    arm.priv = armed_env
    arm.ledger = ledger
    return arm


#: A GRANTED WINDOW is `WINDOW` on the arm that spends it and `CLAIMED` on
#: an arm that RESUMES one already recorded. Both mean "you may trade this
#: window"; only a raised ArmingRefusal means you may not. Asserting the
#: literal string was a bug in the first version of these tests.
GRANTED = ("WINDOW", "CLAIMED")

#: TWO GATES REFUSE A SPENT DAY, and either is correct. The
#: `DailyActionLedger` pre-check runs FIRST and refuses
#: DAILY_WINDOW_BUDGET_SPENT before any work is done; the older
#: sid-prefix count behind it refuses MANDATE_WINDOW_BUDGET_SPENT and
#: remains as a second line of defence. Asserting one name would make the
#: test brittle about which layer caught it rather than about the budget
#: being enforced.
DAY_SPENT = ("DAILY_WINDOW_BUDGET_SPENT", "MANDATE_WINDOW_BUDGET_SPENT")


def test_the_fixture_really_arms(armed_ok, monkeypatch):
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_ok.priv, _payload()))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    claim = armed_ok()
    assert claim["event"] == "WINDOW"
    assert claim["session_id"].startswith("mand.")


# --------- THE REGRESSION THE FIRST TEST SUITE MISSED ENTIRELY ---------
def test_the_signed_rate_is_the_budget_that_binds(armed_ok, monkeypatch):
    """An env var the executor can write MUST NOT raise the owner's signed
    per-day window rate. The previous version of this code computed the
    signed rate and discarded it; `window_budget` read 50 against a
    signature of 2."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    monkeypatch.setenv(AUTH.MAX_WINDOWS_ENV, "50")
    claim = armed_ok()
    assert claim["window_budget"] == 2, claim["window_budget"]
    assert claim["scope"]["max_windows"] == "2"


def test_an_unset_env_defers_to_the_signature(armed_ok, monkeypatch):
    """The env default of 1 would otherwise cap an 8-window mandate at one."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=8)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    assert armed_ok()["window_budget"] == 8


def test_a_tighter_env_still_binds(armed_ok, monkeypatch):
    """Intersection, not substitution: the env may only tighten."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=8)))
    monkeypatch.setenv(AUTH.MAX_WINDOWS_ENV, "3")
    assert armed_ok()["window_budget"] == 3


# ------------------------- 8 windows/day, durably -------------------------
def test_the_per_day_window_budget_is_enforced_from_the_volume(
        armed_ok, monkeypatch):
    """Arm distinct markets until the signed rate is spent. The budget is
    counted from lock files, so this is the durable path."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    armed_ok(market=MARKET)
    armed_ok(market=MARKET2)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market="cpc-btc-updown-15m-2026-10-24-2230z")
    assert _reason(e.value) in DAY_SPENT


def test_duplicate_arming_of_one_market_resumes_not_respends(
        armed_ok, monkeypatch):
    """Re-arming the same slug is the same window. Two arms of one market
    must not consume two of the day's eight."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    assert armed_ok(market=MARKET)["event"] in GRANTED
    # the SAME window is single-use: a duplicate arm is refused rather than
    # granted twice, by the same O_EXCL create that refuses a second claim.
    with pytest.raises(AUTH.Refused) as e:
        armed_ok(market=MARKET)
    assert "ALREADY_USED" in str(e.value)
    # and it consumed exactly one of the day's two, so the other remains
    assert armed_ok(market=MARKET2)["event"] in GRANTED


def test_a_restart_cannot_buy_another_window(armed_ok, monkeypatch):
    """A different pid/boot re-arming must be refused by BUDGET, not
    granted. The claim files on the volume are what enforce it."""
    # per_day=2 rather than 1: at a budget of exactly one the code takes
    # the legacy single-window claim, which is keyed on the session rather
    # than the market, and the refusal would be ALREADY_USED instead of the
    # budget test this is about.
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    armed_ok(market=MARKET)
    armed_ok(market=MARKET2)                       # the day is now spent
    _real = os.getpid()                 # capture BEFORE patching, or the
    monkeypatch.setattr(os, "getpid", lambda: _real + 1)   # lambda recurses
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market="cpc-btc-updown-15m-2026-10-24-2230z")
    assert _reason(e.value) in DAY_SPENT


def test_a_restart_does_not_brick_the_rest_of_the_day(armed_ok, monkeypatch):
    """A standing mandate spans process lifetimes. Under the legacy pid
    comparison a respawn was refused MANDATE_HOLDER_MISMATCH and the rest of
    the UTC day was unreachable without a new owner-signed `seq` or waiting
    for midnight. It takes the campaign's deployment-named rule instead, so
    the day's remaining windows survive a restart."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=4)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    armed_ok(market=MARKET)
    _real = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: _real + 7)
    assert armed_ok(market=MARKET2)["event"] in GRANTED


def test_a_deployment_that_cannot_name_itself_refuses(armed_ok, monkeypatch):
    """The carve-out names the DEPLOYMENT instead of the pid, so an executor
    that cannot identify its own deployment is refused rather than trusted."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=4)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    armed_ok(market=MARKET)
    monkeypatch.delenv("RAILWAY_DEPLOYMENT_ID", raising=False)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market=MARKET2)
    assert _reason(e.value) == "DEPLOYMENT_IDENTITY_UNKNOWN"


def test_day_rollover_opens_a_fresh_window_budget(armed_ok, monkeypatch):
    """A per-day rate must roll at midnight -- that is what makes a mandate
    standing rather than a campaign to be reissued."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2,
                                                     hours_valid=48)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    d1 = dt.datetime.now(dt.timezone.utc).replace(
        hour=23, minute=50, second=0, microsecond=0)
    d2 = d1 + dt.timedelta(minutes=20)        # the next UTC day
    M3 = "cpc-btc-updown-15m-2026-10-24-2230z"
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV,
                       (d2 + dt.timedelta(hours=2)).isoformat())
    a = armed_ok(market=MARKET, when=d1)
    armed_ok(market=MARKET2, when=d1)         # day 1's two are now spent
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market=M3, when=d1)
    assert _reason(e.value) in DAY_SPENT
    b = armed_ok(market=M3, when=d2)          # day 2 starts fresh
    assert a["session_id"] != b["session_id"]
    assert b["event"] in GRANTED


def test_a_new_seq_gets_its_own_day_budget(armed_ok, monkeypatch):
    """Mandate replacement. Two grants must not share a budget, and the old
    one is refused by the watchdog floor once raised."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=1, seq=1)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    a = armed_ok(market=MARKET)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=1, seq=2)))
    b = armed_ok(market=MARKET2)
    assert a["session_id"] != b["session_id"]


def test_a_failed_arming_does_not_consume_a_window(armed_ok, monkeypatch):
    """THE OWNER'S REQUIREMENT. An arm that refuses after the authorization
    step must leave the day's allowance intact."""
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=1)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    with pytest.raises(AUTH.ArmingRefusal):
        armed_ok(market=MARKET, preflight=None)       # fails on preflight
    with pytest.raises(AUTH.ArmingRefusal):
        armed_ok(market=MARKET, halted=True)          # fails on the halt
    # the single window of the day is still there
    assert armed_ok(market=MARKET)["event"] in GRANTED


def test_revocation_after_arming_refuses_the_next_window(
        armed_ok, monkeypatch):
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=4)))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    armed_ok(market=MARKET)
    _serve(monkeypatch, revoked=("test-001", "test-001#1"))
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market=MARKET2)
    assert _reason(e.value).startswith("MANDATE_")


# ===================================================================
#  default off / baseline parity
# ===================================================================
def test_the_flag_is_off_by_default_and_unreadable_values_are_off(monkeypatch):
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    assert AUTH.mandate_as_authorization_enabled() is False
    for junk in ("", " ", "0", "false", "no", "off", "maybe", "2"):
        monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, junk)
        assert AUTH.mandate_as_authorization_enabled() is False, junk


def test_the_flag_turns_on_only_for_explicit_truthy_values(monkeypatch):
    for yes in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, yes)
        assert AUTH.mandate_as_authorization_enabled() is True, yes


def test_the_repo_still_registers_no_trusted_key():
    assert MS.MANDATE_PUBKEYS == {}


def test_with_the_flag_off_a_valid_mandate_does_not_authorize(
        armed_env, monkeypatch):
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) == "OWNER_AUTHORIZATION_ABSENT"


def test_with_the_flag_off_experiment_id_ignores_the_mandate(
        armed_env, monkeypatch):
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert AUTH.experiment_id() == "exp-1"


def test_with_the_flag_on_a_valid_mandate_authorizes(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "40")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) != "OWNER_AUTHORIZATION_ABSENT"


# ===================================================================
#  the claim namespace: VERIFIED-ONLY, and never a campaign's
# ===================================================================
def test_an_unsigned_document_cannot_mint_a_namespace(monkeypatch):
    """THE HOLE A REVIEW FOUND. The namespace used to be derived from the
    environment without verifying, so rotating `seq` in an unsigned document
    minted a fresh per-day submission budget on demand -- with NO registered
    key."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    for seq in (1, 2, 3):
        monkeypatch.setenv(MS.MANDATE_ENV, json.dumps(
            {"key_id": "k", "payload": {"mandate_id": "m-9", "seq": seq}}))
        assert AUTH.mandate_claim_suffix() == ""
        assert AUTH.experiment_id() == "exp-1"


def test_an_empty_campaign_suffix_does_not_fall_back_to_a_mandate(
        monkeypatch):
    """THE SECOND HOLE. `campaign_claim_suffix()` returns "" on an expired or
    momentarily unreadable campaign. That used to switch a campaign- or
    Q1-authorized session onto the mandate namespace, which matches none of
    its claim files -- a fresh budget mid-window, with no re-arming."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setattr(AUTH, "campaign_claim_suffix", lambda: "")
    # nothing verified a mandate, so there is no namespace to fall back to
    assert AUTH.experiment_id() == "exp-1"


def test_the_namespace_appears_only_after_a_verified_arming(
        armed_ok, monkeypatch):
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_ok.priv, _payload()))
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    assert AUTH.experiment_id() == "exp-1"
    armed_ok()
    eid = AUTH.experiment_id()
    assert eid.startswith("exp-1.mand.") and eid.endswith(
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))


def test_a_campaign_keeps_precedence_in_the_claim_namespace(monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    AUTH._set_active_mandate_suffix("mand.deadbeefdeadbeef.20261029")
    monkeypatch.setattr(AUTH, "campaign_claim_suffix", lambda: "camp.abc")
    assert AUTH.experiment_id() == "exp-1.camp.abc"


def test_a_stale_day_yields_no_suffix_so_more_is_counted(monkeypatch):
    """A session armed at 23:50 keeps yesterday's namespace. Handing that
    out after midnight would count yesterday's claims against today's
    budget -- an UNDERCOUNT. It yields "" instead, which falls back to the
    bare id and counts MORE."""
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    AUTH._set_active_mandate_suffix("mand.deadbeefdeadbeef.20261029")
    today = dt.datetime(2026, 10, 29, 12, 0, tzinfo=dt.timezone.utc)
    tomorrow = dt.datetime(2026, 10, 30, 0, 1, tzinfo=dt.timezone.utc)
    assert AUTH.mandate_claim_suffix(today).endswith(".20261029")
    assert AUTH.mandate_claim_suffix(tomorrow) == ""


def test_the_identity_is_day_scoped_and_separates_seq_and_key():
    a = AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261029")
    b = AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261030")
    c = AUTH.mandate_claim_sid("m-1", 2, KEY_ID, day="20261029")
    d = AUTH.mandate_claim_sid("m-1", 1, "other-key", day="20261029")
    assert len({a, b, c, d}) == 4
    assert a.endswith(".20261029")


def test_duplicate_derivation_is_idempotent():
    """SIMULTANEOUS WORKERS. Two attempts deriving the identity at the same
    instant must agree, so they contend for ONE O_EXCL lock file rather than
    taking two claims."""
    xs = {AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261029")
          for _ in range(50)}
    assert len(xs) == 1 and xs != {""}


def test_an_unusable_identity_is_empty_not_a_guess():
    assert AUTH.mandate_claim_sid("", 1, "k", day="20261029") == ""
    assert AUTH.mandate_claim_sid("m", None, "k", day="20261029") == ""
    assert AUTH.mandate_claim_sid("m", "x", "k", day="20261029") == ""
    assert AUTH.mandate_claim_sid("m", 1, "k", day="") == ""


# ===================================================================
#  expiry / revocation / watchdog / scope -- all fail closed
# ===================================================================
def test_an_expired_mandate_refuses_by_its_own_name(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    now = dt.datetime.now(dt.timezone.utc)
    p = _payload()
    p["not_before"] = (now - dt.timedelta(days=2)).isoformat()
    p["not_after"] = (now - dt.timedelta(hours=1)).isoformat()
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, p))
    r = _reason(_arm())
    assert r.startswith("MANDATE_") and r != "OWNER_AUTHORIZATION_ABSENT"


def test_a_revoked_mandate_refuses_by_its_own_name(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    _serve(monkeypatch, revoked=("test-001", "test-001#1"))
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    r = _reason(_arm())
    assert r.startswith("MANDATE_") and r != "OWNER_AUTHORIZATION_ABSENT"


def test_a_rolled_back_seq_refuses(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    _serve(monkeypatch, floor=9)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload(seq=2)))
    assert _reason(_arm()) == "MANDATE_REPLAYED_OR_ROLLED_BACK"


@pytest.mark.parametrize("which,expected", [
    ("floor", "MANDATE_SEQ_FLOOR_UNAVAILABLE"),
    ("revoked", "MANDATE_REVOCATION_UNAVAILABLE"),
])
def test_an_unreachable_watchdog_fails_closed(armed_env, monkeypatch,
                                              which, expected):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    _serve(monkeypatch, raises=which)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) == expected


def test_an_unconfigured_store_url_fails_closed(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.delenv(MS.STORE_URL_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) == "MANDATE_STORE_UNCONFIGURED"


def test_a_missing_halt_latch_fails_closed(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm(halt_gate_present=False)) == \
        "MANDATE_REVOCATION_UNAVAILABLE"


def test_running_limits_wider_than_signed_refuse(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_env, _payload(exposure="2")))
    assert _reason(_arm(limits=_Limits(exposure="20"))) == \
        "MANDATE_LIMITS_WIDER_THAN_SIGNED"


def test_a_tampered_payload_refuses(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    doc = json.loads(_sign(armed_env, _payload()))
    doc["payload"]["max_windows_per_day"] = 999
    monkeypatch.setenv(MS.MANDATE_ENV, json.dumps(doc))
    assert _reason(_arm()) == "MANDATE_SIGNATURE_INVALID"


def test_a_non_maker_mandate_refuses(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_env, _payload(mode="TAKER")))
    r = _reason(_arm())
    assert r.startswith("MANDATE_") and r != "OWNER_AUTHORIZATION_ABSENT", r


# ------------------------------- the rate helper
def test_the_signed_rate_helper_validates_and_refuses():
    assert AUTH.mandate_signed_window_rate({"max_windows_per_day": 8}) == 8
    for bad in (None, 0, -1, True, False, "8", 8.0, {}):
        with pytest.raises(AUTH.ArmingRefusal) as e:
            AUTH.mandate_signed_window_rate({"max_windows_per_day": bad})
        assert _reason(e.value) == "MANDATE_WINDOW_RATE_UNREADABLE"
    with pytest.raises(AUTH.ArmingRefusal):
        AUTH.mandate_signed_window_rate({})


# ------------------------- the signed submission rate
def test_a_backstop_wider_than_the_signed_rate_refuses(
        armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "500")
    assert _reason(_arm()) == "MANDATE_SUBMISSION_BACKSTOP_WIDER_THAN_SIGNED"


def test_no_backstop_at_all_refuses(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    assert _reason(_arm()) == "MANDATE_SUBMISSION_BACKSTOP_ABSENT"


# ===================================================================
#  the campaign path is untouched
# ===================================================================
def test_an_invalid_mandate_still_blocks_a_valid_campaign(
        armed_env, monkeypatch):
    """NO RELAXATION. With a campaign in force the mandate stays REQUIRED
    whether or not the flag is set."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.delenv(MS.MANDATE_ENV, raising=False)
    camp = {"present": True, "ok": True,
            "scope": {"nonce_sid": "n-1", "series": SERIES}}
    assert _reason(_arm(campaign=camp)) == "MANDATE_ABSENT"


# ===================================================================
#  the private key is absent
# ===================================================================
def test_no_private_key_shaped_value_in_a_controlled_environment():
    """Scans a CONTROLLED mapping, not the runner's real environment: a CI
    token that happens to be 64 hex characters would fail this for a reason
    unrelated to the code."""
    env = {"L5_MANDATE_DOC": "{}", "PMUS_API_KEY_ID": "abc",
           "L5_MANDATE_AS_AUTHORIZATION": "1"}
    bad = [k for k, v in env.items()
           if re.fullmatch(r"[0-9a-fA-F]{64}", str(v).strip())
           or "PRIVATE KEY" in str(v)]
    assert not bad, bad


def test_no_runtime_module_imports_the_mandate_signer():
    """NOTE the narrow claim: `pmus_ws_evidence_recorder` does build an
    Ed25519 key, from `PMUS_API_SECRET_KEY`, to sign VENUE requests. That is
    a different key for a different purpose. What must hold is that no
    runtime module can produce a MANDATE signature."""
    import src
    root = os.path.dirname(os.path.dirname(os.path.abspath(src.__file__)))
    for name in os.listdir(os.path.join(root, "src")):
        if name.endswith(".py"):
            body = open(os.path.join(root, "src", name)).read()
            assert "owner_sign_mandate" not in body, name


def test_the_mandate_module_can_verify_but_never_sign():
    body = open(os.path.abspath(MS.__file__.replace(".pyc", ".py"))).read()
    assert "Ed25519PrivateKey" not in body
    assert "private_bytes" not in body
    assert not hasattr(MS, "sign")
    assert hasattr(MS, "verify")


def test_registered_keys_are_public_halves_only():
    for kid, pub in MS.MANDATE_PUBKEYS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", str(pub).lower()), kid


def test_telemetry_never_carries_the_document(armed_env, monkeypatch):
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    v = MS.verify(os.getenv(MS.MANDATE_ENV), environment=ENVIRONMENT,
                  service_role=ROLE, series=SERIES, seq_floor=0,
                  revoked=frozenset())
    t = json.dumps(MS.telemetry(v))
    assert "sig" not in json.loads(t) and "payload" not in json.loads(t)
    for secret in ("not_before", "capital", "promotion"):
        assert secret not in t


# ===================================================================
#  Q1 — renewal and key rotation must NOT reset the day's allowance
# ===================================================================
def test_the_rate_key_ignores_seq_and_key_id(armed_env):
    """A per-day RATE keyed on `seq` would hand a fresh allowance to every
    renewal: sign 8/day, re-sign at noon, and the day permits 16."""
    a = AUTH.mandate_rate_day_key("m-1", day="20261029")
    assert a and a == AUTH.mandate_rate_day_key("m-1", day="20261029")
    # the GRANT identity does separate seq and key; the RATE key must not
    assert AUTH.mandate_claim_sid("m-1", 1, "k1", day="20261029") != \
        AUTH.mandate_claim_sid("m-1", 2, "k1", day="20261029")
    assert AUTH.mandate_rate_day_key("m-1", day="20261029") == \
        AUTH.mandate_rate_day_key("m-1", day="20261029")
    # a different day and a different mandate DO separate
    assert a != AUTH.mandate_rate_day_key("m-1", day="20261030")
    assert a != AUTH.mandate_rate_day_key("m-2", day="20261029")
    assert AUTH.mandate_rate_day_key("", day="20261029") == ""
    assert AUTH.mandate_rate_day_key("m-1", day="") == ""


def test_a_renewal_cannot_reset_the_days_window_allowance(
        armed_ok, monkeypatch):
    """THE OWNER'S QUESTION 1. Spend the day under seq=1, re-sign the SAME
    mandate as seq=2, and the day must still be spent."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2, seq=1)))
    armed_ok(market=MARKET)
    armed_ok(market=MARKET2)                      # day spent under seq 1
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2, seq=2)))
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market="cpc-btc-updown-15m-2026-10-24-2230z")
    assert _reason(e.value) in DAY_SPENT, _reason(e.value)


def test_a_key_rotation_cannot_reset_the_days_window_allowance(
        armed_ok, monkeypatch):
    """Rotating a compromised key is an ordinary act. It must not also hand
    the day a fresh allowance."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    armed_ok(market=MARKET)
    armed_ok(market=MARKET2)
    other, other_pub = _keypair()
    monkeypatch.setitem(MS.MANDATE_PUBKEYS, "rotated-key", other_pub)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(other, _payload(per_day=2, seq=3),
                             key_id="rotated-key"))
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market="cpc-btc-updown-15m-2026-10-24-2230z")
    assert _reason(e.value) in DAY_SPENT, _reason(e.value)


def test_a_renewal_does_not_reset_the_submission_namespace(
        armed_ok, monkeypatch):
    """The submission budget is counted over the claim namespace, so that
    namespace must be renewal-stable too."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=4, seq=1)))
    armed_ok(market=MARKET)
    first = AUTH.experiment_id()
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=4, seq=2)))
    armed_ok(market=MARKET2)
    assert AUTH.experiment_id() == first, "a renewal re-namespaced the budget"


def test_a_different_mandate_id_does_start_a_fresh_day(armed_ok, monkeypatch):
    """Stated plainly rather than hidden: a NEW mandate_id is a new mandate
    and does get its own allowance. That is a deliberate, visible act in a
    signed document, not a side effect of renewing."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=1)))
    armed_ok(market=MARKET)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(
        armed_ok.priv, _payload(per_day=1, mandate_id="test-002")))
    assert armed_ok(market=MARKET2)["event"] in GRANTED


def test_the_window_claim_records_the_daily_rate(armed_ok, monkeypatch):
    """The reservation is auditable from the claim the owner already reads."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=3)))
    claim = armed_ok(market=MARKET)
    assert claim["daily_rate"]["max"] == 3
    assert claim["daily_rate"]["utc_day"]


def test_an_unreadable_rate_ledger_refuses(armed_ok, monkeypatch):
    """"I could not tell" and "nothing used today" differ by the whole day's
    allowance, so an unreadable ledger must refuse."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=2)))
    from src import l5_daily_action_ledger as DAL

    def boom(self, mandate_id, day, prefix):
        raise DAL.DailyLedgerUnavailable("volume unreadable")
    monkeypatch.setattr(DAL.DailyActionLedger, "_list", boom)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market=MARKET)
    assert _reason(e.value) == "DAILY_LEDGER_UNAVAILABLE"


# ===================================================================
#  Q4 — the signed validity must not be cut short by an env deadline
# ===================================================================
def test_a_signed_mandate_does_not_require_an_env_deadline(
        armed_env, monkeypatch):
    """THE OWNER'S QUESTION 4. Requiring L5_LIVE_AUTH_NOT_AFTER forced an
    operator to set it, and step 8 then applies whichever is EARLIER -- so a
    stale 4-hour campaign value would kill a seven-day mandate after four
    hours. The signed `not_after` is verified on every arming, so the env one
    is optional."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "40")
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_env, _payload(per_day=8, hours_valid=168)))
    assert _reason(_arm()) != "MANDATE_HAS_NO_DEADLINE"


def test_a_campaignless_legacy_path_still_requires_the_env_deadline(
        armed_env, monkeypatch):
    """The exemption is for a SIGNED mandate only. Nothing else gained it."""
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    monkeypatch.delenv(AUTH.NOT_AFTER_ENV, raising=False)
    monkeypatch.delenv(MS.MANDATE_ENV, raising=False)
    monkeypatch.setitem(MS.MANDATE_PUBKEYS, KEY_ID, "00" * 32)
    # with no mandate and no campaign the legacy handshake is the only route
    # and its multi-window form still needs an absolute deadline
    assert _reason(_arm()) in ("MANDATE_ABSENT", "OWNER_AUTHORIZATION_ABSENT",
                              "MANDATE_HAS_NO_DEADLINE")


def test_an_env_deadline_can_still_tighten_a_signed_mandate(
        armed_ok, monkeypatch):
    """Optional, but it still binds when set: the EARLIER of the two wins,
    which is the right discipline. This is also the trap to remember -- a
    stale value from a previous campaign silently shortens a mandate.

    Uses the arming fixture because the deadline is checked at step 8, after
    preflight, so a harness with no preflight refuses earlier for an
    unrelated reason."""
    monkeypatch.delenv(AUTH.MAX_WINDOWS_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_ok.priv, _payload(per_day=8,
                                                     hours_valid=168)))
    past = (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(minutes=5)).isoformat()
    monkeypatch.setenv(AUTH.NOT_AFTER_ENV, past)
    with pytest.raises(AUTH.ArmingRefusal) as e:
        armed_ok(market=MARKET)
    assert _reason(e.value) == "AUTHORIZATION_EXPIRED"
