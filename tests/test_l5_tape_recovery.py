"""The one-shot tape-recovery diagnostic: bounded, read-only, allowlisted.

Built to lift the 1415z `RESIDUAL_CAP` record out of the decision tape after
it turned out that note reached the tape and not the logs. These tests pin
the four properties that make running it in a money container acceptable:
it is OFF by default, it cannot scan, it cannot write, and it cannot leak.
"""

from __future__ import annotations

import hashlib
import json
import os

from src import l5_tape_recovery as TR


COID = "l5f-fe3923afb7f1f9fc4f908ad3"


def _line(coid=COID, *, cap=True, inv=True, guard=False, book=True):
    rec = {
        "market": "cpc-btc-updown-15m-2026-10-31-1415z",
        "provenance": "CONTROLLER_GENERATED",
        "stage_reached": "ORDER",
        "refused_by_layer": None, "refused_reason": None,
        "ORDER": {"client_order_id": coid, "submitted": True,
                  "reason": None, "venue_order_id": "DECP5QEG12WE",
                  # must NOT be emitted
                  "intent": {"price": "0.25", "qty": "2.08",
                             "api_key": "SECRET-SHOULD-NEVER-APPEAR"}},
        # must NOT be emitted
        "L5_NATIVE": {"wallet": "0xDEADBEEF-SHOULD-NEVER-APPEAR"},
    }
    if cap:
        rec["RESIDUAL_CAP"] = {
            "residual_realized": "2.17", "outstanding_residual": "0",
            "prospective_before": "2.17",
            "prospective_after_if_filled": "4.25",
            "abs_prospective_before": "2.17",
            "abs_prospective_after": "4.25",
            "clip_used": "2.17", "cap_contracts": "3",
            "prospective_book_flat": False,
            "increases_abs_prospective": True, "binds": True}
    if inv:
        rec["INVENTORY_CLASS"] = {"inventory_class": "INCREASE_RESIDUAL",
                                  "residual_before": "2.17",
                                  "treatment_active": False}
    if guard:
        rec["FINAL_RESIDUAL_GUARD"] = {"ok": False,
                                       "reason": "FINAL_RESIDUAL_ABOVE_CAP"}
    doc = {"t": "2026-10-31T14:21:10+00:00", "record": rec,
           "armed": True, "preflight": "PASSED", "decision_role": "OPEN"}
    if book:
        # the thing most worth dropping: a full book snapshot
        doc["row"] = {"pmus": {"bbo": {"bid": "0.27", "ask": "0.28"},
                               "depth": [["0.27", "124.72"]] * 40}}
    return json.dumps(doc) + "\n"


def _tape(tmp_path, lines):
    p = tmp_path / "decisions.jsonl"
    p.write_text("".join(lines))
    return str(p)


# ------------------------------------------------------- OFF by default

def test_it_is_off_unless_a_coid_is_named(monkeypatch):
    monkeypatch.delenv(TR.COID_ENV, raising=False)
    assert TR.configured() is None
    assert TR.from_env("/nonexistent") is None


def test_a_blank_coid_is_still_off(monkeypatch):
    monkeypatch.setenv(TR.COID_ENV, "   ")
    assert TR.configured() is None


def test_configured_returns_the_coid(monkeypatch):
    monkeypatch.setenv(TR.COID_ENV, COID)
    assert TR.configured() == COID


# ------------------------------------------------------- it finds the record

def test_it_recovers_the_named_record(tmp_path):
    path = _tape(tmp_path, [_line("l5f-other"), _line(), _line("l5f-x")])
    r = TR.recover(path, COID)
    assert r["ok"] is True
    assert r["hits"] == 1
    rec = r["records"][0]
    assert rec["order"]["client_order_id"] == COID
    assert rec["RESIDUAL_CAP"]["binds"] is True
    assert rec["RESIDUAL_CAP"]["prospective_before"] == "2.17"
    assert rec["INVENTORY_CLASS"]["residual_before"] == "2.17"


def test_an_absent_cap_note_is_reported_by_name_not_dropped(tmp_path):
    """The decisive distinction: absent means the gate never ran."""
    path = _tape(tmp_path, [_line(cap=False, inv=False)])
    rec = TR.recover(path, COID)["records"][0]
    assert rec["RESIDUAL_CAP"] == "__ABSENT__"
    assert rec["INVENTORY_CLASS"] == "__ABSENT__"


def test_a_missing_tape_is_reported_not_raised(tmp_path):
    r = TR.recover(str(tmp_path / "nope.jsonl"), COID)
    assert r["ok"] is False
    assert r["reason"] == "TAPE_NOT_FOUND"


# ------------------------------------------------------- it cannot leak

def test_the_book_and_the_l5_slot_never_leave(tmp_path):
    path = _tape(tmp_path, [_line()])
    blob = json.dumps(TR.recover(path, COID))
    assert "SHOULD-NEVER-APPEAR" not in blob
    assert "0xDEADBEEF" not in blob
    assert "SECRET" not in blob
    assert "depth" not in blob
    assert "124.72" not in blob
    assert "L5_NATIVE" not in blob
    assert "intent" not in blob


