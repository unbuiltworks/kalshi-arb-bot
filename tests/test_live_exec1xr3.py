"""LIVE-L5-EXEC-1-XR3: the authorization document, and the two ledgers.

The properties worth testing here are adversarial: a document Claude could
mint, a replayed document, a document that tries to redefine the envelope or
name a side, an unwritable ledger, and the separation between "this
authorization is spent" and "this session made its submission attempt".
"""
from __future__ import annotations

import base64
import json
import time
from decimal import Decimal as D

import pytest

from analysis.l5.live_exec.spec import MAX_POSSIBLE_LOSS_USD, VENUE_MIN_QTY
from analysis.l5.live_exec2 import latch as L
from analysis.l5.live_exec2 import sides as S
from analysis.l5.live_exec2 import spec as XSPEC
from analysis.l5.live_exec4 import authz as A
from analysis.l5.live_exec4 import spec as X
from analysis.l5.live_exec4 import supervisor as SUP

SLUG = "cpc-btc-updown-15m-2026-10-07-0100z"
SID = "xr3-test-session"


def _keys():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return priv, base64.b64encode(pub).decode()


@pytest.fixture
def owner(monkeypatch):
    priv, pub_b64 = _keys()
    monkeypatch.setattr(X, "OWNER_PUBLIC_KEY_B64", pub_b64)
    monkeypatch.setattr(SUP, "OWNER_PUBLIC_KEY_B64", pub_b64)
    return priv, pub_b64


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    d = str(tmp_path / "authz")
    monkeypatch.setattr(XSPEC, "LATCH_DIR", str(tmp_path / "latch"))
    monkeypatch.setattr(L, "LATCH_DIR", str(tmp_path / "latch"))
    return d


def doc(priv, **over):
    now = time.time()
    body = {"probe": X.PROBE, "spec_fingerprint": X.XR3_FINGERPRINT,
            "authorization_id": "authz-1", "session_id": SID,
            "market_slug": SLUG, "quantity_shares": str(VENUE_MIN_QTY),
            "max_possible_loss_usd": str(MAX_POSSIBLE_LOSS_USD),
            "not_before_unix": now - 60, "not_after_unix": now + 3600,
            "max_orders": 1}
    body.update(over)
    sig = priv.sign(A.canonical_bytes(body))
    body["signature_b64"] = base64.b64encode(sig).decode()
    return body


def _verify(d, pub, ledger, **kw):
    kw.setdefault("my_fingerprint", X.XR3_FINGERPRINT)
    kw.setdefault("expect_probe", X.PROBE)
    kw.setdefault("expect_qty", str(VENUE_MIN_QTY))
    kw.setdefault("expect_max_loss", str(MAX_POSSIBLE_LOSS_USD))
    return A.verify(d, public_key_b64=pub, ledger_dir=ledger, **kw)


# ------------------------------------------------------- frozen identity

def test_fingerprint_literal():
    assert X.fingerprint() == X.XR3_FINGERPRINT
    assert X.XR3_FINGERPRINT == (
        "6e7f5aedc5125e3bf4461f61267b16fb81e98397c2d5bb56dc7cde00118199a4")


def test_assert_frozen_not_vacuous(monkeypatch):
    monkeypatch.setitem(X.SPEC, "probe", "TAMPERED")
    with pytest.raises(SystemExit):
        X.assert_frozen()


def test_the_owner_key_is_inside_the_fingerprint(monkeypatch):
    """Supplying a different key MUST change the hash, so the owner
    authorizes the fingerprint that carries their own key."""
    before = X.fingerprint()
    tampered = json.loads(json.dumps(X.SPEC))
    tampered["authorization"]["owner_public_key_b64"] = "SOMEONE_ELSES_KEY"
    assert X.fingerprint(tampered) != before


def test_placeholder_key_cannot_arm(ledger):
    """The build as committed carries no real key, so nothing can arm."""
    assert X.owner_key_supplied() is False
    s = SUP.Supervisor()
    v = s.assess({"anything": True}, ledger_dir=ledger)
    assert v["ok"] is False
    assert "OWNER_PUBLIC_KEY_NOT_SUPPLIED" in v["refusals"]


