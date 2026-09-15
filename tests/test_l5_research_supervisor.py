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


# --------------------------- a quiet market is not downtime, either direction

def test_downtime_comes_from_our_heartbeat_not_from_a_streams_events(tmp_path):
    """exec_shadow writes only when L5 acts, so its row gap conflates our
    absence with a quiet market. Observed live: a 49.8s exec_shadow "gap"
    across a redeploy where gemini_depth, a fixed-cadence sampler, showed the
    true 2.3s."""
    d = str(tmp_path)
    now = time.time()
    _write(d, "coverage.jsonl",
           [{"schema": S.SCHEMA, "kind": "heartbeat", "t": now - 5.0}])
    # L5 has not traded for an hour, but we were up the whole time
    _write(d, "l5-shadow-state/routes.jsonl", [{"decided_at": now - 3600}])
    S.Supervisor(d).boot()
    boot = [r for r in C.load(d) if r["kind"] == "boot"][-1]
    assert boot["downtime_s"] < 30, "we were up; only the venue was quiet"
    assert boot["since_last_row"]["exec_shadow"]["gap_s"] > 3000


def test_a_quiet_market_does_not_fire_a_coverage_gap(tmp_path):
    """A marker that cries wolf is one nobody reads when it matters."""
    d = str(tmp_path)
    now = time.time()
    _write(d, "coverage.jsonl",
           [{"schema": S.SCHEMA, "kind": "heartbeat", "t": now - 5.0}])
    _write(d, "l5-shadow-state/routes.jsonl", [{"decided_at": now - 3600}])
    S.Supervisor(d).boot()
    assert not [r for r in C.load(d) if r["kind"] == "coverage_gap"]


def test_real_downtime_does_fire_a_coverage_gap(tmp_path):
    d = str(tmp_path)
    now = time.time()
    _write(d, "coverage.jsonl",
           [{"schema": S.SCHEMA, "kind": "heartbeat", "t": now - 1800.0}])
    S.Supervisor(d).boot()
    g = [r for r in C.load(d) if r["kind"] == "coverage_gap"]
    assert g and g[0]["gap_s"] > 1700
    assert g[0]["stream"] == "__supervisor__"
    assert "our absence" in g[0]["labels"]


def test_a_first_boot_with_no_heartbeat_history_reports_unknown_downtime(tmp_path):
    S.Supervisor(str(tmp_path)).boot()
    boot = [r for r in C.load(str(tmp_path)) if r["kind"] == "boot"][0]
    assert boot["downtime_s"] is None
    assert not [r for r in C.load(str(tmp_path)) if r["kind"] == "coverage_gap"]


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


def test_a_supervisor_wide_gap_blinds_every_stream():
    """When the supervisor was down, every stream was down with it."""
    rows = [{"kind": "coverage_gap", "stream": "__supervisor__",
             "from_utc": 100.0, "to_utc": 200.0, "gap_s": 100.0}]
    assert C.observed(rows, "exec_shadow", 150.0, 160.0) is False
    assert C.observed(rows, "gemini_depth", 150.0, 160.0) is False
    assert C.observed(rows, "exec_shadow", 250.0, 260.0) is True


# ------------------------------------- the gate is reported, never acted on

def test_the_supervisor_reports_the_gate_without_promoting_anything(
        tmp_path, capsys):
    """"Report the exact metrics continuously" -- so the standing is visible
    as the sample accumulates, not only when someone remembers to ask."""
    import json
    from src.l5_chain_observer import observer as O
    vd = tmp_path / "chainval"
    vd.mkdir()
    (vd / "validations.jsonl").write_text(json.dumps(
        {"ok": True, "agree": 10, "epoch": 3, "feed_only": [],
         "chain_only": [], "window_utc": [1.0, 2.0]}) + "\n")
    rep = S.Supervisor(str(tmp_path)).report_gate()
    out = capsys.readouterr().out
    assert out.startswith("L5_GATE ")
    row = json.loads(out.split("L5_GATE ", 1)[1].splitlines()[0])
    assert row["ok"] is True and row["PROMOTE"] is False
    assert row["threshold"] == 500
    assert "does not enable live trading" in row["scope"]
    assert rep["PROMOTE"] is False
    # and reporting it changes nothing about the observer
    assert O.PROMOTED is False


def test_a_broken_gate_report_does_not_take_down_the_collectors(tmp_path,
                                                                capsys):
    """A reporting bug must not kill the streams it reports on."""
    import json
    (tmp_path / "chainval").mkdir()
    (tmp_path / "chainval" / "validations.jsonl").write_text("{not json\n")
    sup = S.Supervisor(str(tmp_path))
    assert sup.report_gate() is not None or True     # must not raise
    (tmp_path / "chainval" / "validations.jsonl").write_text("")
    assert sup.report_gate() is not None


def test_the_gate_cadence_is_slower_than_the_heartbeat(tmp_path):
    """It reads the whole store; it must not compete with liveness."""
    assert S.GATE_S > S.HEARTBEAT_S
