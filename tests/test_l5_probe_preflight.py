"""G-P8 must BITE. A gate that cannot fail asserts nothing.

Each test here forces one of the four assertions to fail, because the defect
being guarded against -- a settlement lost when the runtime changed operating
state -- went unnoticed for 31 hours precisely by being silent.
"""
import json
import pathlib

import pytest

from src.l5_shadow import preflight as P


# ------------------------------------------------- it passes as it stands

def test_the_gate_passes_on_the_engine_as_shipped():
    assert P.verdict()["verdict"] == "PASS"


def test_a_pass_authorizes_nothing_on_its_own():
    """A precondition is not a promotion. G-P1..G-P7 still decide."""
    v = P.verdict()
    assert "nothing on its own" in v["authorizes"]
    assert "separate human decision" in v["authorizes"]


# ------------------------------------------------- A1: envelope independence

def test_a_settlement_nested_under_a_state_key_is_still_found():
    """The exact legacy shape: settled_now[] inside a shutdown_idle envelope."""
    rec = {"kind": "shutdown_idle",
           "settlement": {"settled_now": [{"kind": "settlement", "slug": "s"}]}}
    found, unreadable = P.unwrap(rec)
    assert [f["slug"] for f in found] == ["s"]
    assert unreadable == []


def test_a1_fails_when_the_reader_selects_on_the_envelope_kind(monkeypatch):
    """Reinstate the legacy rule -- select by the TOP-LEVEL kind only -- and
    A1 must go red. If it does not, A1 is not testing anything."""
    def legacy_unwrap(rec):
        if isinstance(rec, dict) and rec.get("kind") in P.TERMINAL_KINDS:
            return [rec], []
        return [], []                      # silently drops the nested ones
    monkeypatch.setattr(P, "unwrap", legacy_unwrap)
    fills, res = P._sample()
    assert P.a1_envelope_independence(fills, res)["ok"] is False


# ------------------------------------------------- A2: silence is impossible

def test_an_unknown_operating_state_is_tallied_not_dropped():
    found, unreadable = P.unwrap({"kind": "some_future_state", "x": 1})
    assert found == []
    assert len(unreadable) == 1          # the tally that read 0 in production


def test_a_known_non_terminal_kind_is_not_flagged_as_unreadable():
    """A digest carries no money. Flagging it would drown the real signal."""
    for k in P.NON_TERMINAL_KINDS:
        found, unreadable = P.unwrap({"kind": k})
        assert found == [] and unreadable == [], k


def test_a2_fails_when_an_unreadable_record_is_dropped_silently(monkeypatch):
    monkeypatch.setattr(P, "unwrap", lambda rec: ([], []))
    assert P.a2_silence_is_impossible()["ok"] is False


# ------------------------------------------------- A3: the round trip

def test_a3_fails_when_quantity_is_lost(monkeypatch):
    """Drop a fill on the way in; conservation must catch it."""
    fills, res = P._sample()
    real = P.L.positions                      # capture BEFORE patching
    monkeypatch.setattr(P.L, "positions", lambda f: real(f[:1]))
    assert P.a3_round_trip_is_lossless(fills, res)["ok"] is False


def test_realized_is_exactly_its_three_components():
    fills, res = P._sample()
    r = P.a3_round_trip_is_lossless(fills, res)
    assert r["ok"] and r["components_sum_to_realized"]
    assert r["qty_in"] == r["qty_out"]


# ------------------------------------------------- A4: no unknown kinds

def test_a4_fails_on_a_kind_nobody_classified(tmp_path):
    """The whole point: a NEW operating state must break this gate."""
    (tmp_path / "newstate.py").write_text(
        'X = {"kind": "brand_new_halt_state"}\n')
    r = P.a4_every_kind_is_classified(str(tmp_path))
    assert r["ok"] is False
    assert "brand_new_halt_state" in r["unclassified"]


def test_a4_reads_kinds_from_source_not_from_a_hand_list():
    kinds = P.emitted_kinds("src/l5_shadow")
    assert "digest" in kinds and "intent" in kinds


def test_the_verdict_refuses_when_any_assertion_fails(tmp_path):
    (tmp_path / "s.py").write_text('Y = {"kind": "unclassified_state"}\n')
    v = P.verdict(src_dir=str(tmp_path))
    assert v["verdict"] == "REFUSED"
    assert "A4" in v["failed"]


# ------------------------------------------------- fail closed

def test_an_error_while_checking_is_a_refusal_not_a_pass(monkeypatch):
    """A preflight that crashes must not be read as a clean run."""
    def boom(*a, **k):
        raise RuntimeError("rpc down")
    monkeypatch.setattr(P, "a1_envelope_independence", boom)
    v = P.verdict()
    assert v["verdict"] == "REFUSED"
    assert "rpc down" in v["error"]


def test_the_gate_never_calls_shadow_economics_realized_profit():
    v = P.verdict()
    blob = json.dumps(v)
    assert "realized_shadow" in blob
    assert "realized profit" not in blob.replace(
        "never converts shadow economics into realized profit", "")
