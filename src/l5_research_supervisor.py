"""ONE process that owns every L5 research stream, and accounts for its own gaps.

WHY A SUPERVISOR AND NOT THREE SERVICES. Three collectors is three things to
deploy, three restart policies and three ways to be quietly dead. One process
owns them, so there is one deployment, one volume and one place that knows
whether we were watching.

THE PROBLEM THIS EXISTS TO SOLVE, and it is not "processes crash". On
2026-09-15 the container was reclaimed at 14:27:14Z. Two collectors died with
it, their durable JSONL survived, and nothing noticed for nine minutes. The
stores then contained a stretch of time with no observations in it -- which is
INDISTINGUISHABLE, to every reader downstream, from a stretch of time in which
L5 did nothing and no quote filled.

So the contract here is not uptime. It is that DOWNTIME IS RECORDED AS
DOWNTIME:

    boot            every start, with the gap since each stream last wrote
    coverage_gap    an explicit, durable "we were not watching from A to B"
    stream_exit     a child died, with its exit code
    stream_restart  and when it came back
    heartbeat       so a silent store can be distinguished from a stopped one

An analysis that finds no fills in a window must be able to ask whether anyone
was looking, and `analysis/l5/coverage.py` answers from these markers.

RESUME, NEVER RESET. Every stream is started against the SAME out-dir it used
before, so its append-only store continues. Nothing here truncates, rotates or
rewrites a store.

READ ONLY, STRUCTURALLY. This module imports no executor, no signer and no
credential reader, and the image it ships in copies none. A test asserts the
import closure; there is no order verb anywhere in it.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

SCHEMA = "l5_research_coverage/v1"
COVERAGE = "coverage.jsonl"
HEARTBEAT_S = 60.0
# The preregistered chain-observer gate, reported on a slow cadence. It is a
# READ over the volume's own stores -- no network, no stream -- and it is
# emitted continuously so the exact metrics are visible as the sample
# accumulates, rather than only when someone remembers to ask.
GATE_S = 600.0
# a child that exits is restarted, but not in a tight loop: a persistent
# failure should be visible in the markers rather than hidden by churn
BACKOFF_S = (2.0, 5.0, 15.0, 30.0, 60.0)

# name -> (module, state subdirectory, the file whose last row dates the
# stream, and optionally extra argv). The dating entry may be a GLOB: a
# recorder that opens a new timestamped segment on every start has no single
# file, and the newest matching segment is the one that dates it.
STREAMS: Dict[str, tuple] = {
    "exec_shadow": ("src.l5_shadow.service", "l5-shadow-state", "routes.jsonl"),
    "gemini_depth": ("analysis.l5.gemini_depth.probe", "gemdepth",
                     "samples.jsonl"),
    "chain_validate": ("analysis.l5.chain_observer.validate", "chainval",
                       "validations.jsonl"),
    # OWNER 2026-09-17 P0. These two died silently inside the session container
    # TWICE -- 17:16 and 17:37 -- and nothing noticed for ten minutes the
    # second time. A watchdog inside that container shares its failure domain
    # and dies with them, so they belong here, where restartPolicyType is
    # ALWAYS and the process outlives the session.
    "book_context": ("src.l5_book_context", "l5-book-context",
                     "l5_book_context_*.jsonl",
                     ["--minutes", "1440", "--book-interval", "1.0",
                      "--activity-interval", "3.0", "--cohorts",
                      "BTC5m,BTC15m"]),
    "gemini_surface": ("src.gemini_l5_surface_shadow", "gemini-surface",
                       "gemini_surface_*.jsonl",
                       ["--minutes", "1440", "--interval", "2.0"]),
    # OWNER 2026-09-17. The frozen preregistered window study, moved here after
    # the container took it down mid-capture. The argv is EXACTLY the frozen
    # one it was relaunched with; nothing about the procedure changes by
    # running it under a supervisor.
    # OWNER 2026-09-17. The prospective ADAPTED surface shadow, running the
    # FROZEN L5_ADAPTED_V1 spec (sha fe9d6557...). Non-money-moving: no
    # client, no signer, no submit path, asserted by test in a fresh
    # subprocess. It records a desired surface BEFORE each interaction and
    # walks the evidence ladder as the tape arrives.
    "surface_shadow": ("src.l5_surface.shadow", "l5-surface-shadow",
                       "surface_shadow_*.jsonl",
                       ["--minutes", "1440", "--interval", "2.0",
                        "--cohorts", "BTC15m,BTC5m",
                        "--spec", "L5_ADAPTED_V1"]),
    "window_forensics": ("src.l5_window_forensics", "l5-window-forensics",
                         "seg_*/window_*.jsonl",
                         ["--windows", "3", "--lead-s", "120",
                          "--follow-s", "300"]),
}

#: streams whose every start gets its OWN dated out-dir.
#:
#: The window study names its tape after the WINDOW, not the start time, and
#: opens it with mode "a". Two runs that target the same boundary would
#: therefore append into one file and silently splice two partial captures
#: into something that reads like a complete window. A fresh directory per
#: start makes that impossible: the second run writes a different file, both
#: partials stay visible as partials, and nothing merges them.
SEGMENT_PER_START = frozenset({"window_forensics"})

#: per-stream override of STALE_AFTER_S, for streams with long LEGITIMATE
#: silences. The window study sleeps until the next boundary minus its lead --
#: up to ~780s of correct, intended quiet. Watching it at 300s would restart it
#: mid-wait forever and it would never capture anything. Process death is still
#: caught immediately by reap(); this threshold only bounds how long a wedged
#: but living process can hide.
STALE_OVERRIDE_S = {"window_forensics": 1200.0}

#: streams that exit 0 as part of working correctly, rather than by failing.
COMPLETES_NORMALLY = frozenset({"window_forensics"})

#: a stream whose newest row is older than this is STALE even if its process is
#: alive. Conservative on purpose: a real recorder writes every few seconds, so
#: five minutes of silence is never normal and is never a quiet market.
STALE_AFTER_S = 300.0

#: distinguishes "not resolved yet" from a genuinely unresolvable commit
_UNSET = object()


def _newest_segment(state_dir: str, fname: str) -> Optional[str]:
    """The file that dates a stream. A glob picks the newest segment, because
    a recorder that opens a new file on every start has no single store and
    reading a stale segment would report an old timestamp as current."""
    import glob as _g
    if "*" not in fname:
        p = os.path.join(state_dir, fname)
        return p if os.path.exists(p) else None
    hits = _g.glob(os.path.join(state_dir, fname))
    return max(hits, key=os.path.getmtime) if hits else None


def _last_row_time(path: str) -> Optional[float]:
    """Timestamp of the newest record in an append-only store, or None.

    Read from the file's own last line rather than from its mtime: mtime moves
    when anything touches the file, while the row is what an analysis actually
    sees, and the gap has to be measured in the same units the analysis reads.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            back = min(size, 65536)
            fh.seek(size - back)
            tail = fh.read().decode("utf-8", "replace").strip().splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        for k in ("t", "observed_at", "decided_at", "evaluated_at",
                  "resolved_at", "L5_OBSERVATION_CLOCK"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return None


class Supervisor:
    def __init__(self, state_root: str, *, hours: float = 0.0) -> None:
        self.root = state_root
        self.deadline = (time.time() + hours * 3600) if hours else None
        self.procs: Dict[str, subprocess.Popen] = {}
        self.fails: Dict[str, int] = {n: 0 for n in STREAMS}
        self._size_seen: Dict[str, tuple] = {}
        self._segdir: Dict[str, Optional[str]] = {}
        self._starts: Dict[str, int] = {}
        self._commit_sha: Any = _UNSET
        self._last_reason: Dict[str, Optional[str]] = {n: None for n in STREAMS}
        self.next_try: Dict[str, float] = {n: 0.0 for n in STREAMS}
        self.started_at = time.time()
        os.makedirs(self.root, exist_ok=True)

    # ------------------------------------------------------------- markers

    def mark(self, kind: str, **fields: Any) -> Dict[str, Any]:
        rec = {"schema": SCHEMA, "kind": kind, "t": time.time(), **fields}
        try:
            with open(os.path.join(self.root, COVERAGE), "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except OSError:
            pass                      # a marker we cannot write is still logged
        print("L5_SUPERVISOR " + json.dumps(rec, default=str), flush=True)
        return rec

    def last_heartbeat(self) -> Optional[float]:
        """When this supervisor was last demonstrably alive.

        Read from our OWN coverage log, which is written on a fixed cadence
        regardless of whether any venue did anything. That independence is the
        point -- see `boot` for why a stream's own store cannot answer this.
        """
        path = os.path.join(self.root, COVERAGE)
        if not os.path.exists(path):
            return None
        best = None
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 200000))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        for line in reversed(tail):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") in ("heartbeat", "shutdown", "boot"):
                t = r.get("t")
                if isinstance(t, (int, float)):
                    best = float(t)
                    break
        return best

    def boot(self) -> None:
        """Record the gap since each stream last wrote, BEFORE starting them.

        This is the only moment the gap is knowable: once a stream restarts it
        begins appending again and the hole closes silently.

        BUT A STREAM'S OWN STORE CANNOT MEASURE DOWNTIME, and reading it as if
        it could is an error in the opposite direction from the one this whole
        mechanism exists to prevent. `exec_shadow` writes a row only when L5
        acts, so its 49.8s "gap" across the 15:35Z redeploy was mostly L5 being
        quiet -- while `gemini_depth`, which samples at a fixed 2Hz, showed the
        true 2.3s. Treating the first as downtime would fire coverage_gap
        markers during quiet markets, and a marker that cries wolf is a marker
        nobody reads when it matters.

        So downtime comes from OUR OWN heartbeat, which ticks regardless of the
        venues, and the per-stream figures are reported as what they are: an
        upper bound that conflates our absence with the market's quiet.
        """
        gaps = {}
        for name, spec in STREAMS.items():
            sub, fname = spec[1], spec[2]
            seg = _newest_segment(os.path.join(self.root, sub), fname)
            last = _last_row_time(seg) if seg else None
            gaps[name] = {
                "last_row_utc": last,
                "gap_s": (round(self.started_at - last, 1) if last else None),
                "store": f"{sub}/{fname}",
            }
        hb = self.last_heartbeat()
        downtime = (round(self.started_at - hb, 1) if hb else None)
        self.mark("boot", pid=os.getpid(), streams=sorted(STREAMS),
                  since_last_row=gaps,
                  last_supervisor_signal_utc=hb,
                  downtime_s=downtime,
                  labels=("downtime_s is the REAL gap: this boot minus the "
                          "supervisor's own last heartbeat, which ticks "
                          "regardless of what the venues did. since_last_row "
                          "is per stream and is an UPPER BOUND -- a stream "
                          "that writes only on events (exec_shadow) cannot "
                          "distinguish our absence from a quiet market, while "
                          "a fixed-cadence sampler (gemini_depth) can. No "
                          "conclusion of NO ACTION or NO FILL may be drawn "
                          "over downtime_s."))
        # the marker fires on OUR downtime, not on a venue's quiet spell: a
        # coverage_gap that appears whenever L5 stops trading is noise, and a
        # marker that cries wolf is one nobody reads when it matters.
        if downtime and downtime > HEARTBEAT_S * 2:
            self.mark("coverage_gap", stream="__supervisor__",
                      from_utc=hb, to_utc=self.started_at, gap_s=downtime,
                      per_stream_upper_bounds={k: v["gap_s"]
                                               for k, v in gaps.items()},
                      labels=("NOT WATCHING between these instants -- measured "
                              "from our own heartbeat, so this is our absence "
                              "and not the market's quiet."))

    # ------------------------------------------------------------ children

    def _out_dir(self, name: str) -> str:
        """Where this stream's CURRENT start writes.

        For a segment-per-start stream that is a fresh dated directory, minted
        once in start() and reused by every later call, so the provenance
        record names the directory the child is really writing into."""
        base = os.path.join(self.root, STREAMS[name][1])
        if name not in SEGMENT_PER_START:
            return base
        seg = self._segdir.get(name)
        if not seg:
            # the COUNTER, not the clock, is what makes this unique. Two starts
            # inside one second -- a failed spawn retried immediately, say --
            # would otherwise land on the same directory name and append into
            # one tape, which is the exact splice this mechanism exists to
            # prevent.
            self._starts[name] = self._starts.get(name, 0) + 1
            seg = "seg_%s_%03d" % (
                time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                self._starts[name])
            self._segdir[name] = seg
        return os.path.join(base, seg)

    def _launchable(self, name: str) -> bool:
        """False while starting this stream would produce a capture that is
        not a complete preregistered window.

        The window study's first act is to sleep until the next boundary minus
        its lead. Start it at boundary-30s and it begins capturing IMMEDIATELY
        with 30s of pre-window coverage instead of the preregistered 120s --
        an incomplete window that nothing in the tape would flag at write
        time. Holding until the boundary passes costs one window and buys a
        correct one, which is the trade the owner asked for explicitly.
        """
        if name not in SEGMENT_PER_START:
            return True
        from src import l5_window_forensics as W   # lazy: the supervisor's own
        argv = STREAMS[name][3]                    # closure stays minimal
        lead = float(argv[argv.index("--lead-s") + 1])
        now = time.time()
        return ((((int(now) // W.DUR) + 1) * W.DUR) - lead - now) > 0

    def _cmd(self, name: str) -> List[str]:
        spec = STREAMS[name]
        extra = list(spec[3]) if len(spec) > 3 else []
        return ([sys.executable, "-m", spec[0], "--out-dir",
                 self._out_dir(name)] + extra)

    def start(self, name: str) -> None:
        if time.time() < self.next_try[name]:
            return
        if not self._launchable(name):
            return
        if name in SEGMENT_PER_START:
            self._segdir[name] = None      # this start gets its OWN segment
        out = self._out_dir(name)
        os.makedirs(out, exist_ok=True)
        try:
            p = subprocess.Popen(self._cmd(name), stdout=sys.stdout,
                                 stderr=subprocess.STDOUT)
        except Exception as exc:                              # noqa: BLE001
            self.mark("stream_start_failed", stream=name,
                      err=f"{type(exc).__name__}: {exc}")
            self.fails[name] += 1
            self.next_try[name] = time.time() + self._backoff(name)
            return
        self.procs[name] = p
        # EVERY segment gets its provenance, not only the ones a restart
        # opened, so any two segments can be compared for equivalence without
        # anyone having to assume the code was the same across them.
        self.mark("stream_restart" if self.fails[name] else "stream_start",
                  stream=name, pid=p.pid, out_dir=out,
                  consecutive_failures=self.fails[name],
                  provenance=self.provenance(name, self._cmd(name)),
                  labels="resumed against the existing store; nothing reset")

    def _commit(self) -> Optional[str]:
        """Resolved ONCE. A running process cannot change the commit it is
        running, and re-shelling out on every restart would put a subprocess
        spawn on the recovery path.

        In the deployed image there is no `.git`, so `git rev-parse` returns
        nothing and the answer is the RELEASE file the image ships instead --
        written at promotion time and copied in, so it needs no environment
        variable and nothing here can be configured by one.
        """
        if self._commit_sha is not _UNSET:
            return self._commit_sha
        try:
            self._commit_sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL).decode().strip() or None
        except Exception:                                     # noqa: BLE001
            self._commit_sha = None
        if not self._commit_sha:
            for cand in ("RELEASE", "/app/RELEASE"):
                try:
                    with open(cand, "r", encoding="utf-8") as fh:
                        self._commit_sha = (json.load(fh).get("code_sha")
                                            or None)
                    break
                except Exception:                             # noqa: BLE001
                    continue
        return self._commit_sha

    def provenance(self, name: str, cmd: List[str]) -> Dict[str, Any]:
        """Exactly what this segment was started from, so a post-restart
        segment can be PROVEN equivalent to the one before it rather than
        assumed to be."""
        import hashlib
        spec = STREAMS[name]
        commit = self._commit()
        mod_path = os.path.join(*spec[0].split(".")) + ".py"
        sha = None
        if os.path.exists(mod_path):
            h = hashlib.sha256()
            with open(mod_path, "rb") as fh:
                h.update(fh.read())
            sha = h.hexdigest()
        return {"stream": name, "commit": commit, "module": spec[0],
                "module_sha256": sha, "command": cmd,
                "state_subdir": spec[1], "dating_glob": spec[2],
                "out_dir": self._out_dir(name),
                "segment_per_start": name in SEGMENT_PER_START,
                "extra_argv": list(spec[3]) if len(spec) > 3 else [],
                "labels": ("equivalence evidence for this segment. A new "
                           "segment after a restart is a NEW segment and is "
                           "never presented as continuous with the last one.")}

    def recorder_health(self) -> Dict[str, Any]:
        """Freshness, not liveness. A process can be alive and writing nothing,
        which is exactly how these two died unnoticed: the PID was the thing
        being watched and the PID was fine right up until it wasn't."""
        now = time.time()
        out: Dict[str, Any] = {}
        for name, spec in STREAMS.items():
            sub, fname = spec[1], spec[2]
            sdir = os.path.join(self.root, sub)
            seg = _newest_segment(sdir, fname)
            last = _last_row_time(seg) if seg else None
            size = os.path.getsize(seg) if seg else None
            prev = self._size_seen.get(name)
            if size is not None:
                if prev is None or prev[0] != seg:
                    # FIRST sighting of THIS segment: its own mtime, never
                    # `now`. A store that was already dead when this supervisor
                    # booted must not be handed a fresh clock by the mere act
                    # of being looked at. For a segment just opened by a
                    # restart the mtime is ~now, which is also correct.
                    self._size_seen[name] = (seg, size, os.path.getmtime(seg))
                elif size != prev[1]:
                    self._size_seen[name] = (seg, size, now)
            changed = self._size_seen.get(name, (None, None, None))[2]
            out[name] = {
                "process_alive": name in self.procs,
                "latest_output_timestamp": last,
                "output_age_seconds": (round(now - last, 1)
                                       if last is not None else None),
                "file_size": size,
                "last_size_change": changed,
                "size_age_seconds": (round(now - changed, 1)
                                     if changed is not None else None),
                "segment": seg,
                "restart_count": self.fails.get(name, 0),
                "last_restart_reason": self._last_reason.get(name),
            }
        return out

    def watch_freshness(self) -> None:
        """RECORDER_STALE -> restart that recorder ONLY, with its exact frozen
        command, into a NEW segment. The gap is preserved and reported; it is
        never backfilled and the stores are never repaired."""
        health = self.recorder_health()
        for name, h in health.items():
            if len(STREAMS[name]) <= 3:
                continue          # only the rotating recorders are watched here
            if h["segment"] is None and not h["process_alive"]:
                continue          # never started here; the boot path owns that
            if not h["process_alive"] and not self._launchable(name):
                continue          # deliberately held for a clean window, not
                                  # dead -- reap() already recorded the exit
            age = h["output_age_seconds"]
            sage = h["size_age_seconds"]
            dead = not h["process_alive"]
            limit = STALE_OVERRIDE_S.get(name, STALE_AFTER_S)
            row_stale = age is not None and age > limit
            # a recorder that opened a segment and then wrote nothing into it
            # has no row old enough to be stale, so the BYTES are the only
            # witness left. This is the shape the second death actually took.
            size_stale = sage is not None and sage > limit
            if not (dead or row_stale or size_stale):
                continue
            if time.time() < self.next_try.get(name, 0.0):
                continue
            basis = ("process_gone" if dead else
                     "rows_stopped>%ds" % int(limit) if row_stale else
                     "file_not_growing>%ds" % int(limit))
            self.mark("RECORDER_STALE", stream=name, stale_basis=basis,
                      last_good_output_utc=h["latest_output_timestamp"],
                      output_age_seconds=age, size_age_seconds=sage,
                      last_size_change=h["last_size_change"],
                      process_alive=h["process_alive"],
                      segment=h["segment"], file_size=h["file_size"],
                      labels=("the store stopped advancing. The interval from "
                              "last_good_output_utc to the next segment's "
                              "first row is a REAL GAP and stays one."))
            self._last_reason[name] = basis
            p = self.procs.pop(name, None)
            if p is not None:
                try:
                    p.terminate()
                except Exception:                             # noqa: BLE001
                    pass
            self.fails[name] = self.fails.get(name, 0) + 1
            self.start(name)
            self.mark("RECORDER_RESTART", stream=name,
                      reason=self._last_reason[name],
                      restart_count=self.fails[name],
                      provenance=self.provenance(name, self._cmd(name)),
                      labels=("a NEW segment, not a continuation. Nothing "
                              "backfills the missing interval."))

    def _backoff(self, name: str) -> float:
        i = min(self.fails[name], len(BACKOFF_S)) - 1
        return BACKOFF_S[max(0, i)]

    def reap(self) -> None:
        for name in list(self.procs):
            p = self.procs[name]
            rc = p.poll()
            if rc is None:
                continue
            self.procs.pop(name, None)
            # A stream that is SUPPOSED to finish is not failing when it does.
            # The window study exits 0 after its batch of windows; counting
            # that as a failure would climb the backoff and make a healthy
            # cadence read as a restart loop in recorder_health.
            clean = rc == 0 and name in COMPLETES_NORMALLY
            if not clean:
                self.fails[name] += 1
                self.next_try[name] = time.time() + self._backoff(name)
            self.mark("stream_exit", stream=name, pid=p.pid, returncode=rc,
                      consecutive_failures=self.fails[name],
                      completed_normally=clean,
                      labels=("this stream stopped observing at this instant; "
                              "the interval until stream_restart is a coverage "
                              "gap"))

    def stop(self, *_a: Any) -> None:
        self.mark("shutdown", labels=("supervisor asked to stop; children are "
                                      "terminated and the gap starts here"))
        for name, p in list(self.procs.items()):
            try:
                p.terminate()
            except Exception:                                 # noqa: BLE001
                pass
        raise SystemExit(0)

    # ---------------------------------------------------------------- loop

    def report_gate(self) -> Optional[dict]:
        """Print the preregistered gate's current standing.

        A REPORT, never an action: it cannot promote anything, and the
        observer stays unpromoted regardless of what it says. Failing to
        produce it is logged rather than fatal -- a reporting bug must not
        take down the collectors it reports on.
        """
        try:
            from analysis.l5.chain_observer import gate as G
            rep = G.report(self.root)
        except Exception as exc:                              # noqa: BLE001
            print("L5_GATE " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:200]}),
                flush=True)
            return None
        print("L5_GATE " + json.dumps({
            "ok": True, "PROMOTE": rep["PROMOTE"],
            "passing": f"{rep['gates_passing']}/{rep['gates_total']}",
            "blocking": rep["blocking"],
            "joined": rep["gates"]["G-C1_sample_size"]["evidence"]["joined"],
            "threshold": G.MIN_JOINED,
            "attributed": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "attributed"),
            "price_rate": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "price_rate"),
            "size_rate": rep["gates"]["G-C4_attribution"]["evidence"].get(
                "size_rate"),
            "feed_only": rep["gates"]["G-C2_no_unexplained_misses"][
                "evidence"]["feed_only"],
            "chain_only_unresolved": rep["gates"][
                "G-C3_chain_only_classified"]["evidence"]["unresolved"],
            "retractions": rep["gates"]["G-C5_finality"]["evidence"].get(
                "retraction_events"),
            "runs_dropped_unobserved": rep["gates"]["G-C7_coverage"][
                "evidence"].get("runs_dropped_as_unobserved"),
            "excluded_pre_qualifying": rep["excluded_as_pre_qualifying"],
            "scope": ("shadow observation clock only; this report promotes "
                      "nothing and does not enable live trading"),
        }, default=str), flush=True)
        return rep

    def report_economics(self) -> Optional[dict]:
        """P2: what the evidenced fills are worth, on the gate's cadence.

        A REPORT. It reads the shadow's own stores and the venue's published
        resolutions; it places nothing and holds nothing. `realized_shadow` is
        a settled counterfactual and is never realized profit.
        """
        out_dir = os.path.join(self.root, "l5-shadow-state")
        try:
            from analysis.l5.economics import build as E
            rep = E.build(out_dir)
            E.write(out_dir, rep)
        except Exception as exc:                              # noqa: BLE001
            print("L5_ECONOMICS " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:200]}),
                flush=True)
            return None
        print("L5_ECONOMICS " + json.dumps({
            "ok": True,
            "maker_quotes_decided": rep["maker_quotes_decided"],
            "evidenced_fills": rep["evidenced_fills"],
            "positions": rep["positions"],
            "verdicts": rep["census"]["by_venue_verdict"],
            "why_no_fill": rep["fill_forensics"]["by_venue_cause"],
            "evidenced_fill_rate": rep.get("evidenced_fill_rate"),
            "paired_positions": rep.get("paired_positions"),
            "one_sided_positions": rep.get("one_sided_positions"),
            "by_venue": rep["by_venue"], "by_cohort": rep["by_cohort"],
            "by_clock": rep["by_clock"],
            "labels": rep["labels"],
        }, default=str), flush=True)
        return rep

    def report_dual_clock(self) -> Optional[dict]:
        """P1: what the 15s recovery buys, on the same frozen intents.

        A REPORT over stores already written. The chain observer stays
        UNPROMOTED: this measures what promoting it would be worth, and
        promotes nothing.
        """
        out_dir = os.path.join(self.root, "l5-shadow-state")
        try:
            from analysis.l5.dual_clock import study as DC
            rep = DC.report(out_dir)
        except Exception as exc:                              # noqa: BLE001
            print("L5_DUAL_CLOCK " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:200]}),
                flush=True)
            return None
        print("L5_DUAL_CLOCK " + json.dumps(
            {"ok": True, **{k: v for k, v in rep.items()
                            if k not in ("rows", "out_dir")}},
            default=str), flush=True)
        return rep

    def report_pairs(self) -> Optional[dict]:
        """P0: pair FORMATION, and the reactive-vs-state-driven comparison.

        The promotion-critical statistic. Reports only; places nothing.
        """
        out_dir = os.path.join(self.root, "l5-shadow-state")
        for name, mod in (("L5_PAIR_FORMATION",
                           "analysis.l5.pair_formation.study"),
                          ("L5_ARMS", "analysis.l5.pair_formation.arms"),
                          ("L5_ARM_SCORE",
                           "analysis.l5.pair_formation.arm_score"),
                          ("L5_FRONTIER",
                           "analysis.l5.pair_formation.frontier"),
                          # ADDITIVE REPORTING ONLY. The ADAPTED arms read the
                          # same evidenced-fill stream and write their own
                          # store; they do not alter R/S/D, which stays frozen.
                          ("L5_ADAPTED",
                           "analysis.l5.adapted.collect"),
                          # the FAILED experiment's decomposition. Diagnostic
                          # counterfactuals over the frozen sample; no arm, no
                          # threshold selected, nothing deployed from it.
                          ("L5_ADAPTED_FAILURE",
                           "analysis.l5.adapted.failure")):
            try:
                import importlib
                rep = importlib.import_module(mod).report(out_dir)
            except Exception as exc:                          # noqa: BLE001
                print(f"{name} " + json.dumps(
                    {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:200]}),
                    flush=True)
                continue
            print(f"{name} " + json.dumps(
                {"ok": True, **{k: v for k, v in rep.items()
                                if k not in ("rows", "out_dir")}},
                default=str), flush=True)
        return None

    def report_window_surface(self, rows: bool = False) -> Optional[dict]:
        """L5_PASSIVE_SURFACE_PROSPECTIVE_V1, printed from where the volume is.

        The tapes live on /state/l5-research and nothing outside this container
        can read them, so the scoring runs here and only its RESULTS travel out
        through the log. Eligibility is evaluated first by the same call, so a
        window that fails a frozen gate is never scored.

        A REPORT. It reads tapes already written, places nothing, and promotes
        nothing. `rows` emits one line per scored parent and per reversal, and
        is passed once at boot rather than on the cadence so the detail is
        available without repeating every ten minutes.

        The COMPLETE artifact -- every scored parent and reversal row -- is
        also written next to the tapes, because the log rotates and a
        mechanism label without the rows behind it cannot be re-checked.
        """
        root = os.path.join(self.root, "l5-window-forensics")
        try:
            from analysis.l5.window_forensics import prospective as PS
            # enrich=True recovers the chain receipts the recorder failed
            # to write when it pinned a CA bundle absent from this image.
            # Every fill carries its tx, so this needs no new capture.
            rep = PS.report(root, state_root=self.root, enrich=True)
            rep["artifact_path"] = PS.persist(rep, root)
        except Exception as exc:                              # noqa: BLE001
            print("L5_PSP " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:300]}),
                flush=True)
            return None
        slim = {k: v for k, v in rep.items() if k != "windows"}
        slim["windows"] = [{k: v for k, v in w.items()
                            if k not in ("scored_parents", "reversal_rows")}
                           for w in rep["windows"]]
        print("L5_PSP_SUMMARY " + json.dumps(slim, default=str), flush=True)
        if rows:
            for w in rep["windows"]:
                for r in w["scored_parents"]:
                    print("L5_PSP_ROW " + json.dumps(
                        {"window": w["window_start"], **r}, default=str),
                        flush=True)
                for r in w["reversal_rows"]:
                    print("L5_PSP_REV " + json.dumps(
                        {"window": w["window_start"], **r}, default=str),
                        flush=True)
        return rep

    def report_surface_shadow(self) -> Optional[dict]:
        """What the prospective ADAPTED surface did, from its own tape.

        A REPORT. The shadow places nothing, and this reads what it wrote.
        Stages are reported separately on purpose: the distance between
        BOOK_REACHED and EVIDENCED_FILL is the measurement, not an
        inconvenience to average away.
        """
        d = os.path.join(self.root, "l5-surface-shadow")
        try:
            from analysis.l5.surface_shadow import report as SR
            rep = SR.report(d)
        except Exception as exc:                              # noqa: BLE001
            print("L5_SURFACE_SHADOW " + json.dumps(
                {"ok": False, "err": f"{type(exc).__name__}: {exc}"[:300]}),
                flush=True)
            return None
        print("L5_SURFACE_SHADOW " + json.dumps(rep, default=str), flush=True)
        return rep

    def run(self) -> int:
        self.boot()
        # once, with the per-parent detail: the tapes cannot be read from
        # outside this container, so this is the only way the evidence travels
        self.report_window_surface(rows=True)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        last_hb = 0.0
        last_gate = 0.0
        while self.deadline is None or time.time() < self.deadline:
            for name in STREAMS:
                if name not in self.procs:
                    self.start(name)
            self.reap()
            self.watch_freshness()
            now = time.time()
            if now - last_hb >= HEARTBEAT_S:
                last_hb = now
                self.mark("heartbeat",
                          uptime_s=round(now - self.started_at, 1),
                          running=sorted(self.procs),
                          not_running=sorted(set(STREAMS) - set(self.procs)),
                          failures=dict(self.fails),
                          recorder_health=self.recorder_health())
            if now - last_gate >= GATE_S:
                last_gate = now
                self.report_gate()
                self.report_economics()
                self.report_dual_clock()
                self.report_pairs()
                self.report_window_surface()
                self.report_surface_shadow()
            time.sleep(2.0)
        self.stop()
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default="/state/l5-research")
    ap.add_argument("--hours", type=float, default=0.0)
    a = ap.parse_args(argv)
    return Supervisor(a.state_root, hours=a.hours).run()


if __name__ == "__main__":
    raise SystemExit(main())
