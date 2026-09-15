"""The supervisor: it must carry no order path, and it must account for gaps.

The second property is the one that earns the service. An append-only store
with a hole in it looks exactly like a quiet market, so the supervisor's job is
not uptime -- it is making downtime legible.
"""
import json
import os
import subprocess
import sys
import time

from analysis.l5 import coverage as C
from src import l5_research_supervisor as S


def _write(d, name, rows):
    os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
    with open(os.path.join(d, name), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# ---------------------------------------------------------- zero order path

def test_the_supervisor_imports_no_order_path():
    """Computed in a FRESH SUBPROCESS: sys.modules is process-global, so inside
    the suite another test's imports would decide this."""
    code = ("import sys, src.l5_research_supervisor;"
            "print(' '.join(sorted(m for m in sys.modules "
            "if m.startswith('src.'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    banned = [m for m in out.stdout.split()
              if m.startswith(("src.pmus_", "src.kalshi_",
                               "src.l5_venue_adapter"))]
    assert not banned, f"order path reachable from the supervisor: {banned}"


def test_the_image_ships_no_order_path():
    import pathlib
    df = pathlib.Path("deploy/l5_exec_shadow/Dockerfile").read_text()
    copies = [l for l in df.splitlines() if l.strip().startswith("COPY ")]
    for banned in ("l5_adapters/evaluate.py", "l5_venue_adapter", "pmus_",
                   "kalshi_"):
        assert not [l for l in copies if banned in l], banned


def test_the_railway_json_declares_that_it_configures_nothing():
    """It does not. Railway REFUSES Config as Code now -- setting
    railwayConfigFile returns 'Config as Code is deprecated'. The file was
    silently inert when the service was created, so restartPolicyType ALWAYS
    and the promotion watchPattern were NOT in effect on the first two
    deployments. They are set through the API instead, and this test exists so
    the file cannot quietly start looking authoritative again."""
    import pathlib
    cfg = json.loads(
        pathlib.Path("deploy/l5_exec_shadow/railway.json").read_text())
    assert "_NOT_READ_BY_RAILWAY" in cfg
    assert "DEPRECATED" in cfg["_NOT_READ_BY_RAILWAY"]
    # the intended settings are still recorded, as the documentation they are
    assert cfg["deploy"]["restartPolicyType"] == "ALWAYS"
    assert cfg["build"]["watchPatterns"] == ["deploy/_promote/l5-exec-shadow"]
    assert "/state/l5-research" in cfg["deploy"]["startCommand"]


# -------------------------------------------------------------- resume, not reset

def test_the_boot_marker_records_the_gap_since_each_store_last_wrote(tmp_path):
    d = str(tmp_path)
    _write(d, "l5-shadow-state/routes.jsonl", [{"decided_at": time.time() - 900}])
    sup = S.Supervisor(d)
    sup.boot()
    rows = C.load(d)
    boot = [r for r in rows if r["kind"] == "boot"][0]
    g = boot["since_last_row"]["exec_shadow"]
    assert g["gap_s"] > 800
    assert [r for r in rows if r["kind"] == "coverage_gap"], \
        "a 15-minute hole must produce an explicit coverage_gap marker"


def test_a_store_that_never_wrote_reports_unknown_not_zero(tmp_path):
    sup = S.Supervisor(str(tmp_path))
    sup.boot()
    g = [r for r in C.load(str(tmp_path))
         if r["kind"] == "boot"][0]["since_last_row"]["gemini_depth"]
    assert g["last_row_utc"] is None and g["gap_s"] is None


def test_a_short_restart_does_not_manufacture_a_gap_marker(tmp_path):
    d = str(tmp_path)
    _write(d, "gemdepth/samples.jsonl", [{"t": time.time() - 3}])
    S.Supervisor(d).boot()
    assert not [r for r in C.load(d) if r["kind"] == "coverage_gap"]


def test_the_last_row_time_comes_from_the_row_not_the_file_mtime(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"t": 1000.0}) + "\n")
    os.utime(p, (9e9, 9e9))            # mtime far in the future
    assert S._last_row_time(str(p)) == 1000.0