def test_nothing_about_trading_changed():
    from analysis.l5.live_exec3.spec import SPEC as XR2_SPEC
    for dotted in ("size", "orders", "book", "evidence", "deadman",
                   "withdrawal", "accounting", "behaviour", "side_rule",
                   "placement_rule", "timing_explicit", "tte_invariant",
                   "markouts", "one_submission", "cancellation"):
        assert XSPEC._get(X.SPEC, dotted) == XSPEC._get(XR2_SPEC, dotted)
    assert X.SPEC["correction_count"] == 1       # XR2's, not a new one


@pytest.mark.parametrize("dotted,want", X.XR3_INVARIANTS)
def test_every_invariant(dotted, want):
    assert XSPEC._get(X.SPEC, dotted) == want


def test_max_loss_and_quantity_unchanged():
    assert X.SPEC["size"]["MAX_POSSIBLE_LOSS_USD"] == "0.0099"
    assert X.SPEC["size"]["quantity_shares"] == "0.01"


# ------------------------------------------------------ signature is king

def test_a_valid_document_verifies(owner, ledger):
    priv, pub = owner
    assert _verify(doc(priv), pub, ledger)["ok"] is True


def test_unsigned_document_refused(owner, ledger):
    priv, pub = owner
    d = doc(priv)
    del d["signature_b64"]
    assert "BAD_SIGNATURE" in _verify(d, pub, ledger)["refusals"]


def test_wrong_key_refused(owner, ledger):
    priv, _ = owner
    other_priv, other_pub = _keys()
    assert "BAD_SIGNATURE" in _verify(doc(priv), other_pub,
                                      ledger)["refusals"]


@pytest.mark.parametrize("field,value", [
    ("session_id", "attacker-session"),
    ("market_slug", "cpc-btc-updown-15m-2026-10-07-0200z"),
    ("authorization_id", "authz-2"),
    ("max_orders", 2),
    ("not_after_unix", 9_999_999_999),
])
def test_tampering_after_signing_is_detected(owner, ledger, field, value):
    """Claude can carry the bytes; Claude cannot change them."""
    priv, pub = owner
    d = doc(priv)
    d[field] = value
    assert "BAD_SIGNATURE" in _verify(d, pub, ledger)["refusals"]


def test_signature_covers_canonical_bytes_not_key_order(owner, ledger):
    priv, pub = owner
    d = doc(priv)
    reordered = {k: d[k] for k in sorted(d, reverse=True)}
    assert _verify(reordered, pub, ledger)["ok"] is True


# ------------------------------------------- the envelope is not negotiable

@pytest.mark.parametrize("over,reason", [
    ({"quantity_shares": "1.00"}, "QUANTITY_NOT_FROZEN_VALUE"),
    ({"quantity_shares": "0.02"}, "QUANTITY_NOT_FROZEN_VALUE"),
    ({"max_possible_loss_usd": "1.00"}, "MAX_LOSS_NOT_FROZEN_VALUE"),
    ({"max_orders": 2}, "MAX_ORDERS_NOT_ONE"),
])
def test_a_signed_document_cannot_redefine_the_envelope(owner, ledger, over,
                                                        reason):
    """Even correctly signed. The signature authorizes THIS experiment; it
    does not get to change what the experiment is."""
    priv, pub = owner
    v = _verify(doc(priv, **over), pub, ledger)
    assert v["ok"] is False
    assert any(reason in r for r in v["refusals"]), v["refusals"]


@pytest.mark.parametrize("field", ["side", "outcome_side", "price",
                                   "offset_ticks", "pair_cost_reservation",
                                   "tte_cutoff_s", "staleness_bound_s"])
def test_a_document_may_not_carry_a_side_or_a_parameter(owner, ledger,
                                                        field):
    priv, pub = owner
    d = doc(priv)
    d[field] = "anything"
    v = _verify(d, pub, ledger)
    assert v["ok"] is False
    assert any("FORBIDDEN_FIELDS" in r for r in v["refusals"])


