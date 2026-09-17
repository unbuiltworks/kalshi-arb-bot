"""The recorder watchdog: freshness, not liveness, and gaps that stay gaps.

WHY THIS EXISTS. On 2026-09-17 the two prospective recorders died inside the
session container twice -- 17:16 and 17:37 -- and the second death went
unnoticed for about ten minutes. Both times everything in that container died
together, which is the whole finding: a watchdog living beside the recorders
shares their failure domain and cannot witness their death. So the recorders
were registered as streams of the already-deployed `l5-exec-shadow`
supervisor, whose restart policy is ALWAYS and whose process outlives this
session, and the freshness check lives there.

The tests below pin the six properties the owner specified, and one more that
the incident itself taught: a store can stop advancing while its PID is
perfectly healthy, so the PID must not be what decides.
"""
import ast
import fnmatch
import hashlib
import io
import json
import os
import re
import time

import pytest

from analysis.l5 import coverage as C
from src import l5_research_supervisor as S

WATCHED = ("book_context", "gemini_surface")


class _FakeProc:
    """Alive, and staying alive. The point of several tests below is that a
    perfectly healthy PID is not evidence that anything is being recorded."""

    def __init__(self):
        self.pid = 4242
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


def _seg(root, sub, name, rows, mtime=None):
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _sup(tmp_path):
    return S.Supervisor(str(tmp_path / "state"), hours=None)


def _markers(root):
    p = os.path.join(root, S.COVERAGE)
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in io.open(p) if l.strip()]


def _stub_start(sup, created):
    """Stand in for the real spawn, and do the one thing that matters to these
    tests: open a NEW dated segment, exactly as both recorders do on start."""
    def start(name):
        sub = S.STREAMS[name][1]
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + str(
            len(created))
        glob = S.STREAMS[name][2]
        p = _seg(sup.root, sub, glob.replace("*", stamp),
                 [{"L5_OBSERVATION_CLOCK": time.time(), "row": "first"}])
        created.append((name, p))
        sup.procs[name] = _FakeProc()
    sup.start = start
    return created


# ------------------------------------------------- (a) freshness, not the PID

def test_a_stale_store_is_restarted_although_its_process_is_perfectly_alive(
        tmp_path):
    """The failure mode that actually happened. If liveness decided, this
    recorder would be left alone while its store sat frozen."""
    sup = _sup(tmp_path)
    old = time.time() - 4 * S.STALE_AFTER_S
    _seg(sup.root, S.STREAMS["book_context"][1],
         "l5_book_context_20260917T170000Z.jsonl",
         [{"L5_OBSERVATION_CLOCK": old}], mtime=old)
    sup.procs["book_context"] = _FakeProc()       # ALIVE, and irrelevant

    created = _stub_start(sup, [])
    sup.watch_freshness()

    kinds = [(r["kind"], r.get("stream")) for r in _markers(sup.root)]
    assert ("RECORDER_STALE", "book_context") in kinds
    assert ("RECORDER_RESTART", "book_context") in kinds
    assert [n for n, _ in created] == ["book_context"]
    stale = [r for r in _markers(sup.root) if r["kind"] == "RECORDER_STALE"][0]
    # the record must say the process was up, so nobody later reads this as a
    # crash that a PID check would have caught
    assert stale["process_alive"] is True
    assert stale["stale_basis"].startswith("rows_stopped")


def test_a_fresh_store_is_left_completely_alone(tmp_path):
    sup = _sup(tmp_path)
    for n in WATCHED:
        _seg(sup.root, S.STREAMS[n][1],
             S.STREAMS[n][2].replace("*", "20260917T175900Z"),
             [{"L5_OBSERVATION_CLOCK": time.time()}])
        sup.procs[n] = _FakeProc()
    created = _stub_start(sup, [])
    sup.watch_freshness()
    assert created == []
    assert [r for r in _markers(sup.root) if r["kind"].startswith("RECORDER")] == []