def test_a_torn_final_line_does_not_break_the_gap_calculation(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"t": 500.0}) + "\n" + '{"t": 9')
    assert S._last_row_time(str(p)) == 500.0


def test_every_stream_is_started_against_its_existing_out_dir(tmp_path):
    sup = S.Supervisor(str(tmp_path))
    for name in S.STREAMS:
        cmd = sup._cmd(name)
        assert "--out-dir" in cmd
        assert str(tmp_path) in cmd[cmd.index("--out-dir") + 1]


# ------------------------------------------------- an absence is not a zero

def test_an_exit_without_a_restart_is_reported_as_still_down():
    rows = [{"kind": "stream_exit", "stream": "a", "t": 100.0}]
    g = C.gaps(rows)
    assert g[0]["to"] is None and "STILL DOWN" in g[0]["source"]


def test_exit_then_restart_becomes_a_measured_gap():
    rows = [{"kind": "stream_exit", "stream": "a", "t": 100.0},
            {"kind": "stream_restart", "stream": "a", "t": 160.0}]
    assert C.gaps(rows)[0]["gap_s"] == 60.0


def test_observed_is_false_for_any_interval_touching_a_gap():
    rows = [{"kind": "stream_exit", "stream": "a", "t": 100.0},
            {"kind": "stream_restart", "stream": "a", "t": 200.0}]
    assert C.observed(rows, "a", 0.0, 99.0) is True
    assert C.observed(rows, "a", 150.0, 160.0) is False
    assert C.observed(rows, "a", 50.0, 120.0) is False     # partial overlap
    assert C.observed(rows, "a", 250.0, 300.0) is True


def test_a_gap_in_one_stream_does_not_blind_another():
    rows = [{"kind": "stream_exit", "stream": "a", "t": 100.0},
            {"kind": "stream_restart", "stream": "a", "t": 200.0}]
    assert C.observed(rows, "b", 150.0, 160.0) is True


def test_no_markers_at_all_means_unknown_never_full_coverage(tmp_path):
    r = C.report(str(tmp_path))
    assert r["markers"] == 0 and "UNKNOWN" in r["verdict"]


def test_coverage_report_accounts_downtime_per_stream(tmp_path):
    d = str(tmp_path)
    _write(d, "coverage.jsonl",
           [{"kind": "boot", "t": 0.0},
            {"kind": "stream_exit", "stream": "a", "t": 100.0},
            {"kind": "stream_restart", "stream": "a", "t": 160.0},
            {"kind": "heartbeat", "t": 200.0}])
    r = C.report(d)
    assert r["per_stream"]["a"]["downtime_s"] == 60.0
    assert r["per_stream"]["a"]["coverage_fraction"] == 0.7
    assert r["exits"] == 1 and r["restarts"] == 1


# ------------------------------------- a report must know if it was watching

def test_a_report_without_coverage_warns_rather_than_implying_full_uptime(tmp_path):
    from analysis.l5.exec_shadow import report as R
    d = tmp_path / "l5-shadow-state"
    d.mkdir()
    cov = R._coverage(str(d))
    assert cov["available"] is False
    assert "CANNOT be distinguished" in cov["warning"]


def test_a_report_reads_coverage_from_the_state_root_above_the_stream(tmp_path):
    from analysis.l5.exec_shadow import report as R
    d = tmp_path / "l5-shadow-state"
    d.mkdir()
    (tmp_path / "coverage.jsonl").write_text("\n".join([
        json.dumps({"schema": S.SCHEMA, "kind": "boot", "t": 0.0}),
        json.dumps({"schema": S.SCHEMA, "kind": "stream_exit",
                    "stream": "exec_shadow", "t": 100.0}),
        json.dumps({"schema": S.SCHEMA, "kind": "stream_restart",
                    "stream": "exec_shadow", "t": 130.0}),
        json.dumps({"schema": S.SCHEMA, "kind": "heartbeat", "t": 200.0}),
    ]) + "\n")
    cov = R._coverage(str(d))
    assert cov["available"] is True
    assert cov["exec_shadow"]["downtime_s"] == 30.0
    assert cov["exits"] == 1 and cov["restarts"] == 1
