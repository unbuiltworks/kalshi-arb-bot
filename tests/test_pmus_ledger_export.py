"""Isolation proof for the ledger export.

The owner's condition was explicit: "Do not deploy it merely for
convenience without proving it cannot affect execution." These are that
proof. Each one is a property the exporter must hold before it is allowed
anywhere near the probe image.
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from src import pmus_ledger_export as ex

ROOT = Path(__file__).resolve().parents[1]
ON = {"PMUS_LEDGER_EXPORT": "1"}


@pytest.fixture(autouse=True)
def _reset():
    ex._disabled = False
    yield
    ex._disabled = False


def _ledger(tmp_path, rows):
    p = tmp_path / "ledger.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


# ------------------------------------------------------------- default off
def test_it_is_inert_unless_switched_on():
    assert ex.enabled({}) is False
    assert ex.enabled({"PMUS_LEDGER_EXPORT": "0"}) is False
    assert ex.safe_export(Path("/nonexistent"), env={}) is None


def test_switched_on_it_does_export(tmp_path):
    """...and the off-test above is not passing for the wrong reason."""
    p = _ledger(tmp_path, [{"kind": "order", "order_id": "A", "px": "0.41"}])
    out = ex.safe_export(p, env=ON)
    assert out and out["n"] == 1 and out["rows"][0]["order_id"] == "A"


# ------------------------------------------------------- cannot mutate
def test_it_never_opens_the_ledger_for_writing(tmp_path, monkeypatch):
    """THE PROPERTY THAT MATTERS MOST. A write mode on the live ledger
    would corrupt the one durable record of what we actually traded."""
    real = builtins.open
    seen = []

    def spy(file, mode="r", *a, **kw):
        seen.append(str(mode))
        assert not set(str(mode)) & set("wax+"), f"write mode {mode!r}"
        return real(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    p = _ledger(tmp_path, [{"kind": "order", "order_id": "A"}])
    assert ex.safe_export(p, env=ON)["n"] == 1
    assert seen and all(m == "r" for m in seen)


def test_the_ledger_is_byte_identical_after_an_export(tmp_path):
    p = _ledger(tmp_path, [{"kind": "order", "order_id": str(i)}
                           for i in range(50)])
    before = p.read_bytes()
    ex.safe_export(p, env=ON)
    assert p.read_bytes() == before


# --------------------------------------------------------- cannot raise
@pytest.mark.parametrize("boom", [OSError("disk"), MemoryError(),
                                  KeyboardInterrupt(), ValueError("x")])
def test_any_fault_returns_none_and_retires_the_exporter(monkeypatch, boom):
    """The executor must be able to call this on its own thread without a
    try/except of its own -- so a fault here can only cost an export."""
    def explode(*a, **kw):
        raise boom
    monkeypatch.setattr(ex, "export_chunk", explode)
    assert ex.safe_export(Path("/whatever"), env=ON) is None
    assert ex._disabled is True
    # and it stays retired: a later healthy call must not resurrect it
    monkeypatch.setattr(ex, "export_chunk", lambda *a, **kw: {"n": 1})
    assert ex.safe_export(Path("/whatever"), env=ON) is None


# ------------------------------------------------------------- bounded
def test_one_call_is_bounded_by_rows_and_by_bytes(tmp_path):
    p = _ledger(tmp_path, [{"kind": "order", "order_id": str(i)}
                           for i in range(1000)])
    out = ex.safe_export(p, env=ON, max_rows=10)
    assert out["n"] == 10 and out["eof"] is False
    out = ex.safe_export(p, env=ON, max_bytes=100)
    assert 0 < out["n"] < 1000


def test_chunks_walk_forward_and_reassemble_into_the_original(tmp_path):
    rows = [{"kind": "order", "order_id": str(i), "px": "0.41"}
            for i in range(37)]
    p = _ledger(tmp_path, rows)
    chunks, off = [], 0
    for _ in range(50):
        c = ex.safe_export(p, env=ON, max_rows=5, offset=off)
        chunks.append(c)
        if c["eof"]:
            break
        off = c["next_offset"]
    back = ex.reassemble(chunks)
    assert [r["order_id"] for r in back] == [r["order_id"] for r in rows]


def test_reassembly_is_idempotent_when_a_chunk_is_logged_twice(tmp_path):
    """Log delivery is at-least-once; a duplicated chunk must not
    duplicate rows."""
    p = _ledger(tmp_path, [{"kind": "order", "order_id": str(i)}
                           for i in range(6)])
    c = ex.safe_export(p, env=ON, max_rows=6)
    assert len(ex.reassemble([c, dict(c)])) == 6


# ------------------------------------------------------------ redaction
def test_a_field_not_on_the_allowlist_never_leaves():
    out = ex.redact({"order_id": "A", "api_key_id": "kid-123",
                     "authorization": "Bearer abc"})
    assert out == {"order_id": "A"}


def test_a_secret_shaped_value_drops_the_whole_row():
    """Partial redaction invites reasoning about what was removed."""
    assert ex.redact({"order_id": "A" * 60}) is None
    assert ex.redact({"slug": "m", "commit": "a" * 50}) is None


def test_redaction_runs_on_the_real_export_path(tmp_path):
    """The allowlist is worthless if export_chunk forgets to call it."""
    p = _ledger(tmp_path, [{"order_id": "A", "secret_key": "s" * 64},
                           {"order_id": "B", "px": "0.41"}])
    out = ex.safe_export(p, env=ON)
    assert [r["order_id"] for r in out["rows"]] == ["A", "B"]
    assert all("secret_key" not in r for r in out["rows"])


def test_a_corrupt_line_is_dropped_not_fatal(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text('{"order_id": "A"}\nnot json at all\n{"order_id": "B"}\n')
    out = ex.safe_export(p, env=ON)
    assert out["n"] == 2 and out["dropped"] == 1


# --------------------------------------------------------- not deployed
def test_nothing_armed_imports_the_exporter_yet():
    """It is built and proven, not wired. Wiring it is a separate
    decision because it means a new image on a service that moves money."""
    for rel in ("src/pmus_spring_l5_session.py",
                "src/pmus_l5_continuous_session.py",
                "src/pmus_sports_l5_decision.py"):
        p = ROOT / rel
        if p.exists():
            assert "pmus_ledger_export" not in p.read_text(), rel
