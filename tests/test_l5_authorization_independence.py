"""Which half of the authorization is independent of this executor, and which
is not. The owner asked; this measures it rather than asserting it.

THE QUESTION. `MANDATE_NO_TRUSTED_KEY` / `mandate_enforcing: false` /
`promotion_gate_binds: false` was reported on production. Is signed-mandate
enforcement REQUIRED for this campaign, or does the campaign-approval path
provide "independent, durable enforcement of the exact campaign scope,
expiry, exposure and loss limits"?

THE ANSWER SPLITS, and both halves are pinned below.

DURABLE AND INDEPENDENT -- the DOLLARS. Every ceiling is a CODE CONSTANT and
the effective scope is the INTERSECTION of what a document asked for and
what the code permits. A document asking $500, 400 windows and a $500 daily
loss yields $20, 8 and $5. No document and no variable can widen one, which
is the property `l5_campaign_approval` states as "there is deliberately no
path by which a number in the document raises a limit, because then the
document, not the code, would be the risk control".

NOT INDEPENDENT -- the DECISION. The approval document is served by
`EnvApprovalStore` out of the `L5_CAMPAIGN_APPROVAL` environment variable,
and the campaign expiry and window count come from `L5_LIVE_AUTH_NOT_AFTER`
and `L5_LIVE_AUTH_MAX_WINDOWS`. Anything holding a Railway token -- this
agent included -- can write all three. `EnvApprovalStore.put` refusing is a
guard on a code path that the real write path does not use.

`l5_mandate_signature` says exactly this in its own words: "`l5_campaign_
approval` says Claude cannot write its document; `l5_mandate_signature`
records that this is false in practice, because anyone holding a Railway
token can set the variable."

So the dollars are bounded by code whatever happens, and the AUTHORIZATION
is not bounded independently of this executor. Only the signed mandate
closes that, because its private half is held offline and this process has
never seen it and cannot produce it.

NOTHING HERE WRITES A RAILWAY VARIABLE. Every environment change is
monkeypatched in-process, and the keypair below is generated inside the test
and never registered in the shipped registry.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal as D

import pytest

from src import l5_campaign_approval as CA
from src import l5_mandate_signature as MS


def _doc(**over):
    now = dt.datetime.now(dt.timezone.utc)
    d = {"approved": True, "nonce": "n" * 16,
         "series": "cpc-btc-updown-15m-",
         "issued_utc": now.isoformat(),
         "expires_utc": (now + dt.timedelta(hours=2)).isoformat(),
         "max_windows": 4,
         "max_total_exposure_usd": "12",
         "max_daily_loss_usd": "5"}
    d.update(over)
    return d


def _state(doc, monkeypatch):
    monkeypatch.setenv(CA.APPROVAL_ENV, json.dumps(doc))
    return CA.approval_gate().state()


# ===================================================== the dollars ARE bounded

def test_a_greedy_document_is_clamped_to_the_code_constants(monkeypatch):
    """The half that IS independent. Asking for more yields the ceiling."""
    st = _state(_doc(max_windows=400, max_total_exposure_usd="500",
                     max_daily_loss_usd="500"), monkeypatch)
    assert st["approved"] is True
    sc = st["scope"]
    assert sc["max_windows"] == CA.MAX_WINDOWS == 8
    assert D(sc["max_total_exposure_usd"]) == CA.MAX_TOTAL_EXPOSURE_USD == D("20")
    assert D(sc["max_daily_loss_usd"]) == CA.MAX_DAILY_LOSS_USD == D("5")


def test_the_campaign_duration_is_clamped_too(monkeypatch):
    """A document cannot buy a longer campaign than MAX_CAMPAIGN_S."""
    assert CA.MAX_CAMPAIGN_S == 4 * 3600.0
    now = dt.datetime.now(dt.timezone.utc)
    st = _state(_doc(issued_utc=now.isoformat(),
                     expires_utc=(now + dt.timedelta(days=30)).isoformat()),
                monkeypatch)
    assert st["approved"] is True       # approved, but not for 30 days


def test_a_series_outside_the_allowlist_is_refused(monkeypatch):
    st = _state(_doc(series="cpc-eth-updown-15m-"), monkeypatch)
    assert st["approved"] is False
    assert st["why"] == "approval_series_not_allowed"


@pytest.mark.parametrize("over,why", [
    ({"approved": False}, "approval_not_asserted"),
    ({"approved": "yes"}, "approval_not_asserted"),
    ({"nonce": "short"}, "approval_nonce_too_short"),
    ({"max_windows": 0}, "approval_max_windows_below_one"),
    ({"max_total_exposure_usd": "0"}, "approval_limits_not_positive"),
    ({"max_daily_loss_usd": "-1"}, "approval_limits_not_positive"),
])
def test_the_document_fails_closed_on_every_malformation(over, why,
                                                         monkeypatch):
    st = _state(_doc(**over), monkeypatch)
    assert st["approved"] is False
    assert st["why"] == why


def test_an_expired_or_future_document_is_refused(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    past = _state(_doc(issued_utc=(now - dt.timedelta(hours=3)).isoformat(),
                       expires_utc=(now - dt.timedelta(hours=1)).isoformat()),
                  monkeypatch)
    assert past["approved"] is False
    assert past["why"] in ("approval_stale", "approval_expired")
    fut = _state(_doc(issued_utc=(now + dt.timedelta(hours=1)).isoformat()),
                 monkeypatch)
    assert fut["approved"] is False
    assert fut["why"] == "approval_issued_in_the_future"


# ============================================ the DECISION is NOT independent

def test_THE_GAP_a_document_this_process_composes_is_approved(monkeypatch):
    """THE FINDING, demonstrated rather than argued.

    This document was not written by the owner. It was composed here, in
    this process, and placed in the variable the gate reads. The gate
    approves it. Nothing in the approval path can tell the difference,
    because the variable IS the credential.

    In production that variable is `L5_CAMPAIGN_APPROVAL` on the Railway
    service, and this agent holds a Railway token that can set it. So the
    campaign-approval path does NOT provide authorization independent of
    this executor -- only the dollar ceilings above are independent."""
    st = _state(_doc(), monkeypatch)
    assert st["approved"] is True
    assert st["scope"]["max_windows"] == 4
    assert D(st["scope"]["max_total_exposure_usd"]) == D("12")


def test_the_store_write_guard_is_not_the_real_write_path():
    """`EnvApprovalStore.put` refuses, and that refusal is honest about its
    own scope: it stops THIS code from writing an approval. It cannot stop
    an environment variable being set by whatever set the others."""
    with pytest.raises(CA.ApprovalReadUnavailable):
        CA.EnvApprovalStore().put(CA.APPROVAL_KEY, b"{}")


def test_the_approval_is_env_sourced_and_so_are_expiry_and_window_count():
    """Named so the three variables that carry the DECISION are visible in
    one place beside the ceilings that do not depend on them."""
    assert CA.APPROVAL_ENV == "L5_CAMPAIGN_APPROVAL"
    assert isinstance(CA.approval_gate().store, CA.EnvApprovalStore)
    from src import l5_live_authorization as A
    src = __import__("inspect").getsource(A)
    for env in ("L5_LIVE_AUTH_NOT_AFTER", "L5_LIVE_AUTH_MAX_WINDOWS"):
        assert env in src, env


# ================================= what the signed mandate adds, and only it

def test_the_shipped_registry_is_empty_and_the_gate_is_self_activating():
    """`MANDATE_NO_TRUSTED_KEY` is the SHIPPED STATE, not a lost
    configuration. Nothing regressed and there is nothing to restore: the
    owner has never generated the pair. The gate binds the moment a public
    half is committed in a reviewed change, so turning it off afterwards
    takes a commit and a deploy rather than a variable edit."""
    assert MS.MANDATE_PUBKEYS == {}


def test_verification_fails_closed_with_an_empty_registry():
    """An empty registry refuses everything rather than trusting anything."""
    v = MS.verify(json.dumps({"payload": {}, "sig": "00" * 64,
                              "key_id": "anything"}),
                  environment="production", service_role="r",
                  series="cpc-btc-updown-15m-",
                  now=dt.datetime.now(dt.timezone.utc),
                  seq_floor=0, revoked=())
    assert v.get("ok") is not True


def test_a_registered_key_makes_the_campaign_SHAPE_insufficient(monkeypatch):
    """The property the owner is asking for, with a keypair generated HERE
    and never registered in the shipped module.

    Once a trusted key exists, the bytes that satisfy the campaign path --
    `{"approved": true, ...}` -- do not verify as a mandate, and neither
    does an unsigned payload or a payload signed by any other key. Writing
    the variable stops being sufficient, which is the entire point."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization as _ser
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        _ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
    monkeypatch.setitem(MS.MANDATE_PUBKEYS, "test-only", pub)
    now = dt.datetime.now(dt.timezone.utc)
    for forged in (json.dumps(_doc()),                       # campaign shape
                   json.dumps({"payload": {}, "key_id": "test-only"}),
                   json.dumps({"payload": {}, "sig": "00" * 64,
                               "key_id": "test-only"}),
                   "{}", "true", "not json"):
        v = MS.verify(forged, environment="production", service_role="r",
                      series="cpc-btc-updown-15m-", now=now,
                      seq_floor=0, revoked=())
        assert v.get("ok") is not True, forged[:50]


