"""The automatic clean-boundary transition must fail closed everywhere.

OWNER: "when the existing a88dff63 campaign becomes flat, it automatically
runs the already-approved seven-check preflight and activates ranked selector
+ residual quarantine iff all checks pass. Fail closed. No manual owner
action."

Every test below asks the same question from a different angle: can anything
other than "flat, settled, unambiguous, and seven green checks" produce an
activation? The answer must be no.
"""
from __future__ import annotations

import json

import pytest

from src import pmus_boundary_transition as BT


class FakeContinuous:
    def __init__(self, held=(), unsettled=(), ours=0, foreign=0, boom=None):
        self._held, self._unsettled = list(held), list(unsettled)
        self._sweep = {"ours": ours, "foreign": foreign}
        self._boom = boom

    def held_markets(self, t):
        if self._boom == "held":
            raise RuntimeError("venue unreachable")
        return list(self._held)

    def unsettled_slugs(self):
        return list(self._unsettled)

    def _fresh_open_orders(self, t):
        if self._boom == "sweep":
            raise RuntimeError("sweep failed")
        return dict(self._sweep)


def _ok_preflight(monkeypatch, ok=True, failed=()):
    def fake(**kw):
        return {"kind": "boundary_preflight", "ok": ok,
                "checks": [], "failed": list(failed)}
    monkeypatch.setattr(BT.pf, "preflight", fake)


# ---- the boundary itself --------------------------------------------------

def test_a_held_position_is_not_a_clean_boundary():
    b = BT.clean_boundary(FakeContinuous(held=["aec-county-not-ham-2026-09-08"]))
    assert b["clean"] is False
    assert "flat_no_held_inventory" in b["blockers"]


def test_an_unsettled_market_is_not_a_clean_boundary():
    b = BT.clean_boundary(FakeContinuous(unsettled=["x"]))
    assert b["clean"] is False
    assert "settlements_current" in b["blockers"]


def test_a_resting_order_is_not_a_clean_boundary():
    assert BT.clean_boundary(FakeContinuous(ours=1))["clean"] is False
    assert BT.clean_boundary(FakeContinuous(foreign=1))["clean"] is False


def test_flat_settled_and_unambiguous_is_a_clean_boundary():
    assert BT.clean_boundary(FakeContinuous())["clean"] is True


def test_an_unreachable_venue_is_not_a_clean_boundary():
    """'We could not establish that the campaign is flat' is never 'the
    campaign is flat'."""
    b = BT.clean_boundary(FakeContinuous(boom="held"))
    assert b["clean"] is False
    assert "flat_no_held_inventory" in b["blockers"]
    assert "venue unreachable" in b["conditions"]["flat_no_held_inventory_error"]


def test_a_sweep_that_cannot_attribute_orders_blocks(monkeypatch):
    b = BT.clean_boundary(FakeContinuous(boom="sweep"))
    assert b["clean"] is False
    assert "no_resting_orders" in b["blockers"]


# ---- the transition -------------------------------------------------------

def test_not_flat_waits_and_never_activates(tmp_path, monkeypatch):
    _ok_preflight(monkeypatch)
    p = str(tmp_path / "act.json")
    row = BT.attempt_transition(continuous=FakeContinuous(held=["x"]), path=p)
    assert row["transition"] == BT.WAITING
    assert BT.is_active(p) is False


def test_failed_preflight_refuses_and_writes_no_record(tmp_path, monkeypatch):
    _ok_preflight(monkeypatch, ok=False, failed=["settlements_current"])
    p = str(tmp_path / "act.json")
    row = BT.attempt_transition(continuous=FakeContinuous(), path=p)
    assert row["transition"] == BT.REFUSED
    assert row["failed"] == ["settlements_current"]
    assert BT.is_active(p) is False


def test_clean_boundary_and_green_preflight_activates(tmp_path, monkeypatch):
    _ok_preflight(monkeypatch)
    p = str(tmp_path / "act.json")
    row = BT.attempt_transition(continuous=FakeContinuous(), path=p,
                                now=lambda: 1000.0)
    assert row["transition"] == BT.ACTIVATED
    assert BT.is_active(p) is True
    doc = json.loads(open(p).read())
    assert doc["active"] is True
    assert set(doc["activates"]) == set(BT.ACTIVATES)


def test_activation_is_durable_and_one_shot(tmp_path, monkeypatch):
    """A promotion, not a per-market gate: it survives a restart and the
    preflight is not re-run afterwards."""
    _ok_preflight(monkeypatch)
    p = str(tmp_path / "act.json")
    BT.attempt_transition(continuous=FakeContinuous(), path=p)

    calls = []

    def boom(**kw):
        calls.append(1)
        raise AssertionError("preflight must not re-run once active")

    monkeypatch.setattr(BT.pf, "preflight", boom)
    row = BT.attempt_transition(continuous=FakeContinuous(held=["x"]), path=p)
    assert row["transition"] == BT.ALREADY
    assert calls == []


def test_a_preflight_that_raises_refuses_rather_than_skipping_the_gate(
        tmp_path, monkeypatch):
    def boom(**kw):
        raise RuntimeError("transport gone")
    monkeypatch.setattr(BT.pf, "preflight", boom)
    p = str(tmp_path / "act.json")
    row = BT.attempt_transition(continuous=FakeContinuous(), path=p)
    assert row["transition"] == BT.REFUSED
    assert BT.is_active(p) is False


