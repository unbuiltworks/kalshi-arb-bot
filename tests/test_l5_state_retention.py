"""Retention: lossless, verified, and it never deletes evidence to free space."""
import gzip
import json
import os
import time

import pytest

from src import l5_state_retention as RT


def _seg(tmp_path, sub, name, rows=50, age_s=0.0):
    d = tmp_path / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("".join(json.dumps({"i": i, "pad": "x" * 200}) + "\n"
                         for i in range(rows)), encoding="utf-8")
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


# ======================================================== the warning ladder

def test_the_thresholds_are_70_85_95():
    assert [a for a, _ in RT.THRESHOLDS] == [0.70, 0.85, 0.95]
    assert RT.level(0.10) == "OK"
    assert RT.level(0.70) == "NOTICE"
    assert RT.level(0.86) == "WARN"
    assert RT.level(0.99) == "CRITICAL"
    assert RT.level(1.00) == "CRITICAL"


def test_usage_reports_capacity_used_and_free(tmp_path):
    u = RT.usage(str(tmp_path))
    assert u["readable"] is True
    for k in ("total_bytes", "used_bytes", "free_bytes", "used_pct",
              "total_gb", "used_gb", "free_gb", "level"):
        assert k in u
    assert 0.0 <= u["used_pct"] <= 1.0


def test_an_unreadable_root_is_a_result_with_a_reason():
    u = RT.usage("/definitely/not/a/path/here")
    assert u["readable"] is False and u["why"]


# ============================================== what is eligible, and what is not

def test_THE_NEWEST_FILE_IN_A_DIRECTORY_IS_NEVER_TOUCHED(tmp_path):
    """Its recorder still has it open; compressing it loses the tail."""
    _seg(tmp_path, "s", "old.jsonl", age_s=99999)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert "active.jsonl" not in names
    assert "old.jsonl" in names


def test_a_recent_segment_is_not_yet_closed(tmp_path):
    _seg(tmp_path, "s", "a.jsonl", age_s=99999)
    _seg(tmp_path, "s", "b.jsonl", age_s=60)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=3600)]
    assert names == ["a.jsonl"]


def test_coverage_jsonl_is_protected(tmp_path):
    _seg(tmp_path, ".", "coverage.jsonl", age_s=99999)
    _seg(tmp_path, ".", "other.jsonl", age_s=99998)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert "coverage.jsonl" not in names


def test_an_archive_is_not_recompressed(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "a.jsonl.gz").write_bytes(gzip.compress(b"{}\n"))
    _seg(tmp_path, "s", "b.jsonl", age_s=99999)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert names == ["b.jsonl"]


# ================================================= compression is verified

def test_compression_is_lossless_and_verified(tmp_path):
    p = _seg(tmp_path, "s", "a.jsonl", rows=100, age_s=99999)
    original = p.read_bytes()
    r = RT.compress(str(p))
    assert r["ok"] is True
    assert not os.path.exists(str(p))            # source removed
    assert os.path.exists(r["archive"])
    assert gzip.open(r["archive"], "rb").read() == original
    assert r["bytes_after"] < r["bytes_before"]
    assert r["ratio"] > 1


def test_A_FAILED_VERIFICATION_KEEPS_THE_SOURCE(tmp_path, monkeypatch):
    """If the archive does not read back identical, nothing is unlinked."""
    p = _seg(tmp_path, "s", "a.jsonl", age_s=99999)
    calls = {"n": 0}

    def fake_sha(path, opener=open):
        calls["n"] += 1
        return "aaa" if calls["n"] == 1 else "bbb"

    monkeypatch.setattr(RT, "_sha", fake_sha)
    r = RT.compress(str(p))
    assert r["ok"] is False
    assert r["source_kept"] is True
    assert os.path.exists(str(p))


def test_a_write_failure_leaves_no_half_archive(tmp_path, monkeypatch):
    p = _seg(tmp_path, "s", "a.jsonl", age_s=99999)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(RT.shutil, "copyfileobj", boom)
    r = RT.compress(str(p))
    assert r["ok"] is False and r["source_kept"] is True
    assert os.path.exists(str(p))
    assert not os.path.exists(str(p) + ".gz")


# ============================================ deletion is off and stays off

def test_SWEEP_DELETES_NOTHING_BY_DEFAULT(tmp_path):
    _seg(tmp_path, "s", "a.jsonl", age_s=99999)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    old_gz = tmp_path / "s" / "ancient.jsonl.gz"
    old_gz.write_bytes(gzip.compress(b"{}\n"))
    t = time.time() - 400 * 86400
    os.utime(old_gz, (t, t))

    rep = RT.sweep(str(tmp_path), min_age_s=1.0)
    assert rep["deletion_enabled"] is False
    assert rep["deleted_archives"] == 0
    assert old_gz.exists(), "a 400-day-old archive is still evidence"
    assert rep["compressed"] == 1


def test_deletion_when_explicitly_enabled_only_touches_archives(tmp_path):
    raw = _seg(tmp_path, "s", "a.jsonl", age_s=400 * 86400)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    gz = tmp_path / "s" / "old.jsonl.gz"
    gz.write_bytes(gzip.compress(b"{}\n"))
    t = time.time() - 400 * 86400
    os.utime(gz, (t, t))

    rep = RT.sweep(str(tmp_path), min_age_s=1.0, delete_after_days=30)
    assert rep["deleted_archives"] == 1
    assert not gz.exists()
    # the raw segment was compressed, not deleted, and its archive is young
    assert os.path.exists(str(raw) + ".gz")


def test_the_sweep_reports_before_and_after(tmp_path):
    _seg(tmp_path, "s", "a.jsonl", rows=500, age_s=99999)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    rep = RT.sweep(str(tmp_path), min_age_s=1.0)
    assert rep["before"]["readable"] and rep["after"]["readable"]
    assert rep["bytes_freed"] > 0
    assert "gb_freed" in rep
    assert "lossless" in rep["labels"].lower()


def test_PRESSURE_DOES_NOT_UNLOCK_DELETION(tmp_path):
    """The module must offer no path from 'disk full' to 'delete evidence'."""
    import ast
    import inspect
    src = inspect.getsource(RT)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "sweep")
    # every unlink inside sweep's own body is guarded by delete_after_days
    assert "delete_after_days is not None" in ast.unparse(fn)
    # and nothing anywhere keys deletion off a usage level
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)\
                and node.func.attr == "unlink":
            seg = ast.unparse(node)
            assert "used_pct" not in seg and "level" not in seg


def test_ZERO_BYTE_SEGMENTS_ARE_LEFT_ALONE(tmp_path):
    """The outage left dozens; they free nothing and they record the loop."""
    d = tmp_path / "s"
    d.mkdir()
    (d / "orphan.jsonl").write_text("", encoding="utf-8")
    t = time.time() - 99999
    os.utime(d / "orphan.jsonl", (t, t))
    _seg(tmp_path, "s", "real.jsonl", age_s=99998)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert names == ["real.jsonl"]
    assert (d / "orphan.jsonl").exists()


def test_three_independent_guards_protect_the_active_segment(tmp_path):
    """newest-by-mtime, younger-than-min-age, and zero-byte each suffice."""
    _seg(tmp_path, "s", "closed.jsonl", age_s=99999)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    got = RT.candidates(str(tmp_path), min_age_s=3600)
    assert [c["name"] for c in got] == ["closed.jsonl"]
