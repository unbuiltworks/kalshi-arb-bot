"""The owner's emergency HALT, and the things it must refuse.

Context: the halt latch's only writer is HaltAsserter on the watchdog's
volume, and on 2026-10-21 `connect-service-source` returned "Deploys have
been paused temporarily" -- so the dashboard, rollback, redeploy and a
variable change were all unavailable at once. This mechanism is the owner's
remaining control, and these tests are what make it trustworthy enough to
arm against.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from src import l5_emergency_halt_marker as M
from src import l5_halt_gate as HG


SECRET = "owner-secret-not-a-venue-credential"
SERVICE = "l5-pmus-live"
NOW = dt.datetime(2026, 10, 21, 16, 0, 0, tzinfo=dt.timezone.utc)


def marker(**over):
    doc = {"command": "HALT", "service": SERVICE,
           "issued_utc": (NOW - dt.timedelta(minutes=1)).isoformat(),
           "nonce": "halt-0001", "reason": "owner stop"}
    doc.update(over)
    if "hmac" not in doc:
        doc["hmac"] = M.sign(doc, SECRET)
    return json.dumps(doc).encode()


def ev(raw, **kw):
    base = dict(secret=SECRET, service=SERVICE, now=NOW)
    base.update(kw)
    return M.evaluate(raw, **base)


class MemStore:
    """Stands in for the watchdog's own volume store."""
    def __init__(self, data=None, fail=False):
        self.data, self.fail = dict(data or {}), fail
    def get(self, key):
        if self.fail:
            raise RuntimeError("volume unreadable")
        return self.data.get(key)
    def put(self, key, body):
        if self.fail:
            raise RuntimeError("volume unwritable")
        self.data[key] = body
        return {"key": key}


class Src:
    name = "TEST_MARKER"
    def __init__(self, raw=None, exc=None):
        self.raw, self.exc = raw, exc
    def fetch(self):
        if self.exc:
            raise self.exc
        return self.raw


# ------------------------------------------------------- 1. a valid HALT

def test_a_valid_marker_latches_halt_through_the_existing_asserter():
    store = MemStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    out = M.check(Src(marker()), a, store, service=SERVICE, secret=SECRET,
                  now=NOW)
    assert out["acted"] is True
    assert out["reason"] == "HALT_LATCHED_FROM_OWNER_MARKER"
    # the latch is the REAL one, written by HaltAsserter at HALT_KEY
    rec = json.loads(store.data[HG.HALT_KEY])
    assert rec["halt"] is True
    assert rec["reason"] == "OWNER_EMERGENCY_MARKER"
    assert rec["evidence"]["nonce"] == "halt-0001"
    # and the gate the executor reads now refuses
    assert HG.HaltGate(store).state()["permitted"] is False


def test_the_latched_halt_is_what_makes_the_EXECUTOR_cancel():
    """This module does not cancel. It latches, and the executor's existing
    maybe_halt_deadman turns a latched HALT into deadman() -- cancel by
    exact venue id, proof by GET /v1/order/{id}. Asserted on the executor's
    own source so the claim cannot drift."""
    import inspect
    from src import l5_pmus_live_runner as R
    body = inspect.getsource(R.Runner.maybe_halt_deadman)
    assert "deadman(" in body
    assert "MUST ALSO CANCEL" in body.upper()


# --------------------------------------------- 2. malformed / forged markers

@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b"3", b"null",
                                 b'{"command":"HALT"}'])
def test_malformed_markers_never_act(raw):
    assert ev(raw)["act"] is False


def test_an_unsigned_or_forged_marker_is_refused():
    assert ev(marker(hmac=""))["reason"] == "MARKER_SIGNATURE_INVALID"
    assert ev(marker(hmac="0" * 64))["reason"] == "MARKER_SIGNATURE_INVALID"
    # right shape, wrong key
    doc = json.loads(marker())
    doc["hmac"] = M.sign(doc, "attacker-key")
    assert ev(json.dumps(doc).encode())["reason"] \
        == "MARKER_SIGNATURE_INVALID"