def test_side_is_still_derived_from_slug_and_session(owner, ledger):
    priv, pub = owner
    d = doc(priv)
    assert _verify(d, pub, ledger)["ok"] is True
    want = S.side_for(d["market_slug"], d["session_id"])
    assert want["side"] in (S.UP, S.DOWN)
    assert "side" not in d and "outcome_side" not in d


def test_wrong_fingerprint_refused(owner, ledger):
    priv, pub = owner
    d = doc(priv, spec_fingerprint="0" * 64)
    assert "FINGERPRINT_MISMATCH" in _verify(d, pub, ledger)["refusals"]


def test_wrong_probe_refused(owner, ledger):
    priv, pub = owner
    assert "WRONG_PROBE:LIVE-L5-EXEC-1-XR2" in _verify(
        doc(priv, probe="LIVE-L5-EXEC-1-XR2"), pub, ledger)["refusals"]


@pytest.mark.parametrize("slug", ["", "cpc-btc-updown-5m-2026-10-07-0100z",
                                   "some-sports-market",
                                   "cpc-btc-updown-15m-bad"])
def test_non_btc15m_slug_refused(owner, ledger, slug):
    priv, pub = owner
    v = _verify(doc(priv, market_slug=slug), pub, ledger)
    assert v["ok"] is False


# --------------------------------------------------------- validity window

def test_not_yet_valid_refused(owner, ledger):
    priv, pub = owner
    now = time.time()
    d = doc(priv, not_before_unix=now + 600, not_after_unix=now + 1200)
    assert any("NOT_YET_VALID" in r for r in _verify(d, pub,
                                                     ledger)["refusals"])


def test_expired_refused(owner, ledger):
    priv, pub = owner
    now = time.time()
    d = doc(priv, not_before_unix=now - 7200, not_after_unix=now - 3600)
    assert any("EXPIRED" in r for r in _verify(d, pub, ledger)["refusals"])


def test_empty_validity_window_refused(owner, ledger):
    priv, pub = owner
    now = time.time()
    d = doc(priv, not_before_unix=now, not_after_unix=now)
    assert "EMPTY_VALIDITY_WINDOW" in _verify(d, pub, ledger)["refusals"]


# ------------------------------------------------------- replay and nonces

def test_consume_then_replay_refused(owner, ledger):
    priv, pub = owner
    d = doc(priv)
    assert _verify(d, pub, ledger)["ok"] is True
    A.consume_authorization(ledger, d)
    v = _verify(d, pub, ledger)
    assert v["ok"] is False
    assert "AUTHORIZATION_ALREADY_CONSUMED" in v["refusals"]


def test_consume_is_atomic(owner, ledger):
    priv, _ = owner
    d = doc(priv)
    A.consume_authorization(ledger, d)
    with pytest.raises(A.AuthzRefused):
        A.consume_authorization(ledger, d)


def test_consumption_survives_a_reload(owner, ledger):
    priv, pub = owner
    d = doc(priv)
    A.consume_authorization(ledger, d)
    import importlib
    fresh = importlib.reload(A)
    try:
        assert fresh.authz_state(ledger, d["authorization_id"])["consumed"]
    finally:
        importlib.reload(A)


def test_state_never_creates_a_record(ledger):
    for _ in range(3):
        assert A.authz_state(ledger, "never-seen")["consumed"] is False


def test_unwritable_ledger_fails_closed(owner):
    priv, _ = owner
    with pytest.raises(A.AuthzLedgerUnavailable):
        A.consume_authorization("/proc/definitely/not/writable", doc(priv))


def test_sanitised_ids_cannot_collide(ledger):
    assert A.authz_path(ledger, "a/b") != A.authz_path(ledger, "a:b")


def test_authz_module_cannot_release_a_nonce():
    src = open(A.__file__, encoding="utf8").read()
    assert "os.unlink" not in src and "os.remove" not in src
    assert not [n for n in dir(A)
                if n.startswith(("release", "clear", "reset", "delete"))]


