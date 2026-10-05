"""Safety gates for the BTC5m execution canary. Money-moving code."""
import asyncio
import json
import os

import pytest

from src.gemini_execution import arming, btc5m_canary as C
from src.gemini_execution import ci15m_probe as P, payload


# ---- the authorized envelope, verbatim
def test_envelope_matches_the_written_authorization():
    e = C.AUTHORIZED_ENVELOPE
    assert e["quantity"] == "1"
    assert e["max_acquisition_price"] == "0.90"
    assert e["max_entry_attempts"] == 5
    assert e["max_attempts_per_window"] == 1
    assert e["max_outstanding_orders"] == 1
    assert e["max_cohort_acquisition_outlay_usd"] == "4.5315"
    assert e["hold_to_settlement"] is True
    assert e["sell_permitted"] is False
    assert e["averaging_down_permitted"] is False
    assert e["size_changes_permitted"] is False
    assert e["deadman"] is True


def test_the_arithmetic_equals_the_authorized_figure():
    assert str(C.worst_case_per_trade()) == "0.9063"
    assert str(C.worst_case_cohort()) == "4.5315"
    assert (str(C.worst_case_cohort())
            == C.AUTHORIZED_ENVELOPE["max_cohort_acquisition_outlay_usd"])


def test_q1_is_permitted_and_is_100x_the_venue_minimum():
    from decimal import Decimal as D
    q = D(C.AUTHORIZED_ENVELOPE["quantity"])
    assert q >= C.VENUE_QUANTITY_MINIMUM
    assert q % C.VENUE_QUANTITY_INCREMENT == 0
    assert q / C.VENUE_QUANTITY_MINIMUM == 100


# ---- both authorization constants, and the separation they enforce
def test_both_constants_name_this_grant():
    assert arming.EXPECTED_AUTH_ID == C.CANARY_AUTH_ID
    assert payload.SEMANTICS_PROBE_AUTH_ID == C.CANARY_AUTH_ID
    g = C.auth_gate()
    assert g["arming_matches"] and g["taker_key_matches"]


def test_this_grant_closes_the_btc15m_persist4_path():
    """Authorizing the canary must NOT authorize the PERSIST4 cohort."""
    assert P.PROBE_AUTH_ID != arming.EXPECTED_AUTH_ID
    assert C.auth_gate()["ci15m_submit_is_now_refused"] is True


def test_no_sell_path_and_no_sell_import():
    import ast
    import pathlib
    src = pathlib.Path("src/gemini_execution/btc5m_canary.py").read_text()
    mods = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            mods.append(getattr(n, "module", None) or "")
            mods += [a.name for a in n.names]
    assert not any("sell_path" in (m or "") for m in mods)
    assert "SIDE_SELL" not in src


# ---- the frozen rule refuses, for each reason, fail-closed
def _bk(**kw):
    base = {"readable": True, "bid": "0.40", "ask": "0.55",
            "bid_size": "25", "ask_size": "25", "snapshot_age_s": 0.3}
    base.update(kw)
    return base


def test_rule_qualifies_on_a_good_two_sided_book(monkeypatch):
    monkeypatch.setattr(P, "book_from_venue", lambda s: _bk())
    d = C.evaluate("GEMI-BTC05M1-UP", elapsed_since_open_s=30.0,
                   quote_age_s=None)
    assert d["qualifies"] is True and d["failed_checks"] == []
    assert d["side"] == "UP" and d["requested_quantity"] == "1"
    assert d["requested_price"] == "0.55"


@pytest.mark.parametrize("bk,elapsed,why", [
    ({"readable": False, "why": "one-sided"}, 30.0, "ladder_readable"),
    (_bk(ask="0.95"), 30.0, "acquisition_within_cap"),
    (_bk(ask_size="0.5"), 30.0, "economically_two_sided_q1"),
    (_bk(bid_size="0.5"), 30.0, "economically_two_sided_q1"),
    (_bk(), 5.0, "checkpoint_at_or_after_min"),
    (_bk(snapshot_age_s=9.0), 30.0, "quote_fresh"),
])
def test_rule_refuses_each_way(monkeypatch, bk, elapsed, why):
    monkeypatch.setattr(P, "book_from_venue", lambda s: bk)
    d = C.evaluate("GEMI-BTC05M1-UP", elapsed_since_open_s=elapsed,
                   quote_age_s=None)
    assert d["qualifies"] is False
    assert why in d["failed_checks"]


def test_a_caller_cannot_make_a_stale_book_look_fresh(monkeypatch):
    """The ladder's own snapshot age is the authority."""
    monkeypatch.setattr(P, "book_from_venue",
                        lambda s: _bk(snapshot_age_s=30.0))
    d = C.evaluate("GEMI-BTC05M1-UP", elapsed_since_open_s=60.0,
                   quote_age_s=0.0)
    assert d["qualifies"] is False
    assert "quote_fresh" in d["failed_checks"]
    assert d["quote_age_s"] == 30.0