def test_tampering_with_any_signed_field_invalidates_the_signature():
    for field, value in (("service", "other-service"),
                         ("nonce", "halt-9999"), ("reason", "changed"),
                         ("issued_utc", NOW.isoformat()),
                         ("command", "RESUME")):
        doc = json.loads(marker())
        doc[field] = value          # keep the ORIGINAL hmac
        r = ev(json.dumps(doc).encode())
        assert r["act"] is False, field
        # `command` is screened before the signature, by design: an
        # unparseable command must be refused by NAME even when signed.
        want = ("MARKER_COMMAND_NOT_HALT" if field == "command"
                else "MARKER_SIGNATURE_INVALID")
        assert r["reason"] == want, field


def test_a_marker_for_another_service_cannot_halt_this_one():
    assert ev(marker(service="some-other-deployment"))["reason"] \
        == "MARKER_WRONG_SERVICE"


# ------------------------- 3. HALT ONLY: it can never clear or authorize

@pytest.mark.parametrize("cmd", ["RESUME", "CLEAR", "OK", "UNHALT", "START",
                                 "ARM", "halt-please", ""])
def test_no_command_but_HALT_is_ever_acted_on(cmd):
    r = ev(marker(command=cmd))
    assert r["act"] is False
    if cmd.strip().upper() != "HALT":
        assert r["reason"] == "MARKER_COMMAND_NOT_HALT"


def test_a_valid_marker_cannot_clear_an_existing_halt():
    """Belt and braces on the one-way property: even a perfectly signed
    marker reaches only assert_halt, and HaltAsserter has no clear."""
    store = MemStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    a.assert_halt(reason="EARLIER", evidence={"k": "v"})
    M.check(Src(marker(nonce="n2")), a, store, service=SERVICE,
            secret=SECRET, now=NOW)
    assert json.loads(store.data[HG.HALT_KEY])["halt"] is True
    assert HG.HaltGate(store).state()["permitted"] is False
    # and the module has no path that could ever write halt:false
    import inspect
    src = inspect.getsource(M)
    assert "heartbeat_no_halt" not in src
    assert "halt\": false" not in src and "'halt': False" not in src


# --------------------------------- 4. staleness and replay, both directions

def test_a_stale_marker_is_refused_by_name_rather_than_obeyed():
    """The ambiguous state the brief names: a marker from last week
    re-latching after a legitimate owner reset. Refused loudly; the owner
    re-issues with a current timestamp."""
    old = (NOW - dt.timedelta(days=3)).isoformat()
    r = ev(marker(issued_utc=old))
    assert r["act"] is False
    assert r["reason"] == "MARKER_STALE"
    assert r["age_s"] > M.MAX_MARKER_AGE_S


def test_a_future_dated_marker_is_refused_beyond_the_skew_allowance():
    ok = (NOW + dt.timedelta(seconds=30)).isoformat()
    bad = (NOW + dt.timedelta(hours=2)).isoformat()
    assert ev(marker(issued_utc=ok))["act"] is True
    assert ev(marker(issued_utc=bad))["reason"] == "MARKER_FUTURE_DATED"


def test_an_unreadable_timestamp_refuses(monkeypatch):
    assert ev(marker(issued_utc="yesterday"))["reason"] \
        == "MARKER_ISSUED_UTC_UNREADABLE"
    assert ev(marker(issued_utc=None))["reason"] \
        == "MARKER_ISSUED_UTC_UNREADABLE"


def test_a_replayed_nonce_is_not_acted_on_twice():
    assert ev(marker(), acted_nonces=["halt-0001"])["reason"] \
        == "MARKER_ALREADY_ACTED"


def test_a_marker_with_no_nonce_refuses():
    doc = {"command": "HALT", "service": SERVICE,
           "issued_utc": NOW.isoformat(), "nonce": "", "reason": "x"}
    doc["hmac"] = M.sign(doc, SECRET)
    assert ev(json.dumps(doc).encode())["reason"] == "MARKER_NO_NONCE"


# -------------------------------------------- 5. GitHub / network failure

@pytest.mark.parametrize("exc", [TimeoutError("timed out"),
                                 OSError("connection reset"),
                                 RuntimeError("marker_http_503")])
def test_an_unreachable_marker_changes_nothing(exc):
    """RETAIN THE EXISTING SAFETY STATE. Asserting on a failed read would
    latch the system on every transient network error."""
    store = MemStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    out = M.check(Src(exc=exc), a, store, service=SERVICE, secret=SECRET,
                  now=NOW)
    assert out["acted"] is False
    assert out["reason"] == "MARKER_UNREACHABLE"
    assert HG.HALT_KEY not in store.data        # nothing written at all


