"""The frozen window study, run under the supervisor without becoming a different experiment.

Registering a preregistered experiment as a supervised stream is an
infrastructure change, and these tests exist to keep it one. The module is
pinned by hash, the argv is pinned to the frozen command, and the two things a
supervisor could do that WOULD change the experiment -- splice two partial
captures into one tape, or start a capture inside its own lead window -- are
made impossible here rather than trusted not to happen.
"""
import ast
import io
import json
import os
import time

import pytest

from analysis.l5.window_forensics import eligibility as E
from src import l5_research_supervisor as S
from src import l5_window_forensics as W

#: the module as it was when the owner authorized the migration. The whole
#: point of the change is that this does NOT move.
FROZEN_SHA256 = "48214f23bfaee4f7f3999f017fd435c1babac8b68e808debf586777737ce68e6"

#: the exact command the in-container process was running when it was migrated
FROZEN_ARGV = ["--windows", "3", "--lead-s", "120", "--follow-s", "300"]


def _sup(tmp_path):
    return S.Supervisor(str(tmp_path / "state"), hours=None)


# ------------------------------------------------ the experiment is unchanged

def test_the_frozen_module_is_byte_for_byte_what_was_authorized():
    import hashlib
    got = hashlib.sha256(
        io.open("src/l5_window_forensics.py", "rb").read()).hexdigest()
    assert got == FROZEN_SHA256, (
        "L5_WINDOW_FORENSICS_V1 changed. Its logic, constants, scoring, "
        "prediction generation, cadence, market selection and hypotheses are "
        "frozen; migrating it to a supervisor may not touch any of them.")


def test_the_supervisor_runs_exactly_the_frozen_argv():
    assert list(S.STREAMS["window_forensics"][3]) == FROZEN_ARGV
    assert S.STREAMS["window_forensics"][0] == "src.l5_window_forensics"


def test_the_frozen_procedure_constants_are_the_ones_the_argv_reproduces():
    """The argv must not quietly re-parameterize the experiment: every value it
    passes has to equal the module's own preregistered default."""
    argv = dict(zip(FROZEN_ARGV[0::2], FROZEN_ARGV[1::2]))
    assert float(argv["--lead-s"]) == float(W.PROCEDURE["lead_s"])
    assert float(argv["--follow-s"]) == float(W.PROCEDURE["follow_s"])
    assert W.DUR == 900


def test_the_window_study_carries_no_trading_capability():
    tree = ast.parse(io.open("src/l5_window_forensics.py").read())
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    low = {x.lower() for x in names}
    for banned in ("post", "submit", "place_order", "create_order", "signer",
                   "private_key", "api_secret"):
        assert banned not in low, banned


# --------------------------------------------------- no splice, ever

def test_each_start_writes_into_its_own_segment(tmp_path, monkeypatch):
    """The tape is named after the WINDOW and opened with mode 'a'. Two runs
    that target the same boundary would append into ONE file and splice two
    partial captures into something that reads complete. A fresh directory per
    start makes that impossible."""
    class _P:
        pid = 7
    monkeypatch.setattr(S.subprocess, "Popen",
                        lambda cmd, *a, **k: _P() if "-m" in cmd else None)
    monkeypatch.setattr(S.Supervisor, "_launchable", lambda self, n: True)
    sup = _sup(tmp_path)
    seen = []
    for _ in range(3):
        sup.procs.pop("window_forensics", None)
        sup._segdir["window_forensics"] = None
        # THE SAME SECOND for all three: the clock must not be what makes
        # them distinct, or a fast retry would splice two captures into one
        monkeypatch.setattr(S.time, "strftime",
                            lambda f, t=None: "20260917T184300Z")
        sup.start("window_forensics")
        seen.append(sup._out_dir("window_forensics"))
    assert len(set(seen)) == 3, seen
    for d in seen:
        assert os.path.basename(d).startswith("seg_")
        assert os.path.isdir(d)


def test_the_provenance_names_the_directory_the_child_actually_writes(
        tmp_path, monkeypatch):
    class _P:
        pid = 7
    monkeypatch.setattr(S.subprocess, "Popen",
                        lambda cmd, *a, **k: _P() if "-m" in cmd else None)
    monkeypatch.setattr(S.Supervisor, "_launchable", lambda self, n: True)
    sup = _sup(tmp_path)
    sup.start("window_forensics")
    pv = sup.provenance("window_forensics", sup._cmd("window_forensics"))
    out = pv["command"][pv["command"].index("--out-dir") + 1]
    assert out == pv["out_dir"]
    assert pv["segment_per_start"] is True
    assert pv["module_sha256"] == FROZEN_SHA256
    assert pv["extra_argv"] == FROZEN_ARGV


