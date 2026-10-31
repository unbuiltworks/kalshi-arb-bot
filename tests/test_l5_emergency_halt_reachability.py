"""The operator's emergency HALT path, demonstrated without trading.

STAGE B2, 2026-10-31. Clearing `PMUS_Q1_ARM` to stop a live campaign took
effect only at the Railway restart SIX MINUTES later, and the 1445z window
armed 52 seconds after the write and traded in between. The mechanism that
would have stopped it promptly already exists -- `l5_emergency_halt_marker`,
read by the EXISTING watchdog on its EXISTING sweep and turned into one call
to the existing `HaltAsserter` -- and it was NOT CONFIGURED:
`L5_EMERGENCY_HALT_URL` and `L5_EMERGENCY_HALT_SECRET` are absent from the
watchdog service, so `configured()` is False and the path is inert.

These tests exercise the decision offline: no venue, no network, no trading.
They demonstrate the response to a valid marker and, as importantly, the
failure behaviour -- because the dangerous direction here is a marker that
latches when it should not, or one that clears a latch, and neither may be
possible.
"""

from __future__ import annotations

import datetime as dt
import json

from src import l5_emergency_halt_marker as EHM


SECRET = "test-secret-not-a-real-one"
SERVICE = "l5-pmus-live"


def _marker(secret=SECRET, service=SERVICE, command="HALT",
            nonce="n-1", reason="operator halt", age_s=0.0):
    issued = (dt.datetime.now(dt.timezone.utc)
              - dt.timedelta(seconds=age_s)).isoformat()
    doc = {"command": command, "issued_utc": issued, "nonce": nonce,
           "service": service, "reason": reason}
    doc["hmac"] = EHM.sign(doc, secret)
    return json.dumps(doc)


# ------------------------------------------------- it acts on a valid marker

def test_a_valid_operator_marker_asserts_halt():
    r = EHM.evaluate(_marker(), secret=SECRET, service=SERVICE)
    assert r["act"] is True, r
    assert r["nonce"] == "n-1"


# --------------------------------------- and only on a valid one: fail safe

def test_a_forged_signature_is_refused():
    bad = json.loads(_marker())
    bad["hmac"] = "0" * 64
    r = EHM.evaluate(json.dumps(bad), secret=SECRET, service=SERVICE)
    assert r["act"] is False
    assert r["reason"] == "MARKER_SIGNATURE_INVALID"


def test_a_marker_signed_with_the_wrong_secret_is_refused():
    r = EHM.evaluate(_marker(secret="not-the-secret"),
                     secret=SECRET, service=SERVICE)
    assert r["act"] is False
    assert r["reason"] == "MARKER_SIGNATURE_INVALID"


def test_an_unsigned_marker_is_refused():
    doc = json.loads(_marker())
    doc.pop("hmac")
    r = EHM.evaluate(json.dumps(doc), secret=SECRET, service=SERVICE)
    assert r["act"] is False
    assert r["reason"] == "MARKER_SIGNATURE_INVALID"


def test_a_marker_for_another_service_is_refused():
    r = EHM.evaluate(_marker(service="some-other-service"),
                     secret=SECRET, service=SERVICE)
    assert r["act"] is False


def test_a_stale_marker_is_refused_rather_than_obeyed():
    """A week-old marker must not re-latch after a legitimate reset."""
    r = EHM.evaluate(_marker(age_s=EHM.MAX_MARKER_AGE_S + 60),
                     secret=SECRET, service=SERVICE)
    assert r["act"] is False


def test_a_replayed_nonce_is_refused():
    r = EHM.evaluate(_marker(nonce="used"), secret=SECRET, service=SERVICE,
                     acted_nonces=("used",))
    assert r["act"] is False


def test_malformed_bytes_are_refused():
    for raw in ("", "{", "null", "[]", "not json at all"):
        r = EHM.evaluate(raw, secret=SECRET, service=SERVICE)
        assert r["act"] is False, raw


# ------------------- THE DIRECTION THAT MATTERS: it can only ever stop us

def test_no_command_but_HALT_can_ever_be_acted_on():
    """RESUME, CLEAR and OK must all be inert.

    `HaltAsserter` has no clear by construction, so even a forged marker
    cannot authorize trading -- the worst a forger achieves is stopping us,
    which is the safe direction. This pins the parser to the same shape.
    """
    for cmd in ("RESUME", "CLEAR", "OK", "START", "STOP", "NOOP", ""):
        r = EHM.evaluate(_marker(command=cmd), secret=SECRET, service=SERVICE)
        assert r["act"] is False, cmd
        assert r["reason"] == "MARKER_COMMAND_NOT_HALT", cmd
    assert EHM.COMMAND == "HALT"


def test_the_command_is_case_insensitive_which_is_the_safe_direction():
    """`halt` and `Halt` ARE acted on; the parser normalises case.

    Recorded deliberately: I first asserted these were refused, which was
    wrong. Accepting a case variant of HALT can only ever stop trading, so
    the lenient reading is the safe one here -- whereas being lenient about
    RESUME would be the opposite, and is refused above.
    """
    for cmd in ("halt", "Halt", " HALT "):
        r = EHM.evaluate(_marker(command=cmd), secret=SECRET, service=SERVICE)
        assert r["act"] is True, cmd


def test_the_asserter_has_no_clear_at_all():
    """Structural: there is no method on HaltAsserter that clears a halt."""
    from src import l5_halt_gate as HG
    names = [n for n in dir(HG.HaltAsserter) if not n.startswith("_")]
    for forbidden in ("clear", "clear_halt", "resume", "unhalt", "delete"):
        assert forbidden not in names, (forbidden, names)
    assert "assert_halt" in names


# --------------------------------------------- unreachable is NOT a halt

def test_configured_requires_both_url_and_secret(monkeypatch):
    """The reason the path was inert in Stage B2: neither var was set."""
    monkeypatch.delenv(EHM.MARKER_URL_ENV, raising=False)
    monkeypatch.delenv(EHM.MARKER_SECRET_ENV, raising=False)
    assert EHM.configured() is False
    monkeypatch.setenv(EHM.MARKER_URL_ENV, "https://example.invalid/m.json")
    assert EHM.configured() is False, "a URL without a secret is not configured"
    monkeypatch.setenv(EHM.MARKER_SECRET_ENV, SECRET)
    assert EHM.configured() is True
