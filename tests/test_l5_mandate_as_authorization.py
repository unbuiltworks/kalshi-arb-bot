"""The mandate as an ALTERNATIVE authorization source, behind an opt-in.

WHAT THIS CHANGES AND WHAT IT MUST NOT. Before this, a registered trusted
key made a signed mandate an ADDITIONAL requirement on top of a campaign
document or the legacy handshake -- so turning the signature on made the
per-campaign manual install worse, not better. With `L5_MANDATE_AS_AUTHORIZATION`
set, a verified mandate authorizes windows on its own.

THE SAFETY ARGUMENT, and every test below is a piece of it:

  * OFF IS THE SHIPPED STATE and off is byte-for-byte the old behaviour.
  * The new route is reachable only where the old code REFUSED
    (`OWNER_AUTHORIZATION_ABSENT`). With a valid campaign the mandate stays
    required exactly as before, so enabling the flag cannot relax a path
    that works today.
  * The signed scope may only TIGHTEN the running limits, never widen them.
  * Per-day budgets ride the EXISTING durable claim counters by putting the
    UTC date in the claim identity -- so a restart cannot replenish them and
    a day rollover cannot leak yesterday's spend into today.

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


def _keypair():
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=_S.Encoding.Raw, format=_S.PublicFormat.Raw).hex()
    return priv, pub


def _payload(*, seq=1, exposure="5", daily="5", mode="MAKER_ONLY",
             per_day=8, hours_valid=3, mandate_id="test-001"):
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
        "max_windows_per_day": per_day, "max_submissions_per_day": 56,
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
        self.max_total_exposure_usd = AUTH.D(exposure)
        self.daily_realized_loss_halt_usd = AUTH.D(daily)


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


# ------------------------------------------------------------ 8. default off
def test_the_flag_is_off_by_default_and_unreadable_values_are_off(monkeypatch):
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    assert AUTH.mandate_as_authorization_enabled() is False
    for junk in ("", " ", "0", "false", "no", "off", "maybe", "2", "TRUE "):
        monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, junk)
        if junk.strip().lower() in ("1", "true", "yes", "on"):
            continue
        assert AUTH.mandate_as_authorization_enabled() is False, junk


def test_the_flag_turns_on_only_for_explicit_truthy_values(monkeypatch):
    for yes in ("1", "true", "TRUE", "Yes", "on"):
        monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, yes)
        assert AUTH.mandate_as_authorization_enabled() is True, yes


def test_the_repo_still_registers_no_trusted_key():
    """Committing a key would make the gate mandatory for every existing
    test and every one-window probe, silently."""
    assert MS.MANDATE_PUBKEYS == {}


# ----------------------------------------------------- 1. baseline parity
def test_with_the_flag_off_a_valid_mandate_does_not_authorize(
        armed_env, monkeypatch):
    """PARITY. The old behaviour is that a mandate is an EXTRA condition and
    never an authorization, so with no campaign and no handshake this still
    refuses as OWNER_AUTHORIZATION_ABSENT."""
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) == "OWNER_AUTHORIZATION_ABSENT"


def test_with_the_flag_off_experiment_id_ignores_the_mandate(
        armed_env, monkeypatch):
    monkeypatch.delenv(AUTH.MANDATE_AS_AUTH_ENV, raising=False)
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert AUTH.experiment_id() == "exp-1"


# ------------------------------------------------- 2. the new route works
def test_with_the_flag_on_a_valid_mandate_authorizes(armed_env, monkeypatch):
    """It still refuses -- there is no preflight or store in this harness --
    but it must get PAST authorization, which the old code could not."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm()) != "OWNER_AUTHORIZATION_ABSENT"