def test_the_dating_glob_reaches_inside_the_segments(tmp_path):
    sup = _sup(tmp_path)
    d = os.path.join(sup.root, "l5-window-forensics", "seg_20260917T184300Z")
    os.makedirs(d)
    p = os.path.join(d, "window_1789670700.jsonl")
    io.open(p, "w").write(json.dumps({"L5_OBSERVATION_CLOCK": time.time()}) + "\n")
    assert S._newest_segment(os.path.join(sup.root, "l5-window-forensics"),
                             S.STREAMS["window_forensics"][2]) == p


# ------------------------------------------ the lead window is never clipped

def test_a_start_inside_the_lead_window_is_refused(tmp_path, monkeypatch):
    """Starting at boundary-30s would capture with 30s of pre-window coverage
    instead of the preregistered 120s -- an incomplete window that looks
    perfectly healthy from its first row on. The supervisor waits instead."""
    sup = _sup(tmp_path)
    boundary = 1789670700                       # 18:45:00Z
    monkeypatch.setattr(S.time, "time", lambda: boundary - 30)
    assert sup._launchable("window_forensics") is False
    monkeypatch.setattr(S.time, "time", lambda: boundary - 119)
    assert sup._launchable("window_forensics") is False
    # with the full lead still ahead it may start; the module sleeps the rest
    monkeypatch.setattr(S.time, "time", lambda: boundary - 400)
    assert sup._launchable("window_forensics") is True


def test_a_held_stream_is_not_reported_as_stale_or_restarted(tmp_path,
                                                             monkeypatch):
    sup = _sup(tmp_path)
    d = os.path.join(sup.root, "l5-window-forensics", "seg_a")
    os.makedirs(d)
    old = time.time() - 4 * S.STALE_AFTER_S
    p = os.path.join(d, "window_1789668900.jsonl")
    io.open(p, "w").write(json.dumps({"L5_OBSERVATION_CLOCK": old}) + "\n")
    os.utime(p, (old, old))
    monkeypatch.setattr(S.Supervisor, "_launchable", lambda self, n: False)
    started = []
    monkeypatch.setattr(S.Supervisor, "start",
                        lambda self, n: started.append(n))
    sup.watch_freshness()
    assert started == []
    cov = os.path.join(sup.root, S.COVERAGE)
    rows = [json.loads(l) for l in io.open(cov)] if os.path.exists(cov) else []
    assert [r for r in rows if r["kind"].startswith("RECORDER")] == []


def test_the_legitimate_wait_between_windows_is_not_called_stale(tmp_path,
                                                                monkeypatch):
    """Worst case the study is correctly silent for ~780s: it finishes a window
    at window_end + 300 and sleeps to the next boundary - 120. The default 300s
    threshold would restart it mid-wait forever."""
    assert S.STALE_OVERRIDE_S["window_forensics"] > 780.0
    sup = _sup(tmp_path)
    d = os.path.join(sup.root, "l5-window-forensics", "seg_a")
    os.makedirs(d)
    quiet = time.time() - 700
    p = os.path.join(d, "window_1789668900.jsonl")
    io.open(p, "w").write(json.dumps({"L5_OBSERVATION_CLOCK": quiet}) + "\n")
    os.utime(p, (quiet, quiet))
    sup.procs["window_forensics"] = type("P", (), {"pid": 1, "poll": lambda s: None})()
    monkeypatch.setattr(S.Supervisor, "_launchable", lambda self, n: True)
    started = []
    monkeypatch.setattr(S.Supervisor, "start", lambda self, n: started.append(n))
    sup.watch_freshness()
    assert started == []


def test_finishing_its_batch_is_not_counted_as_a_failure(tmp_path):
    sup = _sup(tmp_path)
    sup.procs["window_forensics"] = type(
        "P", (), {"pid": 3, "poll": lambda s: 0})()
    sup.reap()
    assert sup.fails["window_forensics"] == 0
    rows = [json.loads(l) for l in io.open(os.path.join(sup.root, S.COVERAGE))]
    ex = [r for r in rows if r["kind"] == "stream_exit"][0]
    assert ex["completed_normally"] is True
    # a CRASH still counts
    sup.procs["window_forensics"] = type(
        "P", (), {"pid": 4, "poll": lambda s: 1})()
    sup.reap()
    assert sup.fails["window_forensics"] == 1


# -------------------------------------------------------- eligibility rules

def _tape(tmp_path, start, *, first, last, window_end_row, name=None,
          durable=True):
    d = tmp_path / ("seg_20260917T184300Z_001" if durable else "handrun")
    d.mkdir(exist_ok=True)
    p = d / (name or ("window_%d.jsonl" % start))
    rows = [{"kind": "preregistration", "window_start": start,
             "window_end": start + 900,
             "procedure": {"lead_s": 120, "follow_s": 300},
             "L5_OBSERVATION_CLOCK": first}]
    if window_end_row:
        rows.append({"kind": "window_end", "window_start": start,
                     "L5_OBSERVATION_CLOCK": start + 900})
    rows.append({"kind": "book_full_joint", "L5_OBSERVATION_CLOCK": last})
    io.open(p, "w").write("".join(json.dumps(r) + "\n" for r in rows))
    return str(d)