# =================== THE TWO LEDGERS ARE SEPARATE ==========================

def test_authorization_ledger_and_submission_latch_are_different_dirs():
    assert X.AUTHZ_LEDGER_DIR != X.SUBMISSION_LATCH_DIR
    assert X.SPEC["two_ledgers"]["they_are_separate_files"] is True


def test_consuming_an_authorization_does_not_spend_the_latch(owner, ledger,
                                                             monkeypatch):
    priv, _ = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    d = doc(priv)
    A.consume_authorization(ledger, d)
    assert A.authz_state(ledger, d["authorization_id"])["consumed"] is True
    assert L.state()["spent"] is False          # the submission is untouched


def test_spending_the_latch_does_not_free_the_authorization(owner, ledger,
                                                            monkeypatch):
    priv, pub = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    d = doc(priv)
    A.consume_authorization(ledger, d)
    L.consume(slug=SLUG, side="UP", price="0.50", qty="0.01")
    assert L.state()["spent"] is True
    assert "AUTHORIZATION_ALREADY_CONSUMED" in _verify(d, pub,
                                                       ledger)["refusals"]


def test_authorized_but_gates_refused_leaves_the_latch_unspent(owner, ledger,
                                                               monkeypatch):
    """The normal, correct record of 'authorized, nothing attempted'."""
    priv, _ = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    calls = {"n": 0}

    def fake_runner(**kw):
        calls["n"] += 1
        return {"orders_submitted": 0, "stopped_at": "TIMING",
                "refusals": ["PAST_PLACEMENT_CUTOFF"]}

    s = SUP.Supervisor(runner=fake_runner)
    out = s._run_one(doc(priv), ledger_dir=ledger)
    assert calls["n"] == 1
    assert out["experiment"]["orders_submitted"] == 0
    assert A.authz_state(ledger, "authz-1")["consumed"] is True
    assert L.state()["spent"] is False
    assert out["authorization"]["NOT_THE_SUBMISSION_LATCH"] is True


# ---------------------------------------------- the idle loop is DISARMED

def test_idle_tick_holds_no_order_transport(ledger, monkeypatch):
    monkeypatch.delenv(X.AUTHZ_URL_ENV, raising=False)
    s = SUP.Supervisor()
    out = s.tick(url="", ledger_dir=ledger)
    assert out["state"] == "IDLE_DISARMED"
    assert out["orders_submitted"] == 0