def test_an_unreachable_marker_does_not_disturb_an_EXISTING_halt():
    store = MemStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    a.assert_halt(reason="EARLIER", evidence={"k": "v"})
    before = store.data[HG.HALT_KEY]
    M.check(Src(exc=TimeoutError()), a, store, service=SERVICE,
            secret=SECRET, now=NOW)
    assert store.data[HG.HALT_KEY] == before


def test_the_source_enforces_a_strict_timeout_and_raises_rather_than_defaults():
    assert M.MarkerSource("https://x/y").timeout_s == M.DEFAULT_TIMEOUT_S
    assert M.DEFAULT_TIMEOUT_S <= 5.0
    with pytest.raises(Exception):
        M.MarkerSource("https://x/y", opener=lambda u, t: (_ for _ in ()
                                                           ).throw(
            TimeoutError())).fetch()


# ------------------------------------------- 6. restart persistence

def test_an_acted_nonce_survives_a_restart_and_is_not_re_latched():
    store = MemStore()
    a = HG.HaltAsserter(store, who="l5-watchdog")
    first = M.check(Src(marker()), a, store, service=SERVICE, secret=SECRET,
                    now=NOW)
    assert first["acted"] is True
    assert json.loads(store.data[M.ACTED_KEY])["nonces"] == ["halt-0001"]
    # the process restarts: same volume, same marker still published
    a2 = HG.HaltAsserter(store, who="l5-watchdog")
    again = M.check(Src(marker()), a2, store, service=SERVICE,
                    secret=SECRET, now=NOW)
    assert again["acted"] is False
    assert again["reason"] == "MARKER_ALREADY_ACTED"
    # the halt stays latched regardless
    assert HG.HaltGate(store).state()["permitted"] is False


def test_a_second_DIFFERENT_marker_can_still_halt_after_a_reset():
    store = MemStore({M.ACTED_KEY: json.dumps({"nonces": ["halt-0001"]})
                      .encode()})
    a = HG.HaltAsserter(store, who="l5-watchdog")
    out = M.check(Src(marker(nonce="halt-0002")), a, store, service=SERVICE,
                  secret=SECRET, now=NOW)
    assert out["acted"] is True
    assert sorted(json.loads(store.data[M.ACTED_KEY])["nonces"]) \
        == ["halt-0001", "halt-0002"]


def test_an_unreadable_nonce_record_still_latches_a_genuine_halt():
    """A corrupt bookkeeping file must not be able to silence the owner's
    emergency stop. The sentinel `_acted` returns is not any real nonce, so
    the replay check does not match it and the halt goes through. What is
    lost is replay suppression, and a replayed marker can only re-latch
    HALT -- its one cost is overwriting the original halt's evidence.

    Asserted with a REAL nonce: signing the sentinel as the nonce would
    only match it against itself and prove nothing."""
    halt_store = MemStore()
    a = HG.HaltAsserter(halt_store, who="l5-watchdog")
    out = M.check(Src(marker(nonce="halt-REAL-FRESH-0001")), a,
                  MemStore(fail=True), service=SERVICE, secret=SECRET,
                  now=NOW)
    assert out["acted"] is True
    assert out["reason"] == "HALT_LATCHED_FROM_OWNER_MARKER"
    assert HG.HaltGate(halt_store).state()["permitted"] is False


# ----------------------------------- 7. cancellation uncertainty & config

def test_a_latch_failure_is_reported_and_never_claimed_as_success():
    class Dead:
        def assert_halt(self, **kw):
            raise RuntimeError("volume full")
    out = M.check(Src(marker()), Dead(), MemStore(), service=SERVICE,
                  secret=SECRET, now=NOW)
    assert out["acted"] is False
    assert out["reason"] == "MARKER_LATCH_FAILED"