def test_elapsed_unknown_refuses(monkeypatch):
    monkeypatch.setattr(P, "book_from_venue", lambda s: _bk())
    d = C.evaluate("GEMI-BTC05M1-UP", elapsed_since_open_s=None,
                   quote_age_s=None)
    assert d["qualifies"] is False
    assert "checkpoint_at_or_after_min" in d["failed_checks"]


# ---- the write path refuses before touching the venue
def _ledger(tmp_path, monkeypatch, rows=()):
    d = tmp_path / "canary"
    d.mkdir()
    monkeypatch.setattr(C, "LEDGER_DIR", str(d))
    monkeypatch.setattr(C, "LEDGER", str(d / "cohort.jsonl"))
    monkeypatch.setattr(C, "HALT", str(d / "HALT.json"))
    for r in rows:
        C._append(r)


def _good_decision(monkeypatch):
    monkeypatch.setattr(P, "book_from_venue", lambda s: _bk())
    return C.evaluate("GEMI-BTC05M1-UP", elapsed_since_open_s=30.0,
                      quote_age_s=None)


def _no_send(monkeypatch):
    sent = {"n": 0}

    async def boom(**kw):
        sent["n"] += 1
        raise AssertionError("the venue must not be reached")
    monkeypatch.setattr(C.transport, "place", boom)
    return sent


def test_refuses_when_halted(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    C.halt("TEST")
    sent = _no_send(monkeypatch)
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "HALTED" in str(e.value) and sent["n"] == 0


def test_refuses_a_non_qualifying_decision(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    sent = _no_send(monkeypatch)
    with pytest.raises(C.CanaryRefused):
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              {"qualifies": False,
                               "failed_checks": ["x"]}))
    assert sent["n"] == 0


def test_refuses_once_the_attempt_cap_is_spent(tmp_path, monkeypatch):
    """NOTE the refusal ORDER: the authorization gate fires before every
    budget check, so a disarmed environment refuses everything at the gate.
    These tests install the gate in order to reach the budget check they are
    about -- they are not relaxing it."""
    rows = [{"kind": "SLOT", "slot": i + 1, "market": "W%d" % i,
             "committed_usd": "0.10"} for i in range(5)]
    _ledger(tmp_path, monkeypatch, rows)
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": True,
        "env_matches": True})
    sent = _no_send(monkeypatch)
    assert C.cohort_state()["attempts_remaining"] == 0
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "attempt cap" in str(e.value) and sent["n"] == 0


def test_refuses_a_second_attempt_in_the_same_window(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, [
        {"kind": "SLOT", "slot": 1, "market": "GEMI-BTC05M1-UP",
         "committed_usd": "0.60"}])
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": True,
        "env_matches": True})
    sent = _no_send(monkeypatch)
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "already consumed" in str(e.value) and sent["n"] == 0


def test_refuses_when_the_outlay_cap_would_be_breached(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch, [
        {"kind": "SLOT", "slot": 1, "market": "W1", "committed_usd": "4.40"}])
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": True,
        "env_matches": True})
    sent = _no_send(monkeypatch)
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "outlay cap" in str(e.value) and sent["n"] == 0


def test_the_slot_is_durable_before_the_venue_is_touched(tmp_path,
                                                         monkeypatch):
    """An ambiguous send must find its slot already spent."""
    _ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": True,
        "env_matches": True})

    async def ok_outstanding(symbol):
        return {"ok": True}
    monkeypatch.setattr(P, "one_outstanding_order", ok_outstanding)

    async def exploding_place(**kw):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(C.transport, "place", exploding_place)
    row = asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                                _good_decision(monkeypatch)))
    assert row["kind"] == "SUBMIT_AMBIGUOUS"
    st = C.cohort_state()
    assert st["attempts_consumed"] == 1, "the slot must be SPENT"
    assert st["halted"] is not None, "an ambiguous send must HALT"


def test_refuses_when_an_order_is_already_outstanding(tmp_path, monkeypatch):
    _ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": True,
        "env_matches": True})

    async def busy(symbol):
        return {"ok": False, "why": "an order is already outstanding"}
    monkeypatch.setattr(P, "one_outstanding_order", busy)
    sent = _no_send(monkeypatch)
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "outstanding" in str(e.value) and sent["n"] == 0


def test_refuses_when_either_authorization_constant_disagrees(tmp_path,
                                                              monkeypatch):
    _ledger(tmp_path, monkeypatch)
    sent = _no_send(monkeypatch)
    monkeypatch.setattr(C, "auth_gate", lambda: {
        "arming_matches": True, "taker_key_matches": False,
        "env_matches": True})
    with pytest.raises(C.CanaryRefused) as e:
        asyncio.run(C.attempt("GEMI-BTC05M1-UP",
                              _good_decision(monkeypatch)))
    assert "taker_key_matches" in str(e.value) and sent["n"] == 0