def test_a_full_capture_is_complete(tmp_path):
    st = 1789670700
    d = _tape(tmp_path, st, first=st - 120, last=st + 900 + 300,
              window_end_row=True)
    r = E.report(d)
    assert r["n_complete"] == 1 and r["complete_windows"] == [st]


def test_an_interrupted_capture_is_truncated_not_scorable(tmp_path):
    st = 1789670700
    d = _tape(tmp_path, st, first=st - 120, last=st + 400,
              window_end_row=False)
    (c,) = E.report(d)["captures"]
    assert c["verdict"] == E.TRUNCATED
    assert any("close:" in f for f in c["failed"])
    assert any("follow:" in f for f in c["failed"])


def test_a_capture_that_started_inside_its_lead_window_is_truncated(tmp_path):
    """No interruption produces this one -- only a start at the wrong moment,
    and the tape looks healthy from its first row on."""
    st = 1789670700
    d = _tape(tmp_path, st, first=st - 30, last=st + 900 + 300,
              window_end_row=True)
    (c,) = E.report(d)["captures"]
    assert c["verdict"] == E.TRUNCATED
    assert any(f.startswith("lead:") for f in c["failed"])


def test_two_partial_captures_of_one_window_never_add_up_to_a_complete_one(
        tmp_path):
    st = 1789670700
    d = _tape(tmp_path, st, first=st - 120, last=st + 300,
              window_end_row=False, name="window_%d.jsonl" % st)
    _tape(tmp_path, st, first=st + 310, last=st + 1200,
          window_end_row=True, name="second.jsonl")
    # the auditor only reads window_*.jsonl tapes, and neither half is whole
    r = E.report(d)
    assert r["n_complete"] == 0
    assert all(c["verdict"] == E.TRUNCATED for c in r["captures"])


def test_window_one_is_preserved_exactly_as_the_owner_named_it():
    assert E.DECLARED_INELIGIBLE[1789668000].startswith(
        "WINDOW_1_TRUNCATED_NOT_SCORABLE_AS_A_COMPLETE_WINDOW")
    r = E.report("data/l5_window_forensics")
    w1 = [c for c in r["captures"] if c["window_start"] == 1789668000]
    assert len(w1) == 1
    assert w1[0]["verdict"] == E.TRUNCATED
    assert w1[0]["declared_ineligible"] is True
    assert 1789668000 not in r["complete_windows"]


def test_the_1830_window_is_declared_ineligible_by_sequencing(tmp_path):
    st = 1789669800
    d = _tape(tmp_path, st, first=st - 120, last=st + 900 + 300,
              window_end_row=True)
    (c,) = E.report(d)["captures"]
    # even a PERFECT capture of it cannot count
    assert c["verdict"] == E.TRUNCATED
    assert c["declared_ineligible"] is True
    assert "INELIGIBLE_BY_OWNER_SEQUENCING" in c["failed"][0]


def test_a_hand_run_container_capture_cannot_be_one_of_the_three(tmp_path):
    """Structurally perfect and still diagnostic. The same frozen module was
    run by hand in the session container three times today and the container
    killed it each time; counting one of those tapes would put a window of the
    sample back inside the failure domain the migration exists to leave."""
    st = 1789671600
    d = _tape(tmp_path, st, first=st - 120, last=st + 900 + 300,
              window_end_row=True, durable=False)
    r = E.report(d)
    (c,) = r["captures"]
    assert c["durable"] is False
    assert c["verdict"] == E.TRUNCATED
    assert any(f.startswith("origin:") for f in c["failed"])
    # and nothing structural is wrong with it -- only its origin
    assert not [f for f in c["failed"]
                if f.startswith(("lead:", "close:", "follow:"))]
    assert r["n_complete"] == 0 and r["diagnostic_captures"] == 1


def test_the_same_window_counts_when_the_durable_stream_captures_it(tmp_path):
    st = 1789671600
    d = _tape(tmp_path, st, first=st - 120, last=st + 900 + 300,
              window_end_row=True, durable=True)
    r = E.report(d)
    assert r["n_complete"] == 1 and r["complete_windows"] == [st]
    assert r["diagnostic_captures"] == 0


def test_three_attempts_are_not_three_windows(tmp_path):
    good = 0
    for i, ok in enumerate([True, False, True, False]):
        st = 1789670700 + i * 900
        d = _tape(tmp_path, st, first=st - 120,
                  last=(st + 1200) if ok else (st + 400), window_end_row=ok)
        good += ok
    r = E.report(d)
    assert len(r["captures"]) == 4
    assert r["n_complete"] == good == 2
    assert r["satisfied"] is False