def test_supervisor_never_constructs_a_transport_while_idle():
    """Scan the CODE, not the prose. The docstrings discuss the order path;
    what matters is that no statement outside _run_one can reach it."""
    import ast
    tree = ast.parse(open(SUP.__file__, encoding="utf8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_one":
            continue
        if isinstance(node, (ast.Name, ast.Attribute)):
            names.add(ast.unparse(node))
    hits = [n for n in names
            if any(k in n for k in ("BoundedOrderTransport", "submit_one",
                                    "deadman_cancel_all", "cancel_exact"))]
    assert hits == [], hits
    # and the module imports no order module at top level
    imported = {a.name for node in ast.walk(tree)
                if isinstance(node, ast.Import) for a in node.names}
    imported |= {node.module for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert not any("orders" in str(m) for m in imported), imported


def test_unreachable_document_stays_disarmed(ledger):
    s = SUP.Supervisor()
    out = s.tick(url="https://127.0.0.1:1/nope.json", ledger_dir=ledger)
    assert out["state"] == "IDLE_DISARMED"
    assert "fetch failed" in out["reason"]


def test_non_https_url_refused(ledger):
    s = SUP.Supervisor()
    out = s.tick(url="http://example.com/a.json", ledger_dir=ledger)
    assert out["state"] == "IDLE_DISARMED"
    assert "https" in out["reason"]


def test_oversized_document_refused(monkeypatch):
    monkeypatch.setattr(SUP, "MAX_DOC_BYTES", 4)

    class R:
        def read(self, n):
            return b"x" * 10

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    out = SUP.fetch_document("https://example.com/a.json")
    assert out["ok"] is False and "exceeds" in out["why"]


def test_garbage_document_stays_disarmed(owner, ledger):
    s = SUP.Supervisor()
    for junk in ({}, [], "", {"probe": X.PROBE}, {"signature_b64": "x"}):
        v = s.assess(junk, ledger_dir=ledger)
        assert v["ok"] is False


# ------------------------------------------------------------------- caps

def test_per_process_cap(owner, ledger, monkeypatch):
    priv, _ = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    s = SUP.Supervisor(runner=lambda **kw: {"orders_submitted": 0})
    for i in range(X.MAX_EXPERIMENTS_PER_PROCESS):
        s._run_one(doc(priv, authorization_id="a%d" % i), ledger_dir=ledger)
    v = s.assess(doc(priv, authorization_id="one-too-many"),
                 ledger_dir=ledger)
    assert v["ok"] is False
    assert "EXPERIMENT_CAP_REACHED" in v["refusals"]


def test_per_day_cap_is_reported():
    s = SUP.Supervisor()
    c = s.caps()
    assert c["per_day_max"] == X.MAX_EXPERIMENTS_PER_UTC_DAY
    assert c["per_process_max"] == X.MAX_EXPERIMENTS_PER_PROCESS


# --------------------------------------------------- end to end, one order

def test_one_authorization_yields_exactly_one_runner_call(owner, ledger,
                                                          monkeypatch):
    priv, _ = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "unrelated")
    seen = []

    def fake_runner(**kw):
        import os
        seen.append({"slug": os.environ["PMUS_EXEC1_GATE_EVIDENCE"],
                     "sid": os.environ["PMUS_EXEC1_SESSION_ID"],
                     "arm": os.environ["PMUS_EXEC1_ARM"],
                     "live": os.environ["PMUS_EXEC1_LIVE"]})
        return {"orders_submitted": 1, "stopped_at": None}

    s = SUP.Supervisor(runner=fake_runner)
    d = doc(priv)
    out = s.tick(url=None, ledger_dir=ledger)   # no URL -> idle
    assert out["state"] == "IDLE_DISARMED"
    ran = s._run_one(d, ledger_dir=ledger)
    assert len(seen) == 1
    assert seen[0]["slug"] == SLUG
    assert seen[0]["sid"] == SID == seen[0]["arm"]
    assert seen[0]["live"] == "YES"
    assert ran["experiment"]["orders_submitted"] == 1


def test_the_env_projection_is_restored_afterwards(owner, ledger,
                                                   monkeypatch):
    """The document is the authority; the env is borrowed for one call."""
    import os
    priv, _ = owner
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", "before")
    monkeypatch.delenv("PMUS_EXEC1_LIVE", raising=False)
    s = SUP.Supervisor(runner=lambda **kw: {"orders_submitted": 0})
    s._run_one(doc(priv), ledger_dir=ledger)
    assert os.environ["PMUS_EXEC1_SESSION_ID"] == "before"
    assert "PMUS_EXEC1_LIVE" not in os.environ


def test_a_second_tick_after_a_consumed_document_is_idle(owner, ledger,
                                                         monkeypatch):
    priv, pub = owner
    d = doc(priv)
    body = json.dumps(d).encode()

    class R:
        def read(self, n):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: R())
    monkeypatch.setenv("PMUS_EXEC1_SESSION_ID", SID)
    s = SUP.Supervisor(runner=lambda **kw: {"orders_submitted": 1},
                       sleep=lambda s: None)
    first = s.tick(url="https://example.com/a.json", ledger_dir=ledger)
    assert first["state"] == "EXPERIMENT_COMPLETE"
    second = s.tick(url="https://example.com/a.json", ledger_dir=ledger)
    assert second["state"] == "IDLE_DISARMED"
    assert "AUTHORIZATION_ALREADY_CONSUMED" in second["refusals"]