def test_every_external_callable_the_runner_uses_actually_exists():
    """The bug class that has bitten this programme three times: a guessed
    function name inside a try/except, failing silently.

    An earlier draft of the runner called metadata.list_events and
    metadata.active_events -- neither exists -- and discovery would have
    returned empty, so the canary would have found no windows and done
    nothing while reporting success.
    """
    from src.gemini_execution import ci15m_probe as PR
    from src.gemini_execution.r2_exec_runner import _active_events_public
    assert callable(_active_events_public)
    for name in ("book_from_venue", "one_outstanding_order", "poll_terminal",
                 "authenticated_fills", "classify_ack"):
        assert callable(getattr(PR, name)), name
    import src.gemini_execution.metadata as M
    for absent in ("list_events", "active_events"):
        assert not hasattr(M, absent), (
            "%s exists now; the runner's comment is stale" % absent)


def test_runner_preflight_is_credential_free_and_passes(tmp_path,
                                                         monkeypatch):
    """On an EMPTY ledger. Cohort 1 spent all five attempts, so against the
    real ledger this preflight now correctly reports attempts_remaining
    false -- which is the cap working, not a regression. The test isolates
    the ledger so it keeps testing the logic rather than today's balance."""
    from src.gemini_execution import btc5m_canary as B
    from scripts import gemini_btc5m_canary_run as R
    d = tmp_path / "led"
    d.mkdir()
    monkeypatch.setattr(B, "LEDGER_DIR", str(d))
    monkeypatch.setattr(B, "LEDGER", str(d / "cohort.jsonl"))
    monkeypatch.setattr(B, "HALT", str(d / "HALT.json"))
    pf = R.preflight()
    assert pf["ok"] is True, pf["failed"]
    assert pf["checks"]["btc15m_persist4_path_is_closed"] is True
    assert pf["checks"]["arithmetic_matches_authorized_cap"] is True


# --------------------------------------------------------------------------
# PLUMBING REGRESSIONS found while diagnosing a zero-attempt live cohort.
# Neither of these touches the frozen entry rule.
def test_a_window_without_availableAt_is_retried_not_burned():
    """`seen.add` must be reachable ONLY when the window was really worked.

    The first build added the symbol to `seen` before work_window ran, so a
    window discovered in the instant before the venue published its
    strike.availableAt was skipped and then never revisited -- a silent route
    to a cohort that spends no attempts while reporting success.
    """
    import ast
    import inspect
    from scripts import gemini_btc5m_canary_run as R

    tree = ast.parse(inspect.getsource(R.main_async))
    adds = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add"
            and getattr(n.func.value, "id", None) == "seen"]
    assert adds, "the loop no longer records worked windows at all"
    for node in adds:
        guarded = any(
            isinstance(p, ast.If)
            and any(a is node for a in ast.walk(p))
            for p in ast.walk(tree))
        assert guarded, "seen.add is unconditional again"


def test_the_runner_traces_every_decision(capsys):
    """A live run must be observable WHILE it runs, not only at the end."""
    import json as _json
    from scripts import gemini_btc5m_canary_run as R

    R.trace("DECISION", window="BTC05M9999999999", qualifies=False,
            failed_checks=["ladder_readable"])
    rec = _json.loads(capsys.readouterr().out.strip())
    assert rec["kind"] == "DECISION"
    assert rec["failed_checks"] == ["ladder_readable"]
    assert rec["utc"]


def test_the_preflight_fails_closed_when_the_submit_path_cannot_import():
    """Cohort run 1's whole lesson, as a test.

    12 of 13 windows reached a qualifying book at T+15 and every one was
    refused with "active orders could not be read (No module named
    'dotenv')": src.gemini_prediction_auth imports python-dotenv at module
    scope and the workflow did not install it. The executor failed closed
    correctly. The preflight passed, and should not have.
    """
    import builtins
    import sys

    real = builtins.__import__

    def blocked(name, *a, **k):
        if name == "dotenv" or name.startswith("dotenv."):
            raise ModuleNotFoundError("No module named 'dotenv'")
        return real(name, *a, **k)

    saved = {m: sys.modules[m] for m in list(sys.modules)
             if "gemini_prediction_auth" in m or m == "dotenv"}
    try:
        for m in saved:
            del sys.modules[m]
        builtins.__import__ = blocked
        from scripts.gemini_btc5m_canary_run import _auth_path_imports
        ok, why = _auth_path_imports()
        assert ok is False
        assert "dotenv" in why
    finally:
        builtins.__import__ = real
        sys.modules.update(saved)