def test_with_the_flag_on_experiment_id_is_namespaced_by_the_mandate(
        armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    eid = AUTH.experiment_id()
    assert eid.startswith("exp-1.mand.") and eid != "exp-1"


# --------------------------------------- 3/4. expiry and revocation by name
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


# --------------------------------------------- 5. stale / absent watchdog
@pytest.mark.parametrize("which,expected", [
    ("floor", "MANDATE_SEQ_FLOOR_UNAVAILABLE"),
    ("revoked", "MANDATE_REVOCATION_UNAVAILABLE"),
])
def test_an_unreachable_watchdog_fails_closed(armed_env, monkeypatch,
                                              which, expected):
    """A dead watchdog must not keep authorizing. This holds WITH the flag
    on, so the new route cannot be used to dodge the watchdog."""
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
    """The latch is the immediate withdrawal path; a standing mandate
    without one has no fast stop and is refused."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    assert _reason(_arm(halt_gate_present=False)) == \
        "MANDATE_REVOCATION_UNAVAILABLE"


# ------------------------------------------------------- 7. changed scope
def test_running_limits_wider_than_signed_refuse(armed_env, monkeypatch):
    """A limit widened after signing invalidates the scope rather than
    being inherited -- the mandate can only TIGHTEN."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_env, _payload(exposure="2")))
    assert _reason(_arm(limits=_Limits(exposure="20"))) == \
        "MANDATE_LIMITS_WIDER_THAN_SIGNED"


def test_a_non_maker_mandate_refuses(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV,
                       _sign(armed_env, _payload(mode="TAKER")))
    r = _reason(_arm())
    # the verifier's binding check rejects it before the gate's own
    # re-assertion gets the chance -- defence in depth, either is a pass.
    # What must NOT happen is that it authorizes, or that it degrades into
    # the generic "no authorization configured".
    assert r.startswith("MANDATE_") and r != "OWNER_AUTHORIZATION_ABSENT", r


def test_a_tampered_payload_refuses(armed_env, monkeypatch):
    """Writing the variable is insufficient: the bytes must verify."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    doc = json.loads(_sign(armed_env, _payload()))
    doc["payload"]["max_windows_per_day"] = 999
    monkeypatch.setenv(MS.MANDATE_ENV, json.dumps(doc))
    assert _reason(_arm()) == "MANDATE_SIGNATURE_INVALID"


# ---------------------------- 3. mandate never overrides a stricter limit
def test_the_signed_window_rate_can_only_tighten():
    """`min`, not substitution. A mandate naming 8/day cannot widen a
    caller permitting 2, and a mandate naming 3 binds a caller permitting
    8."""
    assert AUTH.mandate_window_rate({"max_windows_per_day": 8}, 2) == 2
    assert AUTH.mandate_window_rate({"max_windows_per_day": 3}, 8) == 3
    assert AUTH.mandate_window_rate({"max_windows_per_day": 4}, 4) == 4


@pytest.mark.parametrize("bad", [None, 0, -1, True, False, "8", 8.0, {}])
def test_an_unreadable_window_rate_refuses_rather_than_defaulting(bad):
    """A per-day budget we cannot read is not a budget. `True` is `1` to
    Python, and "one window a day" is not what `true` meant."""
    with pytest.raises(AUTH.ArmingRefusal) as e:
        AUTH.mandate_window_rate({"max_windows_per_day": bad}, 8)
    assert _reason(e.value) == "MANDATE_WINDOW_RATE_UNREADABLE"


def test_a_missing_window_rate_refuses():
    with pytest.raises(AUTH.ArmingRefusal):
        AUTH.mandate_window_rate({}, 8)


def test_the_gate_applies_the_rate_through_that_one_function(
        armed_env, monkeypatch):
    """Pins that the arming path actually routes through the helper above,
    so the unit tests of the rule are tests of the deployed behaviour."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload(per_day=8)))
    seen = {}

    def spy(scope, caller_max):
        seen["scope_rate"] = scope.get("max_windows_per_day")
        seen["caller"] = caller_max
        return min(int(caller_max), int(scope["max_windows_per_day"]))
    monkeypatch.setattr(AUTH, "mandate_window_rate", spy)
    _arm(max_windows=2)
    assert seen == {"scope_rate": 8, "caller": 2}, seen


# ------------------------------------- 1. the campaign path is untouched
def test_an_invalid_mandate_still_blocks_a_valid_campaign(
        armed_env, monkeypatch):
    """NO RELAXATION. With a campaign in force the mandate stays REQUIRED
    whether or not the flag is set, so enabling the flag cannot weaken any
    path that works today."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.delenv(MS.MANDATE_ENV, raising=False)
    camp = {"present": True, "ok": True,
            "scope": {"nonce_sid": "n-1", "series": SERIES}}
    assert _reason(_arm(campaign=camp)) == "MANDATE_ABSENT"


def test_a_campaign_keeps_precedence_in_the_claim_namespace(
        armed_env, monkeypatch):
    """Both installed: the campaign is the narrower, shorter-lived grant and
    is the thing whose budget is being spent."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    monkeypatch.setattr(AUTH, "campaign_claim_suffix", lambda: "camp.deadbeef")
    assert AUTH.experiment_id() == "exp-1.camp.deadbeef"


# --------------------------------------------------- 8. day-scoped budgets
def test_the_claim_identity_is_day_scoped(armed_env):
    a = AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261029")
    b = AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261030")
    assert a and b and a != b
    assert a.endswith(".20261029") and b.endswith(".20261030")


def test_day_rollover_opens_a_fresh_namespace(armed_env, monkeypatch):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    d1 = dt.datetime(2026, 10, 29, 23, 59, tzinfo=dt.timezone.utc)
    d2 = dt.datetime(2026, 10, 30, 0, 1, tzinfo=dt.timezone.utc)
    assert AUTH.mandate_claim_suffix(d1) != AUTH.mandate_claim_suffix(d2)


def test_a_restart_on_the_same_day_cannot_replenish(armed_env, monkeypatch):
    """A PURE FUNCTION OF THE DOCUMENT AND THE DAY. Re-reading the same
    document at a different moment of the same UTC day derives the same
    identity, so the same claim files are counted."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    a = AUTH.mandate_claim_suffix(
        dt.datetime(2026, 10, 29, 1, 0, tzinfo=dt.timezone.utc))
    b = AUTH.mandate_claim_suffix(
        dt.datetime(2026, 10, 29, 22, 0, tzinfo=dt.timezone.utc))
    assert a == b and a


def test_a_new_seq_opens_a_new_namespace(armed_env):
    """Two grants under one mandate_id must not share a budget."""
    a = AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261029")
    b = AUTH.mandate_claim_sid("m-1", 2, KEY_ID, day="20261029")
    assert a != b


def test_a_rotated_key_opens_a_new_namespace():
    """`seq` monotonicity is tracked per key_id, so the same seq under a
    different key is a different grant."""
    a = AUTH.mandate_claim_sid("m-1", 1, "key-a", day="20261029")
    b = AUTH.mandate_claim_sid("m-1", 1, "key-b", day="20261029")
    assert a != b


def test_duplicate_derivation_is_idempotent():
    """CONCURRENT ARMING. Two attempts deriving the identity at the same
    instant must agree, so they contend for ONE O_EXCL lock file rather than
    taking two claims. The lock itself is the serializer and is covered by
    the durable-claim suite; this pins the input it depends on."""
    xs = {AUTH.mandate_claim_sid("m-1", 1, KEY_ID, day="20261029")
          for _ in range(50)}
    assert len(xs) == 1 and xs != {""}


def test_an_unusable_identity_is_empty_not_a_guess():
    for bad in (("", 1, "k"), ("m", None, "k"), ("m", "x", "k"),
                ("m", 1, "k")):
        sid = AUTH.mandate_claim_sid(bad[0], bad[1], bad[2], day="20261029")
        if bad[0] and bad[1] == 1:
            assert sid
        else:
            assert sid == ""
    assert AUTH.mandate_claim_sid("m", 1, "k", day="") == ""


def test_an_unreadable_mandate_yields_no_suffix_so_more_is_counted(
        monkeypatch):
    """FAILS TOWARD FEWER CLAIMS. "" leaves the bare experiment id, which
    counts every suffixed claim too, so an unreadable document sees MORE
    spent, never less."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    for junk in ("", "not json", "{}", json.dumps({"payload": {}})):
        monkeypatch.setenv(MS.MANDATE_ENV, junk)
        assert AUTH.mandate_claim_suffix() == ""
        assert AUTH.experiment_id() == "exp-1"


def test_the_namespace_does_not_verify_and_so_does_no_io(monkeypatch):
    """If the namespace verified, it would do network I/O per claim and
    return "" whenever the watchdog blinked -- handing out a FRESH budget
    exactly when the system is least healthy."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.delenv(MS.STORE_URL_ENV, raising=False)

    def explode(*a, **k):
        raise AssertionError("the claim namespace must not reach the network")
    monkeypatch.setattr(MS, "HttpSeqFloorStore", explode)
    monkeypatch.setattr(MS, "HttpRevocationStore", explode)
    monkeypatch.setenv(MS.MANDATE_ENV, json.dumps(
        {"key_id": "k", "payload": {"mandate_id": "m-9", "seq": 4}}))
    assert AUTH.mandate_claim_suffix().startswith("mand.")


# ------------------------------------------- 6. the private key is absent
def test_no_private_key_shaped_value_is_in_the_environment():
    """An Ed25519 seed is 32 raw bytes; in a variable it would appear as 64
    hex characters or ~44 of base64. The public half is also 64 hex, so hex
    alone is not proof -- but MANDATE_PUBKEYS is the only place a key
    belongs, and nothing in the environment may carry one."""
    suspicious = []
    for k, v in os.environ.items():
        s = (v or "").strip()
        if re.fullmatch(r"[0-9a-fA-F]{64}", s) or "PRIVATE KEY" in s:
            suspicious.append(k)
    assert not suspicious, suspicious


def test_no_runtime_module_imports_the_mandate_signer():
    """An executor that can sign is an executor that can authorize itself.
    The signer lives in tools/ and nothing on the money path imports it.

    NOTE the deliberately narrow claim. `pmus_ws_evidence_recorder` does
    construct an Ed25519 private key -- from `PMUS_API_SECRET_KEY`, to sign
    VENUE API requests. That is a different key for a different purpose and
    banning Ed25519 outright would be a test that fails for the wrong
    reason. What must hold is that no runtime module can produce a MANDATE
    signature."""
    import src
    root = os.path.dirname(os.path.dirname(os.path.abspath(src.__file__)))
    for name in os.listdir(os.path.join(root, "src")):
        if not name.endswith(".py"):
            continue
        body = open(os.path.join(root, "src", name)).read()
        assert "owner_sign_mandate" not in body, name


def test_the_mandate_module_can_verify_but_never_sign():
    """Verification needs only the public half. A `sign` path in this module
    would put the capability to mint a mandate inside the image."""
    body = open(os.path.abspath(MS.__file__.replace(".pyc", ".py"))).read()
    assert "Ed25519PrivateKey" not in body
    assert "private_bytes" not in body
    assert not hasattr(MS, "sign")
    assert hasattr(MS, "verify")


def test_registered_keys_are_public_halves_only():
    """A 32-byte Ed25519 public key is 64 hex characters. Anything longer
    is not a public key and may be a seed concatenation."""
    for kid, pub in MS.MANDATE_PUBKEYS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", str(pub).lower()), kid


def test_telemetry_never_carries_the_document(armed_env, monkeypatch):
    """A mandate is not a credential, but publishing one in logs invites a
    replay against a rolled-back seq store."""
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    v = MS.verify(os.getenv(MS.MANDATE_ENV), environment=ENVIRONMENT,
                  service_role=ROLE, series=SERIES, seq_floor=0,
                  revoked=frozenset())
    t = json.dumps(MS.telemetry(v))
    assert "sig" not in json.loads(t)
    assert "payload" not in json.loads(t)
    for secret in ("not_before", "capital", "promotion"):
        assert secret not in t


# ------------------------------ the signed SUBMISSION rate must also bind
def _mandate_env(monkeypatch, priv, **kw):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(priv, _payload(**kw)))


def test_a_backstop_wider_than_the_signed_rate_refuses(
        armed_env, monkeypatch):
    """The signed per-day submission rate is made binding by bounding the
    budget that IS enforced. A deployment permitting more than the owner
    signed must not arm."""
    _mandate_env(monkeypatch, armed_env)          # signs 56/day
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "500")
    assert _reason(_arm()) == "MANDATE_SUBMISSION_BACKSTOP_WIDER_THAN_SIGNED"


def test_a_backstop_inside_the_signed_rate_is_accepted(
        armed_env, monkeypatch):
    _mandate_env(monkeypatch, armed_env)          # signs 56/day
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "40")
    assert _reason(_arm()) not in (
        "MANDATE_SUBMISSION_BACKSTOP_WIDER_THAN_SIGNED",
        "MANDATE_SUBMISSION_BACKSTOP_ABSENT",
        "OWNER_AUTHORIZATION_ABSENT")


def test_no_backstop_at_all_refuses(armed_env, monkeypatch):
    """"Unlimited submissions inside a signed envelope" is not something a
    signature can grant here."""
    _mandate_env(monkeypatch, armed_env)
    monkeypatch.delenv(AUTH.MAX_SUBMISSIONS_ENV, raising=False)
    assert _reason(_arm()) == "MANDATE_SUBMISSION_BACKSTOP_ABSENT"


@pytest.mark.parametrize("bad", [0, -1, True, "56"])
def test_an_unreadable_signed_submission_rate_refuses(
        armed_env, monkeypatch, bad):
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv(AUTH.MAX_SUBMISSIONS_ENV, "40")
    p = _payload()
    p["max_submissions_per_day"] = bad
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, p))
    r = _reason(_arm())
    # the verifier's own numeric check may reject it first; either is a
    # refusal and neither authorizes.
    assert r.startswith("MANDATE_") and r != "OWNER_AUTHORIZATION_ABSENT", r


def test_the_submission_namespace_rolls_daily_so_the_backstop_is_per_day(
        armed_env, monkeypatch):
    """The backstop is counted over the claim namespace, and that namespace
    carries the UTC day -- which is what makes a 7-day mandate get a fresh
    submission budget each day instead of one budget for the week."""
    monkeypatch.setenv(AUTH.MANDATE_AS_AUTH_ENV, "1")
    monkeypatch.setenv("L5_EXPERIMENT_ID", "exp-1")
    monkeypatch.setenv(MS.MANDATE_ENV, _sign(armed_env, _payload()))
    d1 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
    d2 = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)
    assert AUTH.mandate_claim_suffix(d1) != AUTH.mandate_claim_suffix(d2)
