"""The hard per-order contract cap for the bounded one-shot probe.

WHY THIS EXISTS. On 2026-10-18 a 0.01-contract cancellation probe was planned
and its worst case quoted as about $0.011 -- but nothing in the money path
could enforce 0.01. The binding gate was `max_cash_deployed_usd` at $1 with
`committed = price * qty`, which permits `qty <= 1/p`: 1.5 contracts at a
price of 0.66 and 10 at 0.10. The quoted bound was wrong by two orders of
magnitude, and the cap that would have made it true did not exist.

These tests drive the REAL `FaithfulExecutor.handle` through the existing
faithful harness -- the same `row`, `FakeTransport` and `SafetyLimits` the
rest of the execution suite uses -- so they prove the refusal where it
actually has to happen: on the intent about to go on the wire.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_live_authorization as AUTH

from tests.test_l5_live_execution import (  # the faithful doubles, reused
    FakeTransport, NOW, limits, row, store)

CAP = E.PROBE_ORDER_CAP_ENV
REQ = E.PROBE_CAP_REQUIRED_ENV


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No ambient probe configuration leaks between cases."""
    monkeypatch.delenv(CAP, raising=False)
    monkeypatch.delenv(REQ, raising=False)


def _handle(tmp_path, side="UP", size=0.01):
    t = FakeTransport(fill_all=True)
    ex = E.FaithfulExecutor(store(tmp_path), limits(), transport=t, now_fn=NOW)
    rec = ex.handle(row(side=side, size=size), {}, market="m", window="16:15")
    return rec, t


# ------------------------------------------------- the cap permits 0.01

@pytest.mark.parametrize("side", ["UP", "DOWN"])
def test_exactly_the_cap_is_permitted(tmp_path, monkeypatch, side):
    monkeypatch.setenv(CAP, "0.01")
    rec, t = _handle(tmp_path, side=side, size=0.01)
    assert rec["ORDER"]["submitted"] is True
    assert rec["refused_by_layer"] is None
    assert len(t.sent) == 1
    assert D(str(t.sent[0]["qty"])) == D("0.01")


# ------------------------------------------------- the cap refuses more

@pytest.mark.parametrize("side", ["UP", "DOWN"])
@pytest.mark.parametrize("size", [0.02, 1, 5, 20])
def test_anything_above_the_cap_is_refused_and_never_sent(
        tmp_path, monkeypatch, side, size):
    monkeypatch.setenv(CAP, "0.01")
    rec, t = _handle(tmp_path, side=side, size=size)
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["reason"] == "PROBE_ORDER_CAP"
    assert rec["ORDER"]["detail"].startswith("PROBE_ORDER_CAP_EXCEEDED:")
    assert rec["refused_by_layer"] == "SAFETY_OVERLAY"
    # THE POINT: nothing reached the venue double at all.
    assert t.sent == []


@pytest.mark.parametrize("side", ["UP", "DOWN"])
def test_refusal_does_not_resize_the_order(tmp_path, monkeypatch, side):
    """A refusal reports the breach; it never shrinks the order to fit."""
    monkeypatch.setenv(CAP, "0.01")
    rec, t = _handle(tmp_path, side=side, size=5)
    assert t.sent == []
    assert D(str(rec["ORDER"]["intent"]["qty"])) == D("5")   # preserved as-is
    assert "5" in rec["ORDER"]["detail"]


# ------------------------------------------------- fail closed in probe mode

def test_declared_probe_mode_with_no_cap_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv(REQ, "YES")
    rec, t = _handle(tmp_path, size=0.01)
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["detail"] == "PROBE_ORDER_CAP_MISSING"
    assert t.sent == []


@pytest.mark.parametrize("bad,why", [
    ("abc", "PROBE_ORDER_CAP_UNREADABLE"),
    ("", "PROBE_ORDER_CAP_MISSING"),
    ("0", "PROBE_ORDER_CAP_NOT_POSITIVE"),
    ("-1", "PROBE_ORDER_CAP_NOT_POSITIVE"),
    ("NaN", "PROBE_ORDER_CAP_NOT_POSITIVE"),
])
def test_an_unverifiable_cap_refuses_rather_than_defaulting(
        tmp_path, monkeypatch, bad, why):
    monkeypatch.setenv(REQ, "YES")
    monkeypatch.setenv(CAP, bad)
    rec, t = _handle(tmp_path, size=0.01)
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["detail"] == why
    assert t.sent == []


def test_an_unreadable_cap_refuses_even_outside_declared_probe_mode(
        tmp_path, monkeypatch):
    """A cap someone TYPED and we cannot parse is never read as no cap."""
    monkeypatch.setenv(CAP, "not-a-number")
    rec, t = _handle(tmp_path, size=0.01)
    assert rec["ORDER"]["submitted"] is False
    assert rec["ORDER"]["detail"] == "PROBE_ORDER_CAP_UNREADABLE"
    assert t.sent == []


# ------------------------------------------- the unconfigured path is intact