def test_an_unpersistable_activation_is_not_reported_as_activated(
        tmp_path, monkeypatch):
    """An activation that does not survive a restart is worse than none: the
    engine would promote itself and silently demote on the next boot."""
    _ok_preflight(monkeypatch)
    monkeypatch.setattr(BT, "_write", lambda *a, **k: False)
    p = str(tmp_path / "act.json")
    row = BT.attempt_transition(continuous=FakeContinuous(), path=p)
    assert row["transition"] == BT.REFUSED
    assert "persist" in row["note"]


def test_a_corrupt_record_reads_as_not_active(tmp_path):
    p = tmp_path / "act.json"
    p.write_text("{not json")
    assert BT.is_active(str(p)) is False
    p.write_text(json.dumps({"active": "yes"}))     # truthy but not True
    assert BT.is_active(str(p)) is False
    p.write_text(json.dumps({"active": False}))
    assert BT.is_active(str(p)) is False


def test_activation_records_the_envelope_it_was_granted_under(
        tmp_path, monkeypatch):
    _ok_preflight(monkeypatch)
    p = str(tmp_path / "act.json")
    BT.attempt_transition(continuous=FakeContinuous(), path=p)
    doc = json.loads(open(p).read())
    assert doc["envelope"] == {k: str(v)
                               for k, v in BT.pf.REQUIRED_ENVELOPE.items()}
    assert doc["acquisition_slots"] == BT.rq.ACTIVE_ACQUISITION_SLOTS == 1


def test_transition_never_raises_on_any_input():
    for kw in ({"continuous": FakeContinuous(boom="held")},
               {"continuous": FakeContinuous(boom="sweep")},
               {"continuous": None, "path": "/proc/nope/act.json"}):
        row = BT.attempt_transition(**kw, run_tests=False)
        assert row["transition"] in (BT.WAITING, BT.REFUSED)


# ---- mutation: each safety transition must be load-bearing ----------------

@pytest.mark.parametrize("mutation,expect_active", [
    ("skip_boundary_check", True),      # would activate while holding
    ("ignore_preflight_ok", True),      # would activate on a red preflight
])
def test_mutations_that_would_permit_a_bad_activation(
        tmp_path, monkeypatch, mutation, expect_active):
    """Proof the guards are what stops activation, not an accident of
    ordering. Each mutation is applied, shown to activate when it must not,
    and left un-restored only within this test."""
    p = str(tmp_path / "act.json")
    if mutation == "skip_boundary_check":
        _ok_preflight(monkeypatch)
        monkeypatch.setattr(BT, "clean_boundary",
                            lambda **kw: {"clean": True, "conditions": {},
                                          "blockers": []})
        cont = FakeContinuous(held=["still-holding"])
    else:
        _ok_preflight(monkeypatch, ok=False, failed=["settlements_current"])
        monkeypatch.setattr(
            BT.pf, "preflight",
            lambda **kw: {"ok": True, "checks": [], "failed":
                          ["settlements_current"]})
        cont = FakeContinuous()
    BT.attempt_transition(continuous=cont, path=p)
    assert BT.is_active(p) is expect_active, (
        "mutation did not change behaviour -- the guard under test is not "
        "the thing preventing activation")


# ---- the runner -----------------------------------------------------------

def test_runner_keeps_waiting_and_never_activates_without_a_boundary(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(BT, "attempt_transition",
                        lambda **kw: BT._row(BT.WAITING))
    BT.run(cycles=3, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert out.count(BT.PREFIX) == 3
    assert BT.ACTIVATED not in out


def test_runner_stops_deciding_once_activated(tmp_path, monkeypatch, capsys):
    """One durable record, then no more decisions -- the process stays alive
    so the service does not read as CRASHED."""
    calls = []

    def once(**kw):
        calls.append(1)
        return BT._row(BT.ACTIVATED)

    monkeypatch.setattr(BT, "attempt_transition", once)
    BT.run(cycles=5, sleep=lambda s: None)
    assert len(calls) == 1
    assert capsys.readouterr().out.count(BT.PREFIX) == 1


def test_runner_survives_an_exception_and_keeps_watching(monkeypatch, capsys):
    def boom(**kw):
        raise RuntimeError("venue down")
    monkeypatch.setattr(BT, "attempt_transition", boom)
    BT.run(cycles=2, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert out.count(BT.WAITING) == 2
    assert "failed closed" in out


def test_supervisor_holds_no_order_path():
    import ast
    tree = ast.parse(open("src/pmus_boundary_transition.py").read())
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    for banned in ("submit", "place_order", "cancel_order", "cancel_all"):
        assert banned not in calls


def test_deploy_manifest_does_not_share_the_probe_watch_path():
    import json as _json
    mine = set(_json.load(open("deploy/pmus_boundary_transition/railway.json"))
               ["build"]["watchPatterns"])
    probe = set(_json.load(open("deploy/pmus_spring_l5/railway.json"))
                ["build"]["watchPatterns"])
    assert not (mine & probe)
