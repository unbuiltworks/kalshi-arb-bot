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


# ================= THE OWNER'S ONE-ACTION HALT, FROM A RAILWAY VARIABLE
#
# The marker above exists for "HALT when Railway is unavailable" and pays for
# that with an HMAC the owner holds and a file they sign. This is the ordinary
# case: the owner is at the dashboard and wants to stop the engine now. Setting
# a Railway variable is ALREADY an authenticated action and adds no new trust
# assumption -- anyone who can write it can already change the service's source
# or delete it, which is strictly more authority than halting it.

class _Latch:
    """A minimal halt store: get/put over one key, like ObjectStore."""

    def __init__(self, initial=None):
        self.data = {} if initial is None else dict(initial)
        self.puts = 0

    def get(self, key):
        return self.data.get(key)

    def put(self, key, raw):
        self.puts += 1
        self.data[key] = raw


class _Asserter:
    """Records what assert_halt was called with. Has no clear, like the real one."""

    def __init__(self, store):
        self.store = store
        self.calls = []

    def assert_halt(self, *, reason, evidence):
        rec = {"halt": True, "t": "2026-11-01T00:00:00+00:00",
               "who": "l5-watchdog", "reason": reason,
               "evidence": dict(evidence)}
        self.store.put(HG_HALT_KEY, json.dumps(rec, sort_keys=True).encode())
        self.calls.append(rec)
        return rec


from src import l5_halt_gate as _HG                           # noqa: E402
HG_HALT_KEY = _HG.HALT_KEY


def test_the_trigger_is_off_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv(EHM.HALT_NOW_ENV, raising=False)
    assert EHM.halt_now_reason() is None
    st = _Latch()
    r = EHM.check_halt_now(_Asserter(st), st, service=SERVICE)
    assert r["acted"] is False
    assert r["reason"] == "TRIGGER_NOT_SET"
    assert st.puts == 0, "an unset trigger must write nothing"


def test_a_blank_variable_is_still_off(monkeypatch):
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "   ")
    assert EHM.halt_now_reason() is None


def test_ANY_non_empty_value_latches_the_halt(monkeypatch):
    """No magic word to get wrong while trying to stop a trading engine."""
    for val in ("stop", "HALT", "x", "losing money", "1", "yes please stop"):
        monkeypatch.setenv(EHM.HALT_NOW_ENV, val)
        st = _Latch()
        a = _Asserter(st)
        r = EHM.check_halt_now(a, st, service=SERVICE)
        assert r["acted"] is True, val
        assert r["reason"] == "LATCHED"
        assert st.puts == 1
        assert val in a.calls[0]["reason"]


def test_the_stated_reason_reaches_the_latch_and_the_evidence(monkeypatch):
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "cap looked wrong on 1415z")
    st = _Latch()
    a = _Asserter(st)
    EHM.check_halt_now(a, st, service=SERVICE)
    assert "cap looked wrong on 1415z" in a.calls[0]["reason"]
    ev = a.calls[0]["evidence"]
    assert ev["env"] == EHM.HALT_NOW_ENV
    assert ev["who"] == EHM.HALT_NOW_WHO
    assert "stated_reason" in ev


# -------------------------------------------- IT CANNOT RESET. THE KEY TEST.

def test_clearing_the_variable_does_NOT_clear_the_latch(monkeypatch):
    """The asymmetry the whole design rests on."""
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "stop now")
    st = _Latch()
    a = _Asserter(st)
    assert EHM.check_halt_now(a, st, service=SERVICE)["acted"] is True
    latched = st.get(HG_HALT_KEY)
    assert latched is not None

    monkeypatch.delenv(EHM.HALT_NOW_ENV, raising=False)
    r = EHM.check_halt_now(a, st, service=SERVICE)
    assert r["acted"] is False
    assert r["reason"] == "TRIGGER_NOT_SET"
    assert st.get(HG_HALT_KEY) == latched, (
        "unsetting the trigger must leave the latch byte-identical")
    assert json.loads(st.get(HG_HALT_KEY))["halt"] is True


def test_the_trigger_cannot_reach_any_clearing_method():
    """Structural, via the AST.

    Scanning the raw source matched the docstring, which SAYS the function
    never clears -- the third time in this session that prose satisfied a
    text assertion. The AST settles it: list every method this function
    actually calls and require that none of them clears or attests no-halt.
    """
    import ast
    import inspect as _i
    tree = ast.parse(_i.getsource(EHM.check_halt_now).lstrip())
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    banned = {"clear", "clear_halt", "resume", "unhalt", "delete", "remove",
              "reset", "heartbeat_no_halt"}
    assert not (called & banned), sorted(called & banned)
    assert "assert_halt" in called, "it must be able to HALT"


def test_the_asserter_itself_exposes_no_clear():
    names = [n for n in dir(_HG.HaltAsserter) if not n.startswith("_")]
    for forbidden in ("clear", "clear_halt", "resume", "unhalt", "delete",
                      "remove", "reset"):
        assert forbidden not in names, forbidden
    assert "assert_halt" in names


# -------------------------------------------- IDEMPOTENCE AND FAIL-CLOSED

def test_an_existing_latch_is_left_exactly_as_it_is(monkeypatch):
    """Rewriting it would move the digest the reset procedure reviews."""
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "first reason")
    st = _Latch()
    a = _Asserter(st)
    EHM.check_halt_now(a, st, service=SERVICE)
    first = st.get(HG_HALT_KEY)
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "a different reason")
    r = EHM.check_halt_now(a, st, service=SERVICE)
    assert r["acted"] is False
    assert r["reason"] == "ALREADY_LATCHED"
    assert st.get(HG_HALT_KEY) == first, "the digest must not move"
    assert st.puts == 1


def test_an_unreadable_latch_refuses_rather_than_overwriting(monkeypatch):
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "stop")
    st = _Latch({HG_HALT_KEY: b"{not json"})
    a = _Asserter(st)
    r = EHM.check_halt_now(a, st, service=SERVICE)
    assert r["acted"] is False
    assert r["reason"] == "LATCH_UNREADABLE"
    assert st.puts == 0


def test_the_trigger_never_raises_into_the_sweep(monkeypatch):
    """A broken store must not take out the independent cancellation path."""
    monkeypatch.setenv(EHM.HALT_NOW_ENV, "stop")

    class _Broken:
        def get(self, key):
            raise RuntimeError("store down")

        def put(self, key, raw):
            raise RuntimeError("store down")

    r = EHM.check_halt_now(_Asserter(_Broken()), _Broken(), service=SERVICE)
    assert r["acted"] is False
    assert r["reason"].startswith(("TRIGGER_FAILED", "LATCH_UNREADABLE"))


def test_the_watchdog_checks_the_trigger_every_sweep_and_first():
    """Wired ahead of the marker fetch, so it cannot sit behind a network call."""
    src = open("scripts/l5_halt_watchdog.py").read()
    assert "EHM.check_halt_now(" in src
    body = src[src.index("    while True:"):]
    first = body.index("EHM.check_halt_now(")
    assert "EHM.check(" not in body[:first], (
        "the one-action halt must be checked before the marker fetch")