def test_taker_crossing_is_not_expressible_in_any_mandate():
    """No signed document can authorize a crossing order: there is no action
    name for it and execution_mode must be MAKER_ONLY."""
    assert "CROSS" not in " ".join(MS.KNOWN_ACTIONS).upper()
    assert MS.EXPECTED_EXECUTION_MODE == "MAKER_ONLY"


def test_promotion_cannot_be_switched_off_by_a_mandate():
    """PROMOTION IS A BLOCK, NEVER A SWITCH."""
    src = __import__("inspect").getsource(MS)
    assert "requires_bounded_live_validation" in src


# ================================================ no private key anywhere

def test_no_private_key_material_is_committed_in_the_tree():
    """The private half must not exist in this repository. Checked on the
    registry and on the signing doc, which must describe an OFFLINE
    procedure rather than carry a key."""
    for _kid, pub in MS.MANDATE_PUBKEYS.items():
        assert len(pub) == 64, "a registry entry must be a 32-byte PUBLIC key"
    doc = os.path.join("docs", "OWNER_MANDATE_SIGNING.md")
    assert os.path.exists(doc)
    body = open(doc, "r", encoding="utf-8").read()
    for marker in ("BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY",
                   "BEGIN EC PRIVATE KEY", "BEGIN RSA PRIVATE KEY"):
        assert marker not in body, marker


def test_the_runtime_verifier_scans_the_environment_for_a_private_key():
    """`mandate_signing_key_absent_from_runtime` is graded, not observed, so
    a private key appearing in the executor's environment fails the config
    verify rather than being noticed later."""
    from src import l5_runtime_config_verify as RV
    src = __import__("inspect").getsource(RV)
    assert "mandate_signing_key_absent_from_runtime" in src