def test_the_halt_stands_even_if_the_nonce_record_cannot_be_written():
    """The latch is the safety property; the nonce record is bookkeeping.
    A failure to record is surfaced, not swallowed, and the worst case is a
    second identical latch."""
    halt_store = MemStore()
    a = HG.HaltAsserter(halt_store, who="l5-watchdog")

    class WriteOnlyFails(MemStore):
        def put(self, key, body):
            raise RuntimeError("nope")
    out = M.check(Src(marker()), a, WriteOnlyFails(), service=SERVICE,
                  secret=SECRET, now=NOW)
    assert out["acted"] is True
    assert out["nonce_record_error"] == "RuntimeError"
    assert json.loads(halt_store.data[HG.HALT_KEY])["halt"] is True


def test_the_mechanism_is_OFF_unless_BOTH_url_and_secret_are_set(monkeypatch):
    """Half-configured is OFF: a URL with no secret would accept an
    unsigned marker. OFF means 'cannot be triggered', never 'halt'."""
    for url, sec, want in ((None, None, False), ("https://x", None, False),
                           (None, "s", False), ("https://x", "s", True)):
        monkeypatch.delenv(M.MARKER_URL_ENV, raising=False)
        monkeypatch.delenv(M.MARKER_SECRET_ENV, raising=False)
        if url:
            monkeypatch.setenv(M.MARKER_URL_ENV, url)
        if sec:
            monkeypatch.setenv(M.MARKER_SECRET_ENV, sec)
        assert M.configured() is want, (url, sec)
    assert M.from_env(MemStore(), object()) is not None


def test_from_env_returns_None_when_unconfigured(monkeypatch):
    monkeypatch.delenv(M.MARKER_URL_ENV, raising=False)
    monkeypatch.delenv(M.MARKER_SECRET_ENV, raising=False)
    assert M.from_env(MemStore(), object()) is None


def test_the_module_holds_no_venue_credential_and_no_order_path():
    import inspect
    src = inspect.getsource(M)
    # Call shapes, not substrings: the docstring legitimately quotes the
    # executor's "stop submitting" comment and names GET /v1/order/{id} to
    # explain where cancellation actually happens. An earlier version of
    # this test rejected its own documentation.
    for forbidden in ("PMUS_API_KEY", "PMUS_API_SECRET", ".submit(",
                      "ORDER_INTENT_", "sign_request(", "transport.",
                      "preview_envelope", "from_intent"):
        assert forbidden not in src, forbidden
    # and it imports nothing order-capable
    for mod in ("l5_pmus_transport", "pmus_q1_executor",
                "pmus_micro_executor", "l5_order_body"):
        assert mod not in src, mod


# ------------------------------------ optional read credential (2026-11-23)
def test_no_read_credential_sends_no_authorization(monkeypatch):
    monkeypatch.setenv(M.MARKER_URL_ENV, "https://example.test/m.json")
    monkeypatch.setenv(M.MARKER_SECRET_ENV, "s")
    monkeypatch.delenv(M.MARKER_BEARER_ENV, raising=False)
    monkeypatch.delenv(M.MARKER_ACCEPT_ENV, raising=False)
    src = M.from_env(MemStore(), object())["source"]
    assert src._header == {}


def test_read_credential_is_sent_as_bearer_with_the_accept_header(
        monkeypatch):
    import urllib.request
    monkeypatch.setenv(M.MARKER_URL_ENV, "https://example.test/m.json")
    monkeypatch.setenv(M.MARKER_SECRET_ENV, "s")
    monkeypatch.setenv(M.MARKER_BEARER_ENV, "tok-123")
    monkeypatch.setenv(M.MARKER_ACCEPT_ENV, "application/vnd.github.raw")
    src = M.from_env(MemStore(), object())["source"]
    seen = {}

    class Resp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=None):
        seen.update({k.lower(): v for k, v in req.header_items()})
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert src.fetch() == b"{}"
    assert seen["authorization"] == "Bearer tok-123"
    assert seen["accept"] == "application/vnd.github.raw"
    assert seen["cache-control"] == "no-cache"


def test_the_read_credential_does_not_authenticate_a_marker(monkeypatch):
    """A marker fetched with a valid token but a wrong signature is still
    refused: the token reads, the HMAC decides."""
    doc = {"command": "HALT", "issued_utc": M._utc().isoformat(),
           "nonce": "n", "service": "svc", "reason": "r"}
    doc["hmac"] = M.sign(doc, "wrong")
    import json as _j
    v = M.evaluate(_j.dumps(doc), secret="right", service="svc")
    assert v["act"] is False and v["reason"] == "MARKER_SIGNATURE_INVALID"