def test_the_canary_workflow_installs_the_auth_dependency():
    wf = open(".github/workflows/gemini-btc5m-canary.yml",
              encoding="utf-8").read()
    install = [ln for ln in wf.splitlines() if "pip install" in ln]
    assert install, "the workflow no longer installs anything"
    assert any("python-dotenv" in ln for ln in install), install


# --------------------------------------------------------------------------
# ATTRIBUTION. Cohort 1's five genuine fills came back FOREIGN because
# ci15m_probe.attribute_fills reads intents from the CI15M cohort root while
# this cohort's authorizations are in its own ledger. The fix adds an exact
# order_id route and TIGHTENS the envelope route rather than relaxing it.
def _attrib_ledger(tmp_path, monkeypatch, *, price="0.72", oid="111"):
    import json as _json
    from src.gemini_execution import btc5m_canary as B
    d = tmp_path / "led"
    d.mkdir()
    monkeypatch.setattr(B, "LEDGER_DIR", str(d))
    monkeypatch.setattr(B, "LEDGER", str(d / "cohort.jsonl"))
    monkeypatch.setattr(B, "HALT", str(d / "HALT.json"))
    with open(d / "cohort.jsonl", "w", encoding="utf-8") as fh:
        fh.write(_json.dumps({"kind": "SLOT", "slot": 1, "market": "M",
                              "requested_price": price,
                              "requested_quantity": "1"}) + "\n")
        fh.write(_json.dumps({"kind": "SUBMITTED", "slot": 1, "market": "M",
                              "venue": {"body": {"orderId": oid}}}) + "\n")
    return B


def test_our_exact_order_id_is_attributed(tmp_path, monkeypatch):
    B = _attrib_ledger(tmp_path, monkeypatch)
    got = B.attribute_fills("M", [{"order_id": "111", "leg": "yes",
                                   "price": "0.72", "qty": "1", "tid": 1}])
    assert got["ok"] is True
    assert got["attributed"][0]["_attribution_route"] == "ORDER_ID"


def test_a_stranger_order_id_is_foreign_even_inside_our_envelope(
        tmp_path, monkeypatch):
    """The hole the order_id route closes, not one it opens.

    The envelope route alone absorbed order_id 999 at 0.50 against our 0.72
    authorization -- a stranger's fill on our symbol, taken onto our books
    because its price looked good. A fill that carries an identifier we never
    minted is FOREIGN however well-behaved its price.
    """
    B = _attrib_ledger(tmp_path, monkeypatch)
    got = B.attribute_fills("M", [{"order_id": "999", "leg": "yes",
                                   "price": "0.50", "qty": "1", "tid": 1}])
    assert got["ok"] is False
    assert got["foreign"]
    assert B.halted() is not None


def test_our_order_id_above_our_limit_is_still_refused(tmp_path, monkeypatch):
    """An exact identifier does not license paying above the envelope."""
    B = _attrib_ledger(tmp_path, monkeypatch)
    got = B.attribute_fills("M", [{"order_id": "111", "leg": "yes",
                                   "price": "0.95", "qty": "1", "tid": 1}])
    assert got["ok"] is False


def test_one_authorization_absorbs_one_fill(tmp_path, monkeypatch):
    B = _attrib_ledger(tmp_path, monkeypatch)
    got = B.attribute_fills("M", [
        {"order_id": "111", "leg": "yes", "price": "0.72", "qty": "1",
         "tid": 1},
        {"order_id": "111", "leg": "yes", "price": "0.72", "qty": "1",
         "tid": 2}])
    assert got["ok"] is False


def test_a_fill_with_no_order_id_still_matches_the_envelope(
        tmp_path, monkeypatch):
    """The crash case: the venue executed an authorization whose orderId we
    never recorded. That must stay attributable."""
    B = _attrib_ledger(tmp_path, monkeypatch, oid=None)
    got = B.attribute_fills("M", [{"order_id": None, "leg": "yes",
                                   "price": "0.70", "qty": "1", "tid": 1}])
    assert got["ok"] is True
    assert got["attributed"][0]["_attribution_route"] == "INTENT_ENVELOPE"


def test_the_runner_records_strike_provenance_not_a_bare_number():
    import ast
    import inspect
    from scripts import gemini_btc5m_canary_run as R

    src = inspect.getsource(R.discover)
    tree = ast.parse(src)
    keys = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "strike_source" in keys and "strike_value" in keys
    assert "ABSENT" in keys, "an absent strike must stay ABSENT"


def test_the_audit_reports_cash_separately_from_settlement():
    from scripts import gemini_btc5m_cohort_audit as A
    chain = A.cohort_chain()
    assert chain, "the cohort ledger is unreadable"
    assert all(c["ACK"]["order_id"] for c in chain)