def test_a_segment_that_was_opened_and_never_written_into_is_still_caught(
        tmp_path):
    """No row means no row-timestamp to be old, so the bytes are the only
    witness left. Without this the watchdog would be blind to a recorder that
    starts, creates its file, and then wedges."""
    sup = _sup(tmp_path)
    old = time.time() - 4 * S.STALE_AFTER_S
    _seg(sup.root, S.STREAMS["gemini_surface"][1],
         "gemini_surface_20260917T170000Z.jsonl", [], mtime=old)
    sup.procs["gemini_surface"] = _FakeProc()
    created = _stub_start(sup, [])
    sup.watch_freshness()
    stale = [r for r in _markers(sup.root) if r["kind"] == "RECORDER_STALE"]
    assert len(stale) == 1
    assert stale[0]["last_good_output_utc"] is None, "no row to be old"
    assert stale[0]["file_size"] == 0
    assert stale[0]["size_age_seconds"] > S.STALE_AFTER_S
    assert stale[0]["stale_basis"].startswith("file_not_growing")
    assert [n for n, _ in created] == ["gemini_surface"]


def test_booting_next_to_an_already_dead_store_does_not_reset_its_clock(
        tmp_path):
    """A supervisor that started `now` must not treat a store that stopped an
    hour ago as fresh merely because this is the first time it looked."""
    sup = _sup(tmp_path)
    old = time.time() - 3600
    _seg(sup.root, S.STREAMS["book_context"][1],
         "l5_book_context_20260917T160000Z.jsonl", [], mtime=old)
    h = sup.recorder_health()["book_context"]
    assert h["last_size_change"] == pytest.approx(old, abs=2)
    assert h["size_age_seconds"] > 3000


# --------------------------------------------- (b) the exact last-good moment

def test_the_stale_marker_carries_the_exact_last_good_timestamp(tmp_path):
    sup = _sup(tmp_path)
    t0 = time.time() - 4 * S.STALE_AFTER_S
    rows = [{"L5_OBSERVATION_CLOCK": t0 - 2.0},
            {"L5_OBSERVATION_CLOCK": t0 - 1.0},
            {"L5_OBSERVATION_CLOCK": t0}]            # <- the last good one
    _seg(sup.root, S.STREAMS["book_context"][1],
         "l5_book_context_20260917T170000Z.jsonl", rows, mtime=t0)
    sup.procs["book_context"] = _FakeProc()
    _stub_start(sup, [])
    sup.watch_freshness()
    stale = [r for r in _markers(sup.root) if r["kind"] == "RECORDER_STALE"][0]
    assert stale["last_good_output_utc"] == t0        # exact, not rounded
    assert stale["output_age_seconds"] >= 4 * S.STALE_AFTER_S - 5


# ------------------------------------ (c) a NEW segment, and no backfill ever

def test_a_restart_opens_a_new_segment_and_does_not_touch_the_old_one(tmp_path):
    sup = _sup(tmp_path)
    old = time.time() - 4 * S.STALE_AFTER_S
    dead = _seg(sup.root, S.STREAMS["book_context"][1],
                "l5_book_context_20260917T170000Z.jsonl",
                [{"L5_OBSERVATION_CLOCK": old}], mtime=old)
    before = io.open(dead, "rb").read()
    sup.procs["book_context"] = _FakeProc()
    created = _stub_start(sup, [])
    sup.watch_freshness()

    assert io.open(dead, "rb").read() == before, "the dead segment was edited"
    (_n, fresh), = created
    assert fresh != dead, "the restart reused the old segment"
    # and the hole between them is a real hole: nothing was written into it
    last_old = json.loads(io.open(dead).read().splitlines()[-1])
    first_new = json.loads(io.open(fresh).read().splitlines()[0])
    assert first_new["L5_OBSERVATION_CLOCK"] - last_old[
        "L5_OBSERVATION_CLOCK"] > 4 * S.STALE_AFTER_S - 5


