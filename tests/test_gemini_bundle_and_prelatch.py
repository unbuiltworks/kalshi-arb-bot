"""The bundle hash, and the ordering property it was added to protect.

TWO AUTHORIZATIONS HAVE NOW BEEN SPENT BY REFUSALS THAT SENT NOTHING.
CONTINUOUS-1 to a preflight that ran after the latch; CONTINUOUS-3 to a
recovery that ran after the latch. Both fixes live in run_continuous.py, which
`executable_sha` cannot see -- so an authorization naming only the driver would
not notice either of them being undone. `bundle_sha` is what closes that gap.
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from src.gemini_execution import bundle as B
from src.gemini_execution import run_continuous as RC
from src.gemini_execution import ws_continuous as WC

SYMBOL = "GEMI-BTC05M2609181620-UP"
STALE = [
    {"kind": "PLACE", "slot": 0, "t": 1.0, "auth_id": "OLD", "symbol": SYMBOL,
     "outcome": "ACCEPTED", "client_order_id": "c0"},
    {"kind": "CANCEL", "slot": 1, "t": 2.0, "auth_id": "OLD", "symbol": SYMBOL,
     "outcome": "UNKNOWN", "client_order_id": "c0"},
]


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop(
        ).run_until_complete(coro)


# ------------------------------------------------------------ BUNDLE HASH

def test_the_bundle_covers_the_invoker_that_executable_sha_cannot_see():
    assert "run_continuous.py" in B.MEMBERS
    assert "ws_continuous.py" in B.MEMBERS
    assert "bundle.py" in B.MEMBERS, (
        "the member list must hash itself, or it could be edited freely")


def test_the_bundle_hash_is_stable_across_calls():
    assert B.bundle_sha() == B.bundle_sha()
    assert len(B.bundle_sha()) == 64


def test_installing_an_authorization_does_not_move_the_bundle():
    """The same detached-signature property the driver needs. Without it, a
    bundle authorization could never be satisfied -- installing it would
    change the very hash it names."""
    body = B.bundle_body()
    assert "<BUNDLE-AUTHORIZATION>" in body
    # count normalized ASSIGNMENT LINES, not raw token occurrences: bundle.py
    # carries the token once more as the literal that DEFINES it, and counting
    # occurrences is exactly the slip this suite already made once.
    pat = re.compile(
        r"^(?:REQUIRED_AUTH_ID|AUTHORIZED_SHA|AUTHORIZED_ENVELOPE|"
        r"AUTHORIZED_BUNDLE|EXPECTED_AUTH_ID)"
        r"(?:: Optional\[str\])? = <BUNDLE-AUTHORIZATION>$", re.MULTILINE)
    for member, want in B.EXPECTED_AUTH_LINES.items():
        got = len(pat.findall(B.normalized(member)))
        assert got == want, (member, got, want)


def test_an_edit_to_the_invoker_moves_the_bundle_but_not_the_driver(
        monkeypatch):
    """The exact property that makes the bundle worth having."""
    real = B.member_source
    monkeypatch.setattr(B, "member_source",
                        lambda n: (real(n) + "\n# edit\n"
                                   if n == "run_continuous.py" else real(n)))
    assert B.bundle_sha() != B.state.__globals__["AUTHORIZED_BUNDLE"] or True
    moved = B.bundle_sha()
    monkeypatch.undo()
    assert moved != B.bundle_sha()
    assert WC.executable_sha() == (
        "842685116fb95c7917b5ec286c12ee3d3da61ccc76d8560069b89e6c9b506ad4")


def test_a_member_gaining_an_authorization_constant_is_refused(monkeypatch):
    real = B.member_source
    monkeypatch.setattr(
        B, "member_source",
        lambda n: (real(n) + '\nAUTHORIZED_SHA = "x"\n'
                   if n == "continuous_probe.py" else real(n)))
    with pytest.raises(B.BundleNormalizationFailed):
        B.bundle_sha()


def test_a_member_losing_an_authorization_constant_is_refused(monkeypatch):
    real = B.member_source
    monkeypatch.setattr(
        B, "member_source",
        lambda n: (real(n).replace('EXPECTED_AUTH_ID = ', '_RETIRED = ')
                   if n == "arming.py" else real(n)))
    with pytest.raises(B.BundleNormalizationFailed):
        B.bundle_sha()


def test_no_bundle_authorization_is_installed_right_now():
    assert B.AUTHORIZED_BUNDLE is None
    assert B.authorized() is False
    assert "no bundle authorization" in " ".join(B.state()["why_not"])


def test_the_bundle_gate_can_open(monkeypatch):
    """CORRECTLY CLOSED vs INCAPABLE, for the bundle too."""
    monkeypatch.setattr(B, "AUTHORIZED_BUNDLE", B.bundle_sha())
    assert B.authorized() is True
    assert B.state()["why_not"] == []


# --------------------------------- A STALE LEDGER MUST NOT COST A LATCH

def _arm_everything(monkeypatch, tmp_path, ledger_rows, spy):
    from src.gemini_execution import latch as L
    p = tmp_path / "led.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in ledger_rows),
                 encoding="utf-8")

    monkeypatch.setattr(RC, "eligible_symbol",
                        lambda *a, **k: {"eligible": True,
                                         "chosen": {"symbol": SYMBOL,
                                                    "tte_s": 250.0}})

    async def _terms():
        return {"accepted": True}
    monkeypatch.setattr(RC.MD, "terms_status", _terms)
    monkeypatch.setattr(RC.PF, "preflight_now", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(RC.PF, "check_frozen", lambda pf: (True, []))
    monkeypatch.setattr(RC.WC, "authorization_state",
                        lambda: {"authorized": True, "why_not": []})
    monkeypatch.setattr(RC.BUNDLE, "state",
                        lambda: {"authorized": True, "why_not": []})

    async def _auth_state(symbol):
        return {"symbol": symbol, "fills": [], "open_orders": [],
                "orders_seen": [], "order_history_raw": {"readable": True},
                "active_raw": {"ok": True}}
    monkeypatch.setattr(RC, "authoritative_state", _auth_state)
    monkeypatch.setattr(L, "consume", spy)
    return str(p)


def test_a_stale_ledger_row_refuses_without_consuming_the_latch(
        tmp_path, monkeypatch):
    """THE CONTINUOUS-3 REGRESSION, pinned behaviourally rather than by
    reading the source. A deterministic, read-only refusal must be free."""
    calls = []

    def spy(*a, **k):
        calls.append(a)
        raise AssertionError("latch.consume must not be reached")

    led = _arm_everything(monkeypatch, tmp_path, STALE, spy)
    rep = _run(RC.main_async("AUTH-TEST", state_dir=str(tmp_path),
                             ledger_path=led,
                             out_path=str(tmp_path / "out.json")))

    assert calls == [], "the latch was consumed by a refusal that sent nothing"
    assert rep["latch"]["consumed"] is False
    assert rep["state"] == WC.RECONCILE
    assert "no recorded outcome" in rep["why"]
    assert rep["pre_latch_recovery"]["checked_before_latch"] is True
    assert rep["pre_latch_recovery"]["recovered"] is False
    assert len(rep["pre_latch_recovery"]["unresolved"]) == 1
    assert "wire" not in rep, "no session should ever have been built"


def test_a_clean_ledger_reaches_the_latch(tmp_path, monkeypatch):
    """The positive counterpart. Without it, a pre-latch gate that ALWAYS
    refused would pass the test above and block every window forever."""
    reached = []

    def spy(auth_id, intent, state_dir, **k):
        reached.append(auth_id)
        raise RuntimeError("stop here: the latch was reached, which is the "
                           "whole assertion")

    led = _arm_everything(monkeypatch, tmp_path, [], spy)
    rep = _run(RC.main_async("AUTH-TEST", state_dir=str(tmp_path),
                             ledger_path=led,
                             out_path=str(tmp_path / "out.json")))
    assert reached == ["AUTH-TEST"], rep.get("why")
    assert rep["pre_latch_recovery"]["recovered"] is True
    assert rep["pre_latch_recovery"]["budget_consumed"] == 0


def test_the_pre_latch_probe_does_not_write_to_the_ledger(
        tmp_path, monkeypatch):
    """recover() is a READ. If the probe ever appended, running the gate would
    itself consume budget."""
    def spy(*a, **k):
        raise AssertionError("not reached")
    led = _arm_everything(monkeypatch, tmp_path, STALE, spy)
    before = open(led, encoding="utf-8").read()
    _run(RC.main_async("AUTH-TEST", state_dir=str(tmp_path), ledger_path=led,
                       out_path=str(tmp_path / "out.json")))
    assert open(led, encoding="utf-8").read() == before


# ------------------------------- THE AUTHORITATIVE READ MUST ACTUALLY READ

def test_order_history_returns_rows_only_when_asked():
    """Default OFF keeps every existing caller summarising. The continuous
    path opts in because it must reconstruct inventory from the rows."""
    import inspect
    from src.gemini_execution import metadata as MD
    sig = inspect.signature(MD.order_history)
    assert sig.parameters["include_rows"].default is False


def test_the_invoker_opts_in_to_rows():
    """Without include_rows the extraction below reads a key that is never
    produced, and fills comes back [] on every call."""
    src = open(RC.__file__, encoding="utf-8").read()
    i = src.index("MD.order_history(")
    assert "include_rows=True" in src[i:i + 200], (
        "authoritative_state must ask for the rows it then extracts")


def test_readable_but_not_extractable_is_unreadable_not_empty(monkeypatch):
    """THE DEFECT THAT HID FOR THREE WINDOWS. order_history reported
    total_rows 2 while the extraction found none, and the result was scored as
    'no fills' rather than 'this read did not work'."""
    async def _hist(**kw):
        # the SHAPE order_history really returns without include_rows:
        # counts and samples, and no row list at all
        return {"readable": True, "verdict": "ORDERS_FOUND", "total_rows": 2,
                "per_status": [{"status_filter": "filled", "rows": 2,
                                "btc_rows": 2, "sample": {"orderId": 1}}]}
    from src.gemini_execution import transport as T
    monkeypatch.setattr(RC.MD, "order_history", _hist)

    async def _active(symbol):
        return {"ok": True, "result": []}
    monkeypatch.setattr(T, "active_orders", _active)
    monkeypatch.setattr(T, "_orders_of", lambda r: [])

    st = _run(RC.authoritative_state(SYMBOL))
    assert st["fills"] is None, (
        "a read that could not be extracted must be UNREADABLE, so recovery "
        "refuses instead of assuming the account is flat")


def test_extractable_rows_are_used(monkeypatch):
    """The positive counterpart: when the rows ARE there they reach the
    Driver as fills, or the fix would be a permanent refusal."""
    async def _hist(**kw):
        assert kw.get("include_rows") is True
        return {"readable": True, "total_rows": 1, "per_status": [
            {"status_filter": "filled", "rows": 1, "orders": [
                {"outcome": "yes", "filledQuantity": "0.01", "price": "0.03"}]}]}
    from src.gemini_execution import transport as T
    monkeypatch.setattr(RC.MD, "order_history", _hist)

    async def _active(symbol):
        return {"ok": True, "result": []}
    monkeypatch.setattr(T, "active_orders", _active)
    monkeypatch.setattr(T, "_orders_of", lambda r: [])

    st = _run(RC.authoritative_state(SYMBOL))
    assert st["fills"] == [{"leg": "YES", "qty": "0.01", "price": "0.03"}]
    assert len(st["orders_seen"]) == 1


def test_metadata_is_in_the_bundle():
    """It feeds authoritative recovery, so a change to it is a change to a
    safety property and an authorization must see it."""
    assert "metadata.py" in B.MEMBERS
