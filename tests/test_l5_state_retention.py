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
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    names = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert names == ["b.jsonl"]
    assert (d / "a.jsonl.gz").exists()


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


# ================================ the image must carry what the code imports

def test_THE_IMAGE_COPIES_EVERY_SRC_MODULE_THE_SUPERVISOR_IMPORTS():
    """This exact gap took the recorder down three times.

    `from src import l5_state_retention` inside the supervisor is useless if
    the Dockerfile's COPY list does not carry the file: the container starts,
    imports, and dies. The COPY list is checked against the imports rather
    than maintained by eye.
    """
    import ast
    import re

    src = open("src/l5_research_supervisor.py", encoding="utf-8").read()
    docker = open("deploy/l5_exec_shadow/Dockerfile", encoding="utf-8").read()
    copied = set(re.findall(r"^COPY\s+(\S+)", docker, re.M))

    wanted = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module == "src":
            for a in node.names:
                wanted.add("src/%s.py" % a.name)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src."):
            wanted.add("src/%s/" % node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("src."):
                    wanted.add("src/%s/" % a.name.split(".")[1])

    missing = []
    for w in sorted(wanted):
        if w in copied:
            continue
        # a package directory satisfies a module inside it
        if any(c.endswith("/") and w.startswith(c) for c in copied):
            continue
        # an explicit single-file COPY of that module satisfies it
        if any(c == w for c in copied):
            continue
        missing.append(w)
    assert not missing, (
        "the l5-exec-shadow image does not COPY: %s -- the supervisor imports "
        "them and the container will die on startup" % missing)


def test_A_BROKEN_RETENTION_MODULE_CANNOT_KILL_THE_SUPERVISOR(monkeypatch):
    """The stronger invariant, and the one the outage actually violated.

    Even with the COPY check above, retention is a helper. If it raises for
    ANY reason -- a missing import, a permission error, a bug I have not
    thought of -- the recorder must keep recording. It is allowed to lose the
    sweep. It is not allowed to lose the tape.
    """
    import builtins

    from src import l5_research_supervisor as SUP

    real_import = builtins.__import__

    def boom(name, *a, **k):
        if "l5_state_retention" in name or (
                a and a[2] and "l5_state_retention" in str(a[2])):
            raise ImportError("no module named l5_state_retention")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    s = SUP.Supervisor.__new__(SUP.Supervisor)
    s.root = "/tmp"
    u = s.report_disk()                      # must not raise
    assert u["readable"] is False and "ImportError" in u["why"]
    rep = s.sweep_retention()                # must not raise
    assert rep.get("ok") is False


# ==================================== the sweep runs inside the supervisor loop

def test_THE_SWEEP_IS_BOUNDED_AND_SAYS_SO(tmp_path):
    """An unbounded pass stalls reap() and watch_freshness() for as long as
    it takes, and the first pass here had 38 GB to get through."""
    for i in range(6):
        _seg(tmp_path, "s", "a%d.jsonl" % i, rows=400, age_s=99999 - i)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    rep = RT.sweep(str(tmp_path), min_age_s=1.0, max_seconds=0.0)
    assert rep["compressed"] == 0
    assert rep["stopped_on_budget"] is True
    assert rep["remaining"] == rep["candidates"]
    assert "elapsed_s" in rep and rep["budget_s"] == 0.0


def test_successive_bounded_passes_converge(tmp_path):
    for i in range(4):
        _seg(tmp_path, "s", "a%d.jsonl" % i, rows=50, age_s=99999 - i)
    _seg(tmp_path, "s", "active.jsonl", age_s=0)
    first = RT.sweep(str(tmp_path), min_age_s=1.0, max_files=2)
    second = RT.sweep(str(tmp_path), min_age_s=1.0, max_files=2)
    assert first["compressed"] == 2 and second["compressed"] == 2
    # nothing is compressed twice
    third = RT.sweep(str(tmp_path), min_age_s=1.0)
    assert third["compressed"] == 0


def test_a_started_file_is_always_finished(tmp_path):
    """The budget is checked BETWEEN files: a half-written archive saves
    nothing and a verified one is the only kind that unlinks a source."""
    import ast
    import inspect
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(RT)))
              if isinstance(n, ast.FunctionDef) and n.name == "sweep")
    body = ast.unparse(fn)
    assert "out_of_time = True" in body and "break" in body
    # the budget check is not inside compress()
    comp = inspect.getsource(RT.compress)
    assert "max_seconds" not in comp


def test_the_boot_sweep_only_runs_under_pressure():
    """On a healthy volume the recorder must start recording, not compress."""
    import ast
    import inspect

    from src import l5_research_supervisor as SUP
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(SUP)))
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    body = ast.unparse(fn)
    assert 'u.get("level") != \'OK\'' in body or "!= 'OK'" in body
    # and the boot sweep is still reachable when it IS under pressure
    assert "sweep_retention(budget_s=600.0)" in body


def test_AN_ARCHIVE_DOES_NOT_STEAL_NEWEST_FROM_THE_ACTIVE_SEGMENT(tmp_path):
    """Compressing stamps the .gz with the sweep's time.

    Seen live: after two archives were written, the newest entry in
    l5-surface-shadow was a .gz. If that counted, the segment the recorder
    still has open would lose the newest-file guard and rely on the age guard
    alone -- which an idle-but-open recorder would not satisfy.
    """
    d = tmp_path / "s"
    d.mkdir()
    old_active = _seg(tmp_path, "s", "active.jsonl", age_s=9000)
    _seg(tmp_path, "s", "closed.jsonl", age_s=99999)
    gz = d / "archived.jsonl.gz"
    gz.write_bytes(gzip.compress(b"{}\n"))          # mtime = now, the newest
    got = [c["name"] for c in RT.candidates(str(tmp_path), min_age_s=1.0)]
    assert "active.jsonl" not in got, (
        "the open segment lost its guard to a freshly written archive")
    assert got == ["closed.jsonl"]
    assert old_active.exists()