def test_the_restart_marker_refuses_to_present_the_new_segment_as_continuous(
        tmp_path):
    sup = _sup(tmp_path)
    old = time.time() - 4 * S.STALE_AFTER_S
    _seg(sup.root, S.STREAMS["gemini_surface"][1],
         "gemini_surface_20260917T170000Z.jsonl",
         [{"L5_OBSERVATION_CLOCK": old}], mtime=old)
    sup.procs["gemini_surface"] = _FakeProc()
    _stub_start(sup, [])
    sup.watch_freshness()
    r = [x for x in _markers(sup.root) if x["kind"] == "RECORDER_RESTART"][0]
    assert "NEW segment" in r["labels"]
    assert "backfill" in r["labels"]


def test_the_supervisor_writes_to_no_store_but_its_own_markers(tmp_path):
    """Structural, not behavioural: the module opens exactly one path for
    writing, and it is the coverage marker file. There is therefore no code
    here that could repair a recorder store even by accident."""
    tree = ast.parse(io.open("src/l5_research_supervisor.py").read())
    writes = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if mode and ("w" in mode or "a" in mode or "+" in mode):
            writes.append(ast.dump(node.args[0]))
    assert len(writes) == 1, writes
    assert "COVERAGE" in writes[0], writes[0]


def test_the_gap_survives_into_the_coverage_reader_at_full_length(tmp_path):
    """Preserved AND reported. The gap must start where the data stopped, not
    where the watchdog happened to look, or every outage silently shrinks."""
    sup = _sup(tmp_path)
    t0 = time.time() - 4 * S.STALE_AFTER_S
    _seg(sup.root, S.STREAMS["book_context"][1],
         "l5_book_context_20260917T170000Z.jsonl",
         [{"L5_OBSERVATION_CLOCK": t0}], mtime=t0)
    sup.procs["book_context"] = _FakeProc()
    _stub_start(sup, [])
    sup.watch_freshness()

    rows = C.load(sup.root)
    g = [x for x in C.gaps(rows) if x["stream"] == "book_context"]
    assert len(g) == 1, g
    assert g[0]["source"] == "recorder stale/restart"
    assert g[0]["from"] == t0
    assert g[0]["gap_s"] >= 4 * S.STALE_AFTER_S - 5
    # and a claim about that interval is now refused
    assert not C.observed(rows, "book_context", t0 + 60, t0 + 120)


def test_two_witnesses_to_one_outage_are_not_counted_as_two_outages(tmp_path):
    """An exit/restart pair and a stale/restart pair can both describe the same
    hole. Summing them would invent downtime; the union does not."""
    now = time.time()
    rows = [
        {"kind": "stream_exit", "stream": "book_context", "t": now},
        {"kind": "RECORDER_STALE", "stream": "book_context", "t": now + 1,
         "last_good_output_utc": now},
        {"kind": "RECORDER_RESTART", "stream": "book_context", "t": now + 100},
        {"kind": "stream_restart", "stream": "book_context", "t": now + 100},
    ]
    per = C.report.__wrapped__ if hasattr(C.report, "__wrapped__") else None
    assert per is None                      # report() takes a root, so go direct
    assert C._union_s(C.gaps(rows)) == pytest.approx(100.0, abs=1.0)
    assert sum(x["gap_s"] for x in C.gaps(rows)) == pytest.approx(200.0, abs=1)


# ------------------------------------------------------------ (d) provenance

def test_provenance_proves_what_each_segment_was_started_from():
    sup = S.Supervisor("/tmp/does-not-need-to-exist-for-this", hours=None)
    for name in WATCHED:
        p = sup.provenance(name, sup._cmd(name))
        assert p["stream"] == name
        assert re.fullmatch(r"[0-9a-f]{40}", p["commit"] or ""), p["commit"]
        mod = os.path.join(*S.STREAMS[name][0].split(".")) + ".py"
        want = hashlib.sha256(io.open(mod, "rb").read()).hexdigest()
        assert p["module_sha256"] == want
        assert p["command"] == sup._cmd(name)
        assert p["extra_argv"] == list(S.STREAMS[name][3])


