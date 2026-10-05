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


def test_runner_preflight_is_credential_free_and_passes():
    from scripts import gemini_btc5m_canary_run as R
    pf = R.preflight()
    assert pf["ok"] is True, pf["failed"]
    assert pf["checks"]["btc15m_persist4_path_is_closed"] is True
    assert pf["checks"]["arithmetic_matches_authorized_cap"] is True