def test_only_allowlisted_order_fields_are_emitted(tmp_path):
    path = _tape(tmp_path, [_line()])
    rec = TR.recover(path, COID)["records"][0]
    assert set(rec["order"]) <= set(TR.KEEP_ORDER)


# ------------------------------------------------------- it cannot scan

def test_it_stops_at_max_hits(tmp_path):
    path = _tape(tmp_path, [_line() for _ in range(50)])
    r = TR.recover(path, COID, max_hits=3)
    assert r["hits"] == 3
    assert r["truncated"] is True


def test_it_stops_at_max_lines(tmp_path):
    path = _tape(tmp_path, [_line("l5f-other") for _ in range(500)] + [_line()])
    r = TR.recover(path, COID, max_lines=10)
    assert r["scanned_lines"] == 10
    assert r["hits"] == 0
    assert r["truncated"] is True


def test_the_bounds_are_finite_constants():
    assert TR.MAX_LINES <= 200_000
    assert TR.MAX_HITS <= 16


# ------------------------------------------------------- it cannot write

def test_the_tape_is_byte_identical_afterwards(tmp_path):
    """THE REQUIREMENT: preserve the original tape unchanged."""
    lines = [_line("l5f-other"), _line(), _line("l5f-z")]
    path = _tape(tmp_path, lines)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    before_mtime = os.stat(path).st_mtime_ns
    TR.recover(path, COID)
    after = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert after == before
    assert os.stat(path).st_mtime_ns == before_mtime


def test_the_module_opens_exactly_one_file_read_only():
    """Structural, via the AST so prose cannot satisfy or break it.

    Scanning raw source for "truncate" matched the docstring, which says the
    module does not truncate anything; and a token-level docstring stripper
    missed the module docstring. The AST settles it: find every open() call
    and assert there is one, in mode "r".
    """
    import ast
    tree = ast.parse(open("src/l5_tape_recovery.py").read())
    opens = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "open"]
    assert len(opens) == 1, [ast.dump(o) for o in opens]
    mode = opens[0].args[1]
    assert isinstance(mode, ast.Constant) and mode.value == "r", ast.dump(mode)


def test_the_module_calls_nothing_that_mutates_the_filesystem():
    """No remove/rename/truncate/write/unlink/shutil ANYWHERE in the code."""
    import ast
    tree = ast.parse(open("src/l5_tape_recovery.py").read())
    banned = {"remove", "unlink", "rename", "replace", "truncate",
              "write", "writelines", "rmtree", "mkdir", "makedirs"}
    called = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute):
                called.add(f.attr)
            elif isinstance(f, ast.Name):
                called.add(f.id)
    assert not (called & banned), sorted(called & banned)
    imported = {a.name.split(".")[0]
                for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    assert "shutil" not in imported


def test_unparseable_lines_are_counted_not_swallowed(tmp_path):
    path = _tape(tmp_path, ["{not json" + COID + "\n", _line()])
    r = TR.recover(path, COID)
    assert r["unparseable_lines"] == 1
    assert r["hits"] == 1


# ------------------------------------------------------- it is ONE-SHOT
#
# The first version had no latch. Its call site is `Runner.roll`, which fires
# on every window roll, so with the variable set it re-read the whole tape at
# 17:26, 18:00, 18:15 and 18:30 on 2026-10-31 -- read-only and within bounds
# each time, and still a continuous scan, which is what "one-shot" forbids.

def test_from_env_runs_once_per_process(tmp_path, monkeypatch):
    TR.reset_for_tests()
    root = tmp_path
    (root / "decisions.jsonl").write_text(_line())
    monkeypatch.setenv(TR.COID_ENV, COID)
    first = TR.from_env(str(root))
    assert first is not None and first["hits"] == 1
    for _ in range(5):
        assert TR.from_env(str(root)) is None, "the latch must hold"


def test_the_latch_is_per_coid(tmp_path, monkeypatch):
    TR.reset_for_tests()
    root = tmp_path
    (root / "decisions.jsonl").write_text(_line() + _line("l5f-second"))
    monkeypatch.setenv(TR.COID_ENV, COID)
    assert TR.from_env(str(root)) is not None
    assert TR.from_env(str(root)) is None
    monkeypatch.setenv(TR.COID_ENV, "l5f-second")
    assert TR.from_env(str(root)) is not None, (
        "a different coid is a different question and may run once too")


def test_the_latch_does_not_open_the_file_again(tmp_path, monkeypatch):
    """After the first run the tape is not reopened at all."""
    TR.reset_for_tests()
    root = tmp_path
    tape = root / "decisions.jsonl"
    tape.write_text(_line())
    monkeypatch.setenv(TR.COID_ENV, COID)
    assert TR.from_env(str(root)) is not None
    tape.unlink()                      # gone: a second read would error
    assert TR.from_env(str(root)) is None