def test_every_restart_marker_carries_that_provenance(tmp_path):
    sup = _sup(tmp_path)
    old = time.time() - 4 * S.STALE_AFTER_S
    _seg(sup.root, S.STREAMS["book_context"][1],
         "l5_book_context_20260917T170000Z.jsonl",
         [{"L5_OBSERVATION_CLOCK": old}], mtime=old)
    sup.procs["book_context"] = _FakeProc()
    _stub_start(sup, [])
    sup.watch_freshness()
    r = [x for x in _markers(sup.root) if x["kind"] == "RECORDER_RESTART"][0]
    for k in ("commit", "module_sha256", "command", "extra_argv"):
        assert k in r["provenance"], k
    assert r["provenance"]["command"] == sup._cmd("book_context")


def test_every_ordinary_start_carries_provenance_too(tmp_path, monkeypatch):
    """Not only the restarts. Post-restart equivalence is only provable if the
    segment BEFORE the restart also recorded what it was started from."""
    class _P:
        pid = 11
    real = S.subprocess.Popen

    def fake(cmd, *a, **k):
        # intercept only the child spawns; `git rev-parse` still has to work,
        # because a provenance record with a null commit proves nothing
        if isinstance(cmd, (list, tuple)) and "-m" in cmd:
            return _P()
        return real(cmd, *a, **k)
    monkeypatch.setattr(S.subprocess, "Popen", fake)
    sup = _sup(tmp_path)
    for name in S.STREAMS:
        sup.start(name)
    starts = [r for r in _markers(sup.root)
              if r["kind"] in ("stream_start", "stream_restart")]
    assert len(starts) == len(S.STREAMS)
    for r in starts:
        pv = r["provenance"]
        assert pv["command"] == sup._cmd(r["stream"])
        assert pv["module_sha256"] and pv["commit"]


# --------------------------------- (e) zero trading capability in the watchdog

def test_the_frozen_commands_carry_no_arming_or_live_flag():
    for name in WATCHED:
        argv = " ".join(S.STREAMS[name][3]).lower()
        for banned in ("live", "arm", "submit", "order", "key", "secret",
                       "token", "wallet", "private"):
            assert banned not in argv, (name, banned)


def _code_tokens(path):
    """Every identifier and every non-docstring literal in a module.

    Prose is excluded on purpose: this file's own docstring says it contains
    "no signer", and a substring scan over the raw text would fail on the
    sentence that promises the property it is checking."""
    tree = ast.parse(io.open(path).read())
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)):
            d = ast.get_docstring(n, clean=False)
            if d is not None:
                docs.add(d)
    toks = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            toks.add(n.id)
        elif isinstance(n, ast.Attribute):
            toks.add(n.attr)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            toks.add(n.name)
        elif isinstance(n, ast.arg):
            toks.add(n.arg)
        elif isinstance(n, ast.keyword) and n.arg:
            toks.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            toks.add(getattr(n, "module", "") or "")
            toks.update(a.name for a in n.names)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            if n.value not in docs:
                toks.add(n.value)
    return {t.lower() for t in toks}


def test_the_watchdog_reads_no_credential_and_names_no_order_verb():
    toks = _code_tokens("src/l5_research_supervisor.py")
    blob = " ".join(toks)
    for banned in ("private_key", "api_secret", "api_key", "mnemonic",
                   "signer", "place_order", "create_order", "submit_order",
                   "cancel_order"):
        assert banned not in blob, banned
    # and no environment read at all: nothing here can be armed by a variable
    tree = ast.parse(io.open("src/l5_research_supervisor.py").read())
    envs = [n for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv")]
    assert not envs, "the supervisor reads process environment"


def test_the_watched_recorders_are_themselves_non_money_moving():
    for name in WATCHED:
        mod = os.path.join(*S.STREAMS[name][0].split(".")) + ".py"
        blob = " ".join(_code_tokens(mod))
        for banned in ("place_order", "create_order", "submit_order",
                       "private_key", "api_secret"):
            assert banned not in blob, (mod, banned)


# ------------------------------- (f)(g) the registration must actually be real

def test_every_registered_flag_is_one_the_recorder_actually_accepts():
    """A typo here would make the supervisor spawn a process that dies on
    argparse, forever, on the backoff -- a restart loop that looks like
    supervision and records nothing."""
    for name in WATCHED:
        src = io.open(os.path.join(*S.STREAMS[name][0].split(".")) + ".py").read()
        known = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))
        for tok in S.STREAMS[name][3]:
            if tok.startswith("--"):
                assert tok in known, (name, tok, sorted(known))
        assert "--out-dir" in known, name