def test_no_cap_and_no_declaration_leaves_behaviour_exactly_as_before(
        tmp_path, monkeypatch):
    """Requirement 5: the pre-existing limits keep doing their job alone."""
    assert E.probe_order_contract_cap() == (None, None)
    rec, t = _handle(tmp_path, size=5)
    assert rec["ORDER"]["submitted"] is True
    assert D(str(t.sent[0]["qty"])) == D("5")


# ------------------------------------------------- structural guarantees

def test_the_cap_sits_at_the_only_submit_call_site():
    """The mechanism, asserted rather than described.

    If a second submit path is ever added, this fails and the cap's claim to
    cover EVERY order path has to be re-established.
    """
    src = (E.__file__ or "")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    assert text.count("self.transport.submit(") == 1
    before = text.split("self.transport.submit(")[0]
    # the cap is evaluated, and refuses, above that single call
    assert "probe_order_contract_cap()" in before
    assert before.rindex("probe_order_contract_cap()") > before.rindex(
        "record_submit(")


def test_probe_mode_is_not_inferred_from_the_market_pin(tmp_path, monkeypatch):
    """The regression that broke five one-shot tests, pinned.

    The one-shot ALWAYS pins a market, so the pin must never by itself make a
    cap mandatory -- otherwise every pre-existing one-shot path refuses.
    """
    monkeypatch.setenv(AUTH.MARKET_ENV, "cpc-btc-updown-15m-2026-10-18-0330z")
    assert E.probe_order_contract_cap() == (None, None)
    rec, t = _handle(tmp_path, size=5)
    assert rec["ORDER"]["submitted"] is True


def test_no_cancel_function_anywhere_references_the_cap():
    """Requirement 7: cancellation must not acquire a new refusal.

    Parsed, not string-sliced: every function whose name mentions cancel is
    walked and its whole body checked, so the answer does not depend on where
    the helper happens to sit in the file.
    """
    import ast
    src = (E.__file__ or "")
    with open(src, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "cancel" not in node.name.lower():
            continue
        checked += 1
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        assert "probe_order_contract_cap" not in names, node.name
        assert "PROBE_ORDER_CAP_ENV" not in names, node.name
    assert checked > 0, "no cancel function found; the check proved nothing"


# ------------------------------------------- the startup refusal gate

def _gate(monkeypatch, cap=None, required=None, recover=None):
    import scripts.l5_pmus_live_oneshot as O
    for name, val in ((CAP, cap), (REQ, required), ("L5_RECOVER_ONLY", recover)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    return O.probe_cap_startup_gate()


def test_startup_refuses_when_nothing_is_configured(monkeypatch):
    assert _gate(monkeypatch) == "PROBE_ORDER_CAP_NOT_DECLARED"


def test_startup_refuses_when_only_the_cap_is_set(monkeypatch):
    assert _gate(monkeypatch, cap="0.01") == "PROBE_ORDER_CAP_NOT_DECLARED"


def test_startup_refuses_when_only_the_declaration_is_set(monkeypatch):
    assert _gate(monkeypatch, required="YES") == "PROBE_ORDER_CAP_MISSING"


@pytest.mark.parametrize("bad,why", [
    ("abc", "PROBE_ORDER_CAP_UNREADABLE"),
    ("0", "PROBE_ORDER_CAP_NOT_POSITIVE"),
    ("-1", "PROBE_ORDER_CAP_NOT_POSITIVE"),
    ("NaN", "PROBE_ORDER_CAP_NOT_POSITIVE"),
])
def test_startup_refuses_an_unusable_cap(monkeypatch, bad, why):
    assert _gate(monkeypatch, cap=bad, required="YES") == why


def test_startup_passes_only_when_both_are_correct(monkeypatch):
    assert _gate(monkeypatch, cap="0.01", required="YES") is None


def test_recover_only_is_never_blocked_by_the_startup_gate(monkeypatch):
    """Recovery cancels and reconciles; it must not be gated on a cap typo."""
    assert _gate(monkeypatch, recover="YES") is None
    assert _gate(monkeypatch, cap="abc", required="YES", recover="YES") is None


def test_observe_only_mode_installs_a_transport_that_cannot_transmit():
    """The structural reason observe-only cannot submit OR cancel."""
    from src import l5_live_execution as EX
    t = EX.NullTransport()
    with pytest.raises(EX.NotArmed):
        t.submit({"market": "m", "side": "UP", "price": "0.5", "qty": "0.01"})
    with pytest.raises(EX.NotArmed):
        t.cancel("any-id")
    assert t.open_orders() == [] and t.positions() == []
    assert t.armed is False


def test_the_oneshot_refuses_before_claiming_anything(monkeypatch):
    """The gate is evaluated in main() before any authorization is claimed."""
    import inspect
    import scripts.l5_pmus_live_oneshot as O
    src = inspect.getsource(O.main)
    gate_at = src.index("probe_cap_startup_gate()")
    for later in ("claim", "run()", "observe()"):
        if later in src:
            assert gate_at < src.index(later), later