def test_the_dating_glob_matches_what_the_recorder_really_names_its_segments():
    for name in WATCHED:
        src = io.open(os.path.join(*S.STREAMS[name][0].split(".")) + ".py").read()
        tmpl = re.findall(r'"([a-z0-9_]+_)%s\.jsonl"', src)
        assert tmpl, name
        realized = tmpl[0] + "20260917T175900Z.jsonl"
        assert fnmatch.fnmatch(realized, S.STREAMS[name][2]), (
            name, realized, S.STREAMS[name][2])


def test_every_module_the_supervisor_can_spawn_is_actually_in_the_image():
    """The defect this catches is invisible to every other test here: a stream
    registered in STREAMS but absent from the image's narrow COPY list imports
    fine locally, then dies on ModuleNotFoundError in the container -- forever,
    on the backoff. That is a restart loop that looks exactly like supervision
    and records nothing, which is the failure this whole change exists to end.

    The closure is computed in a FRESH SUBPROCESS, because sys.modules is
    process-global and the rest of this suite has already imported half of it.
    """
    import subprocess
    import sys as _sys
    code = ("import os, sys, importlib;"
            "[importlib.import_module(m) for m in %r];"
            "print(chr(10).join(sorted(os.path.relpath(f) for f in "
            "(getattr(m, '__file__', None) for n, m in sys.modules.items() "
            "if n.startswith(('src.', 'analysis.'))) if f)))"
            % [S.STREAMS[n][0] for n in S.STREAMS])
    out = subprocess.run([_sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    needed = [f for f in out.stdout.split() if f.endswith(".py")]
    assert needed, out.stdout

    df = io.open("deploy/l5_exec_shadow/Dockerfile").read()
    copied = [l.split()[1] for l in df.splitlines()
              if l.strip().startswith("COPY ")]

    def shipped(path):
        if path in copied:
            return True
        # a directory COPY ships everything beneath it
        if any(c.endswith("/") and path.startswith(c) for c in copied):
            return True
        # an __init__.py that is not copied leaves a NAMESPACE package, which
        # imports fine, so it is only needed when it has content of its own
        if os.path.basename(path) == "__init__.py":
            return os.path.getsize(path) == 0 or not [
                l for l in io.open(path).read().splitlines()
                if l.strip() and not l.strip().startswith("#")]
        return False

    missing = [f for f in needed if not shipped(f)]
    assert not missing, ("registered streams import modules the image does not "
                         "ship: %s" % missing)


def test_the_watchdog_leaves_the_three_original_streams_to_the_exit_path(
        tmp_path):
    """Only the rotating recorders are freshness-watched. The event-driven
    streams write on events, so silence there is not evidence of death and
    restarting them on it would be churn."""
    sup = _sup(tmp_path)
    created = _stub_start(sup, [])
    sup.watch_freshness()
    assert created == []
    for n in ("exec_shadow", "gemini_depth", "chain_validate"):
        assert len(S.STREAMS[n]) == 3, n


def test_health_reports_exactly_the_fields_the_owner_asked_for(tmp_path):
    sup = _sup(tmp_path)
    want = {"process_alive", "latest_output_timestamp", "output_age_seconds",
            "file_size", "last_size_change", "restart_count",
            "last_restart_reason"}
    h = sup.recorder_health()
    assert set(h) == set(S.STREAMS)
    for name in WATCHED:
        assert want <= set(h[name]), want - set(h[name])
